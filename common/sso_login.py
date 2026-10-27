"""SSO login integration test. Credentials are supplied through environment variables."""
import base64
import json
import os
import secrets
import time
from pathlib import Path

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from common.sso_credentials import sso_credentials

BASE_URL = os.getenv("SSO_BASE_URL", "https://sso.suxi-test.com").rstrip("/")
TIMEOUT = (10, 50)


def get_data(session, path):
    response = session.get(BASE_URL + path, timeout=TIMEOUT)
    assert response.status_code == 200, f"{path}: HTTP {response.status_code}"
    body = response.json()
    assert str(body.get("code")) == "200", f"{path}: {body.get('msg')}"
    return body["data"]


def login(session, client_id=None, username=None, password=None):
    implicit_login = username is None and password is None
    username, password = sso_credentials(username, password)
    if implicit_login:
        # Keep business audit/DB assertions on the real requester; explicit reviewers stay separate.
        os.environ["SSO_USERNAME"] = username

    # Load the OCR model before obtaining the short-lived captcha.
    mode = os.getenv("SSO_CAPTCHA_MODE", "ocr")
    if mode not in {"ocr", "manual"}:
        pytest.fail("SSO_CAPTCHA_MODE 必须是 ocr 或 manual", pytrace=False)
    if mode == "ocr":
        import ddddocr
        recognizer = ddddocr.DdddOcr(show_ad=False)

    print("[1/5] 获取密码加密公钥", flush=True)
    key = get_data(session, "/sso/auth/publicKey")
    print("[2/5] 获取本次验证码及 token", flush=True)
    captcha = get_data(session, "/sso/auth/captchaCode")
    fetched_at = time.monotonic()
    image = base64.b64decode(captcha["imageBase64Data"].split(",", 1)[-1], validate=True)
    if mode == "manual":
        folder = Path(__file__).resolve().parents[1] / "artifacts"
        folder.mkdir(exist_ok=True)
        image_path = folder / "captcha.png"
        image_path.write_bytes(image)
        print(f"验证码图片：{image_path.resolve()}")
        if os.name == "nt":
            os.startfile(str(image_path.resolve()))
        code = input("请输入验证码（有效期 60 秒）：").strip()
    else:
        code = recognizer.classification(image).strip()
    print(f"[3/5] 验证码处理完成（{mode}）", flush=True)
    assert code, "验证码识别结果为空；请使用 manual 模式"
    assert time.monotonic() - fetched_at < float(captcha.get("expireTime", 60)), "验证码已过期，请重新运行"

    public_key = serialization.load_der_public_key(base64.b64decode(key["publicKey"]))
    plaintext = json.dumps({"pwd": password, "ts": int(time.time() * 1000),
                            "nonce": secrets.token_hex(8)}, separators=(",", ":")).encode()
    encrypted = public_key.encrypt(plaintext, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    payload = {"loginName": username, "keyId": key["keyId"],
               "encrypted": base64.b64encode(encrypted).decode(),
               "captchaCode": code, "captchaToken": captcha["captchaToken"],
               "loginDeviceId": os.getenv("SSO_DEVICE_ID", "pytest-" + secrets.token_hex(16)),
               "loginTimeZone": "Asia/Shanghai", "loginUtcOffsetMinutes": 480}
    if client_id or os.getenv("SSO_CLIENT_ID"):
        payload["loginSystemClientId"] = client_id or os.environ["SSO_CLIENT_ID"]
    # One attempt only: avoid retrying credentials or hiding an actual failure.
    print("[4/5] 加密密码并发送 POST /sso/auth/validate", flush=True)
    response = session.post(BASE_URL + "/sso/auth/validate", json=payload, timeout=TIMEOUT)
    print(f"[5/5] 检查响应：HTTP {response.status_code}", flush=True)
    assert response.status_code == 200, f"登录 HTTP 状态码：{response.status_code}"
    body = response.json()
    # Only expose code and msg on failure, never tokens or full response bodies.
    actual = {"code": body.get("code"), "msg": body.get("msg")}
    print(f"登录响应摘要：{actual}")
    hint = "；如验证码错误，请使用 manual 模式" if str(body.get("code")) == "9999" else ""
    assert str(body.get("code")) == "200", f"业务登录失败：{actual}{hint}"
    assert body.get("msg") == "result.success", f"成功文案与预期不一致：{actual}"

