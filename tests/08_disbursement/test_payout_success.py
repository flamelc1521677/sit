"""场景 08：对最新借款的人审通过单，将对应放款单审批为成功并校验同步。"""
import json
import os
import re

from common.admin_client import ADMIN_URL, authenticate_admin
from common.payout_client import PayoutAttempt, build_payout_payload
from common.phone_sequence import locked
from common.sso_login import TIMEOUT
from common.state_store import ROOT, write_json


def test_payout_approved_success(session, database_checks):
    authenticate_admin(session)
    database_checks.login_account(os.environ["SSO_USERNAME"])
    latest = json.loads((ROOT / ".state/latest_user.json").read_text(encoding="utf-8"))
    loans = json.loads((ROOT / ".state/loan_attempts.json").read_text(encoding="utf-8"))
    targets = [loan for loan in loans.values() if loan.get("phone") == latest["phone"] and loan.get("status") == "db_verified"]
    assert len(targets) == 1, "最新用户没有唯一已提交并校验的借款"
    with locked(ROOT / ".state/payout_operation.lock"):
        header, contract, before = database_checks.payout_context(targets[0]["header_id"])
        print(f"[放款 1/4] 目标用户 {latest['phone']}，合同 {before['BID_ID']}，放款单 {before['ID']}，当前状态 {before['STATE']}", flush=True)
        path = ROOT / ".state/payout_attempts.json"
        attempts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        assert before["ID"] not in attempts, "放款单已有修改尝试记录，禁止自动重试"
        response = session.get(ADMIN_URL + "/pay/borTransferBillMoco/edit/" + before["ID"], timeout=TIMEOUT)
        assert response.status_code == 200
        match = re.search(r'var\s+currentDateTime\s*=\s*"([^"]+)"', response.text)
        assert match, "页面未返回商户当前时间"
        payload = build_payout_payload(response.text, before, match.group(1))
        old_flows = database_checks.payout_flow_ids(before["ID"])
        fresh = database_checks.payout_bill(before["ID"])
        attempt = PayoutAttempt(session, before, payload)
        attempts[before["ID"]] = {"status": "request_sent", "phone": latest["phone"], "header_id": header["ID"], "contract_id": before["BID_ID"], "borrow_id": before["CONTRACT_ID"], "biz_id": before["BIZ_ID"]}
        write_json(path, attempts)
        print("[放款 2/4] POST /pay/borTransferBillMoco/edit，state=1，仅提交一次", flush=True)
        saved = attempt.execute(fresh)
        attempts[before["ID"]].update({"status": "api_success", "code": saved.get("code"), "msg": saved.get("msg")})
        write_json(path, attempts)
        print(f"[放款 3/4] 响应 code={saved.get('code')}，msg={saved.get('msg')}", flush=True)
        database_checks.payout_success(before, payload, old_flows, os.environ["SSO_USERNAME"])
        attempts[before["ID"]]["status"] = "db_verified"
        write_json(path, attempts)
        print("[放款 4/4] 放款单成功，修改流水及借款/合同状态同步校验通过", flush=True)
