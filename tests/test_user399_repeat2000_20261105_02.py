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
UUID='530093cb84124cdbb2552f4f5f173d36'
KEY='spicaCreditos:'+UUID
REQUEST='user399-repeat2000-3periods-20261105-02'
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
 u=lookup_user_by_phone(session,'7804609515','spicaCreditos')
 assert u['uuid']==UUID and str(u['id'])=='399'
 c.prealert_user(u,'7804609515')
 def verify_identity():
  rows=c.database.select(f'SELECT ID,UUID,APP_OWNERSHIP,PHONE FROM `{c.biz}`.t_mgt_user WHERE UUID=%s AND APP_OWNERSHIP=%s',(UUID,u['appOwnership']))
  assert len(rows)==1 and rows[0]['ID']==399 and rows[0]['PHONE']==u['phoneEncrypt']
 c.check('Explicit UUID, application and full phone ciphertext match',verify_identity,timeout=0)
 with locked(ROOT/'.state/loan_operation.lock'):
  path=ROOT/'.state/loan_attempts.json';attempts=json.loads(path.read_text(encoding='utf-8'));assert attempts.get(KEY,{}).get('status')=='db_verified','Previous attempt must be verified'
  before=c.loan_headers(u)
  headers=c.database.select(f'SELECT * FROM `{c.loan}`.bor_pre_repayment_plan_header WHERE UUID=%s AND OWNERSHIP=%s ORDER BY CREATE_TIME,ID',(UUID,u['appOwnership']))
  assert len(headers)==len(before) and len(headers)>=17
  legacy=[];snapshot=[]
  for h in headers:
   plans=c.database.select(f'SELECT * FROM `{c.loan}`.bor_pre_repayment_plan WHERE HEADER_ID=%s ORDER BY PERIOD_NO',(h['ID'],))
   cards=c.database.select(f'SELECT * FROM `{c.loan}`.bor_pre_repayment_payout_card WHERE HEADER_ID=%s',(h['ID'],))
   assert int(h['ENTRY_STATUS'])==0 and h['VIRTUAL_ORDER_ID'] and len(plans)==int(h['TOTAL_PERIOD'])
   assert [int(p['PERIOD_NO']) for p in plans]==list(range(1,len(plans)+1))
   assert sum(Decimal(str(p['REPAY_PRINCIPAL'])) for p in plans)==Decimal(str(h['CONTRACT_AMOUNT']))
   assert cards and all(r['UUID']==UUID and r['OWNERSHIP']==u['appOwnership'] for r in cards)
   legacy.append({'status':'db_verified','header_id':h['ID'],'user_id':399,'uuid':UUID,'appOwnership':u['appOwnership'],'source':'read_only_import_of_existing_submitted_header_and_plan','loanAmount':str(h['CONTRACT_AMOUNT'])})
   snapshot.append({'header':h,'plans':plans,'cards':cards})
  previous=attempts[KEY]
  known={previous['header_id']}|{x['header_id'] for x in previous.get('previous_loans',[])}
  assert known <= {h['ID'] for h in headers},'Known historical loan missing'
  imported=[]
  for h in headers:
   if h['ID'] in known:continue
   approvals=[c.approval_record(x['id']) for x in c.approval_candidates(h['ID'])]
   assert len(approvals)==1 and str(approvals[0]['status'])=='1'
   contracts=c.database.select(f'SELECT CONTRACT_ID,APPLY_ID,PAYMENT_DATE FROM `{c.loan}`.bor_contract WHERE VIRTUAL_ORDER_ID=%s AND SALE_UUID=%s AND OWNERSHIP=%s',(h['VIRTUAL_ORDER_ID'],UUID,u['appOwnership']))
   assert len(contracts)==1 and contracts[0]['PAYMENT_DATE']
   co=contracts[0]
   bills=c.database.select(f'SELECT ID,STATE,TRADE_AMOUNT FROM `{c.payout}`.pay_trade_bill_moco WHERE BID_ID=%s AND CONTRACT_ID=%s AND SYSTEM_SOURCE_ID=%s',(co['CONTRACT_ID'],co['APPLY_ID'],'BORROWER'))
   assert len(bills)==1 and str(bills[0]['STATE'])=='1' and Decimal(str(bills[0]['TRADE_AMOUNT']))==Decimal(str(h['IN_HAND_AMOUNT']))
   imported.append({'status':'db_verified','header_id':h['ID'],'user_id':399,'uuid':UUID,'appOwnership':u['appOwnership'],'source':'read_only_reconciliation_external_successful_loan','contract_id':co['CONTRACT_ID'],'payout_id':bills[0]['ID'],'loanAmount':str(h['CONTRACT_AMOUNT'])})
  if imported:
   original_previous=previous
   previous={**imported[-1],'previous_loans':previous.get('previous_loans',[])+[{k:v for k,v in previous.items() if k!='previous_loans'}]+imported[:-1]}
  history=repeat_loan_history(previous,before,REQUEST,'399',u['id'])
  r={'status':'history_verified','uuid':UUID,'user_id':399,'request_id':REQUEST,'legacy_snapshot':snapshot,'historical_count':len(history)};save(r)
  checked_json(session.get(ADMIN_URL+'/user/user/offline/startLoan/precheck',params={'id':399,'appOwnership':u['appOwnership']},timeout=TIMEOUT),'Repeat eligibility')
  html=session.get(ADMIN_URL+'/user/user/offline/startLoan/399',params={'appOwnership':u['appOwnership']},timeout=TIMEOUT);assert html.status_code==200
  config=json.loads((ROOT/'config/loan_application.json').read_text(encoding='utf-8'));config.update(loanAmount='2000',installmentCount='3')
  inputs=application_inputs(html.text,u,config)
  products=checked_json(session.get(ADMIN_URL+'/user/user/offline/startLoan/products',timeout=TIMEOUT),'Products')['data'];assert any(str(p['productId'])==inputs['productId'] for p in products)
  c.loan_profile(u,inputs['payoutBankCardIds']);attempt=LoanAttempt(session,inputs);attempt.trial();c.trial_did_not_submit(u,before)
  r.update(status='trial_verified',submission=attempt.submission,plan=attempt.plan);save(r)
  print('[Trial] 2000 MXN, 3 periods, in-hand '+attempt.submission['inHandAmount'],flush=True)
  attempts[KEY]={'status':'request_sent','user_id':399,'phone':'7804609515','uuid':UUID,'appOwnership':u['appOwnership'],'repeat_request_id':REQUEST,'previous_loans':history,'productId':inputs['productId'],'loanAmount':'2000','submitted_by':os.environ['SSO_USERNAME']}
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

def test_03_payout_success(session,database_checks):
 authorize();c=database_checks;r=read();assert r['status']=='approval_db_verified'
 login(session);c.login_account(os.environ['SSO_USERNAME'])
 with locked(ROOT/'.state/payout_operation.lock'):
  h,contract,before=c.payout_context(r['header_id']);assert h['UUID']==UUID and h['OWNERSHIP']=='spicaCreditos'
  assert Decimal(str(h['IN_HAND_AMOUNT']))==Decimal(r['submission']['inHandAmount'])
  assert str(before['STATE']) in {'2','4','6'},'Already successful or unexpected payout; read-only reconciliation required'
  response=session.get(ADMIN_URL+'/pay/borTransferBillMoco/edit/'+before['ID'],timeout=TIMEOUT);assert response.status_code==200
  match=re.search(r'var\s+currentDateTime\s*=\s*"([^"]+)"',response.text);assert match
  payload=build_payout_payload(response.text,before,match.group(1));old=c.payout_flow_ids(before['ID']);fresh=c.payout_bill(before['ID'])
  path=ROOT/'.state/payout_attempts.json';a=json.loads(path.read_text(encoding='utf-8'));assert before['ID'] not in a
  a[before['ID']]={'status':'request_sent','uuid':UUID,'header_id':h['ID'],'contract_id':before['BID_ID'],'borrow_id':before['CONTRACT_ID'],'biz_id':before['BIZ_ID']};write_json(path,a)
  r.update(status='payout_request_sent',contract_id=contract['CONTRACT_ID'],payout_id=before['ID']);save(r)
  result=PayoutAttempt(session,before,payload).execute(fresh);a[before['ID']].update(status='api_success',code=result.get('code'));write_json(path,a)
  c.payout_success(before,payload,old,os.environ['SSO_USERNAME']);a[before['ID']]['status']='db_verified';write_json(path,a)
  plans=c.repayment_snapshot(contract['CONTRACT_ID']);assert len(plans)==3 and sum(Decimal(str(p['MONTH_CAPITAL_ORIGIN'])) for p in plans)==Decimal('2000')
  r.update(status='db_verified',final_plans=plans,in_hand_amount=str(h['IN_HAND_AMOUNT']));save(r)
  print('[Complete] '+contract['CONTRACT_ID']+'; principal 2000 MXN; 3 periods; payout '+str(before['TRADE_AMOUNT']),flush=True)
