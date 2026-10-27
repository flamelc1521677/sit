import json,runpy,time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from common.admin_client import authenticate_admin
from common.database import connect_database
from common.database_checks import DatabaseChecks
from common.identity_client import latest_user
from common.state_store import ROOT,write_json

batch_path=ROOT/'.state/batch_profiles_20261021.json'
assert not batch_path.exists(), '本批次已有记录，请先核对状态，禁止重复创建'
modules=[runpy.run_path(str(ROOT/'tests/02_user_management/test_add_user.py')),runpy.run_path(str(ROOT/'tests/03_identity_verification/test_identity_ocr.py'))]

def login_with_captcha_retry(session):
    for attempt in range(1,4):
        try:
            return authenticate_admin(session)
        except AssertionError as error:
            if '验证码有误' not in str(error) or attempt==3:
                raise
            print(f'[登录] 验证码识别失败；仅重新登录 ({attempt}/3)，不重发业务请求',flush=True)
            session.cookies.clear()

for module,name in zip(modules,('test_add_offline_user','test_upload_ocr_and_save_identity')):
    module[name].__globals__['authenticate_admin']=login_with_captcha_retry

original=requests.Session.request
def forbid_loans(self,method,url,*args,**kwargs):
    assert '/startLoan' not in urlsplit(url).path, '本批次禁止调用试算或借款接口'
    return original(self,method,url,*args,**kwargs)
requests.Session.request=forbid_loans

def verify_no_loans(checks,user):
    assert not checks.loan_headers(user), "用户存在借款主记录"

records=[]
write_json(batch_path,records)
for index in range(1,6):
    record={'index':index,'status':'creating'}
    records.append(record);write_json(batch_path,records)
    print(f'\n========== 用户 {index}/5：新增及完善资料，不申请借款 ========== ',flush=True)
    with requests.Session() as session,connect_database() as db:
        checks=DatabaseChecks(db)
        modules[0]['test_add_offline_user'](session,checks)
        user,phone,_=latest_user(session,ROOT/'.state/latest_user.json')
        record.update(user_id=user['id'],phone=phone,uuid=user['uuid'],appOwnership=user['appOwnership'],status='created')
        write_json(batch_path,records)
    with requests.Session() as session,connect_database() as db:
        checks=DatabaseChecks(db)
        modules[1]['test_upload_ocr_and_save_identity'](session,checks)
        identity=json.loads((ROOT/'.state/last_identity_result.json').read_text(encoding='utf-8'))
        bank=json.loads((ROOT/'.state/last_bank_result.json').read_text(encoding='utf-8'))
        assert identity['user_id']==user['id'] and identity['readback_verified']
        assert bank['user_id']==user['id'] and bank['readback_verified']
        checks.check('本批次用户无借款主记录',lambda: verify_no_loans(checks,user),timeout=0)
        record.update(status='complete',identity_date=identity['date_segment'],bank_last4=bank['card_last4'],no_loans=True)
        write_json(batch_path,records)
        print('[批次完成] '+json.dumps(record,ensure_ascii=False),flush=True)
print('\n全部完成：5个用户，身份证及银行卡校验通过，无借款。',flush=True)
