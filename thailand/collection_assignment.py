"""Precisely transfer one explicit overdue collection order to ob5."""
import json
import re
from decimal import Decimal

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json
from common.phone_sequence import locked
from thailand.prealert_assignment import AssignmentForm, javascript_json, verify_receiver_option
from common.sso_login import TIMEOUT
from common.state_store import write_json
from thailand.client import TH_ROOT as ROOT, STATE, authenticate_thailand, configuration


def collection_payload(html,before,receiver,config):
    assert config=={'receiverLogin':'ob5','receiverId':'1593','departmentId':'11804','distributionMode':'1','opType':'2'}, '催收手工转交固定为 ob5 / 11804'
    assert str(receiver['USER_ID'])=='1593' and receiver['LOGIN_NAME']=='ob5' and str(receiver['DEPT_ID'])=='11804', '接收人或部门不匹配'
    parser=AssignmentForm();parser.feed(html)
    assert {'distributionMode','deptSelect','deptIdList','ownerDictList','userName','opType','id','isFilter'} <= parser.names and parser.op_type=='2', '催收转交表单协议变化'
    records=javascript_json(html,'records')
    assert isinstance(records,list) and len(records)==1 and isinstance(records[0],dict), '必须仅包含一笔准确的催收单'
    row=records[0]
    for api,column in {'id':'ID','contractId':'CONTRACT_ID','period':'PERIOD','merchantId':'MERCHANT_ID','remindBillId':'REMIND_BILL_ID','remindStatus':'REMIND_STATUS','overdueDays':'OVERDUE_DAYS','remindGrade':'REMIND_GRADE'}.items():
        assert str(row.get(api))==str(before[column]), f'表单目标字段 {api} 与数据库不一致'
    for api,column in {'ownerName':'OWNER_NAME','userId':'USER_ID','deptId':'DEPT_ID'}.items():
        assert str(row.get(api) or '')==str(before.get(column) or ''), '原归属已经变化'
    assert str(row['remindStatus']) in {'0','1'}, '已结清或委外订单不可转交'
    assert Decimal(str(row['overdueAmount']))==Decimal(str(before['OVERDUE_AMOUNT'])), '催收金额不一致'
    filters=javascript_json(html,'remindBillVo')
    assert isinstance(filters,dict) and str(filters.get('listType'))=='5', '必须从所有待催收入口精准选单'
    assert before['ID'] and ',' not in before['ID'], '禁止空 ID 或多笔批量分单'
    return {'distributionMode':'1','deptSelect':receiver['DEPT_NAME'],'deptIdList':'11804','isFilter':'',
        'ownerDictList':'1593','userName':'1593-ob5','opType':'2','id':before['ID'],
        'contractId':'','contractIdList':'','startDate':'','endDate':'','phoneNum':'',
        'gradeChangeDateStr':'','gradeChangeDateStrEnd':'','remindStatus':'','isOption':'',
        'noSameDay':'','noSameDayEnd':'','listType':'5','acCase':'','isLock':'0'}


class CollectionAssignmentAttempt:
    def __init__(self,session):
        self.session=session
        self.sent=False

    def execute(self,payload,before,fresh):
        assert not self.sent, '转交已尝试，禁止自动重发'
        assert fresh==before and payload.get('id') and payload.get('id')==before['ID'] and ',' not in payload['id'], '记录变化或目标不准确'
        assert all(payload.get(k)==v for k,v in {'distributionMode':'1','opType':'2','deptIdList':'11804','ownerDictList':'1593','userName':'1593-ob5'}.items()), '必须单笔精准转交给固定接收人'
        self.sent=True
        return checked_json(self.session.post(ADMIN_URL+'/loan/remindBill/updateDistributeOrTransferBills',data=payload,timeout=(10,120),allow_redirects=False),'催收手工转交')


def assign_collection(session,checks,*,order_id=None,contract_id=None,period=None,dry_run=True,operator):
    assert order_id or contract_id, '必须显式提供催收单 ID 或合同'
    if period:
        assert re.fullmatch(r'[1-9][0-9]*',str(period)), '期次必须为正整数'
    config=json.loads((ROOT/'config/collection_assignment.json').read_text(encoding='utf-8'))
    with locked(STATE / 'overdue_operation.lock'), locked(STATE / 'collection_assignment.lock'):
        receiver=checks.assignment_receiver(config)
        before=checks.collection_assignment_target(order_id,contract_id,period)
        path=STATE / 'collection_assignment_attempts.json'
        attempts=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        previous=attempts.get(before['ID'])
        if previous:
            assert previous['status']=='db_verified', '已有未确认催收转交，禁止重发；先核对归属与流水'
        if str(before.get('USER_ID'))=='1593' and before.get('OWNER_NAME')=='ob5' and str(before.get('DEPT_ID'))=='11804':
            checks.check('催收单已归属固定接收人，仅只读回查',lambda: checks.collection_assignment_record(before['ID']),timeout=0)
            print('[催收分单] 已归属 ob5 / 11804，仅回查，不重复转交',flush=True)
            return before
        assert not previous, '曾成功转交的催收单归属已变化，不能自动覆盖'
        for name in ('overdue_attempts.json','overdue_days_attempts.json'):
            other=STATE/name
            values=json.loads(other.read_text(encoding='utf-8')).values() if other.exists() else []
            assert not any(r['contract_id']==before['CONTRACT_ID'] and r['status']!='db_verified' for r in values), '该合同有未确认逾期操作'
        verify_receiver_option(session,receiver)
        response=session.get(ADMIN_URL+'/loan/remindBill/distributeOrTransferBills',params={
            'opType':'2','ids':before['REMIND_BILL_ID'],'listType':'5','isLock':'0'},timeout=TIMEOUT)
        assert response.status_code==200 and 'html' in response.headers.get('Content-Type','').lower(), '未获取真实催收转交表单'
        payload=collection_payload(response.text,before,receiver,config)
        plans=checks.repayment_snapshot(before['CONTRACT_ID'])
        fresh=checks.collection_assignment_record(before['ID'])
        assert fresh==before, '提交前记录或归属变化'
        checks.assignment_receiver(config)
        print(f"[催收分单] 单笔 {before['ID']}，合同 {before['CONTRACT_ID']}，转交 ob5 / 11804",flush=True)
        if dry_run:
            print('[只读预检] 表单、目标、接收人及数据库核验通过；未提交转交',flush=True)
            return before
        old_ids={r['id'] for r in checks.collection_assignment_flows(before)}
        record={'status':'request_sent','order_id':before['ID'],'contract_id':before['CONTRACT_ID'],
            'period':before['CURRENT_PERIOD'],'receiver':'ob5','receiver_id':'1593','department_id':'11804',
            'operator':operator,'old_flow_ids':sorted(old_ids),'original':json.loads(json.dumps(before,default=str)),
            'original_plans':json.loads(json.dumps(plans,default=str))}
        attempts[before['ID']]=record;write_json(path,attempts)
        result=CollectionAssignmentAttempt(session).execute(payload,before,fresh)
        record.update(status='api_success',code=result.get('code'),msg=result.get('msg'));write_json(path,attempts)
        row,flow=checks.collection_assignment_success(before,receiver,old_ids,operator,plans)
        record.update(status='db_verified',flow_id=flow['id']);write_json(path,attempts)
        listing=checked_json(session.post(ADMIN_URL+'/loan/remindBill/list',data={'contractId':before['CONTRACT_ID'],'listType':'5','pageNum':1,'pageSize':100},timeout=TIMEOUT),'催收转交后列表回查')
        visible=[r for r in listing.get('rows',[]) if r.get('id')==before['ID']]
        if visible:
            assert len(visible)==1 and str(visible[0].get('userId'))=='1593' and str(visible[0].get('deptId'))=='11804', '列表归属与数据库不一致'
        record['api_list_visible']=bool(visible);write_json(path,attempts)
        print(f'[完成] 催收已转交 ob5，数据库归属及新增流水通过；列表可见={bool(visible)}',flush=True)
        return row
