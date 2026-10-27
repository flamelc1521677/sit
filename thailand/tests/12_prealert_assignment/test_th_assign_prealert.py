"""独立复用：明确指定预提醒订单，精准转交给 ob5 所属部门及本人。"""
import json
import os
import re

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json, lookup_user_by_phone
from common.phone_sequence import locked
from thailand.prealert_assignment import AssignmentAttempt, assignment_payload, verify_receiver_option
from common.sso_login import TIMEOUT
from common.state_store import write_json
from thailand.client import TH_ROOT as ROOT, STATE, authenticate_thailand, configuration


def test_th_assign_prealert_to_ob5(session, database_checks):
    order_id = os.getenv("TH_PREALERT_ORDER_ID", "").strip() or None
    contract_id = os.getenv("TH_PREALERT_ASSIGN_CONTRACT_ID", "").strip() or None
    phone = os.getenv("TH_PREALERT_ASSIGN_PHONE", "").strip()
    period = os.getenv("TH_PREALERT_ASSIGN_PERIOD", "").strip() or None
    assert order_id or contract_id or phone, "必须显式提供预提醒 ID、合同信息或手机号，不自动挑选订单"
    if period:
        assert period.isdigit() and int(period) > 0, "分单期次必须为正整数"
    if phone:
        assert re.fullmatch(r"\d{10}", phone), "手机号必须为 10 位数字"
    config = json.loads((ROOT / "config/prealert_assignment.json").read_text(encoding="utf-8"))
    dry_run = os.getenv("TH_PREALERT_ASSIGN_DRY_RUN", "1").strip()
    assert dry_run in {"0", "1"}
    authenticate_thailand(session, database_checks)
    operator = configuration()['employeeLogin']
    database_checks.login_account(operator)
    with locked(STATE / "prealert_assignment.lock"):
        receiver = database_checks.assignment_receiver(config)
        if phone:
            user = lookup_user_by_phone(session, phone, os.getenv("TH_PREALERT_ASSIGN_APP_OWNERSHIP"))
            database_checks.prealert_user(user, phone)
            contract = database_checks.prealert_contract(user, contract_id)
            contract_id = contract["CONTRACT_ID"]
        before = database_checks.assignment_target(order_id, contract_id, period)
        state_path = STATE / "prealert_assignment_attempts.json"
        attempts = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        previous = attempts.get(before["ID"])
        if previous:
            assert previous["status"] == "db_verified", "该预提醒已有未确认转交尝试，禁止自动重发；请先核对接口、归属及流水"
        if (str(before.get("USER_ID")) == config["receiverId"] and before.get("OWNER_NAME") == config["receiverLogin"] and str(before.get("DEPT_ID")) == config["departmentId"]):
            print(f"[分单] 预提醒 {before['ID']} 已归属 ob5 / 部门 {config['departmentId']}；只读回查，不重复转交", flush=True)
            return
        assert not previous, "曾转交成功的订单归属已被更改，停止自动重新覆盖"
        verify_receiver_option(session, receiver)
        response = session.get(ADMIN_URL + "/loan/preReminder/distributeOrTransferBills", params={
            "opType": "2", "ids": before["ID"], "merchantIdList": str(before["MERCHANT_ID"])}, timeout=TIMEOUT)
        assert response.status_code == 200 and "html" in response.headers.get("Content-Type", "").lower(), "未读取到真实工单转交表单"
        payload = assignment_payload(response.text, before, receiver, config)
        fresh = database_checks.assignment_record(before["ID"])
        assert fresh == before, "提交前订单状态或原归属发生变化，停止覆盖"
        database_checks.assignment_receiver(config)
        old_flows = {r["ID"] for r in database_checks.assignment_flows(before)}
        print(f"[分单 1/3] 预提醒 {before['ID']}，合同 {before['CONTRACT_ID']}，第 {before['PERIOD']} 期", flush=True)
        print(f"[分单] 接收部门 {receiver['DEPT_NAME']}（{receiver['DEPT_ID']}），接收人 ob5（{receiver['USER_ID']}）", flush=True)
        if dry_run == "1":
            print("[泰国预提醒分单只读预检通过] OB5接收人、单笔表单和归属已核验；未提交转交", flush=True)
            return
        record = {"status": "request_sent", "order_id": before["ID"], "contract_id": before["CONTRACT_ID"],
            "period": before["PERIOD"], "receiver": config["receiverLogin"], "receiver_id": config["receiverId"],
            "department_id": config["departmentId"], "previous_owner": before.get("OWNER_NAME"),
            "previous_user_id": before.get("USER_ID"), "operator": operator}
        attempts[before["ID"]] = record
        write_json(state_path, attempts)
        print("[分单 2/3] 单笔精准转交，仅发送一次 POST updateDistributeOrTransferBills", flush=True)
        result = AssignmentAttempt(session).execute(payload, before, fresh)
        record.update(status="api_success", code=result.get("code"), msg=result.get("msg"))
        write_json(state_path, attempts)
        row, flow = database_checks.assignment_success(before, receiver, old_flows, operator)
        record.update(status="db_verified", flow_id=flow["ID"])
        write_json(state_path, attempts)
        # The new receiver's list can be scoped by permissions; preserve this separately.
        listing = checked_json(session.post(ADMIN_URL + "/loan/preReminder/list", data={
            "contractId": before["CONTRACT_ID"], "pageNum": 1, "pageSize": 100}, timeout=TIMEOUT), "分单后预提醒列表回查")
        visible = [r for r in listing.get("rows", []) if r.get("id") == before["ID"]]
        if visible:
            assert len(visible) == 1 and str(visible[0].get("userId")) == config["receiverId"] and str(visible[0].get("deptId")) == config["departmentId"], "列表接收人或部门与数据库不一致"
        record["api_list_visible"] = bool(visible)
        write_json(state_path, attempts)
        print(f"[分单 3/3] ob5 接收成功，归属与流水已核验；当前账号列表可见={bool(visible)}", flush=True)
