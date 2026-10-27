"""场景 05：最新新增用户，先试算，再提交借款，校验数据库主单与分期计划。"""
import json
import os
from pathlib import Path

from common.admin_client import ADMIN_URL, authenticate_admin
from common.identity_client import checked_json
from common.loan_client import LoanAttempt, application_inputs, repeat_loan_history
from common.loan_target import loan_target_user
from common.phone_sequence import locked
from common.sso_login import TIMEOUT
from common.state_store import ROOT, write_json


def test_trial_then_start_loan(session, database_checks):
    config = json.loads(Path(os.getenv("LOAN_CONFIG_FILE", str(ROOT / "config/loan_application.json"))).read_text(encoding="utf-8"))
    authenticate_admin(session)
    with locked(ROOT / ".state" / "loan_operation.lock"):
        user, phone, _ = loan_target_user(session)
        database_checks.prealert_user(user, phone)
        attempts_path = ROOT / ".state" / "loan_attempts.json"
        attempts = json.loads(attempts_path.read_text(encoding="utf-8")) if attempts_path.exists() else {}
        key = user["appOwnership"] + ":" + user["uuid"]
        before = database_checks.loan_headers(user)
        repeat_id = os.getenv("LOAN_REPEAT_REQUEST_ID", "").strip()
        repeat_user = os.getenv("LOAN_REPEAT_USER_ID", "").strip()
        history = []
        if repeat_id or repeat_user:
            history = repeat_loan_history(attempts.get(key), before, repeat_id, repeat_user, user["id"])
            print(f"[复借] 用户 {user['id']}，独立申请标识 {repeat_id}，保留 {len(history)} 笔历史", flush=True)
        else:
            assert key not in attempts, "该用户已有提交尝试记录，禁止自动重复借款；请先核对后台和数据库"
            assert not before, "目标用户已有借款主记录，停止重复申请"
        print(f"[借款 1/5] 目标用户 id={user['id']}，phone={phone}，检查借款资格", flush=True)
        response = session.get(ADMIN_URL + "/user/user/offline/startLoan/precheck",
                               params={"id": user["id"], "appOwnership": user["appOwnership"]}, timeout=TIMEOUT)
        checked_json(response, "借款前置检查")
        response = session.get(ADMIN_URL + "/user/user/offline/startLoan/" + str(user["id"]),
                               params={"appOwnership": user["appOwnership"]}, timeout=TIMEOUT)
        assert response.status_code == 200, "读取借款表单失败"
        inputs = application_inputs(response.text, user, config)
        products = checked_json(session.get(ADMIN_URL + "/user/user/offline/startLoan/products", timeout=TIMEOUT), "读取借款产品")["data"]
        assert any(str(item["productId"]) == inputs["productId"] for item in products), "参考产品当前不可用"
        database_checks.loan_profile(user, inputs["payoutBankCardIds"])
        print(f"[借款 2/5] 试算：产品 {inputs['productId']}、金额 {inputs['loanAmount']}、{inputs['installmentCount']}期、每期{inputs['periodDays']}天", flush=True)
        attempt = LoanAttempt(session, inputs)
        trial = attempt.trial()
        database_checks.trial_did_not_submit(user, before)
        print(f"[借款 3/5] 试算通过，共{len(attempt.plan)}期，实到金额 {attempt.submission['inHandAmount']}，税费 {attempt.submission['taxes']}", flush=True)
        attempts[key] = {"status": "request_sent", "user_id": user["id"], "phone": phone,
                         "repeat_request_id": repeat_id, "previous_loans": history,
                         "productId": inputs["productId"], "loanAmount": inputs["loanAmount"],
                         "submitted_by": os.environ["SSO_USERNAME"]}
        # Persist before sending. A timeout cannot safely be treated as permission to retry.
        write_json(attempts_path, attempts)
        print("[借款 4/5] 携带本次试算费用及还款计划，提交借款一次", flush=True)
        saved = attempt.submit()
        attempts[key].update({"status": "api_success", "code": saved.get("code"), "msg": saved.get("msg")})
        write_json(attempts_path, attempts)
        print(f"提交响应：code={saved.get('code')}，msg={saved.get('msg')}", flush=True)
        header_id = database_checks.submitted_loan(user, before, attempt.submission, attempt.plan)
        attempts[key].update({"status": "db_verified", "header_id": header_id})
        write_json(attempts_path, attempts)
        print(f"[借款 5/5] 数据库主单、{len(attempt.plan)}期还款计划及收款卡全部通过，header_id={header_id}", flush=True)
