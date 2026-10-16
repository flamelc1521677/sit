from decimal import Decimal
from unittest.mock import Mock

import pytest

from common.approval_client import ApprovalAttempt, action_spec, build_approval_payload, verify_detail, verify_reviewer_context, verify_previous_attempt


def pending():
    return {"id": "test-approval", "approval_type": 1, "status": 0,
            "version": 0, "contract_amount": Decimal("2200")}


@pytest.mark.parametrize("action,field,status", [("pass", "approvalReason", 1), ("reject", "rejectReason", 2), ("close", "reason", 3)])
def test_each_result_has_its_own_endpoint_and_reason_field(action, field, status):
    session = Mock()
    response = Mock(status_code=200, headers={"Content-Type": "application/json"})
    response.json.return_value = {"code": 0, "msg": "操作成功"}
    session.post.return_value = response
    attempt = ApprovalAttempt(session, pending(), action, "test-reason", "2200")
    attempt.execute(pending())
    assert session.post.call_args.args[0].endswith("/" + action)
    assert session.post.call_args.kwargs["data"] == {"id": "test-approval", field: "test-reason", "expectedAmount": "2200"}
    assert action_spec(action)["status"] == status
    with pytest.raises(AssertionError, match="只能发送一次"):
        attempt.execute(pending())
    assert session.post.call_count == 1


@pytest.mark.parametrize("status", [1, 2, 3])
def test_processed_approval_cannot_switch_result(status):
    row = pending()
    row["status"] = status
    with pytest.raises(AssertionError, match="不是待审批"):
        build_approval_payload(row, "pass", "test", "2200")


def test_action_is_required_and_has_no_default():
    with pytest.raises(AssertionError, match="必须明确选择"):
        action_spec("")


def test_concurrent_version_change_blocks_post():
    session = Mock()
    attempt = ApprovalAttempt(session, pending(), "close", "test", "2200")
    fresh = pending()
    fresh["version"] = 1
    with pytest.raises(AssertionError, match="版本已变化"):
        attempt.execute(fresh)
    session.post.assert_not_called()


def test_api_failure_is_not_retried():
    session = Mock()
    response = Mock(status_code=200, headers={"Content-Type": "application/json"})
    response.json.return_value = {"code": 500, "msg": "already processed"}
    session.post.return_value = response
    attempt = ApprovalAttempt(session, pending(), "reject", "test", "2200")
    with pytest.raises(AssertionError):
        attempt.execute(pending())
    with pytest.raises(AssertionError, match="只能发送一次"):
        attempt.execute(pending())
    assert session.post.call_count == 1


def test_amount_mismatch_and_wrong_approval_type_block_post():
    with pytest.raises(AssertionError, match="合同金额不一致"):
        build_approval_payload(pending(), "pass", "test", "9999")
    row = pending()
    row["approval_type"] = 2
    with pytest.raises(AssertionError, match="放款改卡"):
        build_approval_payload(row, "pass", "test", "2200")


def test_detail_cannot_target_another_approval():
    with pytest.raises(AssertionError, match="目标审批 ID 不一致"):
        verify_detail('<input id="approvalId" value="another">', "test-approval")


def test_same_account_review_is_blocked_before_submission():
    with pytest.raises(AssertionError, match="不能审核自己"):
        verify_reviewer_context({"submitted_by": "requester"}, 1590, "requester")
    # Assigned sales owner can differ from the actual account that sent the loan request.
    verify_reviewer_context({"submitted_by": "requester", "creator_id": "1589", "creator_name": "reviewer"}, 1589, "reviewer")


def test_review_still_requires_requester_trace():
    with pytest.raises(AssertionError, match="缺少实际借款"):
        verify_reviewer_context({}, 1590, "requester")


def test_only_known_self_rejection_can_resume_with_another_reviewer():
    previous = {"status": "self_approval_rejected", "attempted_by": "requester", "action": "pass", "approval_version": 0}
    verify_previous_attempt(previous, "pass", "reviewer", 0)
    with pytest.raises(AssertionError):
        verify_previous_attempt(previous, "pass", "requester", 0)
    with pytest.raises(AssertionError):
        verify_previous_attempt(previous, "reject", "reviewer", 0)
    with pytest.raises(AssertionError):
        verify_previous_attempt(dict(previous, status="request_sent"), "pass", "reviewer", 0)
