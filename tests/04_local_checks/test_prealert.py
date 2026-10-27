import copy
import json
from datetime import date, datetime
from unittest.mock import Mock
from decimal import Decimal

import pytest
import requests

from common.prealert_client import (PrealertAttempt, date_payload, job_edit_payload,
    reminder_days, select_period, server_business_clock, target_date, validate_job)
from common.prealert_database import unchanged_value
from common.database_checks import DatabaseChecks


def clock_response(wall="2026-12-31 23:30:00", http="Fri, 01 Jan 2027 05:30:00 GMT"):
    return Mock(status_code=200, headers={"Content-Type": "text/html", "Date": http},
                text=f'<script>var __cn = "{wall}";</script>')


@pytest.mark.parametrize("days,expected", [(0, date(2026, 12, 31)), (1, date(2027, 1, 1)), (2, date(2027, 1, 2))])
def test_due_date_uses_server_business_day_not_http_utc_day(days, expected):
    clock = server_business_clock(clock_response(), "America/Mexico_City")
    assert target_date(clock, days) == expected


@pytest.mark.parametrize("value", [None, "", "-1", "3", "1,2", "2;run", "1.0"])
def test_unsupported_job_params_fail_before_any_write(value):
    with pytest.raises(AssertionError):
        reminder_days(value)


def test_unknown_server_clock_cannot_fall_back_to_local_time():
    response = clock_response()
    response.text = '<html>not logged in</html>'
    with pytest.raises(AssertionError, match="缺少商户当前时间"):
        server_business_clock(response, "America/Mexico_City")


def test_wrong_country_timezone_is_rejected():
    with pytest.raises(AssertionError, match="时间不一致"):
        server_business_clock(clock_response(), "Asia/Shanghai")


def plans():
    return [{"id": "a", "contractId": "c", "period": 1, "repayStatus": "1", "repayTimeStr": "2026-10-28", "monthCapital": 100, "receivableTotalAmount": 101},
            {"id": "b", "contractId": "c", "period": 2, "repayStatus": "1", "repayTimeStr": "2026-11-04", "monthCapital": 100}]


def test_date_edit_preserves_other_period_and_amounts():
    before = plans()
    original = copy.deepcopy(before)
    payload = json.loads(date_payload(before, 1, date(2026, 10, 23))["repayData"])
    assert before == original
    assert payload[0]["repayTimeStr"] == "2026-10-23" and payload[0]["monthCapital"] == 100
    assert payload[1] == before[1]
    assert "receivableTotalAmount" not in payload[0]


def test_cannot_move_date_past_next_period():
    with pytest.raises(AssertionError, match="后一期"):
        date_payload(plans(), 1, date(2026, 11, 5))


def test_cannot_skip_earliest_unpaid_period():
    with pytest.raises(AssertionError, match="前序欠款"):
        select_period(plans(), "2")


def test_partial_repayment_is_not_eligible():
    rows = plans()
    rows[0]["actualTotalAmount"] = 10
    with pytest.raises(AssertionError, match="已有还款"):
        select_period(rows)


def test_wrong_job_country_scope_is_rejected():
    config = {"jobId": "job", "beanName": "loanTask", "methodName": "executePreAlertListTimer", "dbGroupId": "1", "targetGroup": "2"}
    job = {**config, "id": "job", "params": "2", "targetGroup": "0"}
    with pytest.raises(AssertionError, match="targetGroup"):
        validate_job(job, config)


def test_job_edit_preserves_schedule_and_scope():
    job = {"id": "job", "beanName": "loanTask", "methodName": "executePreAlertListTimer", "params": "2", "cronExpression": "cron", "remark": "remark", "dbGroupId": "1", "targetGroup": "2"}
    html = '<form id="form-job-edit">' + ''.join(f'<input name="{k}" value="{v}">' for k, v in job.items()) + '</form>'
    assert job_edit_payload(html, job, 1) == {**job, "params": "1"}


def test_task_timeout_cannot_trigger_automatic_second_run():
    session = Mock()
    session.post.side_effect = requests.Timeout("unknown result")
    attempt = PrealertAttempt(session)
    with pytest.raises(requests.Timeout):
        attempt.run("job")
    with pytest.raises(AssertionError, match="禁止自动重复执行"):
        attempt.run("job")
    assert session.post.call_count == 1


def test_only_observed_nullable_accounting_fields_allow_zero_normalization():
    assert unchanged_value("MITIGATE_LATE_CHARGE", None, Decimal("0.00"))
    assert unchanged_value("AMOUNT_DUE_BEFORE_SETTLEMENT", None, Decimal("0.00"))
    assert not unchanged_value("MONTH_CAPITAL", None, Decimal("0.00"))
    assert not unchanged_value("MITIGATE_LATE_CHARGE", None, Decimal("1.00"))


def date_checks_fixture():
    before = [{"ID": "a", "CONTRACT_ID": "c", "PERIOD": 1, "REPAY_TIME": datetime(2026, 10, 28, 23, 59, 59), "MONTH_CAPITAL": Decimal("100"), "MITIGATE_LATE_CHARGE": None},
              {"ID": "b", "CONTRACT_ID": "c", "PERIOD": 2, "REPAY_TIME": datetime(2026, 11, 4, 23, 59, 59), "MONTH_CAPITAL": Decimal("100"), "MITIGATE_LATE_CHARGE": None}]
    after = copy.deepcopy(before)
    after[0]["REPAY_TIME"] = datetime(2026, 10, 23, 2)
    after[1]["REPAY_TIME"] = datetime(2026, 11, 4, 2)
    for row in after:
        row["MITIGATE_LATE_CHARGE"] = Decimal("0.00")
    database = Mock()
    database.select.return_value = after
    checks = DatabaseChecks(database)
    original_check = checks.check
    checks.check = lambda label, fn: original_check(label, fn, timeout=0)
    return checks, before, after


def test_saved_dates_allow_observed_server_time_normalization():
    checks, before, after = date_checks_fixture()
    assert checks.repayment_date_saved(before, 1, date(2026, 10, 23)) == after


def test_other_period_calendar_day_cannot_change():
    checks, before, after = date_checks_fixture()
    after[1]["REPAY_TIME"] = datetime(2026, 11, 5, 2)
    with pytest.raises(AssertionError, match="其他期次日期"):
        checks.repayment_date_saved(before, 1, date(2026, 10, 23))


def test_changed_principal_cannot_be_hidden_by_date_edit():
    checks, before, after = date_checks_fixture()
    after[0]["MONTH_CAPITAL"] = Decimal("90")
    with pytest.raises(AssertionError, match="MONTH_CAPITAL"):
        checks.repayment_date_saved(before, 1, date(2026, 10, 23))
