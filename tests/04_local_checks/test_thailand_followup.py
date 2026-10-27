"""Country boundaries, exact date writes, rollback, receiver and request guards."""
import copy
from datetime import datetime
from decimal import Decimal
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import requests

from common.overdue_client import storage_datetime
from thailand import overdue_fixture
from thailand.client import TH_ROOT
from thailand.collection_assignment import collection_payload, CollectionAssignmentAttempt
from thailand.database_checks import ThailandDatabaseChecks
from thailand.overdue_days import target_dates
from thailand.overdue_fixture import OverdueFixtureWriter
from thailand.prealert_assignment import assignment_payload
from thailand.workflows import load


def fixture_rows():
    plan = {'ID': 'plan', 'CONTRACT_ID': 'contract', 'PERIOD': 1, 'REPAY_STATUS': '1', 'REPAY_TIME': datetime(2026, 11, 3, 1), 'LATE_CHARGE_ORIGIN': Decimal(0), 'MONTH_CAPITAL': Decimal('1166.67')}
    reminder = {'ID': 'pre', 'CONTRACT_ID': 'contract', 'PERIOD': 1, 'REPAYMENT_STATUS': '1', 'IS_FINAL_STATE': '0', 'REPAYMENT_DATE': plan['REPAY_TIME'], 'SINGLE_FLAG': '1', 'USER_ID': '1593', 'OWNER_NAME': 'ob5'}
    return plan, reminder


def transaction(second_count=1):
    plan, reminder = fixture_rows()
    target = datetime(2026, 10, 26, 2)
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchall.side_effect = [[{'CONTRACT_STATUS': '1'}], [plan], [reminder], [{**plan, 'REPAY_TIME': target}], [{**reminder, 'REPAYMENT_DATE': target}]]
    updates = []
    def execute(sql, values):
        if sql.startswith('UPDATE '):
            updates.append((sql, values))
            cursor.rowcount = 1 if len(updates) == 1 else second_count
    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = cursor
    return connection, cursor, updates, plan, reminder, target


def test_thai_date_transaction_cannot_touch_mexico_or_money():
    connection, _, updates, plan, reminder, target = transaction()
    OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, target)
    assert len(updates) == 2
    for sql, params in updates:
        assert '`off_th_saas_loan`' in sql and 'off_mex' not in sql
        assert 'WHERE ID=%s AND CONTRACT_ID=%s AND PERIOD=%s' in sql
        assert params[2:4] == ('contract', 1)
        assert 'SET `REPAY_TIME`' in sql or 'SET `REPAYMENT_DATE`' in sql
    connection.commit.assert_called_once()


def test_thai_second_row_failure_rolls_back_transaction():
    connection, _, _, plan, reminder, target = transaction(0)
    with pytest.raises(AssertionError, match='更新行数'):
        OverdueFixtureWriter(connection).backdate_due_dates(plan, reminder, target)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_unknown_commit_prevents_replay():
    connection, _, updates, plan, reminder, target = transaction()
    connection.commit.side_effect = ConnectionError('unknown commit')
    writer = OverdueFixtureWriter(connection)
    with pytest.raises(ConnectionError):
        writer.backdate_due_dates(plan, reminder, target)
    with pytest.raises(AssertionError, match='禁止自动重发'):
        writer.backdate_due_dates(plan, reminder, target)
    assert len(updates) == 2


def test_thai_writer_rejects_mexico_schema_and_other_hosts(monkeypatch):
    with pytest.raises(AssertionError, match='SIT TH'):
        OverdueFixtureWriter(Mock(), 'off_mex_saas_loan')
    monkeypatch.setattr(overdue_fixture, 'connection_options', lambda: {'host': 'another.example', 'port': 3306})
    connect = Mock()
    monkeypatch.setattr(overdue_fixture.pymysql, 'connect', connect)
    with pytest.raises(AssertionError, match='禁止连接其他环境'):
        with overdue_fixture.connect_overdue_fixture():
            pytest.fail('must not connect')
    connect.assert_not_called()


def test_unassigned_or_other_period_cannot_begin_date_write():
    plan, reminder = fixture_rows()
    connection = Mock()
    for row in [{**reminder, 'USER_ID': None}, {**reminder, 'PERIOD': 2}]:
        with pytest.raises(AssertionError):
            OverdueFixtureWriter(connection).backdate_due_dates(plan, row, datetime(2026, 10, 26, 2))
    connection.begin.assert_not_called()


def test_no_generic_amount_or_status_updates():
    writer = OverdueFixtureWriter(Mock())
    cursor = Mock()
    for table, column in [('bor_installment_act', 'LATE_CHARGE_ORIGIN'), ('bor_contract', 'CONTRACT_STATUS'), ('rem_bill', 'OVERDUE_DAYS')]:
        with pytest.raises(AssertionError, match='仅允许'):
            writer._update(cursor, table, column, {}, datetime(2026, 10, 26, 2))
    cursor.execute.assert_not_called()


def test_thai_days_use_server_calendar_across_year_boundary():
    clock = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo('Asia/Bangkok'))
    wall, stored = storage_datetime(clock, 1, 'Asia/Shanghai')
    assert wall.isoformat() == '2026-12-31T01:00:00+07:00'
    assert stored == datetime(2026, 12, 31, 2)
    _, due, overdue = target_dates(clock, 2, load(TH_ROOT/'config/overdue.json'))
    assert due == datetime(2026, 12, 30, 2) and overdue == datetime(2026, 12, 31, 2)


def test_mexico_job_cannot_pass_thai_scope_guard():
    checks = ThailandDatabaseChecks(Mock())
    with pytest.raises(AssertionError, match='其他国家'):
        checks.scheduled_job_configuration({'dbGroupId': '1', 'targetGroup': '2'})
    checks.database.select.assert_not_called()


def form(pre, before, extra=False):
    names = ['distributionMode','deptSelect','deptIdList','ownerDictList','userName','isFilter'] + (['ids','isAssistAllocation'] if pre else ['id'])
    fields = '<form id="form-distributeOrTransferBills-add">'+''.join(f'<input name="{x}">' for x in names)+'<input name="opType" value="2"></form>'
    mapping = {'id':'ID','contractId':'CONTRACT_ID','period':'PERIOD','merchantId':'MERCHANT_ID','userId':'USER_ID','ownerName':'OWNER_NAME','deptId':'DEPT_ID'}
    mapping.update({'repaymentStatus':'REPAYMENT_STATUS'} if pre else {'remindBillId':'REMIND_BILL_ID','remindStatus':'REMIND_STATUS','overdueDays':'OVERDUE_DAYS','remindGrade':'REMIND_GRADE','overdueAmount':'OVERDUE_AMOUNT'})
    rows = [{k: before[v] for k,v in mapping.items()}]
    if extra: rows.append(dict(rows[0]))
    return fields+'<script>var records = '+json.dumps(rows)+';var '+('preReminderVo' if pre else 'remindBillVo')+' = '+json.dumps({'merchantIdList':['11784']} if pre else {'listType':'5'})+';</script>'


def assignment_rows():
    before = {'ID':'record-id','CONTRACT_ID':'contract','PERIOD':1,'CURRENT_PERIOD':1,'MERCHANT_ID':'11784','USER_ID':None,'OWNER_NAME':None,'DEPT_ID':11804,'REPAYMENT_STATUS':'1','IS_FINAL_STATE':'0','REMIND_BILL_ID':'REM-business-id','REMIND_STATUS':'0','OVERDUE_DAYS':'1','REMIND_GRADE':'grade','OVERDUE_AMOUNT':'1200'}
    receiver = {'USER_ID':1593,'LOGIN_NAME':'ob5','DEPT_ID':11804,'DEPT_NAME':'业务二部'}
    return before, receiver


def test_both_transfers_pin_ob5_and_distinguish_record_ids():
    before, receiver = assignment_rows()
    pre = assignment_payload(form(True,before), before, receiver, load(TH_ROOT/'config/prealert_assignment.json'))
    col = collection_payload(form(False,before), before, receiver, load(TH_ROOT/'config/collection_assignment.json'))
    assert pre['ids'] == before['ID'] and 'id' not in pre
    assert col['id'] == before['ID'] and 'ids' not in col
    assert pre['ownerDictList'] == col['ownerDictList'] == '1593'
    assert pre['deptIdList'] == col['deptIdList'] == '11804'
    assert pre['userName'] == col['userName'] == '1593-ob5'


@pytest.mark.parametrize('pre', [True, False])
def test_bulk_and_mexico_receivers_are_rejected(pre):
    before, receiver = assignment_rows()
    fn = assignment_payload if pre else collection_payload
    config = load(TH_ROOT/('config/prealert_assignment.json' if pre else 'config/collection_assignment.json'))
    with pytest.raises(AssertionError): fn(form(pre,before,True),before,receiver,config)
    with pytest.raises(AssertionError): fn(form(pre,before),before,{**receiver,'USER_ID':1590},config)


def test_thai_transfer_timeout_never_resends():
    before, receiver = assignment_rows()
    payload = collection_payload(form(False,before), before, receiver, load(TH_ROOT/'config/collection_assignment.json'))
    session = Mock()
    session.post.side_effect = requests.Timeout('unknown')
    attempt = CollectionAssignmentAttempt(session)
    with pytest.raises(requests.Timeout): attempt.execute(payload,before,copy.deepcopy(before))
    with pytest.raises(AssertionError,match='禁止自动重发'): attempt.execute(payload,before,before)
    assert session.post.call_count == 1


def test_assigned_collection_day_change_only_updates_three_dates():
    plan, reminder = fixture_rows()
    bill = {'ID':'collection','CONTRACT_ID':'contract','PERIOD':1,'CURRENT_PERIOD':1,'PERIOD_LIST':'1','DEL_FLAG':'0','REMIND_STATUS':'1','OVERDUE_DATE':datetime(2026,10,27,2),'OVERDUE_DAYS':'1','REMIND_GRADE':'S0-code'}
    due, entered = datetime(2026,10,25,2), datetime(2026,10,26,2)
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.rowcount = 1
    cursor.fetchall.side_effect = [[{'CONTRACT_STATUS':'2'}],[plan],[reminder],[bill],[{**plan,'REPAY_TIME':due}],[{**reminder,'REPAYMENT_DATE':due}],[{**bill,'OVERDUE_DATE':entered}]]
    connection = Mock()
    connection.cursor.return_value = cursor
    OverdueFixtureWriter(connection).update_overdue_dates(plan,reminder,bill,due,entered)
    updates = [c.args[0] for c in cursor.execute.call_args_list if c.args[0].startswith('UPDATE ')]
    assert len(updates) == 3
    for sql, column in zip(updates,['REPAY_TIME','REPAYMENT_DATE','OVERDUE_DATE']):
        assert f'SET `{column}`=%s' in sql and '`off_th_saas_loan`' in sql
        assert 'SET `OVERDUE_DAYS`' not in sql and 'SET `REMIND_GRADE`' not in sql
    connection.commit.assert_called_once()


@pytest.mark.parametrize('action,status,log_action', [('reject',2,'REJECT'),('close',3,'CLOSE')])
def test_thai_alternative_review_is_exclusive_and_has_actual_actor(monkeypatch,action,status,log_action):
    checks = ThailandDatabaseChecks(Mock())
    monkeypatch.setattr(checks,'check',lambda label,fn:fn())
    row = {'id':'approval','status':status,'version':1,'pre_repayment_plan_header_id':'header','contract_amount':'3500','expected_amount':None,'approval_reason':'reason','reject_reason':'reason','auditor_id':'1572','audit_time':datetime(2026,10,27)}
    monkeypatch.setattr(checks,'approval_record',lambda _:row)
    monkeypatch.setattr(checks,'approval_logs',lambda _:[{'id':'new','action_type':log_action,'operator_type':'USER','operator_id':'1572','from_status':0,'to_status':status}])
    checks.approval_result({'id':'approval','version':0,'pre_repayment_plan_header_id':'header'},action,'reason','3500',set(),1572)
    with pytest.raises(AssertionError):
        checks.approval_result({'id':'approval','version':0,'pre_repayment_plan_header_id':'header'},'pass','reason','3500',set(),1572)
