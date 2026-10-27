import copy
import json
from unittest.mock import Mock
from decimal import Decimal

import pytest
import requests

from common.prealert_assignment import AssignmentAttempt, assignment_payload, javascript_json
from common import loan_target
from common.database_checks import DatabaseChecks


def fixture_data():
    config = {"receiverLogin": "ob4-mx", "receiverId": "1590", "departmentId": "11807", "opType": "2", "distributionMode": "1", "isAssistAllocation": "1"}
    receiver = {"USER_ID": 1590, "LOGIN_NAME": "ob4-mx", "DEPT_ID": 11807, "DEPT_NAME": "预提醒部"}
    before = {"ID": "order", "CONTRACT_ID": "contract", "PERIOD": 1, "MERCHANT_ID": "11801", "IS_FINAL_STATE": "0", "REPAYMENT_STATUS": "1", "USER_ID": None, "OWNER_NAME": None, "DEPT_ID": 11807}
    return config, receiver, before


def form(before, extra=None, filters=None):
    row = {"id": before["ID"], "contractId": before["CONTRACT_ID"], "period": before["PERIOD"], "merchantId": 11801, "repaymentStatus": "1", "userId": None, "ownerName": None, "deptId": 11807}
    rows = [row] + ([extra] if extra else [])
    names = ("distributionMode", "deptSelect", "deptIdList", "ownerDictList", "userName", "ids", "isAssistAllocation", "isFilter")
    return '<form id="form-distributeOrTransferBills-add"><input name="opType" value="2">' + ''.join(f'<input name="{n}">' for n in names) + '</form><script>var records = ' + json.dumps(rows) + '; var preReminderVo = ' + json.dumps(filters or {"merchantIdList": ["11801"], "isLock": 0, "params": {}}) + ';</script>'


def test_single_reminder_is_precisely_assigned_to_fixed_receiver():
    config, receiver, before = fixture_data()
    payload = assignment_payload(form(before), before, receiver, config)
    assert payload["ids"] == "order" and payload["distributionMode"] == "1"
    assert payload["deptIdList"] == "11807" and payload["ownerDictList"] == "1590"
    assert payload["userName"] == "1590-ob4-mx" and payload["isAssistAllocation"] == "1"


def test_multiple_records_cannot_turn_into_bulk_assignment():
    config, receiver, before = fixture_data()
    with pytest.raises(AssertionError, match="禁止批量"):
        assignment_payload(form(before, extra={"id": "other"}), before, receiver, config)


def test_changed_owner_in_form_blocks_overwrite():
    config, receiver, before = fixture_data()
    before["USER_ID"] = 999
    with pytest.raises(AssertionError, match="原归属已变化"):
        assignment_payload(form(before), before, receiver, config)


def test_filter_cannot_override_explicit_order_id():
    config, receiver, before = fixture_data()
    with pytest.raises(AssertionError, match="参数重名"):
        assignment_payload(form(before, filters={"ids": "other"}), before, receiver, config)


def test_wrong_department_is_not_silently_accepted():
    config, receiver, before = fixture_data()
    receiver["DEPT_ID"] = 11808
    with pytest.raises(AssertionError, match="部门与固定配置不一致"):
        assignment_payload(form(before), before, receiver, config)


def test_finalized_reminder_cannot_be_transferred():
    config, receiver, before = fixture_data()
    before["IS_FINAL_STATE"] = "1"
    with pytest.raises(AssertionError, match="失效"):
        assignment_payload(form(before), before, receiver, config)


def test_js_metadata_parser_does_not_execute_code():
    with pytest.raises(AssertionError, match="不是有效 JSON"):
        javascript_json('<script>var records = alert("unexpected")</script>', "records")


def test_unknown_transfer_result_cannot_be_resent():
    session = Mock()
    session.post.side_effect = requests.Timeout("unknown")
    attempt = AssignmentAttempt(session)
    config, receiver, before = fixture_data()
    payload = assignment_payload(form(before), before, receiver, config)
    with pytest.raises(requests.Timeout):
        attempt.execute(payload, before, before)
    with pytest.raises(AssertionError, match="禁止自动重发"):
        attempt.execute(payload, before, before)
    assert session.post.call_count == 1


def test_fresh_snapshot_change_blocks_network_request():
    session = Mock()
    config, receiver, before = fixture_data()
    fresh = copy.deepcopy(before)
    fresh["USER_ID"] = 999
    with pytest.raises(AssertionError, match="原归属发生变化"):
        AssignmentAttempt(session).execute(assignment_payload(form(before), before, receiver, config), before, fresh)
    session.post.assert_not_called()


def test_explicit_loan_phone_does_not_read_or_rewrite_latest_user(monkeypatch):
    monkeypatch.setenv("LOAN_TARGET_PHONE", "0658900214")
    lookup = Mock(return_value={"id": 941})
    monkeypatch.setattr(loan_target, "lookup_user_by_phone", lookup)
    latest = Mock(side_effect=AssertionError("should not read latest user"))
    monkeypatch.setattr(loan_target, "latest_user", latest)
    assert loan_target.loan_target_user(Mock())[:2] == ({"id": 941}, "0658900214")
    latest.assert_not_called()


def test_invalid_explicit_loan_phone_fails_before_lookup(monkeypatch):
    monkeypatch.setenv("LOAN_TARGET_PHONE", "0658900214,0658900215")
    with pytest.raises(AssertionError, match="10 位"):
        loan_target.loan_target_phone()


def transfer_checks_fixture():
    _, receiver, before = fixture_data()
    before["PEND_MONEY"] = Decimal("733.66")
    before["REPAYMENT_DATE"] = "2026-10-23"
    after = {**before, "USER_ID": 1590, "OWNER_NAME": "ob4-mx", "DEPT_ID": 11807,
             "SINGLE_FLAG": "1", "SINGLE_TIME": "server-time", "UPDATE_BY": "operator"}
    flow = {"ID": "new-flow", "USER_ID": "1590", "PRE_NAME": "ob4-mx", "DEPT_ID": "11807",
            "PRE_STATUS": "1", "CREATE_BY": "operator", "REMARK": "工单转交", "REPAY_TIME": "2026-10-23"}
    checks = DatabaseChecks(Mock())
    checks.assignment_record = Mock(return_value=after)
    checks.assignment_flows = Mock(return_value=[flow])
    original = checks.check
    checks.check = lambda label, fn: original(label, fn, timeout=0)
    return checks, receiver, before, after, flow


def test_transfer_needs_matching_receiver_and_new_audit_flow():
    checks, receiver, before, after, flow = transfer_checks_fixture()
    assert checks.assignment_success(before, receiver, set(), "operator") == (after, flow)


def test_transfer_cannot_change_debt_amount():
    checks, receiver, before, after, flow = transfer_checks_fixture()
    after["PEND_MONEY"] = Decimal("700")
    with pytest.raises(AssertionError, match="PEND_MONEY"):
        checks.assignment_success(before, receiver, set(), "operator")


def test_transfer_cannot_use_historical_audit_as_proof():
    checks, receiver, before, after, flow = transfer_checks_fixture()
    with pytest.raises(AssertionError, match="本次分单流水"):
        checks.assignment_success(before, receiver, {"new-flow"}, "operator")


def test_wrong_audit_actor_cannot_pass():
    checks, receiver, before, after, flow = transfer_checks_fixture()
    flow["CREATE_BY"] = "somebody-else"
    with pytest.raises(AssertionError, match="操作人不一致"):
        checks.assignment_success(before, receiver, set(), "operator")
