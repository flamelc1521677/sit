"""Explicit batch: new users -> profiles -> loans -> review pass -> SELECT-only unpaid check.

No disbursement scenario is allowed. Existing batch IDs stop before any new user.
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
from common.approval_credentials import approval_credentials
from common.database import connect_database
from common.database_checks import DatabaseChecks
from common.phone_sequence import locked
from common.sso_credentials import sso_credentials
from common.state_store import write_json

STAGES = (
    'tests/02_user_management/test_add_user.py',
    'tests/03_identity_verification/test_identity_ocr.py',
    'tests/05_loan_application/test_start_loan.py',
    'tests/07_payout_approval/test_payout_approval.py',
    'tests/16_approved_unpaid/test_approved_unpaid.py',
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--loan-config', default='config/loan_6000_3periods.json')
    args = parser.parse_args()
    assert 1 <= args.count <= 20 and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', args.batch_id)
    config = json.loads((ROOT / args.loan_config).read_text(encoding='utf-8'))
    env = dict(os.environ)
    env.update(PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', SSO_CREDENTIAL_SOURCE='local',
        LOAN_TARGET_PHONE='', LOAN_REPEAT_USER_ID='', LOAN_REPEAT_REQUEST_ID='',
        APPROVAL_ID='', APPROVAL_ACTION='pass', APPROVAL_EXPECTED_AMOUNT=config['loanAmount'],
        LOAN_CONFIG_FILE=str(ROOT / args.loan_config))
    requester, _ = sso_credentials()
    reviewer, _ = approval_credentials(requester)
    env['SSO_USERNAME'] = requester
    with connect_database() as db:
        checks = DatabaseChecks(db)
        checks.login_account(requester)
        checks.login_account(reviewer)
    state_path = ROOT / '.state' / (args.batch_id + '.json')
    with locked(ROOT / '.state/approved_unpaid_batch.lock'):
        assert not state_path.exists(), '该批次已有运行记录，禁止重跑或换标识绕过未确认结果'
        batch = {'batch_id': args.batch_id, 'count': args.count, 'status': 'running',
            'loan_amount': config['loanAmount'], 'installments': config['installmentCount'], 'users': []}
        write_json(state_path, batch)
        for index in range(1, args.count + 1):
            row = {'index': index, 'status': 'running', 'completed_stages': []}
            batch['users'].append(row)
            for stage_index, stage in enumerate(STAGES):
                row.update(current_stage=stage, status='stage_started')
                write_json(state_path, batch)
                if stage_index > 0:
                    latest = json.loads((ROOT / '.state/latest_user.json').read_text(encoding='utf-8'))
                    assert latest['phone'] == row['phone'], '最新用户发生变化，停止避免错单'
                for attempt in range(1, 4):
                    print(f'[批次 {index}/{args.count}] {stage}，尝试 {attempt}', flush=True)
                    log_path = ROOT / 'artifacts' / f'{args.batch_id}_user{index}_step{stage_index + 1}_attempt{attempt}.log'
                    log_path.parent.mkdir(exist_ok=True)
                    output = []
                    with log_path.open('w', encoding='utf-8') as log:
                        process = subprocess.Popen([sys.executable, '-m', 'pytest', stage, '-v', '-s', '--tb=short', '-x'],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
                        for line in process.stdout:
                            log.write(line)
                            log.flush()
                            output.append(line)
                            print(line, end='', flush=True)
                        code = process.wait()
                    row.setdefault('logs', []).append(str(log_path.relative_to(ROOT)))
                    if code == 0:
                        break
                    # This specific failure is before authentication completes, before any business POST.
                    text = ''.join(output)
                    captcha_only = "'code': 9999" in text and '验证码有误' in text and 'common\\sso_login.py' in text
                    if not captcha_only or attempt == 3:
                        row.update(status='failed', exit_code=code)
                        batch['status'] = 'stopped_on_failure'
                        write_json(state_path, batch)
                        return code or 1
                    print('[验证码] 登录尚未完成，仅重新获取验证码；未重发业务请求', flush=True)
                row['completed_stages'].append(stage)
                if stage_index == 0:
                    row['phone'] = json.loads((ROOT / '.state/latest_user.json').read_text(encoding='utf-8'))['phone']
                row['status'] = 'stage_verified'
                write_json(state_path, batch)
            result = json.loads((ROOT / '.state/last_approved_unpaid_result.json').read_text(encoding='utf-8'))
            assert result['phone'] == row['phone']
            row.update(result)
            write_json(state_path, batch)
            print(f"[批次完成 {index}/{args.count}] 用户 {result['user_id']} / {result['phone']} 人审通过，未放款", flush=True)
        batch['status'] = 'db_verified'
        write_json(state_path, batch)
        print('[批次全部完成] ' + json.dumps(batch['users'], ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
