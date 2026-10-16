import pytest

from common.loan_client import repeat_loan_history


def test_verified_loan_can_have_one_explicit_new_application():
    old = {"status": "db_verified", "header_id": "old"}
    assert repeat_loan_history(old, [{"ID": "old"}], "request-2", "862", 862) == [old]


@pytest.mark.parametrize("status", ["request_sent", "api_success"])
def test_unknown_previous_submission_blocks_repeat(status):
    with pytest.raises(AssertionError, match="结果未确认"):
        repeat_loan_history({"status": status}, [], "request-2", "862", 862)


def test_same_repeat_request_cannot_submit_again():
    with pytest.raises(AssertionError, match="禁止重复申请"):
        repeat_loan_history({"status": "db_verified", "header_id": "old", "repeat_request_id": "request-2"},
                            [{"ID": "old"}], "request-2", "862", 862)


def test_repeat_cannot_target_other_user():
    with pytest.raises(AssertionError, match="准确用户"):
        repeat_loan_history({"status": "db_verified"}, [], "request-2", "861", 862)


def test_untracked_database_loan_blocks_repeat():
    with pytest.raises(AssertionError, match="历史不一致"):
        repeat_loan_history({"status": "db_verified", "header_id": "old"},
                            [{"ID": "old"}, {"ID": "untracked"}], "request-2", "862", 862)
