"""场景 07：按明确选择，执行一种人工审批结果；绝不在同一单上跑三种结果。"""
import json
import os

from common.admin_client import ADMIN_URL, authenticate_admin
from common.approval_client import ApprovalAttempt, ApprovalBusinessError, action_spec, verify_detail, verify_reviewer_context, verify_previous_attempt
from common.approval_credentials import approval_credentials
from common.loan_target import loan_target_phone
from common.phone_sequence import locked
from common.sso_login import TIMEOUT
from common.state_store import ROOT, write_json


def resolve_approval_id(checks):
    explicit = os.getenv("APPROVAL_ID", "").strip()
    if explicit:
        return explicit
    latest = {"phone": loan_target_phone()}
    attempts = json.loads((ROOT / ".state/loan_attempts.json").read_text(encoding="utf-8"))
    matching = [value for value in attempts.values() if value.get("phone") == latest["phone"]
                and value.get("status") == "db_verified" and value.get("header_id")]
    assert len(matching) == 1, "最新新增用户没有唯一已提交并通过数据库校验的借款，请先执行借款场景"
    def verify_pending():
        candidates = [checks.approval_record(row["id"]) for row in checks.approval_candidates(matching[0]["header_id"])]
        pending = [row for row in candidates if str(row["status"]) == "0"]
        assert len(pending) == 1, "关联借款未生成唯一待审批记录或已审批，停止操作；不会改选其他用户"
        return pending[0]["id"]
    return checks.check("关联借款异步生成唯一待审记录（仅查询，不重发借款）", verify_pending, timeout=60)


def test_selected_payout_approval(session, database_checks):
    action = os.getenv("APPROVAL_ACTION", "").strip()
    spec = action_spec(action)
    config = json.loads((ROOT / "config/approval_actions.json").read_text(encoding="utf-8"))
    reason = os.getenv("APPROVAL_REASON", config["reasons"][action]).strip()
    amount = os.getenv("APPROVAL_EXPECTED_AMOUNT", config["expectedAmount"]).strip()
    approval_id = resolve_approval_id(database_checks)
    assigned = database_checks.pending_approval(approval_id)
    login_name, reviewer_password = approval_credentials(os.getenv("SSO_USERNAME"), assigned)
    authenticate_admin(session, username=login_name, password=reviewer_password)
    actor = database_checks.login_account(login_name)
    with locked(ROOT / ".state/approval_operation.lock"):
        state_path = ROOT / ".state/approval_attempts.json"
        attempts = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        before = database_checks.pending_approval(approval_id)
        loan_records = json.loads((ROOT / ".state/loan_attempts.json").read_text(encoding="utf-8"))
        submitters = {item.get("submitted_by") for item in loan_records.values()
                      if item.get("header_id") == before["pre_repayment_plan_header_id"] and item.get("submitted_by")}
        assert len(submitters) <= 1, "同一借款存在多个实际发起账号记录"
        before["submitted_by"] = next(iter(submitters), os.getenv("LOAN_REQUESTER_USERNAME"))
        verify_reviewer_context(before, actor["USER_ID"], login_name)
        previous = attempts.get(approval_id)
        verify_previous_attempt(previous, action, login_name, before["version"], before)
        print(f"[人审 1/4] 审批单 {approval_id}，结果选择：{spec['label']}", flush=True)
        response = session.get(ADMIN_URL + "/borrower/payoutApproval/detail/" + approval_id, timeout=TIMEOUT)
        assert response.status_code == 200, "审批详情读取失败"
        verify_detail(response.text, approval_id)
        attempt = ApprovalAttempt(session, before, action, reason, amount)
        old_logs = {row["id"] for row in database_checks.approval_logs(approval_id)}
        fresh = database_checks.approval_record(approval_id)
        assert str(fresh["status"]) == "0" and fresh["version"] == before["version"], "审批状态或版本已变化，停止提交"
        attempts[approval_id] = {"action": action, "status": "request_sent", "header_id": before["pre_repayment_plan_header_id"], "expectedAmount": amount,
                                 "attempted_by": login_name, "approval_version": before["version"],
                                 "previous_attempt": previous}
        write_json(state_path, attempts)
        print(f"[人审 2/4] POST /borrower/payoutApproval/{action}，expectedAmount={amount}，仅提交一次", flush=True)
        try:
            saved = attempt.execute(fresh)
        except ApprovalBusinessError as error:
            attempts[approval_id].update({"status": "self_approval_rejected" if error.message and "不能审批本人" in error.message else "api_rejected", "code": error.code, "msg": error.message})
            write_json(state_path, attempts)
            raise
        attempts[approval_id].update({"status": "api_success", "code": saved.get("code"), "msg": saved.get("msg")})
        write_json(state_path, attempts)
        print(f"[人审 3/4] 接口响应 code={saved.get('code')}，msg={saved.get('msg')}", flush=True)
        database_checks.approval_result(before, action, reason, amount, old_logs, actor["USER_ID"])
        attempts[approval_id]["status"] = "db_verified"
        write_json(state_path, attempts)
        print(f"[人审 4/4] {spec['label']}数据库状态及日志校验通过", flush=True)
