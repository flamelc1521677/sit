import json,pickle,runpy
from datetime import date
from common.admin_client import ADMIN_URL
from common.state_store import ROOT,write_json
from common.database import connect_database
from common.database_checks import DatabaseChecks
from common.phone_sequence import locked
from common.prealert_client import read_job,select_period,PrealertAttempt,get_plans,target_date
helpers=runpy.run_path(str(ROOT/'tests/11_prealert/test_generate_prealert.py'))
config=json.loads((ROOT/'config/prealert.json').read_text(encoding='utf-8'))
s=pickle.loads((ROOT/'artifacts/prealert_session.pkl').read_bytes())
key='C1320261021150342BSR8l:1:2026-10-23:2'
with locked(ROOT/'.state/prealert_operation.lock'),connect_database() as db:
 checks=DatabaseChecks(db)
 path=ROOT/'.state/prealert_attempts.json'
 attempts=json.loads(path.read_text(encoding='utf-8'))
 record=attempts[key]
 assert record['status']=='plan_api_success', '不是可核验的成功保存状态，禁止继续或重发'
 original=json.loads((ROOT/'artifacts/prealert_plans.json').read_text(encoding='utf-8'))['rows']
 assert all(r['contractId']==record['contract_id'] for r in original)
 assert select_period(original)['repayTimeStr']==record['original_due_date']
 due=date.fromisoformat(record['due_date'])
 user=helpers['lookup_user'](s,record['phone'])
 checks.prealert_user(user,record['phone'])
 contract=checks.prealert_contract(user,record['contract_id'])
 before=checks.recover_saved_date_from_api_snapshot(original,record['period'],due)
 current=get_plans(s,record['contract_id'])
 checks.repayment_matches_api(record['contract_id'],current)
 job=read_job(s,config);checks.prealert_job(job)
 assert int(job['params'])==record['days']
 clock,_=helpers['clock_page'](s,config)
 assert target_date(clock,record['days'])==due
 old=checks.prealert_records(record['contract_id'])
 assert not any(r['PERIOD']==record['period'] for r in old)
 grade=checks.prealert_grade(record['days'],config['countryCode'])
 old_logs=checks.prealert_log_ids(job['id'])
 record.update(status='date_db_verified',original_plans=original,recovery_source='pre-save API snapshot')
 write_json(path,attempts)
 assert checks.repayment_snapshot(record['contract_id'])==before
 assert read_job(s,config)==job
 record['status']='job_run_request_sent';write_json(path,attempts)
 print('[恢复] 已核验应还日期 2026-10-23；不重复保存，仅执行一次预提醒任务',flush=True)
 result=PrealertAttempt(s).run(job['id'])
 record.update(status='job_run_api_success',job_msg=result.get('msg'));write_json(path,attempts)
 log=checks.prealert_job_finished(job,old_logs)
 record.update(status='job_log_verified',job_log_id=log['ID']);write_json(path,attempts)
 reminder=checks.generated_prealert(contract,select_period(original),due,record['days'],grade,{r['ID'] for r in old})
 record['reminder_id']=reminder['ID']
 record['api_list_visible']=helpers['list_visibility'](s,record['contract_id'],record['period'],reminder['ID'])
 record['status']='db_verified';write_json(path,attempts)
 print('[完成] '+json.dumps({k:v for k,v in record.items() if k!='original_plans'},ensure_ascii=False),flush=True)
