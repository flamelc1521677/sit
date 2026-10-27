"""Explicit assigned pre-reminder -> bounded date fixture -> entry job -> penalty job."""
import json
import os
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from common.admin_client import ADMIN_URL, authenticate_admin
from common.identity_client import checked_json
from common.overdue_client import logical_age, positive_count, storage_datetime, penalty_task_count
from common.overdue_fixture import connect_overdue_fixture, snapshot_unchanged
from common.phone_sequence import locked
from common.prealert_client import get_plans, server_business_clock
from common.scheduled_jobs import JobRunAttempt, read_scheduled_job
from common.sso_login import TIMEOUT
from common.state_store import ROOT, write_json


def read_clock(session, config):
    response = session.get(ADMIN_URL + "/system/job/edit/" + config["entryJob"]["jobId"], timeout=TIMEOUT)
    return server_business_clock(response, config["serverBusinessTimezone"])


def verify_penalty_api(session, checks, plan):
    plans = get_plans(session, plan["CONTRACT_ID"])
    checks.repayment_matches_api(plan["CONTRACT_ID"], plans)
    selected = [p for p in plans if p["id"] == plan["ID"]]
    assert len(selected) == 1
    for api, column in (("lateChargeOrigin", "LATE_CHARGE_ORIGIN"), ("lateChargeActual", "LATE_CHARGE_ACTUAL")):
        assert Decimal(str(selected[0].get(api) or 0)) == Decimal(str(plan[column] or 0)), "合同还款计划罚息接口与数据库不一致"


def test_generate_overdue_and_penalty(session, database_checks):
    order_id = os.getenv("OVERDUE_ORDER_ID", "").strip() or None
    contract_id = os.getenv("OVERDUE_CONTRACT_ID", "").strip() or None
    period = os.getenv("OVERDUE_PERIOD", "").strip() or None
    mode = os.getenv("OVERDUE_MODE", "create").strip()
    dry_run = os.getenv("OVERDUE_DRY_RUN", "0").strip()
    request_id = os.getenv("OVERDUE_REQUEST_ID", "").strip()
    assert mode in {"create", "penalty_only"} and dry_run in {"0", "1"}, "模式或预览标志无效"
    assert order_id or (contract_id and period), "必须显式提供已分单预提醒 ID，或合同+期次"
    if period:
        assert period.isdigit() and int(period) > 0, "必须提供正整数期次"
    if dry_run == "0":
        assert re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", request_id), "真实执行必须提供本次唯一 OVERDUE_REQUEST_ID；重跑保留原值"
    config = json.loads((ROOT / "config/overdue.json").read_text(encoding="utf-8"))
    expected_daily = os.getenv("OVERDUE_EXPECTED_DAILY_PENALTY", "").strip() or None
    expected_grade = os.getenv("OVERDUE_EXPECTED_GRADE", "").strip() or None
    if expected_daily:
        expected = Decimal(expected_daily)
        assert expected.is_finite() and expected > 0 and expected == expected.quantize(Decimal("0.01")), "单次任务期望罚息增量必须是正数且最多两位小数"
    authenticate_admin(session)
    database_checks.login_account(os.environ["SSO_USERNAME"])
    # Same order of locks as the prerequisite workflows; no concurrent date edits or assignment.
    with locked(ROOT / ".state/overdue_operation.lock"), locked(ROOT / ".state/prealert_operation.lock"), locked(ROOT / ".state/prealert_assignment.lock"):
        checks = database_checks
        state_path = ROOT / ".state/overdue_attempts.json"
        attempts = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        previous = attempts.get(request_id) if dry_run == "0" else None
        if previous:
            assert previous["status"] == "db_verified", "本次操作已有未确认进度；先核对 SQL、任务日志与罚息，禁止重发"
            fingerprint = previous["fingerprint"]
            assert fingerprint["mode"] == mode, "同一请求标识的执行模式发生变化"
            assert fingerprint.get("expected_grade") == expected_grade, "同一请求标识的期望账龄发生变化"
            if order_id:
                assert order_id == fingerprint["prealert_id"], "同一请求标识的订单发生变化"
            if contract_id:
                assert contract_id == fingerprint["contract_id"], "同一请求标识的合同发生变化"
            if period:
                assert int(period) == fingerprint["period"], "同一请求标识的期次发生变化"
            requested_days = os.getenv("OVERDUE_DAYS", "1" if mode == "create" else "").strip()
            if requested_days:
                assert int(requested_days) == fingerprint["overdue_days"], "同一请求标识的逾期天数发生变化"
            requested_rounds = os.getenv("OVERDUE_PENALTY_DAYS", "").strip() or "1"
            assert int(requested_rounds) == fingerprint["penalty_rounds"] and expected_daily == fingerprint["expected_daily_penalty"], "同一请求标识的计息参数发生变化"
            contract, reminder, plan, _ = checks.overdue_context(fingerprint["prealert_id"], fingerprint["contract_id"], fingerprint["period"], "penalty_only")
            assert plan["REPAY_TIME"] == datetime.fromisoformat(previous["storage_due_time"]), "成功记录的原应还日期已发生变化"
            assert Decimal(str(plan["LATE_CHARGE_ORIGIN"])) >= Decimal(previous["final_penalty_origin"]), "罚息小于历史成功结果，不能确认"
            verify_penalty_api(session, checks, plan)
            print("[逾期] 此请求已完成，仅回查，未重复改期、入催或累加罚息", flush=True)
            return
        contract, reminder, plan, all_plans = checks.overdue_context(order_id, contract_id, period, mode)
        contract_id, period = contract["CONTRACT_ID"], int(plan["PERIOD"])
        update_path = ROOT / ".state/overdue_days_attempts.json"
        updates = json.loads(update_path.read_text(encoding="utf-8")) if update_path.exists() else {}
        assert not any(r["contract_id"] == contract_id and int(r["period"]) == period and r["status"] != "db_verified" for r in updates.values()), "该期次存在未确认的逾期天数更新，先核对结果"
        clock = read_clock(session, config)
        checks.storage_clock(config["storageTimezone"])
        storage_zone = ZoneInfo(config["storageTimezone"])
        if mode == "create":
            days = positive_count(os.getenv("OVERDUE_DAYS", "1"), config["maximumDays"], "逾期天数")
            due_wall, due_storage = storage_datetime(clock, days, config["storageTimezone"])
            for other in all_plans:
                if other["ID"] != plan["ID"] and str(other["REPAY_STATUS"]) == "1":
                    local_due = other["REPAY_TIME"].replace(tzinfo=storage_zone).astimezone(clock.tzinfo)
                    assert local_due.date() > clock.date(), "其他未还期次已到期，不适用单期逾期模拟"
            # Current MX job can accrue multiple overdue days in its first execution.
            default_rounds = "1"
        else:
            due_storage = plan["REPAY_TIME"]
            due_wall = due_storage.replace(tzinfo=storage_zone).astimezone(clock.tzinfo)
            days = (clock.date() - due_wall.date()).days
            positive_count(days, config["maximumDays"], "实际逾期天数")
            if os.getenv("OVERDUE_DAYS", "").strip():
                assert days == int(os.environ["OVERDUE_DAYS"]), "追加罚息模式不能修改实际逾期天数"
            default_rounds = "1"
        rounds = penalty_task_count(mode, os.getenv("OVERDUE_PENALTY_DAYS", "").strip() or default_rounds, config["maximumPenaltyRounds"])
        grade = checks.overdue_grade(days, config["countryCode"], expected_grade)
        entry_job = read_scheduled_job(session, config["entryJob"])
        penalty_job = read_scheduled_job(session, config["penaltyJob"])
        checks.scheduled_job_configuration(entry_job, "入催任务")
        checks.scheduled_job_configuration(penalty_job, "罚息任务")
        api_plans = get_plans(session, contract_id)
        checks.repayment_matches_api(contract_id, api_plans)
        preview = {"mode": mode, "contract_id": contract_id, "period": period, "prealert_id": reminder["ID"],
            "server_business_time": clock.isoformat(), "business_due_time": due_wall.isoformat(),
            "storage_due_time": str(due_storage), "overdue_days": days, "descriptive_age": logical_age(days),
            "actual_grade": grade["REMIND_GRADE_NAME"], "actual_grade_code": grade["REMIND_GRADE_CODE"],
            "penalty_rounds": rounds, "expected_daily_penalty": expected_daily,
            "expected_grade": expected_grade,
            "entry_job_id": entry_job["id"], "entry_job_params": entry_job["params"],
            "penalty_job_id": penalty_job["id"], "penalty_job_params": penalty_job["params"]}
        print("[逾期预检] " + json.dumps(preview, ensure_ascii=False), flush=True)
        if dry_run == "1":
            print("[预览完成] 前置条件、服务器时间、账龄和任务配置通过；未执行 SQL UPDATE 或定时任务", flush=True)
            return
        fingerprint = {k: preview[k] for k in ("mode", "contract_id", "period", "prealert_id", "overdue_days", "penalty_rounds", "expected_daily_penalty", "expected_grade")}
        assert not any(r["contract_id"] == contract_id and r["period"] == period and r["status"] != "db_verified" for r in attempts.values()), "同一期次存在未确认操作，不能通过新请求标识绕过保护"
        record = {**preview, "fingerprint": fingerprint, "status": "prepared", "rounds": [],
                  "original_plan": json.loads(json.dumps(plan, default=str)),
                  "original_prealert_dates": {"ID": reminder["ID"], "REPAYMENT_DATE": str(reminder["REPAYMENT_DATE"])}}
        attempts[request_id] = record

        def status(value):
            record["status"] = value
            write_json(state_path, attempts)

        def same_server_day():
            assert read_clock(session, config).date() == clock.date(), "执行中服务端跨日，停止后续写入或任务"

        def run_job(job, job_config, label):
            same_server_day()
            fresh = read_scheduled_job(session, job_config)
            assert fresh == job, "任务参数已变化，停止执行"
            old_logs = checks.prealert_log_ids(job["id"])
            status(label + "_request_sent")
            JobRunAttempt(session, job).run(fresh)
            status(label + "_api_success")
            log = checks.scheduled_job_finished(job, old_logs, label)
            record.setdefault("job_logs", []).append({"stage": label, "id": log["ID"]})
            status(label + "_log_verified")

        status("prepared")
        if mode == "create":
            same_server_day()
            old_bill_ids = {r["ID"] for r in checks.overdue_bills(contract_id)}
            status("two_table_date_write_started")
            with connect_overdue_fixture() as writer:
                writer.backdate_due_dates(plan, reminder, due_storage)
            status("two_table_date_committed")
            plan = checks.overdue_fixture_saved(plan, reminder, due_storage, all_plans)
            status("two_table_date_db_verified")
            print("[逾期] 两表目标日期已核验，执行一次入催任务", flush=True)
            run_job(entry_job, config["entryJob"], "入催任务")
            bill = checks.overdue_entered(contract_id, period, days, grade, old_bill_ids)
            record["collection_id"] = bill["ID"]
            status("collection_db_verified")
        else:
            bill = checks.overdue_bills(contract_id)[0]
            assert int(bill["OVERDUE_DAYS"]) == days and bill["REMIND_GRADE"] == grade["REMIND_GRADE_CODE"], "已有催收单天数/账龄已变化，先核对"
            record["collection_id"] = bill["ID"]
        for number in range(1, rounds + 1):
            same_server_day()
            plan = next(p for p in checks.repayment_snapshot(contract_id) if p["ID"] == plan["ID"])
            round_record = {"number": number, "penalty_origin_before": str(plan["LATE_CHARGE_ORIGIN"] or 0)}
            record["rounds"].append(round_record)
            # Step 5: reset only after an existing successful accrual, never before the first new day.
            if mode == "penalty_only" or number > 1:
                _, reset_time = storage_datetime(clock, 1, config["storageTimezone"])
                if plan["PENALTY_UPDATE_TIME"] != reset_time:
                    status(f"penalty_{number}_clock_write_started")
                    with connect_overdue_fixture() as writer:
                        writer.reset_penalty_clock(plan, reset_time)
                    updated = next(p for p in checks.repayment_snapshot(contract_id) if p["ID"] == plan["ID"])
                    snapshot_unchanged(plan, updated, "PENALTY_UPDATE_TIME", reset_time)
                    plan = updated
                status(f"penalty_{number}_clock_verified")
            print(f"[罚息] 第 {number}/{rounds} 轮，只执行一次任务，校验本轮增量", flush=True)
            run_job(penalty_job, config["penaltyJob"], f"罚息第{number}轮")
            after, delta = checks.penalty_round_result(plan, expected_daily or record.get("observed_daily_penalty"))
            verify_penalty_api(session, checks, after)
            round_record.update(delta=str(delta), penalty_origin_after=str(after["LATE_CHARGE_ORIGIN"]), penalty_update_time=str(after["PENALTY_UPDATE_TIME"]))
            record["completed_penalty_rounds"] = number
            record.setdefault("observed_daily_penalty", str(delta))
            plan = after
            status(f"penalty_{number}_db_verified")
        listing = checked_json(session.post(ADMIN_URL + "/loan/remindBill/list", data={
            "contractId": contract_id, "listType": "5", "pageNum": 1, "pageSize": 100}, timeout=TIMEOUT), "所有待催收列表回查")
        record["api_list_visible"] = any(r.get("id") == record["collection_id"] for r in listing.get("rows", []))
        record["final_penalty_origin"] = str(plan["LATE_CHARGE_ORIGIN"])
        status("db_verified")
        print(f"[完成] 合同 {contract_id}，逾期 {days} 天，实际账龄 {grade['REMIND_GRADE_NAME']}，已核验 {rounds} 轮罚息；列表可见={record['api_list_visible']}", flush=True)
