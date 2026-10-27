"""Prove independent manual approval and absence of disbursement, SELECT-only."""
from decimal import Decimal
from common.approved_unpaid import assert_unpaid_snapshot
from common.state_store import write_json
from thailand.client import STATE, authenticate_thailand, reviewer_credentials
from thailand.workflows import current_loan


def test_th_approved_unpaid(session, database_checks):
    checks = database_checks
    authenticate_thailand(session, checks)
    _, loan = current_loan(session, checks)
    _, contract, bill = checks.payout_context(loan['header_id'])
    reviewer, _ = reviewer_credentials()
    actor = checks.reviewer_account(reviewer)
    def verify():
        candidates = checks.approval_candidates(loan['header_id'])
        assert len(candidates) == 1
        approval = checks.approval_record(candidates[0]['id'])
        assert str(approval['status']) == '1' and str(approval['auditor_id']) == str(actor['USER_ID'])
        assert Decimal(str(approval['contract_amount'])) == Decimal(loan['trial_submission']['loanAmount'])
        logs = [r for r in checks.approval_logs(approval['id']) if r['action_type'] == 'PASS' and r['operator_type'] == 'USER' and str(r['operator_id']) == str(actor['USER_ID'])]
        assert len(logs) == 1, '必须有chengzi的唯一人工通过流水'
        contracts = checks.database.select(f'SELECT CONTRACT_STATUS,PAYMENT_DATE FROM `{checks.loan}`.bor_contract WHERE CONTRACT_ID=%s', (contract['CONTRACT_ID'],))
        flows = checks.database.select(f'SELECT ID FROM `{checks.payout}`.pay_trade_bill_flow WHERE BILL_ID=%s AND STATE=1', (bill['ID'],))
        assert_unpaid_snapshot(bill, contracts, flows)
        return approval
    approval = checks.check('泰国chengzi人工通过、未成功放款及无成功流水', verify, timeout=0)
    result = {'status': 'approved_unpaid_verified', 'user_id': loan['user_id'], 'phone': loan['phone'], 'header_id': loan['header_id'], 'contract_id': contract['CONTRACT_ID'], 'approval_id': approval['id'], 'reviewer': reviewer, 'payout_id': bill['ID'], 'payout_state': str(bill['STATE'])}
    write_json(STATE / 'last_approved_unpaid_result.json', result)
