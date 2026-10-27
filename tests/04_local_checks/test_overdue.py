import copy
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import requests

from common.database import Database
from common.overdue_client import grade_for_days, logical_age, penalty_delta, positive_count, storage_datetime
from common.overdue_fixture import OverdueFixtureWriter, snapshot_unchanged
from common import overdue_fixture
from common.overdue_client import penalty_task_count
from common.scheduled_jobs import JobRunAttempt


def test_creation_does_not_treat_overdue_days_as_accrual_rounds():
    assert penalty_task_count("create", "1", 30) == 1
    with pytest.raises(AssertionError, match="不能将逾期天数当作任务轮数"):
        penalty_task_count("create", "15", 30)


def test_explicit_extra_penalty_mode_keeps_bounded_rounds():
    assert penalty_task_count("penalty_only", "2", 30) == 2
    with pytest.raises(AssertionError):
        penalty_task_count("penalty_only", "31", 30)


def test_yesterday_is_computed_in_server_business_timezone_across_year():
    clock = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo("America/Mexico_City"))
    wall, stored = storage_datetime(clock, 1, "Asia/Shanghai")
    assert wall.isoformat() == "2026-12-31T01:00:00-06:00"
    assert stored == datetime(2026, 12, 31, 15)
    assert stored.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(clock.tzinfo) == wall


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "3650", "1;UPDATE", ""])
def test_invalid_day_count_stops_before_any_operation(value):
    with pytest.raises(AssertionError):
        positive_count(value, 365, "逾期天数")


def test_requested_age_cannot_hide_actual_environment_config():
    rows = [{"OVERDUE_START_DAYS": 1, "OVERDUE_END_DAYS": 1, "REMIND_GRADE_NAME": "S0", "REMIND_GRADE_CODE": "code"}]
    assert grade_for_days(rows, 1)["REMIND_GRADE_NAME"] == "S0"
    assert logical_age(1) == "S1"
    with pytest.raises(AssertionError, match="实际配置"):
        grade_for_days(rows, 1, "S1")


def test_overlapping_age_config_is_rejected():
    row = {"OVERDUE_START_DAYS": 1, "OVERDUE_END_DAYS": 3, "REMIND_GRADE_NAME": "S1"}
    with pytest.raises(AssertionError, match="唯一实际账龄"):
        grade_for_days([row, row], 2)


def fixture_rows():
    plan = {"ID": "plan-id", "CONTRACT_ID": "contract-id", "PERIOD": 1, "REPAY_STATUS": "1",
        "REPAY_TIME": datetime(2026, 10, 23, 2), "PENALTY_UPDATE_TIME": None,
        "LATE_CHARGE_ORIGIN": Decimal("0"), "LATE_CHARGE_ACTUAL": Decimal("0"), "MONTH_CAPITAL": Decimal("733.33")}
    reminder = {"ID": "reminder-id", "CONTRACT_ID": "contract-id", "PERIOD": 1, "REPAYMENT_STATUS": "1",
        "REPAYMENT_DATE": datetime(2026, 10, 23, 2), "IS_FINAL_STATE": "0", "SINGLE_FLAG": "1",
        "USER_ID": 1590, "OWNER_NAME": "ob4-mx", "PEND_MONEY": Decimal("733.66")}
    return plan, reminder


def fake_transaction(plan, reminder, target, second_update_count=1):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    new_plan = {**plan, "REPAY_TIME": target}
    new_reminder = {**reminder, "REPAYMENT_DATE": target}
    cursor.fetchall.side_effect = [[{"CONTRACT_STATUS": "1"}], [plan], [reminder], [new_plan], [new_reminder]]
    updates = []

    def execute(sql, params):
        if sql.startswith("UPDATE "):
            updates.append((sql, params))
            cursor.rowcount = 1 if len(updates) == 1 else second_update_count

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = cursor
    return connection, cursor, updates


def test_two_date_updates_are_atomic_and_scoped_to_exact_rows():
    plan, reminder = fixture_rows()
    target = datetime(2026, 10, 20, 15)
    connection, cursor, updates = fake_transaction(plan, reminder, target)
    OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, target)
    assert len(updates) == 2
    for sql, params in updates:
        assert "WHERE ID=%s AND CONTRACT_ID=%s AND PERIOD=%s" in sql
        assert params[1] in {"plan-id", "reminder-id"} and params[2:4] == ("contract-id", 1)
        assert "PEND_MONEY" not in sql and "LATE_CHARGE_ORIGIN" not in sql
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_second_table_failure_rolls_back_first_update():
    plan, reminder = fixture_rows()
    target = datetime(2026, 10, 20, 15)
    connection, cursor, updates = fake_transaction(plan, reminder, target, 0)
    with pytest.raises(AssertionError, match="更新行数不是 1"):
        OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, target)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_mismatched_period_cannot_start_transaction():
    plan, reminder = fixture_rows()
    reminder["PERIOD"] = 2
    connection = Mock()
    with pytest.raises(AssertionError, match="同一期次"):
        OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, datetime(2026, 10, 20, 15))
    connection.begin.assert_not_called()


def test_missing_assignment_cannot_start_transaction():
    plan, reminder = fixture_rows()
    reminder["USER_ID"] = None
    connection = Mock()
    with pytest.raises(AssertionError, match="预提醒分单"):
        OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, datetime(2026, 10, 20, 15))
    connection.begin.assert_not_called()


def test_penalty_clock_cannot_be_reset_before_first_accrual():
    plan, _ = fixture_rows()
    connection = Mock()
    with pytest.raises(AssertionError, match="先生成至少一天罚息"):
        OverdueFixtureWriter(connection).reset_penalty_clock(plan, datetime(2026, 10, 20, 15))
    connection.begin.assert_not_called()


def test_money_change_during_fixture_write_is_not_allowed():
    plan, _ = fixture_rows()
    after = {**plan, "REPAY_TIME": datetime(2026, 10, 20, 15), "MONTH_CAPITAL": Decimal("700")}
    with pytest.raises(AssertionError, match="MONTH_CAPITAL"):
        snapshot_unchanged(plan, after, "REPAY_TIME", after["REPAY_TIME"])


def test_general_database_interface_remains_read_only():
    connection = Mock()
    with pytest.raises(ValueError, match="只允许 SELECT"):
        Database(connection).select("UPDATE bor_installment_act SET REPAY_TIME=NOW()")
    connection.cursor.assert_not_called()


def test_writer_rejects_other_schema():
    with pytest.raises(AssertionError, match="固定 SIT MX"):
        OverdueFixtureWriter(Mock(), "production")


def test_task_timeout_does_not_allow_second_trigger():
    session = Mock()
    session.post.side_effect = requests.Timeout("unknown")
    job = {"id": "entry", "params": ""}
    attempt = JobRunAttempt(session, job)
    with pytest.raises(requests.Timeout):
        attempt.run(copy.deepcopy(job))
    with pytest.raises(AssertionError, match="禁止自动重发"):
        attempt.run(job)
    assert session.post.call_count == 1


def test_penalty_must_increase_and_advance_clock():
    before = {"LATE_CHARGE_ORIGIN": Decimal("10"), "LATE_CHARGE_ACTUAL": Decimal("10"), "PENALTY_UPDATE_TIME": datetime(2026, 10, 20, 15)}
    after = {"LATE_CHARGE_ORIGIN": Decimal("20"), "LATE_CHARGE_ACTUAL": Decimal("20"), "PENALTY_UPDATE_TIME": datetime(2026, 10, 21, 15)}
    assert penalty_delta(before, after, "10") == Decimal("10")
    with pytest.raises(AssertionError, match="罚息没有增加"):
        penalty_delta(before, before)
    after["PENALTY_UPDATE_TIME"] = before["PENALTY_UPDATE_TIME"]
    with pytest.raises(AssertionError, match="时钟未推进"):
        penalty_delta(before, after)


def test_write_connector_cannot_open_other_environment(monkeypatch):
    monkeypatch.setattr(overdue_fixture, "connection_options", lambda: {"host": "production.example", "port": 3306})
    connect = Mock()
    monkeypatch.setattr(overdue_fixture.pymysql, "connect", connect)
    with pytest.raises(AssertionError, match="禁止连接其他环境"):
        with overdue_fixture.connect_overdue_fixture():
            pytest.fail("must not connect")
    connect.assert_not_called()


def test_penalty_reset_updates_only_its_clock_field():
    plan, _ = fixture_rows()
    plan.update(LATE_CHARGE_ORIGIN=Decimal("10"), LATE_CHARGE_ACTUAL=Decimal("10"), PENALTY_UPDATE_TIME=datetime(2026, 10, 21, 15))
    target = datetime(2026, 10, 20, 15)
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.rowcount = 1
    cursor.fetchall.side_effect = [[{"CONTRACT_STATUS": "2"}], [plan], [{**plan, "PENALTY_UPDATE_TIME": target}]]
    connection = Mock()
    connection.cursor.return_value = cursor
    OverdueFixtureWriter(connection).reset_penalty_clock(plan, target)
    updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE ")]
    assert len(updates) == 1 and "SET `PENALTY_UPDATE_TIME`=%s" in updates[0].args[0]
    assert "SET `LATE_CHARGE" not in updates[0].args[0]
    connection.commit.assert_called_once()


def test_unknown_commit_does_not_allow_automatic_replay():
    plan, reminder = fixture_rows()
    target = datetime(2026, 10, 20, 15)
    connection, cursor, updates = fake_transaction(plan, reminder, target)
    connection.commit.side_effect = ConnectionError("commit result unknown")
    writer = OverdueFixtureWriter(connection)
    with pytest.raises(ConnectionError):
        writer.backdate_due_dates(plan, reminder, target)
    with pytest.raises(AssertionError, match="禁止自动重发"):
        writer.backdate_due_dates(plan, reminder, target)
    assert len(updates) == 2


def test_changed_job_parameters_block_trigger():
    session = Mock()
    attempt = JobRunAttempt(session, {"id": "penalty", "params": "200"})
    with pytest.raises(AssertionError, match="配置已变化"):
        attempt.run({"id": "penalty", "params": "500"})
    session.post.assert_not_called()


def test_prepared_dates_do_not_issue_noop_updates():
    plan, reminder = fixture_rows()
    target = datetime(2026, 10, 20, 15)
    plan["REPAY_TIME"] = reminder["REPAYMENT_DATE"] = target
    connection, cursor, updates = fake_transaction(plan, reminder, target)
    OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, target)
    assert updates == []
    connection.commit.assert_called_once()


def test_prepared_first_table_does_not_prevent_atomic_second_update():
    plan, reminder = fixture_rows()
    target = datetime(2026, 10, 20, 15)
    plan["REPAY_TIME"] = target
    connection, cursor, updates = fake_transaction(plan, reminder, target)
    cursor.fetchall.side_effect = [[{"CONTRACT_STATUS": "1"}], [plan], [reminder], [{**reminder, "REPAYMENT_DATE": target}]]
    OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, target)
    assert len(updates) == 1 and 'pre_pend_repayment' in updates[0][0]
    connection.commit.assert_called_once()
