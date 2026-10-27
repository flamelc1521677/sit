"""SELECT/API readback only: verify an existing overdue result without sending any job."""
import json
import os
from datetime import datetime
from decimal import Decimal

from common.admin_client import authenticate_admin
from common.prealert_client import get_plans
from common.state_store import ROOT


def test_verify_existing_overdue_result(session,database_checks):
    request_id=os.environ['VERIFY_OVERDUE_REQUEST_ID']
    expected_days=int(os.environ['VERIFY_OVERDUE_DAYS'])
    expected_principal=Decimal(os.environ['VERIFY_OVERDUE_PRINCIPAL'])
    expected_penalty=Decimal(os.environ['VERIFY_OVERDUE_PENALTY'])
    record=json.loads((ROOT/'.state/overdue_attempts.json').read_text(encoding='utf-8'))[request_id]
    assert record['overdue_days']==expected_days
    authenticate_admin(session)
    checks=database_checks
    checks.login_account(os.environ['SSO_USERNAME'])
    _,reminder,plan,plans=checks.overdue_context(record['prealert_id'],record['contract_id'],record['period'],'penalty_only')
    def verify():
        assert len(plans)==3 and sum(Decimal(str(r['MONTH_CAPITAL_ORIGIN'])) for r in plans)==expected_principal
        assert plan['REPAY_TIME']==reminder['REPAYMENT_DATE']==datetime.fromisoformat(record['storage_due_time'])
        assert Decimal(str(plan['LATE_CHARGE_ORIGIN']))==Decimal(str(plan['LATE_CHARGE_ACTUAL']))==expected_penalty
        normalized=json.loads(json.dumps(plan,default=str))
        for key,value in record['original_plan'].items():
            if key not in {'REPAY_TIME','LATE_CHARGE_ORIGIN','LATE_CHARGE_ACTUAL','PENALTY_UPDATE_TIME','MODIFY_TIME'}:
                assert normalized[key]==value, f'原还款计划字段变化：{key}'
        for other in plans:
            if other['ID']!=plan['ID']:
                assert str(other['REPAY_STATUS'])=='1' and Decimal(str(other['LATE_CHARGE_ORIGIN'] or 0))==0
                assert Decimal(str(other['LATE_CHARGE_ACTUAL'] or 0))==0
        bills=checks.overdue_bills(record['contract_id'])
        assert len(bills)==1 and bills[0]['ID']==record['collection_id']
        assert int(bills[0]['OVERDUE_DAYS'])==expected_days and bills[0]['REMIND_GRADE']==record['actual_grade_code']
        assert int(bills[0]['CURRENT_PERIOD'])==record['period']
        for log in record['job_logs']:
            rows=checks.database.select(f'SELECT JOB_STATUS,ERROR FROM `{checks.admin}`.schedule_job_log WHERE ID=%s',(log['id'],))
            assert len(rows)==1 and str(rows[0]['JOB_STATUS'])=='0' and not rows[0]['ERROR']
        return True
    checks.check('既有逾期天数/账龄、三期本金合计、罚息及成功任务日志只读核验',verify,timeout=0)
    api_plans=get_plans(session,record['contract_id'])
    checks.repayment_matches_api(record['contract_id'],api_plans)
    api=next(p for p in api_plans if p['id']==plan['ID'])
    assert Decimal(str(api['lateChargeOrigin']))==Decimal(str(api['lateChargeActual']))==expected_penalty
    print(f'[只读核验完成] 合同 {record["contract_id"]}，本金 {expected_principal}，3期，第一期逾期 {expected_days} 天，罚息 {expected_penalty}；未执行SQL写入或任何任务',flush=True)
