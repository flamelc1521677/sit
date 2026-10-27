"""One new Thai user only; durable stages, explicit run ID, no replay of unknown results."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from common.database import connect_database
from common.phone_sequence import locked
from common.state_store import write_json
from thailand.client import STATE, TH_ROOT, authenticate_thailand, reviewer_credentials, configuration, assert_no_pending
from thailand.database_checks import ThailandDatabaseChecks
from thailand.workflows import load

STAGES = [
    'thailand/tests/02_user_management/test_th_add_user.py',
    'thailand/tests/03_identity_verification/test_th_profile.py',
    'thailand/tests/05_loan_application/test_th_loan.py',
    'thailand/tests/07_payout_approval/test_th_review.py',
    'thailand/tests/08_disbursement/test_th_payout.py',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--stop-after', choices=['profile', 'submission', 'review', 'payout'], default='payout')
    parser.add_argument('--overdue-days', type=int, default=0)
    parser.add_argument('--resume-before-create', action='store_true')
    parser.add_argument('--resume-verified-created', action='store_true')
    parser.add_argument('--resume-approved', action='store_true')
    args = parser.parse_args()
    assert 0 <= args.overdue_days <= 365 and (args.overdue_days == 0 or args.stop_after == 'payout')
    assert args.overdue_days == 0 or len(args.run_id) <= 40, '新增到逾期的运行标识最多40位'
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.run_id), '运行标识只允许字母数字下划线或短横线'
    path = STATE / 'runs' / (args.run_id + '.json')
    previous_run = load(path)
    assert sum([args.resume_before_create, args.resume_verified_created, args.resume_approved]) <= 1
    if args.resume_approved:
        assert previous_run and previous_run['status'] == 'existing_review_db_verified'
        user = previous_run['created_user']
        assert load(STATE / 'profile_attempts.json')[user['uuid']]['status'] == 'db_verified'
        loan = load(STATE / 'loan_attempts.json')[user['uuid']]
        assert loan['status'] == 'db_verified'
        review = load(STATE / 'approval_attempts.json')[loan['header_id']]
        assert review['status'] == 'db_verified' and review['reconciled_readonly']
    elif args.resume_verified_created:
        assert previous_run and previous_run['status'] == 'existing_created_user_db_verified' and previous_run['created_user']
        user = previous_run['created_user']
        created = load(STATE / 'add_user_attempts.json')[user['phone']]
        assert created['status'] == 'db_verified' and created['uuid'] == user['uuid'] and created['reconciled_readonly']
    elif args.resume_before_create:
        assert previous_run and previous_run['status'] == 'verified_no_create_request' and previous_run['created_user'] is None
        assert previous_run.get('precreate_audit', {}).get('create_request_sent') is False
        assert all(r['status'] == 'occupied_existing' for r in load(STATE / 'add_user_attempts.json', {}).values())
    else:
        assert previous_run is None, '此运行标识已经使用，先核对原进度，不新建第二名用户'
    reviewer_credentials()
    assert (TH_ROOT / 'assets/identity_front.png').is_file()
    with locked(STATE / 'full_flow.lock'):
        assert load(path) == previous_run, '等待锁期间运行状态发生变化，禁止重复新增'
        (ROOT / 'artifacts').mkdir(parents=True, exist_ok=True)
        for previous in (STATE / 'runs').glob('*.json'):
            if (args.resume_before_create or args.resume_verified_created or args.resume_approved) and previous == path:
                continue
            assert load(previous)['status'] in {'payout_success_verified', 'loan_submitted_verified', 'approved_unpaid_verified', 'profile_verified', 'overdue_verified'}, '已有未完成泰国全流程，请继续原用户，不换标识新增'
        assert_no_pending(load(STATE / 'add_user_attempts.json', {}))
        # Real authentication, permissions and schema preflight before creating a user.
        with connect_database() as db, requests.Session() as reviewer, requests.Session() as requester:
            checks = ThailandDatabaseChecks(db)
            authenticate_thailand(reviewer, checks, reviewer=True)
            authenticate_thailand(requester, checks)
            config = configuration()
            checks.employee_line_config(config['employeeLineConfigId'], config['employeeLine'], config['employeeLogin'])
            for schema, table in [(checks.biz, 't_mgt_user'), (checks.loan, 'bor_pre_repayment_plan_header'), (checks.borrower, 'sal_apply_info'), (checks.payout, 'pay_trade_bill_moco')]:
                rows = db.select('SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (schema, table))
                assert len(rows) == 1, '泰国真实库表预检失败'
            if args.overdue_days:
                from common.prealert_client import read_job
                from common.scheduled_jobs import read_scheduled_job
                from thailand.prealert_assignment import verify_receiver_option
                checks.prealert_job(read_job(requester, load(TH_ROOT / 'config/prealert.json')))
                overdue_config = load(TH_ROOT / 'config/overdue.json')
                checks.overdue_grade(args.overdue_days, 'TH')
                for key in ('entryJob', 'penaltyJob', 'updateDaysJob'):
                    checks.scheduled_job_configuration(read_scheduled_job(requester, overdue_config[key]))
                verify_receiver_option(requester, checks.assignment_receiver(load(TH_ROOT / 'config/prealert_assignment.json')))
        run = previous_run or {'run_id': args.run_id, 'status': 'preflight_verified', 'country': 'TH', 'stages': [], 'created_user': None}
        assert run.get('stop_after', 'payout') == args.stop_after or previous_run is None, '续跑不得改变原运行停止点'
        run['stop_after'] = args.stop_after
        assert run.get('overdue_days', 0) == args.overdue_days or previous_run is None, '续跑不得改变原逾期目标'
        run['overdue_days'] = args.overdue_days
        write_json(path, run)
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', TH_CREDENTIAL_SOURCE='local', SSO_CAPTCHA_MODE='ocr')
        env.update(TH_TARGET_PHONE='', TH_REPEAT_USER_ID='', TH_REPEAT_REQUEST_ID='', TH_FLOW_RUN_ID=args.run_id)
        if args.resume_verified_created or args.resume_approved:
            env.update(TH_FLOW_USER_UUID=run['created_user']['uuid'], TH_FLOW_USER_ID=str(run['created_user']['user_id']))
        selected_stages = STAGES if args.stop_after == 'payout' else STAGES[:2] if args.stop_after == 'profile' else STAGES[:3] if args.stop_after == 'submission' else STAGES[:4] + ['thailand/tests/16_approved_unpaid/test_th_approved_unpaid.py']
        for stage in selected_stages:
            if args.resume_approved and stage in STAGES[:4]:
                print(f'[泰国全流程] 保留已核验阶段，不重发：{stage}', flush=True)
                continue
            if args.resume_verified_created and stage == STAGES[0]:
                print('[泰国全流程] 新增结果已只读复核，保留原用户，跳过新增请求', flush=True)
                continue
            run['current_stage'] = stage
            run['status'] = 'stage_started'
            write_json(path, run)
            log_path = ROOT / 'artifacts' / (args.run_id + '_' + Path(stage).stem + '.log')
            revision = 1
            while log_path.exists():
                log_path = ROOT / 'artifacts' / (args.run_id + '_' + Path(stage).stem + f'_resume{revision}.log')
                revision += 1
            print(f'[泰国全流程] 执行 {stage}', flush=True)
            with log_path.open('x', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-m', 'pytest', '-c', 'thailand/pytest.ini', stage, '-v', '-s', '--tb=short', '-x'], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
                for line in child.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end='', flush=True)
                exit_code = child.wait()
            run['stages'].append({'file': stage, 'exit_code': exit_code, 'log': str(log_path.relative_to(ROOT))})
            if exit_code:
                run['status'] = 'stopped_preserving_original_attempts'
                write_json(path, run)
                print(f'[泰国全流程停止] 保留原用户和状态，查看 {log_path}', flush=True)
                raise SystemExit(exit_code)
            current = load(STATE / 'latest_user.json')
            if run['created_user'] is None:
                assert current.get('flow_run_id') == args.run_id, '新增阶段完成后最新用户已被其他流程替换，停止'
                run['created_user'] = current
                env.update(TH_FLOW_USER_UUID=current['uuid'], TH_FLOW_USER_ID=str(current['user_id']))
            else:
                assert current == run['created_user'], '执行中最新用户被其他流程修改，停止'
            run['status'] = 'stage_db_verified'
            write_json(path, run)
            print(f'[泰国全流程] 已通过：{Path(stage).stem}', flush=True)
        if args.stop_after != 'payout':
            user = run['created_user']
            run['receipt'] = {'user_id': user['user_id'], 'phone': user['phone']}
            if args.stop_after != 'profile':
                loan = load(STATE / 'loan_attempts.json')[user['uuid']]
                run['receipt']['header_id'] = loan['header_id']
            if args.stop_after == 'review':
                run['receipt'] = load(STATE / 'last_approved_unpaid_result.json')
                assert run['receipt']['header_id'] == loan['header_id']
            run['status'] = {'profile': 'profile_verified', 'submission': 'loan_submitted_verified', 'review': 'approved_unpaid_verified'}[args.stop_after]
            write_json(path, run)
            print('[泰国流程完成] ' + json.dumps(run['receipt'], ensure_ascii=False), flush=True)
            return
        with connect_database() as db:
            checks = ThailandDatabaseChecks(db)
            user = run['created_user']
            loan = load(STATE / 'loan_attempts.json')[user['uuid']]
            header, contract, bill = checks.paid_chain(loan['header_id'])
            run['receipt'] = {'user_id': user['user_id'], 'phone': user['phone'], 'header_id': header['ID'], 'contract_id': contract['CONTRACT_ID'], 'payout_id': bill['ID'], 'payout_state': str(bill['STATE']), 'trade_amount': str(bill['TRADE_AMOUNT']), 'payment_date': str(contract['PAYMENT_DATE'])}
        run['status'] = 'payout_success_verified'
        write_json(path, run)
        write_json(STATE / 'last_full_flow_result.json', run['receipt'])
        print('[泰国放款阶段完成] ' + json.dumps(run['receipt'], ensure_ascii=False), flush=True)
        if args.overdue_days:
            request_id = args.run_id + '_overdue_chain'
            run['status'] = 'paid_overdue_chain_started'
            write_json(path, run)
            child = subprocess.run([sys.executable, str(TH_ROOT / 'run_to_overdue.py'), '--phone', run['receipt']['phone'], '--contract', run['receipt']['contract_id'], '--period', '1', '--days', str(args.overdue_days), '--request-id', request_id, '--execute'], cwd=ROOT, env=env)
            if child.returncode:
                run['status'] = 'paid_overdue_chain_stopped'
                write_json(path, run)
                raise SystemExit(child.returncode)
            overdue = load(STATE / 'overdue_runs' / (request_id + '.json'))
            assert overdue['status'] == 'overdue_verified' and overdue['contract_id'] == run['receipt']['contract_id']
            run.update(status='overdue_verified', overdue_receipt=overdue)
            write_json(path, run)


if __name__ == '__main__':
    main()
