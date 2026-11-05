"""Read-only assertion: human review passed, no successful disbursement."""
import json
import os
from decimal import Decimal

from common.approval_credentials import approval_credentials
from common.approved_unpaid import assert_unpaid_snapshot
from common.loan_target import loan_target_phone
from common.state_store import ROOT, write_json


def test_approved_but_not_disbursed(database_checks):
    checks = database_checks
    phone = loan_target_phone()
    loans = json.loads((ROOT / '.state/loan_attempts.json').read_text(encoding='utf-8'))
    matches = [r for r in loans.values() if r.get('phone') == phone and r.get('status') == 'db_verified']
    assert len(matches) == 1, '没有唯一已核验借款'
    loan = matches[0]
    _, contract, bill = checks.payout_context(loan['header_id'])
    assigned = checks.approval_candidates(loan['header_id'])
    assert len(assigned) == 1, '审批关联记录不唯一'
    reviewer, _ = approval_credentials(os.environ['SSO_USERNAME'], checks.approval_record(assigned[0]['id']))
    actor = checks.login_account(reviewer)
    def verify():
        approvals = checks.approval_candidates(loan['header_id'])
        assert len(approvals) == 1, '审批关联记录不唯一'
        approval = checks.approval_record(approvals[0]['id'])
        assert str(approval['status']) == '1' and str(approval['auditor_id']) == str(actor['USER_ID']), '必须由独立审批账号人审通过'
        assert Decimal(str(approval['expected_amount'])) == Decimal(os.environ['APPROVAL_EXPECTED_AMOUNT'])
        rows = checks.database.select(f'SELECT CONTRACT_STATUS,PAYMENT_DATE FROM `{checks.loan}`.bor_contract WHERE CONTRACT_ID=%s', (contract['CONTRACT_ID'],))
        flows = checks.database.select(f'SELECT ID FROM `{checks.payout}`.pay_trade_bill_flow WHERE BILL_ID=%s AND STATE=1', (bill['ID'],))
        assert_unpaid_snapshot(bill, rows, flows)
        return approval
    approval = checks.check('独立人审通过、放款单未成功、合同未放款及无成功放款流水', verify, timeout=0)
    result = {'user_id': loan['user_id'], 'phone': phone, 'header_id': loan['header_id'],
        'approval_id': approval['id'], 'approval_status': 1, 'reviewer': reviewer,
        'contract_id': contract['CONTRACT_ID'], 'payout_id': bill['ID'], 'payout_state': str(bill['STATE']),
        'loan_amount': os.environ['APPROVAL_EXPECTED_AMOUNT'], 'status': 'approved_unpaid_db_verified'}
    write_json(ROOT / '.state/last_approved_unpaid_result.json', result)
    print('[完成] ' + json.dumps(result, ensure_ascii=False), flush=True)
