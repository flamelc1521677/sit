"""场景 03：最新新增用户 -> 身份证 OCR 与保存 -> 银行卡保存与回查。"""
import os
from pathlib import Path

from common.admin_client import authenticate_admin
from common.bank_client import save_and_verify_bank_card
from common.identity_client import latest_user, ocr_info, save_identity, upload_identity, verify_persisted_identity
from common.identity_sequence import reserve_identity
from common.state_store import ROOT, write_json


def test_upload_ocr_and_save_identity(session, database_checks):
    image = Path(os.getenv("IDENTITY_IMAGE", str(ROOT / "assets" / "identity_front.png")))
    assert image.is_file(), "固定测试证件图片不存在"
    authenticate_admin(session)
    user, phone, channel = latest_user(session, ROOT / ".state" / "latest_user.json")
    print(f"[身份 1/5] 目标用户 id={user['id']}，phone={phone}", flush=True)
    previous_cipher = database_checks.identity_snapshot(user)
    print("[身份 2/5] 上传固定图片并验证 OCR 识别", flush=True)
    info = upload_identity(session, user, image, channel)
    state = Path(os.getenv("IDENTITY_STATE_FILE", str(ROOT / ".state" / "identity_date.json")))
    document_no, birthday = reserve_identity(state)
    print(f"[身份 3/5] 证件号日期段={birthday:%y%m%d}，下一日期已持久化", flush=True)
    print("[身份 4/5] 保存修改后的证件号及 OCR 个人信息", flush=True)
    saved = save_identity(session, user, info, document_no)
    print(f"保存响应：code={saved.get('code')}，msg={saved.get('msg')}", flush=True)
    result = {"user_id": user["id"], "phone": phone, "date_segment": birthday.strftime("%y%m%d"),
              "code": saved.get("code"), "msg": saved.get("msg"), "readback_verified": False}
    write_json(ROOT / ".state" / "last_identity_result.json", result)
    persisted = ocr_info(session, user)
    verify_persisted_identity(info, persisted, document_no)
    database_checks.identity(user, document_no, persisted, previous_cipher)
    result["readback_verified"] = True
    write_json(ROOT / ".state" / "last_identity_result.json", result)
    print("[身份 5/5] 回查通过：证件号已更新，OCR 姓名保持一致", flush=True)
    # Only continue with the same user after identity save AND readback have passed.
    save_and_verify_bank_card(session, user, phone, database_checks)
