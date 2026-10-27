"""14 · 已逾期订单 / 更新为指定逾期天数。"""
import os

from thailand.client import authenticate_thailand, configuration
from thailand.overdue_days import update_overdue_days


def test_th_update_overdue_days(session, database_checks):
    dry_run = os.getenv('TH_UPDATE_OVERDUE_DRY_RUN', '1').strip()
    assert dry_run in {'0', '1'}, 'UPDATE_OVERDUE_DRY_RUN 仅允许 0/1'
    # Validate explicit input before authentication or business calls.
    collection_id = os.getenv('TH_UPDATE_OVERDUE_COLLECTION_ID', '').strip() or None
    contract_id = os.getenv('TH_UPDATE_OVERDUE_CONTRACT_ID', '').strip() or None
    period = os.getenv('TH_UPDATE_OVERDUE_PERIOD', '').strip() or None
    assert collection_id or (contract_id and period), '必须提供催收单 ID，或合同+期次'
    authenticate_thailand(session, database_checks)
    database_checks.login_account(configuration()['employeeLogin'])
    update_overdue_days(session, database_checks,
        collection_id=collection_id, contract_id=contract_id, period=period,
        days=os.getenv('TH_UPDATE_OVERDUE_DAYS', '2').strip(),
        request_id=os.getenv('TH_UPDATE_OVERDUE_REQUEST_ID', '').strip(), dry_run=dry_run == '1')
