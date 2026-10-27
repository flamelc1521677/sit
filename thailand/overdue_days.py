"""Reusable update of one existing overdue order; dates only, followed by the real job."""
import json
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json
from common.overdue_client import positive_count, storage_datetime
from thailand.overdue_fixture import connect_overdue_fixture, snapshot_unchanged
from common.phone_sequence import locked
from common.prealert_client import get_plans, server_business_clock
from common.scheduled_jobs import JobRunAttempt, read_scheduled_job
from common.sso_login import TIMEOUT
from common.state_store import write_json
from thailand.client import TH_ROOT as ROOT, STATE, authenticate_thailand, configuration


def target_dates(clock, days, config):
    days = positive_count(days, config['maximumDays'], '目标逾期天数')
    wall, due = storage_datetime(clock, days, config['storageTimezone'])
    overdue = (wall + timedelta(days=1)).astimezone(ZoneInfo(config['storageTimezone'])).replace(tzinfo=None)
    return wall, due, overdue


def collection_snapshot(checks, collection_id=None, contract_id=None, period=None):
    assert collection_id or (contract_id and period), '必须指定催收单 ID，或合同+期次'
    clauses, values = ["COALESCE(DEL_FLAG,'0')='0'"], []
    for key, value in (('ID', collection_id), ('CONTRACT_ID', contract_id), ('CURRENT_PERIOD', period)):
        if value:
            clauses.append(key + '=%s')
            values.append(value)
    rows = checks.database.select(f'SELECT * FROM `{checks.loan}`.rem_bill WHERE ' + ' AND '.join(clauses), tuple(values))
    assert len(rows) == 1, '没有唯一有效催收单，请指定准确 ID 或合同+期次'
    row = rows[0]
    assert str(row['REMIND_STATUS']) in {'0', '1'}, '仅支持未结清的有效催收单'
    assert int(row['CURRENT_PERIOD']) == int(row['PERIOD']) and str(row['PERIOD_LIST']) == str(row['PERIOD']), '多期合并催收暂不支持，避免影响其他期次'
    return row


def verify_result(checks, record, require_days):
    """Repeatable SELECT-only verification, including unchanged money and other periods."""
    def verify():
        from datetime import datetime
        original = record['original']
        plans = checks.repayment_snapshot(record['contract_id'])
        normalize = lambda value: json.loads(json.dumps(value, default=str))
        by_id = {row['ID']: row for row in normalize(plans)}
        assert set(by_id) == {p['ID'] for p in original['plans']}, '期次集合变化'
        for before in original['plans']:
            after = by_id[before['ID']]
            if before['ID'] == record['plan_id']:
                snapshot_unchanged(before, after, 'REPAY_TIME', record['storage_due_time'])
            else:
                assert after == before, '其他期次发生变化'
        reminder = normalize(checks.assignment_record(record['prealert_id']))
        snapshot_unchanged(original['reminder'], reminder, 'REPAYMENT_DATE', record['storage_due_time'])
        bill = collection_snapshot(checks, record['collection_id'])
        after = normalize(bill)
        before = dict(original['collection'])
        # Days/grade are task outputs, never SQL fixture write targets.
        for key in ('OVERDUE_DAYS', 'REMIND_GRADE'):
            before.pop(key, None)
            after.pop(key, None)
        if require_days:
            # A task may refresh derived metadata; money and ownership remain strict.
            for key in ('GRADE_CHANGE_DATE', 'SUMMARY_REFRESH_TIME'):
                before.pop(key, None)
                after.pop(key, None)
        snapshot_unchanged(before, after, 'OVERDUE_DATE', record['storage_overdue_time'])
        assert bill['OVERDUE_DATE'] == datetime.fromisoformat(record['storage_overdue_time'])
        assert str(checks.database.select(f'SELECT CONTRACT_STATUS FROM `{checks.loan}`.bor_contract WHERE CONTRACT_ID=%s', (record['contract_id'],))[0]['CONTRACT_STATUS']) == '2', '合同不再处于逾期状态'
        if require_days:
            assert int(bill['OVERDUE_DAYS']) == record['days'], '定时任务未将目标逾期天数更新到期望值'
        else:
            assert bill['OVERDUE_DAYS'] == original['collection']['OVERDUE_DAYS'] and bill['REMIND_GRADE'] == original['collection']['REMIND_GRADE'], '日期事务意外改变了天数或账龄'
        return bill
    return checks.check('逾期日期、目标天数、金额与其他期次核验' if require_days else '三表日期事务及非日期字段核验', verify, timeout=60 if require_days else 0)


def update_overdue_days(session, checks, *, days, collection_id=None, contract_id=None,
                        period=None, request_id='', dry_run=True):
    """No implicit target, no automatic new overdue creation, no penalty job execution."""
    config = json.loads((ROOT / 'config/overdue.json').read_text(encoding='utf-8'))
    days = positive_count(days, config['maximumDays'], '目标逾期天数')
    assert collection_id or (contract_id and period), '必须提供催收单 ID 或合同+期次'
    if period is not None:
        assert str(period).isdigit() and int(period) > 0, '期次必须是正整数'
    assert dry_run or re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', request_id), '实际执行必须提供唯一 UPDATE_OVERDUE_REQUEST_ID，重跑保持原值'
    with locked(STATE / 'overdue_operation.lock'), locked(STATE / 'prealert_operation.lock'), locked(STATE / 'prealert_assignment.lock'):
        path = STATE / 'overdue_days_attempts.json'
        attempts = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        previous = attempts.get(request_id) if not dry_run else None
        if previous:
            assert previous['status'] == 'db_verified', '本次操作结果尚未确认，禁止重发；先核对事务及任务日志'
            assert previous['days'] == days, '相同请求标识的逾期天数变化'
            for supplied, key in ((collection_id, 'collection_id'), (contract_id, 'contract_id'), (period, 'period')):
                if supplied is not None:
                    assert str(supplied) == str(previous[key]), '相同请求标识的目标变化'
            bill = verify_result(checks, previous, True)
            checks.repayment_matches_api(previous['contract_id'], get_plans(session, previous['contract_id']))
            print('[已完成] 相同请求仅只读核验，未重复改期或执行任务', flush=True)
            return bill
        bill = collection_snapshot(checks, collection_id, contract_id, period)
        contract_id, period = bill['CONTRACT_ID'], int(bill['CURRENT_PERIOD'])
        _, reminder, plan, plans = checks.overdue_context(contract_id=contract_id, period=period, mode='update_days')
        job = read_scheduled_job(session, config['updateDaysJob'])
        checks.scheduled_job_configuration(job, '催收更新逾期天数任务')
        def clock_now():
            response = session.get(ADMIN_URL + '/system/job/edit/' + job['id'], timeout=TIMEOUT)
            return server_business_clock(response, config['serverBusinessTimezone'])
        clock = clock_now()
        checks.storage_clock(config['storageTimezone'])
        wall, due, overdue = target_dates(clock, days, config)
        for other in plans:
            if other['ID'] != plan['ID'] and str(other['REPAY_STATUS']) == '1':
                assert other['REPAY_TIME'].replace(tzinfo=ZoneInfo(config['storageTimezone'])).astimezone(clock.tzinfo).date() > clock.date(), '其他未还期次已到期，不能使用单期场景'
        checks.repayment_matches_api(contract_id, get_plans(session, contract_id))
        record = {'contract_id': contract_id, 'period': period, 'collection_id': bill['ID'],
                  'prealert_id': reminder['ID'], 'plan_id': plan['ID'], 'days': days,
                  'server_business_time': clock.isoformat(), 'business_due_time': wall.isoformat(),
                  'storage_due_time': str(due), 'storage_overdue_time': str(overdue),
                  'original': json.loads(json.dumps({'plans': plans, 'reminder': reminder, 'collection': bill}, default=str))}
        print('[更新逾期预检] ' + json.dumps({k:v for k,v in record.items() if k != 'original'}, ensure_ascii=False), flush=True)
        if dry_run:
            print('[只读预检通过] 未修改日期、未执行共享任务', flush=True)
            return record
        for state_file in (path, STATE / 'overdue_attempts.json'):
            records = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
            assert not any(r['contract_id'] == contract_id and int(r['period']) == period and r['status'] != 'db_verified' for r in records.values()), '同一目标存在未确认操作，不能换请求标识绕过'
        attempts[request_id] = record
        def status(value):
            record['status'] = value
            write_json(path, attempts)
        def same_day():
            assert clock_now().date() == clock.date(), '执行中服务端跨日，停止后续操作'
        same_day()
        fresh = read_scheduled_job(session, config['updateDaysJob'])
        assert fresh == job, '任务配置变化，停止日期写入'
        status('date_transaction_started')
        with connect_overdue_fixture() as writer:
            writer.update_overdue_dates(plan, reminder, bill, due, overdue)
        status('date_transaction_committed')
        verify_result(checks, record, False)
        status('date_db_verified')
        same_day()
        fresh = read_scheduled_job(session, config['updateDaysJob'])
        assert fresh == job, '任务配置变化，禁止执行'
        old_logs = checks.prealert_log_ids(job['id'])
        record['previous_job_log_ids'] = sorted(old_logs)
        status('job_request_sent')
        JobRunAttempt(session, job).run(fresh)
        status('job_api_success')
        log = checks.scheduled_job_finished(job, old_logs, '更新逾期天数任务')
        record['job_log_id'] = log['ID']
        status('job_log_verified')
        same_day()
        result = verify_result(checks, record, True)
        checks.repayment_matches_api(contract_id, get_plans(session, contract_id))
        listing = checked_json(session.post(ADMIN_URL + '/loan/remindBill/list', data={'contractId':contract_id, 'listType':'5', 'pageNum':1, 'pageSize':100}, timeout=TIMEOUT), '催收列表回查')
        visible = [r for r in listing.get('rows', []) if r.get('id') == bill['ID']]
        if visible:
            assert len(visible) == 1 and int(visible[0]['overdueDays']) == days, '列表接口逾期天数与数据库不一致'
        record.update(api_list_visible=bool(visible), final_grade=result['REMIND_GRADE'], final_overdue_days=int(result['OVERDUE_DAYS']))
        status('db_verified')
        print(f'[完成] 合同 {contract_id} 第{period}期，逾期 {days} 天；数据库及任务日志通过；列表可见={bool(visible)}；未执行罚息任务', flush=True)
        return result
