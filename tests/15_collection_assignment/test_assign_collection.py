"""15 · 催收手工分单 / 单笔转交给 ob4-mx。"""
import os

from common.admin_client import authenticate_admin
from common.collection_assignment import assign_collection


def test_assign_collection_to_ob4(session,database_checks):
    order_id=os.getenv('COLLECTION_ORDER_ID','').strip() or None
    contract_id=os.getenv('COLLECTION_CONTRACT_ID','').strip() or None
    period=os.getenv('COLLECTION_PERIOD','').strip() or None
    dry_run=os.getenv('COLLECTION_DRY_RUN','1').strip()
    assert order_id or contract_id, '必须显式提供催收单 ID 或合同，不自动选择订单'
    assert dry_run in {'0','1'}, 'COLLECTION_DRY_RUN 只能为 0/1'
    authenticate_admin(session)
    operator=os.environ['SSO_USERNAME']
    database_checks.login_account(operator)
    assign_collection(session,database_checks,order_id=order_id,contract_id=contract_id,period=period,
        dry_run=dry_run=='1',operator=operator)
