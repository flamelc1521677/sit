"""Explicit UUID repeat loan from verified legacy history; single authorized request."""
import json,os,re
from decimal import Decimal
import requests
from common.admin_client import ADMIN_URL,authenticate_admin
from common.identity_client import checked_json,lookup_user_by_phone
from common.loan_client import LoanAttempt,application_inputs,repeat_loan_history
from common.approval_credentials import approval_credentials
from common.approval_client import ApprovalAttempt,verify_detail,verify_reviewer_context
from common.payout_client import PayoutAttempt,build_payout_payload
from common.phone_sequence import locked
from common.sso_login import TIMEOUT
from common.state_store import ROOT,write_json
UUID='cad4039ac8d842cc8a9c2861e3ddd891'
KEY='mxOffline01:'+UUID
REQUEST='user875-repeat2000-unpaid-20261106-01'
AUDIT=ROOT/'.state'/f'{REQUEST}.json'
def serial(v):return json.loads(json.dumps(v,default=str))
def save(r):write_json(AUDIT,serial(r))
def read():return json.loads(AUDIT.read_text(encoding='utf-8'))
def authorize():assert os.getenv('MX_REPEAT_UUID_REQUEST')==REQUEST,'Explicit request required'
def login(s):authenticate_admin(s)

def test_01_trial_submit(session,database_checks):
 authorize();c=database_checks
 assert not AUDIT.exists(),'Already attempted; inspect persisted state'
 reviewer,password=approval_credentials(os.environ['SSO_USERNAME'])
 # Validate the actual independent reviewer account before any loan submission.
 with requests.Session() as independent:
  authenticate_admin(independent,username=reviewer,password=password);c.login_account(reviewer)
 login(session);c.login_account(os.environ['SSO_USERNAME'])
 u=lookup_user_by_phone(session,'4473455062','mxOffline01')
 assert u['uuid']==UUID and str(u['id'])=='875'
 c.prealert_user(u,'4473455062')
 def verify_identity():
  rows=c.database.select(f'SELECT ID,UUID,APP_OWNERSHIP,PHONE FROM `{c.biz}`.t_mgt_user WHERE UUID=%s AND APP_OWNERSHIP=%s',(UUID,u['appOwnership']))
  assert len(rows)==1 and rows[0]['ID']==875 and rows[0]['PHONE']==u['phoneEncrypt']
 c.check('Explicit UUID, application and full phone ciphertext match',verify_identity,timeout=0)
 with locked(ROOT/'.state/loan_operation.lock'):
  path=ROOT/'.state/loan_attempts.json';attempts=json.loads(path.read_text(encoding='utf-8'));assert KEY not in attempts,'Existing local attempt must be reconciled'
  before=c.loan_headers(u)
  headers=c.database.select(f'SELECT * FROM `{c.loan}`.bor_pre_repayment_plan_header WHERE UUID=%s AND OWNERSHIP=%s ORDER BY CREATE_TIME,ID',(UUID,u['appOwnership']))
  assert len(headers)==len(before)==2
  legacy=[];snapshot=[]
  for h in headers:
   plans=c.database.select(f'SELECT * FROM `{c.loan}`.bor_pre_repayment_plan WHERE HEADER_ID=%s ORDER BY PERIOD_NO',(h['ID'],))
   cards=c.database.select(f'SELECT * FROM `{c.loan}`.bor_pre_repayment_payout_card WHERE HEADER_ID=%s',(h['ID'],))
   assert int(h['ENTRY_STATUS'])==0 and h['VIRTUAL_ORDER_ID'] and len(plans)==int(h['TOTAL_PERIOD'])
   assert [int(p['PERIOD_NO']) for p in plans]==list(range(1,len(plans)+1))
   assert sum(Decimal(str(p['REPAY_PRINCIPAL'])) for p in plans)==Decimal(str(h['CONTRACT_AMOUNT']))
   assert cards and all(r['UUID']==UUID and r['OWNERSHIP']==u['appOwnership'] for r in cards)
   legacy.append({'status':'db_verified','header_id':h['ID'],'user_id':875,'uuid':UUID,'appOwnership':u['appOwnership'],'source':'read_only_import_of_existing_submitted_header_and_plan','loanAmount':str(h['CONTRACT_AMOUNT'])})
   snapshot.append({'header':h,'plans':plans,'cards':cards})
  previous={**legacy[-1],'previous_loans':legacy[:-1]}
  history=repeat_loan_history(previous,before,REQUEST,'875',u['id'])
  r={'status':'history_verified','uuid':UUID,'user_id':875,'request_id':REQUEST,'legacy_snapshot':snapshot,'historical_count':len(history)};save(r)
  checked_json(session.get(ADMIN_URL+'/user/user/offline/startLoan/precheck',params={'id':875,'appOwnership':u['appOwnership']},timeout=TIMEOUT),'Repeat eligibility')
  html=session.get(ADMIN_URL+'/user/user/offline/startLoan/875',params={'appOwnership':u['appOwnership']},timeout=TIMEOUT);assert html.status_code==200
  config=json.loads((ROOT/'config/loan_application.json').read_text(encoding='utf-8'));config.update(loanAmount='2000',installmentCount='3')
  inputs=application_inputs(html.text,u,config)
  products=checked_json(session.get(ADMIN_URL+'/user/user/offline/startLoan/products',timeout=TIMEOUT),'Products')['data'];assert any(str(p['productId'])==inputs['productId'] for p in products)
  c.loan_profile(u,inputs['payoutBankCardIds']);attempt=LoanAttempt(session,inputs);attempt.trial();c.trial_did_not_submit(u,before)
  r.update(status='trial_verified',submission=attempt.submission,plan=attempt.plan);save(r)
  print('[Trial] 2000 MXN, 3 periods, in-hand '+attempt.submission['inHandAmount'],flush=True)
  attempts[KEY]={'status':'request_sent','user_id':875,'uuid':UUID,'appOwnership':u['appOwnership'],'phone':'4473455062','repeat_request_id':REQUEST,'previous_loans':history,'productId':inputs['productId'],'loanAmount':'2000','submitted_by':os.environ['SSO_USERNAME']}
  write_json(path,attempts);r['status']='loan_request_sent';save(r)
  result=attempt.submit();attempts[KEY].update(status='api_success',code=result.get('code'));write_json(path,attempts)
  hid=c.submitted_loan(u,before,attempt.submission,attempt.plan)
  attempts[KEY].update(status='db_verified',header_id=hid);write_json(path,attempts)
  assert {x['ID'] for x in c.loan_headers(u)}=={x['ID'] for x in before}|{hid}
  r.update(status='loan_db_verified',header_id=hid);save(r)
  print('[Loan verified] '+hid,flush=True)

def test_02_independent_approval(session,database_checks):
 authorize();c=database_checks;r=read();assert r['status']=='loan_db_verified'
 def pending():
  rows=[c.approval_record(x['id']) for x in c.approval_candidates(r['header_id'])];assert len(rows)==1 and str(rows[0]['status'])=='0';return rows[0]['id']
 aid=c.check('New loan unique pending approval',pending,timeout=60)
 assigned=c.pending_approval(aid)
 reviewer,password=approval_credentials(os.environ['SSO_USERNAME'],assigned);authenticate_admin(session,username=reviewer,password=password);actor=c.login_account(reviewer)
 with locked(ROOT/'.state/approval_operation.lock'):
  before=c.pending_approval(aid);before['submitted_by']=os.environ['SSO_USERNAME'];verify_reviewer_context(before,actor['USER_ID'],reviewer)
  response=session.get(ADMIN_URL+'/borrower/payoutApproval/detail/'+aid,timeout=TIMEOUT);assert response.status_code==200;verify_detail(response.text,aid)
  config=json.loads((ROOT/'config/approval_actions.json').read_text(encoding='utf-8'));reason=config['reasons']['pass'];amount='2000'
  old={x['id'] for x in c.approval_logs(aid)};fresh=c.approval_record(aid);assert fresh['version']==before['version'] and str(fresh['status'])=='0'
  path=ROOT/'.state/approval_attempts.json';a=json.loads(path.read_text(encoding='utf-8'));assert aid not in a
  a[aid]={'status':'request_sent','action':'pass','header_id':r['header_id'],'attempted_by':reviewer,'expectedAmount':amount,'approval_version':before['version']};write_json(path,a)
  r.update(status='approval_request_sent',approval_id=aid);save(r)
  result=ApprovalAttempt(session,before,'pass',reason,amount).execute(fresh);a[aid].update(status='api_success',code=result.get('code'));write_json(path,a)
  c.approval_result(before,'pass',reason,amount,old,actor['USER_ID']);a[aid]['status']='db_verified';write_json(path,a)
  r.update(status='approval_db_verified',reviewer=reviewer);save(r)

def test_03_verify_unpaid(session,database_checks):
 authorize();c=database_checks;r=read();assert r['status']=='approval_db_verified'
 from common.approved_unpaid import assert_unpaid_snapshot
 h,co,bill=c.payout_context(r['header_id'])
 a=c.approval_record(r['approval_id']);assert str(a['status'])=='1'
 reviewer,_=approval_credentials(os.environ['SSO_USERNAME'],a);actor=c.login_account(reviewer)
 assert str(a['auditor_id'])==str(actor['USER_ID']) and a['audit_time']
 def verify():
  b=c.payout_bill(bill['ID'])
  contracts=c.database.select(f'SELECT CONTRACT_STATUS,PAYMENT_DATE FROM `{c.loan}`.bor_contract WHERE CONTRACT_ID=%s',(co['CONTRACT_ID'],))
  flows=c.database.select(f'SELECT ID FROM `{c.payout}`.pay_trade_bill_flow WHERE BILL_ID=%s AND STATE=1',(bill['ID'],))
  assert_unpaid_snapshot(b,contracts,flows)
  return b
 b=c.check('Approval passed; payout not successful; no payment date or successful payout flow',verify,timeout=0)
 r.update(status='approved_unpaid_db_verified',contract_id=co['CONTRACT_ID'],payout_id=b['ID'],payout_state=str(b['STATE']),reviewer=reviewer);save(r)
 print('[Approved unpaid] '+co['CONTRACT_ID']+'; payout state '+str(b['STATE']),flush=True)


def test_01b_reconcile_saved_loan(database_checks):
 authorize();c=database_checks;r=read();assert r['status']=='loan_request_sent'
 path=ROOT/'.state/loan_attempts.json';a=json.loads(path.read_text(encoding='utf-8'));assert a[KEY]['status']=='api_success'
 u={'uuid':UUID,'appOwnership':'mxOffline01','id':875}
 before=[{'ID':x['header']['ID']} for x in r['legacy_snapshot']]
 ids={x['ID'] for x in c.loan_headers(u)}-{x['ID'] for x in before};assert len(ids)==1
 hid=next(iter(ids));h=c.database.select(f'SELECT SKIP_USER_CONFIRM FROM `{c.loan}`.bor_pre_repayment_plan_header WHERE ID=%s',(hid,))[0]
 assert h['SKIP_USER_CONFIRM']==0
 actual=dict(r['submission']);actual['skipUserConfirm']='false'
 assert c.submitted_loan(u,before,actual,r['plan'])==hid
 r.update(status='loan_db_verified',header_id=hid,server_mode_difference={'requested_skip_user_confirm':True,'saved_skip_user_confirm':False,'original_submission_preserved':True})
 a[KEY].update(status='db_verified',header_id=hid,server_mode_difference=r['server_mode_difference']);write_json(path,a);save(r)
