import json
from datetime import datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from common import overdue_days
from common.overdue_fixture import OverdueFixtureWriter


CONFIG = {'maximumDays': 365, 'storageTimezone': 'Asia/Shanghai'}


@pytest.mark.parametrize('today,days,expected', [
    ('2026-03-06', 2, '2026-03-04'),
    ('2028-03-01', 2, '2028-02-28'),
    ('2027-01-01', 1, '2026-12-31'),
])
def test_business_dates_and_next_day_collection_date(today, days, expected):
    clock = datetime.fromisoformat(today).replace(hour=23, tzinfo=ZoneInfo('America/Mexico_City'))
    wall, due, overdue = overdue_days.target_dates(clock, days, CONFIG)
    assert wall.date().isoformat() == expected
    assert due.hour == overdue.hour == 15
    assert overdue - due == timedelta(days=1)
    assert due.replace(tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(clock.tzinfo) == wall


def rows():
    common = {'CONTRACT_ID':'contract', 'PERIOD':1}
    plan = {**common, 'ID':'plan', 'REPAY_STATUS':'1', 'REPAY_TIME':datetime(2026,10,20,15), 'LATE_CHARGE_ORIGIN':'220.00'}
    reminder = {**common, 'ID':'pre', 'REPAYMENT_STATUS':'1', 'REPAYMENT_DATE':plan['REPAY_TIME'], 'USER_ID':1590, 'OWNER_NAME':'ob4-mx', 'SINGLE_FLAG':'1'}
    bill = {**common, 'ID':'bill', 'CURRENT_PERIOD':1, 'PERIOD_LIST':'1', 'OVERDUE_DATE':datetime(2026,10,21,15), 'REMIND_STATUS':'0', 'DEL_FLAG':'0', 'OVERDUE_DAYS':'1', 'REMIND_GRADE':'S0'}
    return plan, reminder, bill


def transaction(fail_last=False):
    plan, pre, bill = rows()
    due = datetime(2026,10,19,15)
    overdue = due + timedelta(days=1)
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchall.side_effect = [[{'CONTRACT_STATUS':'2'}], [plan], [pre], [bill],
        [{**plan,'REPAY_TIME':due}], [{**pre,'REPAYMENT_DATE':due}], [{**bill,'OVERDUE_DATE':overdue}]]
    updates=[]
    def execute(sql, params):
        if sql.startswith('UPDATE '):
            updates.append((sql,params))
            cursor.rowcount = 0 if fail_last and len(updates)==3 else 1
    cursor.execute.side_effect=execute
    connection=Mock()
    connection.cursor.return_value=cursor
    return connection, updates, (plan, pre, bill, due, overdue)


def test_three_dates_commit_together_and_money_never_written():
    connection, updates, args=transaction()
    OverdueFixtureWriter(connection).update_overdue_dates(*args)
    assert len(updates)==3
    for sql, params in updates:
        assert 'WHERE ID=%s AND CONTRACT_ID=%s AND PERIOD=%s' in sql
        assert params[2:4]==('contract',1)
        assert all(key not in sql for key in ('OVERDUE_DAYS','REMIND_GRADE','LATE_CHARGE'))
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_collection_write_failure_rolls_back_all_three_dates():
    connection, updates, args=transaction(True)
    with pytest.raises(AssertionError, match='更新行数'):
        OverdueFixtureWriter(connection).update_overdue_dates(*args)
    assert len(updates)==3
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize('bad', ['multi', 'nextday', 'wrong_contract', 'closed'])
def test_ineligible_fixture_does_not_begin(bad):
    plan, pre, bill=rows()
    due=datetime(2026,10,19,15)
    overdue=due+timedelta(days=1)
    if bad=='multi': bill['PERIOD_LIST']='1,2'
    if bad=='nextday': overdue=due
    if bad=='wrong_contract': bill['CONTRACT_ID']='other'
    if bad=='closed': bill['REMIND_STATUS']='1'
    connection=Mock()
    with pytest.raises(AssertionError):
        OverdueFixtureWriter(connection).update_overdue_dates(plan,pre,bill,due,overdue)
    connection.begin.assert_not_called()


def setup_state(tmp_path, monkeypatch, record):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/overdue.json').write_text(json.dumps(CONFIG),encoding='utf-8')
    (tmp_path/'.state').mkdir()
    (tmp_path/'.state/overdue_days_attempts.json').write_text(json.dumps({'request':record}),encoding='utf-8')
    monkeypatch.setattr(overdue_days,'ROOT',tmp_path)


def test_unknown_request_cannot_be_replayed(tmp_path,monkeypatch):
    setup_state(tmp_path,monkeypatch,{'status':'job_request_sent'})
    checks,session=Mock(),Mock()
    with pytest.raises(AssertionError,match='禁止重发'):
        overdue_days.update_overdue_days(session,checks,days=2,collection_id='bill',request_id='request',dry_run=False)
    checks.database.select.assert_not_called()
    session.post.assert_not_called()


def test_completed_request_only_reads_and_never_triggers_job(tmp_path,monkeypatch):
    record={'status':'db_verified','days':2,'collection_id':'bill','contract_id':'contract','period':1}
    setup_state(tmp_path,monkeypatch,record)
    verify=Mock(return_value={'OVERDUE_DAYS':'2'})
    monkeypatch.setattr(overdue_days,'verify_result',verify)
    monkeypatch.setattr(overdue_days,'get_plans',Mock(return_value=[]))
    checks,session=Mock(),Mock()
    overdue_days.update_overdue_days(session,checks,days=2,collection_id='bill',request_id='request',dry_run=False)
    verify.assert_called_once_with(checks,record,True)
    session.post.assert_not_called()


def test_completed_request_rejects_changed_days(tmp_path,monkeypatch):
    setup_state(tmp_path,monkeypatch,{'status':'db_verified','days':2})
    with pytest.raises(AssertionError,match='逾期天数变化'):
        overdue_days.update_overdue_days(Mock(),Mock(),days=3,collection_id='bill',request_id='request',dry_run=False)


def test_readonly_preview_cannot_write_dates_or_trigger_job(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    config={**CONFIG,'serverBusinessTimezone':'America/Mexico_City','updateDaysJob':{'jobId':'job'}}
    (tmp_path/'config/overdue.json').write_text(json.dumps(config),encoding='utf-8')
    monkeypatch.setattr(overdue_days,'ROOT',tmp_path)
    plan,pre,bill=rows()
    plan['REPAY_TIME']=datetime(2026,10,20,15)
    monkeypatch.setattr(overdue_days,'collection_snapshot',Mock(return_value=bill))
    monkeypatch.setattr(overdue_days,'read_scheduled_job',Mock(return_value={'id':'job'}))
    clock=datetime(2026,10,21,2,tzinfo=ZoneInfo('America/Mexico_City'))
    monkeypatch.setattr(overdue_days,'server_business_clock',Mock(return_value=clock))
    monkeypatch.setattr(overdue_days,'get_plans',Mock(return_value=[]))
    writer,runner=Mock(),Mock()
    monkeypatch.setattr(overdue_days,'connect_overdue_fixture',writer)
    monkeypatch.setattr(overdue_days,'JobRunAttempt',runner)
    checks=Mock()
    checks.overdue_context.return_value=({},pre,plan,[plan])
    result=overdue_days.update_overdue_days(Mock(),checks,collection_id='bill',days=2,dry_run=True)
    assert result['business_due_time']=='2026-10-19T01:00:00-06:00'
    writer.assert_not_called()
    runner.assert_not_called()
    assert not (tmp_path/'.state/overdue_days_attempts.json').exists()
