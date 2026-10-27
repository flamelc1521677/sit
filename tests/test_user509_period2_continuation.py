"""Explicit user509 period2 continuation; never save full repayment plan."""
import json, os, importlib.util
from datetime import date
from decimal import Decimal
from common.admin_client import authenticate_admin
from common.identity_client import lookup_user_by_phone
from common.prealert_client import read_job,get_plans,PrealertAttempt,target_date
from common.state_store import ROOT,write_json as _write_json
def write_json(path,value): _write_json(path,json.loads(json.dumps(value,default=str)))
from common.phone_sequence import locked
from common.database_checks import DatabaseChecks

def load(relative,name):
 spec=importlib.util.spec_from_file_location(name,ROOT/relative);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_continue_period2(session,database_checks,monkeypatch):
 c=database_checks;cid='C13202610261814546SDs6'
 authenticate_admin(session);c.login_account(os.environ['SSO_USERNAME'])
 u=lookup_user_by_phone(session,'8224922978');c.prealert_user(u,'8224922978')
 contract=c.prealert_contract(u,cid)
 before=c.repayment_snapshot(cid);first=next(r for r in before if r['PERIOD']==1)
 assert str(first['REPAY_STATUS'])=='2' and first['FINAL_REPAYMENT_DATE']
 assert next(r for r in before if r['PERIOD']==2)['REPAY_STATUS']=='1'
 audit=ROOT/'.state/user509_period2_continuation.json'
 if audit.exists():
  record=json.loads(audit.read_text(encoding='utf-8'));assert record['status']=='prealert_verified'
  assert record['first_period_preserved']==json.loads(json.dumps(first,default=str))
 else:
  record={'status':'prepared','contract_id':cid,'period':2,'first_period_preserved':first,'original_plans':before}
  write_json(audit,record)
 def save(s):record['status']=s;write_json(audit,record)
 def preserve_first():
  assert next(r for r in c.repayment_snapshot(cid) if r['PERIOD']==1)==first,'First period changed'
 if record['status']!='prealert_verified':
  pre=load('tests/11_prealert/test_generate_prealert.py','pre_recovery')
  config=json.loads((ROOT/'config/prealert.json').read_text(encoding='utf-8'))
  with locked(ROOT/'.state/prealert_operation.lock'):
   path=ROOT/'.state/prealert_attempts.json';attempts=json.loads(path.read_text(encoding='utf-8'))
   matches=[(k,v) for k,v in attempts.items() if v.get('contract_id')==cid and v.get('period')==2]
   assert len(matches)==1
   key,old=matches[0];assert old['status']=='plan_api_success'
   clock,_=pre.clock_page(session,config);job=read_job(session,config);c.prealert_job(job)
   days=int(job['params']);due=target_date(clock,days)
   assert due==date.fromisoformat(old['due_date'])
   plans=get_plans(session,cid);c.repayment_matches_api(cid,plans)
   selected=next(r for r in plans if int(r['period'])==2)
   assert selected['repayTimeStr']==due.isoformat()
   # Reconcile known previous save without repeating it. Retain the original incident.
   old['recovery']={'user_instruction':'Preserve current period1 timestamps; continue without full-plan API','prior_status':old['status'],'preserved_period1':first,'observed_plans':before}
   existing=c.prealert_records(cid);assert not any(r['PERIOD']==2 for r in existing)
   grade=c.prealert_grade(days,config['countryCode']);logs=c.prealert_log_ids(job['id'])
   assert read_job(session,config)==job
   old['status']='job_run_request_sent';write_json(path,attempts);save('prealert_job_request_sent')
   result=PrealertAttempt(session).run(job['id'])
   old['status']='job_run_api_success';write_json(path,attempts)
   log=c.prealert_job_finished(job,logs)
   reminder=c.generated_prealert(contract,selected,due,days,grade,{r['ID'] for r in existing})
   preserve_first()
   old.update(status='db_verified',reminder_id=reminder['ID'],job_log_id=log['ID'],recovered_with_acknowledged_prior_timestamp_change=True)
   write_json(path,attempts);record['prealert_id']=reminder['ID'];save('prealert_verified')
 else:
  reminder={'ID':record['prealert_id']}
 assign=load('tests/12_prealert_assignment/test_assign_prealert.py','assign_period2')
 monkeypatch.setenv('PREALERT_ORDER_ID',reminder['ID']);assign.test_assign_prealert_to_ob4(session,c);preserve_first();save('assignment_verified')
 # Closed, fully repaid historical first-period bills must not block new period2 entry.
 original=DatabaseChecks.overdue_bills
 def current_bills(self,contract_id):
  bills=original(self,contract_id);result=[]
  for b in bills:
   if str(b['REMIND_STATUS'])=='2':
    assert contract_id==cid and str(b['PERIOD_LIST'])=='1' and int(b['CURRENT_PERIOD'])==1
    assert next(r for r in self.repayment_snapshot(cid) if r['PERIOD']==1)==first
   else:result.append(b)
  return result
 monkeypatch.setattr(DatabaseChecks,'overdue_bills',current_bills)
 overdue=load('tests/13_overdue/test_generate_overdue.py','overdue_period2')
 for k,v in {'OVERDUE_ORDER_ID':reminder['ID'],'OVERDUE_CONTRACT_ID':cid,'OVERDUE_PERIOD':'2','OVERDUE_DAYS':'10','OVERDUE_PENALTY_DAYS':'1','OVERDUE_MODE':'create','OVERDUE_REQUEST_ID':'user509-period2-days10-20261027-01','OVERDUE_DRY_RUN':'1'}.items():monkeypatch.setenv(k,v)
 overdue.test_generate_overdue_and_penalty(session,c);preserve_first();save('overdue_preflight_verified')
 monkeypatch.setenv('OVERDUE_DRY_RUN','0');save('overdue_execution_started')
 overdue.test_generate_overdue_and_penalty(session,c);preserve_first();save('db_verified')
