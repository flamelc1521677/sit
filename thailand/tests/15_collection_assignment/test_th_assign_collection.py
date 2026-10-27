"""15 · 催收手工分单 / 单笔转交给 ob5。"""
import os

from thailand.client import authenticate_thailand, configuration
from thailand.collection_assignment import assign_collection


def test_th_assign_collection_to_ob5(session,database_checks):
    order_id=os.getenv('TH_COLLECTION_ORDER_ID','').strip() or None
    contract_id=os.getenv('TH_COLLECTION_CONTRACT_ID','').strip() or None
    period=os.getenv('TH_COLLECTION_PERIOD','').strip() or None
    dry_run=os.getenv('TH_COLLECTION_DRY_RUN','1').strip()
    assert order_id or contract_id, '必须显式提供催收单 ID 或合同，不自动选择订单'
    assert dry_run in {'0','1'}, 'COLLECTION_DRY_RUN 只能为 0/1'
    authenticate_thailand(session, database_checks)
    operator=configuration()['employeeLogin']
    database_checks.login_account(operator)
    assign_collection(session,database_checks,order_id=order_id,contract_id=contract_id,period=period,
        dry_run=dry_run=='1',operator=operator)
