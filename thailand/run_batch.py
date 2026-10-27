"""Sequential Thai new-user batches; stop at profile, submission, review or payout."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.phone_sequence import locked
from common.state_store import write_json
from thailand.client import STATE, TH_ROOT
from thailand.workflows import load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--count', required=True, type=int)
    parser.add_argument('--stop-after', choices=['profile', 'submission', 'review', 'payout'], default='review')
    args = parser.parse_args()
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,60}', args.batch_id) and 1 <= args.count <= 20
    path = STATE / 'batches' / (args.batch_id + '.json')
    assert not path.exists(), '该批次已运行，先核对原用户；禁止换标识掩盖未知结果'
    with locked(STATE / 'batch_operation.lock'):
        assert not path.exists(), '等待锁期间批次已创建，停止重复新增'
        batch = {'status': 'started', 'batch_id': args.batch_id, 'count': args.count, 'stop_after': args.stop_after, 'users': []}
        write_json(path, batch)
        for number in range(1, args.count + 1):
            run_id = args.batch_id + f'_user{number}'
            assert not (STATE / 'runs' / (run_id + '.json')).exists()
            batch.update(status='user_started', current_run_id=run_id)
            write_json(path, batch)
            result = subprocess.run([sys.executable, str(TH_ROOT / 'run_full_flow.py'), '--run-id', run_id, '--stop-after', args.stop_after], cwd=ROOT, env=dict(os.environ))
            if result.returncode:
                batch['status'] = 'stopped_preserving_original_users'
                write_json(path, batch)
                raise SystemExit(result.returncode)
            run = load(STATE / 'runs' / (run_id + '.json'))
            assert run['status'] in {'profile_verified', 'loan_submitted_verified', 'approved_unpaid_verified', 'payout_success_verified'}
            batch['users'].append({'run_id': run_id, **run['receipt']})
            write_json(path, batch)
        assert len({u['user_id'] for u in batch['users']}) == args.count
        # Final SELECT-only review of every already-approved but unpaid member;
        # detecting a later payout is a failure, never replaced with another user.
        if args.stop_after == 'review':
            for user in batch['users']:
                env = dict(os.environ, TH_TARGET_PHONE=user['phone'], TH_CREDENTIAL_SOURCE='local', TH_FLOW_USER_UUID='', TH_FLOW_USER_ID='')
                result = subprocess.run([sys.executable, '-m', 'pytest', '-c', 'thailand/pytest.ini', 'thailand/tests/16_approved_unpaid/test_th_approved_unpaid.py', '-q', '-s', '-x'], cwd=ROOT, env=env)
                if result.returncode:
                    batch['status'] = 'final_unpaid_audit_failed'
                    write_json(path, batch)
                    raise SystemExit(result.returncode)
        batch['status'] = 'completed'
        write_json(path, batch)


if __name__ == '__main__':
    main()
