"""Execute an explicitly reviewed two-user plan, preserving every stage and attempt.

The plan contains existing users, not a request to create new users. No unknown
business result is retried. All business assertions remain in the scenario tests.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.database import connect_database
from common.database_checks import DatabaseChecks
from common.phone_sequence import locked
from common.state_store import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--resume-verified-review', action='store_true')
    parser.add_argument('--overdue-existing-paid', action='store_true', help='仅处理已核验的原合同及第一次复贷，不提交借款或审批放款')
    args = parser.parse_args()
    assert re.fullmatch(r'[A-Za-z0-9_-]+', args.plan), '计划标识格式错误'
    path = ROOT / '.state' / (args.plan + '.json')
    plan = json.loads(path.read_text(encoding='utf-8'))
    if args.overdue_existing_paid:
        assert not args.resume_verified_review
        assert plan['status'] == 'awaiting_backend_virtual_order_generation'
        assert all(not u.get('ordered_contracts') for u in plan['users']), '已有改期进度，先只读核验'
    elif args.resume_verified_review:
        assert plan['status'] == 'review_preflight_failure_verified_no_request', '仅允许已核对未发送审批请求的前置检查失败续跑'
    else:
        assert plan['status'] == 'selected_verified', '计划已尝试执行，先核对现有进度；禁止自动重跑'
    assert len(plan['users']) == 2 and plan['ordered_first_period_overdue_days'] == [3, 2, 1]
    env = dict(os.environ)
    env.update(PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', SSO_CREDENTIAL_SOURCE='local',
        LOAN_CONFIG_FILE=str(ROOT / 'config/loan_6000_3periods.json'),
        APPROVAL_ID='', APPROVAL_ACTION='pass', APPROVAL_EXPECTED_AMOUNT='6000',
        PREALERT_DAYS='', PREALERT_PERIOD='1', PREALERT_ASSIGN_PHONE='',
        OVERDUE_MODE='create', OVERDUE_DRY_RUN='0', OVERDUE_PERIOD='1',
        OVERDUE_PENALTY_DAYS='1', OVERDUE_EXPECTED_GRADE='', OVERDUE_EXPECTED_DAILY_PENALTY='')

    def save(status):
        plan['status'] = status
        write_json(path, plan)

    def stage(label, file, record):
        if file in record.get('completed_stages', []):
            print(f'[已完成阶段] {label}，保留原结果，不重发', flush=True)
            return
        for attempt in range(1, 4):
            log_path = ROOT / 'artifacts' / f'{args.plan}_{label}_attempt{attempt}.log'
            revision = 1
            while log_path.exists():
                log_path = ROOT / 'artifacts' / f'{args.plan}_{label}_attempt{attempt}_resume{revision}.log'
                revision += 1
            record.update(current_stage=file, current_label=label)
            save(label + '_started')
            print(f'[执行] {label}，{file}，登录尝试 {attempt}', flush=True)
            output = []
            with log_path.open('x', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-m', 'pytest', file, '-v', '-s', '--tb=short', '-x'],
                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
                for line in child.stdout:
                    output.append(line)
                    log.write(line)
                    log.flush()
                    print(line, end='', flush=True)
                code = child.wait()
            record.setdefault('logs', []).append(str(log_path.relative_to(ROOT)))
            if code == 0:
                record.setdefault('completed_stages', []).append(file)
                save(label + '_verified')
                return
            text = ''.join(output)
            if not ("'code': 9999" in text and '验证码有误' in text and 'common\\sso_login.py' in text) or attempt == 3:
                save(label + '_failed')
                raise RuntimeError('场景失败，保留原进度，不自动重发业务请求')
            print('[验证码] 仅重试尚未完成的登录，不重发业务操作', flush=True)

    with locked(ROOT / '.state/two_user_ordered_overdue.lock'):
        save('repeat_loans_started')
        execution_users = sorted(plan['users'], key=lambda u: plan.get('resume_user_order', [r['user_id'] for r in plan['users']]).index(u['user_id']))
        if args.overdue_existing_paid:
            execution_users = []
        for user in execution_users:
            env.update(LOAN_TARGET_PHONE=user['phone'], LOAN_TARGET_APP_OWNERSHIP=user['ownership'],
                       LOAN_REPEAT_USER_ID=str(user['user_id']))
            for number in (1, 2):
                request_id = f"user{user['user_id']}-repeat{number}-{args.plan}"
                env['LOAN_REPEAT_REQUEST_ID'] = request_id
                existing = [r for r in user['new_loan_requests'] if r['number'] == number]
                assert len(existing) <= 1
                record = existing[0] if existing else {'number': number, 'request_id': request_id}
                assert record['request_id'] == request_id
                if not existing:
                    user['new_loan_requests'].append(record)
                prefix = f"u{user['user_id']}_repeat{number}"
                stage(prefix + '_loan', 'tests/05_loan_application/test_start_loan.py', record)
                stage(prefix + '_review', 'tests/07_payout_approval/test_payout_approval.py', record)
                stage(prefix + '_payout', 'tests/08_disbursement/test_payout_success.py', record)
                loans = json.loads((ROOT / '.state/loan_attempts.json').read_text(encoding='utf-8'))
                current = loans[user['ownership'] + ':' + user['uuid']]
                matching = [r for r in current.get('previous_loans', []) + [current] if r.get('repeat_request_id') == request_id]
                assert len(matching) == 1
                loan = matching[0]
                assert loan['status'] == 'db_verified' and loan['repeat_request_id'] == request_id
                with connect_database() as db:
                    checks = DatabaseChecks(db)
                    _, contract, bill = checks.payout_context(loan['header_id'])
                    assert str(bill['STATE']) == '1'
                    record.update(header_id=loan['header_id'], contract_id=contract['CONTRACT_ID'], payout_id=bill['ID'])
                save(prefix + '_paid_verified')
                print(f"[复贷完成] 用户 {user['user_id']} 第{number}笔复贷已放款，合同 {record['contract_id']}", flush=True)
        # Freeze the entire contract set and its chronological order before any due-date mutation.
        with connect_database() as db:
            checks = DatabaseChecks(db)
            for user in plan['users']:
                contracts = db.select(f'SELECT CONTRACT_ID,CREATE_TIME,PAYMENT_DATE,CONTRACT_STATUS FROM `{checks.loan}`.bor_contract WHERE SALE_UUID=%s AND OWNERSHIP=%s ORDER BY CREATE_TIME,CONTRACT_ID', (user['uuid'], user['ownership']))
                expected = {user['original_contract']['CONTRACT_ID']} | {r['contract_id'] for r in user['new_loan_requests'] if r.get('contract_id')}
                expected_count = 2 if args.overdue_existing_paid else 3
                assert len(contracts) == expected_count and len(expected) == expected_count and {c['CONTRACT_ID'] for c in contracts} == expected
                if args.overdue_existing_paid:
                    latest = json.loads((ROOT / '.state/loan_attempts.json').read_text(encoding='utf-8'))[user['ownership'] + ':' + user['uuid']]
                    pending = db.select(f'SELECT ID,VIRTUAL_ORDER_ID FROM `{checks.loan}`.bor_pre_repayment_plan_header WHERE ID=%s AND UUID=%s', (latest['header_id'], user['uuid']))
                    assert len(pending) == 1 and not pending[0]['VIRTUAL_ORDER_ID'] and not checks.approval_candidates(latest['header_id'])
                    user['deferred_third_application'] = {'header_id': latest['header_id'], 'target_overdue_days': 1, 'reason': 'no_linked_paid_contract'}
                assert all(str(c['CONTRACT_STATUS']) == '1' and c['PAYMENT_DATE'] for c in contracts)
                user['ordered_contracts'] = []
                for c, days in zip(contracts, [3, 2, 1]):
                    snapshots = checks.repayment_snapshot(c['CONTRACT_ID'])
                    assert len(snapshots) == 3 and all(str(r['REPAY_STATUS']) == '1' and not r['LATE_CHARGE_ORIGIN'] for r in snapshots)
                    assert not checks.prealert_records(c['CONTRACT_ID']) and not checks.overdue_bills(c['CONTRACT_ID'])
                    user['ordered_contracts'].append({'contract_id': c['CONTRACT_ID'], 'created_at': str(c['CREATE_TIME']),
                        'days': days, 'period': 1, 'original_plans': json.loads(json.dumps(snapshots, default=str))})
        save('existing_paid_contracts_ordered_verified')
        for user in plan['users']:
            for index, order in enumerate(user['ordered_contracts'], 1):
                prefix = f"u{user['user_id']}_order{index}"
                env.update(PREALERT_PHONE=user['phone'], PREALERT_CONTRACT_ID=order['contract_id'])
                stage(prefix + '_prealert', 'tests/11_prealert/test_generate_prealert.py', order)
                records = json.loads((ROOT / '.state/prealert_attempts.json').read_text(encoding='utf-8'))
                matches = [r for r in records.values() if r.get('contract_id') == order['contract_id'] and r.get('period') == 1 and r.get('status') == 'db_verified']
                assert len(matches) == 1
                order['prealert_id'] = matches[0]['reminder_id']
                env.update(PREALERT_ORDER_ID=order['prealert_id'], PREALERT_ASSIGN_CONTRACT_ID=order['contract_id'], PREALERT_ASSIGN_PERIOD='1')
                stage(prefix + '_assignment', 'tests/12_prealert_assignment/test_assign_prealert.py', order)
        save('selected_prealerts_generated_and_assigned')
        for user in plan['users']:
            for index, order in enumerate(user['ordered_contracts'], 1):
                prefix = f"u{user['user_id']}_order{index}"
                request_id = f"{args.plan}-u{user['user_id']}-order{index}-d{order['days']}"
                env.update(OVERDUE_ORDER_ID=order['prealert_id'], OVERDUE_CONTRACT_ID=order['contract_id'],
                    OVERDUE_DAYS=str(order['days']), OVERDUE_REQUEST_ID=request_id)
                order['overdue_request_id'] = request_id
                stage(prefix + '_overdue', 'tests/13_overdue/test_generate_overdue.py', order)
                order['status'] = 'overdue_db_verified'
                save(prefix + '_overdue_verified')
                print(f"[逾期完成] 用户 {user['user_id']} 第{index}笔合同 {order['contract_id']} 首期逾期{order['days']}天", flush=True)
        save('existing_paid_overdue_pending_final_audit' if args.overdue_existing_paid else 'all_steps_verified_pending_final_audit')
        print('[已选合同处理完成] 等待统一只读复核；未生成合同的申请保留原状态', flush=True)


if __name__ == '__main__':
    main()
