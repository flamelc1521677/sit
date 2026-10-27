import copy
import json
from unittest.mock import Mock

import pytest
import requests

from common import collection_assignment as module
from common.collection_assignment import CollectionAssignmentAttempt,collection_payload


CONFIG={'receiverLogin':'ob4-mx','receiverId':'1590','departmentId':'11807','distributionMode':'1','opType':'2'}
RECEIVER={'USER_ID':1590,'LOGIN_NAME':'ob4-mx','DEPT_ID':11807,'DEPT_NAME':'墨西哥二部-预提醒'}
BEFORE={'ID':'uuid-id','CONTRACT_ID':'contract','PERIOD':1,'CURRENT_PERIOD':1,'MERCHANT_ID':'11801',
    'REMIND_BILL_ID':'REM-business-id','REMIND_STATUS':'0','OVERDUE_DAYS':'1','REMIND_GRADE':'grade',
    'USER_ID':None,'OWNER_NAME':None,'DEPT_ID':11807,'OVERDUE_AMOUNT':'953.66'}


def html(records=None):
    if records is None:
        mapping={'id':'ID','contractId':'CONTRACT_ID','period':'PERIOD','merchantId':'MERCHANT_ID',
            'remindBillId':'REMIND_BILL_ID','remindStatus':'REMIND_STATUS','overdueDays':'OVERDUE_DAYS',
            'remindGrade':'REMIND_GRADE','userId':'USER_ID','ownerName':'OWNER_NAME','deptId':'DEPT_ID','overdueAmount':'OVERDUE_AMOUNT'}
        records=[{k:BEFORE[v] for k,v in mapping.items()}]
    names=['distributionMode','deptSelect','deptIdList','ownerDictList','userName','id','isFilter']
    return '<form id="form-distributeOrTransferBills-add">'+''.join(f'<input name="{n}">' for n in names)+'<input name="opType" value="2"></form><script>var records = '+json.dumps(records)+'; var remindBillVo = {"listType":"5"};</script>'


def test_page_business_id_is_not_submitted_as_record_id():
    payload=collection_payload(html(),BEFORE,RECEIVER,CONFIG)
    assert payload['id']=='uuid-id' and 'ids' not in payload
    assert payload['distributionMode']=='1' and payload['opType']=='2'
    assert payload['deptIdList']=='11807' and payload['ownerDictList']=='1590'
    assert 'isAssistAllocation' not in payload


@pytest.mark.parametrize('records',[[],[None],[{},{}]])
def test_empty_or_bulk_page_blocks_submission(records):
    with pytest.raises(AssertionError,match='必须仅包含一笔'):
        collection_payload(html(records),BEFORE,RECEIVER,CONFIG)


def test_stale_owner_or_wrong_record_is_rejected():
    for field,value in [('OWNER_NAME','other'),('ID','other-id'),('OVERDUE_AMOUNT','900')]:
        before={**BEFORE,field:value}
        with pytest.raises(AssertionError):
            collection_payload(html(),before,RECEIVER,CONFIG)


def test_other_receiver_cannot_be_selected():
    with pytest.raises(AssertionError,match='接收人或部门不匹配'):
        collection_payload(html(),BEFORE,{**RECEIVER,'USER_ID':1591},CONFIG)


def test_unknown_timeout_cannot_resend():
    session=Mock()
    session.post.side_effect=requests.Timeout('unknown')
    attempt=CollectionAssignmentAttempt(session)
    payload=collection_payload(html(),BEFORE,RECEIVER,CONFIG)
    with pytest.raises(requests.Timeout):
        attempt.execute(payload,BEFORE,copy.deepcopy(BEFORE))
    with pytest.raises(AssertionError,match='禁止自动重发'):
        attempt.execute(payload,BEFORE,BEFORE)
    assert session.post.call_count==1


def test_concurrent_record_change_does_not_submit():
    session=Mock()
    with pytest.raises(AssertionError,match='记录变化'):
        CollectionAssignmentAttempt(session).execute(collection_payload(html(),BEFORE,RECEIVER,CONFIG),BEFORE,{**BEFORE,'OWNER_NAME':'other'})
    session.post.assert_not_called()


def test_already_assigned_order_is_readonly(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/collection_assignment.json').write_text(json.dumps(CONFIG),encoding='utf-8')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    before={**BEFORE,'USER_ID':1590,'OWNER_NAME':'ob4-mx'}
    checks=Mock()
    checks.collection_assignment_target.return_value=before
    session=Mock()
    module.assign_collection(session,checks,order_id='uuid-id',dry_run=False,operator='ob4-mx')
    session.get.assert_not_called()
    session.post.assert_not_called()
    assert not (tmp_path/'.state/collection_assignment_attempts.json').exists()


def test_unknown_persisted_attempt_cannot_be_replayed(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/collection_assignment.json').write_text(json.dumps(CONFIG),encoding='utf-8')
    (tmp_path/'.state').mkdir()
    (tmp_path/'.state/collection_assignment_attempts.json').write_text(json.dumps({'uuid-id':{'status':'request_sent'}}),encoding='utf-8')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    checks=Mock()
    checks.collection_assignment_target.return_value=BEFORE
    session=Mock()
    with pytest.raises(AssertionError,match='禁止重发'):
        module.assign_collection(session,checks,order_id='uuid-id',dry_run=False,operator='ob4-mx')
    session.post.assert_not_called()
