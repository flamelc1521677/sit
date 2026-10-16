"""Upload the fixed ID fixture, save a unique document number, and read it back."""
import json
import re
from html.parser import HTMLParser

from common.admin_client import ADMIN_URL
from common.sso_login import TIMEOUT
from common.state_store import ROOT


def checked_json(response, stage):
    assert response.status_code == 200, f"{stage} HTTP {response.status_code}"
    assert "json" in response.headers.get("Content-Type", "").lower(), f"{stage} 未返回 JSON，可能登录已失效"
    body = response.json()
    assert str(body.get("code")) == "0", f"{stage}失败：code={body.get('code')}，msg={body.get('msg')}"
    return body


class DetailFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") in {"uuid", "appOwnership", "adhFortChannel"}:
            self.fields[attrs["name"]] = attrs.get("value", "")


def latest_user(session, state_path):
    assert state_path.exists(), "没有最新新增用户记录，请先执行场景 02"
    target = json.loads(state_path.read_text(encoding="utf-8"))
    response = session.post(ADMIN_URL + "/user/user/list",
                            data={"phone": target["phone"], "employeeLine": target["employeeLine"],
                                  "pageNum": 1, "pageSize": 10}, timeout=TIMEOUT)
    assert response.status_code == 200, "查询最新新增用户失败"
    body = response.json()
    assert body.get("total") == 1 and len(body.get("rows", [])) == 1, "最新新增用户查询结果不唯一，停止操作"
    user = body["rows"][0]
    expected_lines = {target["employeeLine"]}
    config = json.loads((ROOT / "config/user_creation.json").read_text(encoding="utf-8"))
    if target["employeeLine"] == config["employeeLine"]:
        expected_lines.add(config["employeeLineName"] + "-" + config["employeeLine"])
        assert user.get("employeeName") == config["employeeLogin"], "用户归属员工与固定配置不一致"
    assert user.get("employeeLine") in expected_lines, "用户 IM 与新增记录不一致"
    assert user.get("id") and user.get("uuid") and user.get("appOwnership"), "用户身份字段不完整"
    response = session.get(ADMIN_URL + "/user/user/detail/" + str(user["id"]),
                           params={"appOwnership": user["appOwnership"]}, timeout=TIMEOUT)
    assert response.status_code == 200, "用户详情读取失败"
    parser = DetailFields()
    parser.feed(response.text)
    assert parser.fields.get("uuid") == user["uuid"], "详情 UUID 与目标用户不一致"
    assert parser.fields.get("appOwnership") == user["appOwnership"], "详情所属应用与目标用户不一致"
    return user, target["phone"], parser.fields.get("adhFortChannel", "")


def ocr_info(session, user):
    response = session.post(ADMIN_URL + "/user/user/identity/ocrInfo",
                            data={"uuid": user["uuid"], "appOwnership": user["appOwnership"]}, timeout=TIMEOUT)
    data = checked_json(response, "身份信息回查").get("data")
    assert isinstance(data, dict), "身份信息回查缺少 data"
    return data


def upload_identity(session, user, image_path, channel):
    with image_path.open("rb") as image:
        response = session.post(ADMIN_URL + "/user/user/identity/uploadOcr",
            data={"uuid": user["uuid"], "appOwnership": user["appOwnership"],
                  "cardType": "ocrFort", "ocrChannel": channel, "i18n": "zh_CN"},
            files={"file": (image_path.name, image, "image/png")},
            timeout=(10, 120), allow_redirects=False)
    body = checked_json(response, "身份证上传 OCR")
    data = body.get("data")
    assert isinstance(data, dict) and data.get("documentNo") and data.get("documentName"), "OCR 缺少证件号或姓名"
    assert data.get("cardfrontImg"), "OCR 未返回正面图片"
    return ocr_info(session, user)


def save_identity(session, user, info, document_no):
    # Preserve OCR-recognized personal fields; change only the requested document number.
    names = ["fullName", "birthday", "gender", "region", "province", "city",
             "firstName", "secondName", "thaiDateOfBirth", "thaiDateOfIssue", "issueDate",
             "thaiDateOfExpiry", "issueAuthority", "religion", "adhFortChannel", "handIdPicUrl"]
    payload = {name: "" if info.get(name) is None else str(info.get(name, "")) for name in names}
    payload.update({"uuid": user["uuid"], "appOwnership": user["appOwnership"],
                    "aadhaarNo": document_no, "aadhaarName": payload["fullName"], "i18n": "zh_CN"})
    response = session.post(ADMIN_URL + "/user/user/saveIdentityVerification",
                            files={name: (None, value) for name, value in payload.items()},
                            timeout=TIMEOUT, allow_redirects=False)
    body = checked_json(response, "保存个人身份信息")
    assert any(word in str(body.get("msg", "")) for word in ("成功", "success")), "保存响应缺少成功文案"
    return body


def verify_persisted_identity(before, after, document_no):
    actual_no = after.get("aadhaarNo")
    assert actual_no, "回查未返回持久化证件号"
    if re.fullmatch(r"MAPA\d{6}HMCRND05", str(actual_no)):
        assert actual_no == document_no, "持久化证件号与请求不一致"
    else:
        # Current service returns encrypted aadhaarNo and does not echo it on save.
        # Check that the stored value changed; do not pretend ciphertext proves the exact plaintext.
        assert actual_no != before.get("aadhaarNo"), "证件号未从 OCR 原号码更新"
    assert after.get("fullName") == before.get("fullName"), "OCR 姓名保存后发生变化"
