"""指定手机号的已放款合同 -> 服务端日期改期 -> 执行预提醒任务 -> SQL/接口回查。"""
import json
import os
import re

from common.admin_client import ADMIN_URL, authenticate_admin
from common.identity_client import checked_json, lookup_user_by_phone
from common.phone_sequence import locked
from common.prealert_client import (PrealertAttempt, date_payload, get_plans, job_edit_payload,
    read_job, reminder_days, select_period, server_business_clock, target_date)
from common.sso_login import TIMEOUT
from common.state_store import ROOT, write_json


def lookup_user(session, phone):
    return lookup_user_by_phone(session, phone, os.getenv("PREALERT_APP_OWNERSHIP"))


def clock_page(session, config):
    response = session.get(ADMIN_URL + "/system/job/edit/" + config["jobId"], timeout=TIMEOUT)
    return server_business_clock(response, config["serverBusinessTimezone"]), response.text


def list_visibility(session, contract_id, period, reminder_id):
    body = checked_json(session.post(ADMIN_URL + "/loan/preReminder/list", data={
        "contractId": contract_id, "pageNum": 1, "pageSize": 100}, timeout=TIMEOUT), "预提醒列表回查")
    matches = [r for r in body.get("rows", []) if r.get("id") == reminder_id
               and r.get("contractId") == contract_id and str(r.get("period")) == str(period)]
    assert len(matches) <= 1, "预提醒列表目标记录重复"
    if matches:
        print("[预提醒] 列表回查已找到本次合同及期次", flush=True)
    else:
        print("[预提醒] 当前账号列表未返回目标记录；数据库结果单独核验，不宣称列表可见", flush=True)
    return bool(matches)


def test_generate_prealert_for_phone(session, database_checks):
    phone = os.getenv("PREALERT_PHONE", "").strip()
    assert re.fullmatch(r"\d{10}", phone), "必须显式指定 10 位 PREALERT_PHONE，不默认选择最新用户"
    config = json.loads((ROOT / "config/prealert.json").read_text(encoding="utf-8"))
    requested_days = os.getenv("PREALERT_DAYS", "").strip()
    if requested_days:
        reminder_days(requested_days)
    authenticate_admin(session)
    with locked(ROOT / ".state/prealert_operation.lock"):
        user = lookup_user(session, phone)
        database_checks.prealert_user(user, phone)
        contract = database_checks.prealert_contract(user, os.getenv("PREALERT_CONTRACT_ID") or None)
        contract_id = contract["CONTRACT_ID"]
        job = read_job(session, config)
        database_checks.prealert_job(job)
        days = reminder_days(requested_days if requested_days else job["params"])
        grade = database_checks.prealert_grade(days, config["countryCode"])
        clock, edit_html = clock_page(session, config)
        due_date = target_date(clock, days)
        plans = get_plans(session, contract_id)
        selected = select_period(plans, os.getenv("PREALERT_PERIOD"))
        period = int(selected["period"])
        before = database_checks.repayment_matches_api(contract_id, plans)
        assert selected["repayTimeStr"] >= clock.date().isoformat(), "目标期次已逾期，不适用预提醒生成场景"
        payload = date_payload(plans, period, due_date)
        original_reminders = database_checks.prealert_records(contract_id)
        state_path = ROOT / ".state/prealert_attempts.json"
        attempts = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        key = f"{contract_id}:{period}:{due_date}:{days}"
        previous = attempts.get(key)
        if previous:
            assert previous["status"] == "db_verified", "本次预提醒已有未确认请求记录，停止；禁止自动重发保存或任务"
            matches = [r for r in original_reminders if r["ID"] == previous["reminder_id"]
                       and r["PERIOD"] == period and r["REPAYMENT_DATE"].date() == due_date
                       and int(r["PRE_DAYS"]) == days and r["PRE_GRADE"] == grade["PRE_GRADE_CODE"]]
            assert len(matches) == 1 and str(matches[0]["IS_FINAL_STATE"]) == "0", "历史成功预提醒状态发生变化，请核对"
            print(f"[预提醒] 已存在本次成功记录 {previous['reminder_id']}，仅回查，不重复改期或触发任务", flush=True)
            list_visibility(session, contract_id, period, previous["reminder_id"])
            return
        assert not any(r["PERIOD"] == period for r in original_reminders), "所选期次已有预提醒数据，避免重复或覆盖其他账龄，请先核对"
        assert not any(v.get("contract_id") == contract_id and v.get("period") == period and v.get("status") != "db_verified" for v in attempts.values()), "该合同期次有未确认操作，禁止换参数绕过重试保护"
        record = {"status": "prepared", "phone": phone, "user_id": user["id"], "contract_id": contract_id,
            "period": period, "days": days, "age": "T0" if days == 0 else f"T-{days}",
            "server_business_time": clock.isoformat(), "due_date": due_date.isoformat(),
            "original_due_date": selected["repayTimeStr"], "job_id": job["id"], "original_job_params": job["params"]}
        record["original_plans"] = plans
        attempts[key] = record

        def save_status(status):
            record["status"] = status
            write_json(state_path, attempts)

        save_status("prepared")
        attempt = PrealertAttempt(session)
        print(f"[预提醒 1/5] 用户 {user['id']} / {phone}，合同 {contract_id}，第 {period} 期", flush=True)
        print(f"[预提醒] 服务端业务时间 {clock.isoformat()}，任务参数 {days} -> {record['age']}，应还日期 {due_date}", flush=True)
        if str(job["params"]) != str(days):
            fields = job_edit_payload(edit_html, job, days)
            assert read_job(session, config) == job, "任务被其他人修改，停止覆盖"
            save_status("job_edit_request_sent")
            attempt.edit_job(fields)
            original_job = job
            job = read_job(session, config)
            assert str(job["params"]) == str(days), "任务参数修改未生效"
            for field in ("id", "beanName", "methodName", "cronExpression", "dbGroupId", "targetGroup", "jobStatus"):
                assert job[field] == original_job[field], f"修改参数意外改变任务 {field}"
            database_checks.prealert_job(job)
            save_status("job_params_verified")
        # Re-read before every mutation; do not submit a stale full repayment-plan snapshot.
        fresh_clock, _ = clock_page(session, config)
        assert fresh_clock.date() == clock.date(), "服务端日期跨日，请重新核对，停止改期"
        assert get_plans(session, contract_id) == plans, "还款计划被其他人修改，停止覆盖"
        assert read_job(session, config) == job, "任务参数在改期前变化，停止操作"
        print("[预提醒 2/5] 保存所选期次应还日期，仅发送一次", flush=True)
        save_status("plan_request_sent")
        saved = attempt.save_plan(payload)
        record["plan_msg"] = saved.get("msg")
        save_status("plan_api_success")
        saved_rows = database_checks.repayment_date_saved(before, period, due_date)
        reread = get_plans(session, contract_id)
        database_checks.repayment_matches_api(contract_id, reread)
        save_status("date_db_verified")
        # A shared job can be changed by other operators after the date save.
        assert read_job(session, config) == job, "任务参数在执行前变化，停止执行；请先核对已保存的日期"
        fresh_clock, _ = clock_page(session, config)
        assert fresh_clock.date() == clock.date(), "任务执行前服务端已跨日，停止执行"
        database_checks.prealert_job(job)
        assert database_checks.repayment_snapshot(contract_id) == saved_rows, "执行前还款计划发生变化"
        old_log_ids = database_checks.prealert_log_ids(job["id"])
        print(f"[预提醒 3/5] 执行一次共享任务，params={days}；任务也会处理当前国家其他符合条件订单", flush=True)
        save_status("job_run_request_sent")
        result = attempt.run(job["id"])
        record["job_msg"] = result.get("msg")
        save_status("job_run_api_success")
        log = database_checks.prealert_job_finished(job, old_log_ids)
        record["job_log_id"] = log["ID"]
        save_status("job_log_verified")
        print("[预提醒 4/5] 任务成功，核验本次新增目标预提醒", flush=True)
        reminder = database_checks.generated_prealert(contract, selected, due_date, days, grade, {r["ID"] for r in original_reminders})
        record["reminder_id"] = reminder["ID"]
        record["api_list_visible"] = list_visibility(session, contract_id, period, reminder["ID"])
        save_status("db_verified")
        print(f"[预提醒 5/5] 已生成 {record['age']} 预提醒 {reminder['ID']}，合同/期次/日期/金额及任务日志全部通过", flush=True)
