"""SSO login -> offadmin OAuth -> create one offline user."""
import os
import json
from pathlib import Path

from common.admin_client import ADMIN_URL, authenticate_admin
from common.phone_sequence import reserve_phone
from common.sso_login import TIMEOUT
from common.state_store import record_latest_user
from common.state_store import ROOT
from common.identity_client import latest_user


def test_add_offline_user(session, database_checks):
    authenticate_admin(session)
    config = json.loads((ROOT / "config" / "user_creation.json").read_text(encoding="utf-8"))
    database_checks.employee_line_config(config["employeeLineConfigId"], config["employeeLine"], config["employeeLogin"])
    state = Path(os.getenv("PHONE_STATE_FILE", str(Path(__file__).resolve().parents[2] / ".state" / "phone.json")))
    phone = reserve_phone(state)
    print(f"[新增 1/3] 本次 phone={phone}；下次号码已持久化", flush=True)
    payload = {key: config[key] for key in ("employeeLineConfigId", "employeeLine", "registrationType")}
    payload["phone"] = phone
    print(f"[新增] 员工 WhatsApp：{config['employeeLogin']} / {payload['employeeLine']}（配置 {payload['employeeLineConfigId']}）", flush=True)
    print("[新增 2/3] POST /user/user/offline/addUser", flush=True)
    response = session.post(ADMIN_URL + "/user/user/offline/addUser", data=payload,
                            timeout=TIMEOUT, allow_redirects=False)
    assert response.status_code == 200, f"新增用户 HTTP {response.status_code}；本次号码已占用，请勿重发"
    assert "json" in response.headers.get("Content-Type", "").lower(), "新增接口未返回 JSON，可能登录已失效"
    body = response.json()
    actual = {"code": body.get("code"), "msg": body.get("msg")}
    print(f"[新增 3/3] 响应摘要：{actual}", flush=True)
    assert str(body.get("code")) == "0", f"用户建档失败：{actual}"
    record_latest_user(phone, payload["employeeLine"])
    assert body.get("msg") == "线下用户建档成功。", f"建档文案不符合预期：{actual}"
    user, _, _ = latest_user(session, ROOT / ".state" / "latest_user.json")
    database_checks.created_user(user, phone, config["employeeLineConfigId"], config["employeeLine"], config["employeeLogin"])
