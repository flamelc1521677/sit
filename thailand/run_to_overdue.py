"""Existing paid Thai contract -> prealert -> OB5 -> overdue/penalty -> collection.

Default is read-only initial preflight. --execute performs the explicit chain;
each remote stage owns durable anti-replay state and requires business DB proof.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from common.admin_client import ADMIN_URL
from common.database import connect_database
from common.identity_client import lookup_user_by_phone
from common.overdue_client import positive_count, storage_datetime
from common.phone_sequence import locked
from common.prealert_client import read_job, get_plans, select_period, server_business_clock, reminder_days
from common.scheduled_jobs import read_scheduled_job
from common.state_store import write_json
from thailand.client import STATE, TH_ROOT, authenticate_thailand
from thailand.database_checks import ThailandDatabaseChecks
from thailand.prealert_assignment import verify_receiver_option
from thailand.workflows import load


def initial_preflight(session, checks, phone, contract_id, period, days):
    user = lookup_user_by_phone(session, phone)
    checks.prealert_user(user, phone)
    contract = checks.prealert_contract(user, contract_id)
    contract_id = contract['CONTRACT_ID']
    for filename in ('prealert_attempts.json', 'prealert_assignment_attempts.json', 'overdue_attempts.json', 'overdue_days_attempts.json', 'collection_assignment_attempts.json'):
        records = load(STATE / filename, {})
        assert not any(r.get('contract_id') == contract_id and int(r.get('period', period)) == period and r.get('status') != 'db_verified' for r in records.values()), '目标合同期次存在未确认业务进度，停止串联操作'
    for previous in (STATE / 'overdue_runs').glob('*.json'):
        old = load(previous)
        assert old.get('contract_id') != contract_id or old.get('status') == 'overdue_verified', '此合同已有未完成串联流程，禁止换标识重跑'
    plans = get_plans(session, contract_id)
    selected = select_period(plans, str(period))
    checks.repayment_matches_api(contract_id, plans)
    config = load(TH_ROOT / 'config/overdue.json')
    pre_config = load(TH_ROOT / 'config/prealert.json')
    pre_job = read_job(session, pre_config)
    checks.prealert_job(pre_job)
    checks.prealert_grade(reminder_days(pre_job['params']), 'TH')
    jobs = [read_scheduled_job(session, config[k]) for k in ('entryJob', 'penaltyJob', 'updateDaysJob')]
    for job in jobs:
        checks.scheduled_job_configuration(job)
    page = session.get(ADMIN_URL + '/system/job/edit/' + pre_job['id'], timeout=(10, 50))
    clock = server_business_clock(page, config['serverBusinessTimezone'])
    checks.storage_clock(config['storageTimezone'])
    days = positive_count(days, config['maximumDays'], '目标逾期天数')
    wall, storage = storage_datetime(clock, days, config['storageTimezone'])
    grade = checks.overdue_grade(days, 'TH')
    for plan in plans:
        if int(plan['period']) != period and str(plan['repayStatus']) == '1':
            assert plan['repayTimeStr'] > clock.date().isoformat(), '其他未还期次已到期，不适用单期模拟'
    snapshots = checks.repayment_snapshot(contract_id)
    current = next(p for p in snapshots if int(p['PERIOD']) == period)
    assert not current['FINAL_REPAYMENT_DATE'] and all(Decimal(str(current.get(k) or 0)) == 0 for k in ('LOAN_PRINCIPAL_REPAID', 'LOAN_SERVICE_FEE_REPAID', 'LOAN_PRINCIPAL_REDUCED', 'LOAN_SERVICE_FEE_REDUCED')), '部分还款或减免期次不适用'
    assert not current['LATE_CHARGE_ORIGIN'] and not current['LATE_CHARGE_ACTUAL'], '目标已有罚息，不能作为新增逾期'
    assert not checks.overdue_bills(contract_id), '已有逾期单，应使用TH14更新天数或TH13.2追加罚息'
    existing = [r for r in checks.prealert_records(contract_id) if int(r['PERIOD']) == period]
    assert len(existing) <= 1
    if existing:
        assert str(existing[0]['IS_FINAL_STATE']) == '0' and str(existing[0]['REPAYMENT_STATUS']) == '1'
    for name in ('prealert_assignment', 'collection_assignment'):
        receiver = checks.assignment_receiver(load(TH_ROOT / f'config/{name}.json'))
        verify_receiver_option(session, receiver)
    return {'phone': phone, 'user_id': user['id'], 'uuid': user['uuid'], 'appOwnership': user['appOwnership'],
        'contract_id': contract_id, 'period': period, 'days': days, 'server_business_time': clock.isoformat(),
        'business_due_time': wall.isoformat(), 'storage_due_time': str(storage), 'actual_grade': grade['REMIND_GRADE_NAME'],
        'prealert_task_params': str(pre_job['params']), 'prealert_id': existing[0]['ID'] if existing else None,
        'jobs': [{k: j[k] for k in ('id', 'beanName', 'methodName', 'params', 'dbGroupId', 'targetGroup')} for j in [pre_job] + jobs]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phone', required=True)
    parser.add_argument('--contract')
    parser.add_argument('--period', type=int, default=1)
    parser.add_argument('--days', type=int, required=True)
    parser.add_argument('--request-id', default='')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--skip-collection-assignment', action='store_true')
    args = parser.parse_args()
    assert re.fullmatch(r'[0-9]{10}', args.phone) and args.period > 0
    assert not args.execute or re.fullmatch(r'[A-Za-z0-9_-]{1,60}', args.request_id), '实际执行必须提供本次唯一标识'
    path = STATE / 'overdue_runs' / (args.request_id + '.json') if args.execute else None
    assert path is None or not path.exists(), '本次串联流程已有进度，不自动重发；请先核对原用户和任务结果'
    with locked(STATE / 'overdue_chain.lock'):
        assert path is None or not path.exists(), '等待锁期间出现已有进度，停止重复执行'
        (ROOT / 'artifacts').mkdir(parents=True, exist_ok=True)
        with connect_database() as db, requests.Session() as session:
            checks = ThailandDatabaseChecks(db)
            authenticate_thailand(session, checks)
            record = initial_preflight(session, checks, args.phone, args.contract, args.period, args.days)
        print('[泰国逾期串联初始预检] ' + json.dumps(record, ensure_ascii=False), flush=True)
        if not args.execute:
            print('[只读完成] 已验证已放款合同、目标期次、泰国任务/账龄/接收人；后续实际转交及入催结果须在生成后校验。未改期、未执行任务。', flush=True)
            return
        record.update(status='preflight_verified', stages=[])
        write_json(path, record)
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', TH_CREDENTIAL_SOURCE='local', SSO_CAPTCHA_MODE='ocr')
        env.update(TH_PREALERT_PHONE=args.phone, TH_PREALERT_APP_OWNERSHIP=record['appOwnership'], TH_PREALERT_CONTRACT_ID=record['contract_id'],
            TH_PREALERT_PERIOD=str(args.period), TH_PREALERT_DAYS='', TH_PREALERT_DRY_RUN='0',
            TH_PREALERT_ASSIGN_PHONE='', TH_PREALERT_ASSIGN_CONTRACT_ID=record['contract_id'], TH_PREALERT_ASSIGN_PERIOD=str(args.period), TH_PREALERT_ASSIGN_DRY_RUN='0',
            TH_OVERDUE_CONTRACT_ID=record['contract_id'], TH_OVERDUE_PERIOD=str(args.period), TH_OVERDUE_MODE='create',
            TH_OVERDUE_DAYS=str(args.days), TH_OVERDUE_PENALTY_DAYS='1', TH_OVERDUE_REQUEST_ID=args.request_id+'_overdue', TH_OVERDUE_DRY_RUN='0',
            TH_OVERDUE_EXPECTED_GRADE='', TH_OVERDUE_EXPECTED_DAILY_PENALTY='', TH_COLLECTION_CONTRACT_ID=record['contract_id'],
            TH_COLLECTION_PERIOD=str(args.period), TH_COLLECTION_ORDER_ID='', TH_COLLECTION_DRY_RUN='0')
        def stage(file):
            record['status'] = 'stage_started'
            record['current_stage'] = file
            write_json(path, record)
            log_path = ROOT / 'artifacts' / (args.request_id + '_' + Path(file).stem + '.log')
            assert not log_path.exists(), '日志已存在，停止覆盖'
            with log_path.open('x', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-m', 'pytest', '-c', 'thailand/pytest.ini', file, '-v', '-s', '-x'], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
                for line in child.stdout:
                    log.write(line); log.flush(); print(line, end='', flush=True)
                code = child.wait()
            record['stages'].append({'file': file, 'exit_code': code, 'log': str(log_path.relative_to(ROOT))})
            record['status'] = 'stage_db_verified' if code == 0 else 'stopped_preserving_original_attempts'
            write_json(path, record)
            if code:
                raise SystemExit(code)
        if not record['prealert_id']:
            stage('thailand/tests/11_prealert/test_th_generate_prealert.py')
            attempts = load(STATE / 'prealert_attempts.json', {})
            matching = [a for a in attempts.values() if a['contract_id'] == record['contract_id'] and int(a['period']) == args.period and a['status'] == 'db_verified']
            assert len(matching) == 1
            record['prealert_id'] = matching[0]['reminder_id']
        env.update(TH_PREALERT_ORDER_ID=record['prealert_id'], TH_OVERDUE_ORDER_ID=record['prealert_id'])
        stage('thailand/tests/12_prealert_assignment/test_th_assign_prealert.py')
        stage('thailand/tests/13_overdue/test_th_generate_overdue.py')
        if not args.skip_collection_assignment:
            stage('thailand/tests/15_collection_assignment/test_th_assign_collection.py')
        with connect_database() as db:
            checks = ThailandDatabaseChecks(db)
            bills = checks.overdue_bills(record['contract_id'])
            assert len(bills) == 1 and int(bills[0]['OVERDUE_DAYS']) == args.days
            plans = checks.repayment_snapshot(record['contract_id'])
            selected = next(p for p in plans if int(p['PERIOD']) == args.period)
            assert selected['LATE_CHARGE_ORIGIN'] > 0
            record.update(status='overdue_verified', collection_id=bills[0]['ID'], final_penalty=str(selected['LATE_CHARGE_ORIGIN']))
        write_json(path, record)
        print('[泰国逾期串联完成] ' + json.dumps({k: record[k] for k in ('phone','contract_id','period','days','actual_grade','collection_id','final_penalty')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
