import copy
from unittest.mock import Mock

import pytest

from common.loan_client import LoanAttempt, submission_from_trial


def inputs():
    return {"installmentCount": "3", "loanAmount": "2200", "totalInterest": "1"}


def trial_data():
    data = {key: "0" for key in ("taxes", "totalServiceFee", "creditServiceFee", "paymentChannelFee", "serviceFee")}
    data["inHandAmount"] = "2200"
    data["repaymentPlan"] = [
        {"periodNo": n, "repayDate": f"2026-11-{n*7:02}", "repayTotalAmount": total,
         "repayPrincipal": principal, "repayInterest": interest}
        for n, principal, interest, total in [(1, "733.33", "0.33", "733.66"), (2, "733.33", "0.33", "733.66"), (3, "733.34", "0.34", "733.68")]]
    return data


def response(code=0, data=None):
    value = Mock(status_code=200, headers={"Content-Type": "application/json"})
    value.json.return_value = {"code": code, "msg": "test", "data": data}
    return value


def test_cannot_submit_before_trial():
    session = Mock()
    with pytest.raises(AssertionError, match="先试算成功"):
        LoanAttempt(session, inputs()).submit()
    session.post.assert_not_called()


def test_failed_trial_never_submits():
    session = Mock()
    session.post.return_value = response(500)
    attempt = LoanAttempt(session, inputs())
    with pytest.raises(AssertionError):
        attempt.trial()
    with pytest.raises(AssertionError, match="先试算成功"):
        attempt.submit()
    assert session.post.call_count == 1


def test_changed_inputs_require_new_trial():
    session = Mock()
    session.post.return_value = response(data=trial_data())
    attempt = LoanAttempt(session, inputs())
    attempt.trial()
    attempt.inputs["loanAmount"] = "9999"
    with pytest.raises(AssertionError, match="参数已变更"):
        attempt.submit()
    assert session.post.call_count == 1


def test_modified_plan_cannot_be_submitted():
    session = Mock()
    session.post.return_value = response(data=trial_data())
    attempt = LoanAttempt(session, inputs())
    attempt.trial()
    attempt.submission["inHandAmount"] = "9999"
    with pytest.raises(AssertionError, match="被修改"):
        attempt.submit()


def test_trial_then_submit_exactly_once():
    session = Mock()
    session.post.side_effect = [response(data=trial_data()), response()]
    attempt = LoanAttempt(session, inputs())
    attempt.trial()
    expected = copy.deepcopy(attempt.submission)
    attempt.submit()
    assert session.post.call_args_list[0].args[0].endswith('/trial')
    assert session.post.call_args_list[1].kwargs["data"] == expected
    with pytest.raises(AssertionError, match="不得重复提交"):
        attempt.submit()
    assert session.post.call_count == 2


def test_principal_total_must_match():
    data = trial_data()
    data["repaymentPlan"][0]["repayPrincipal"] = "0"
    with pytest.raises(AssertionError, match="本金合计"):
        submission_from_trial(inputs(), data)
