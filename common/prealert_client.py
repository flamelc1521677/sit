"""Generate one target pre-reminder via the real shared job, never via SQL writes."""
import copy
import json
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json
from common.sso_login import TIMEOUT

DISPLAY_FIELDS = {
    "receivableTotalAmount", "receivableContractPrincipal", "receivableLoanPrincipal",
    "receivableLoanServiceFee", "receivableInterest", "receivableServiceFee", "receivablePenalty",
    "actualRepayDate", "actualTotalAmount", "actualContractPrincipal", "actualLoanPrincipal",
    "actualLoanServiceFee", "actualInterest", "actualServiceFee", "actualPenalty",
    "mitigateTotalAmount", "mitigateContractPrincipal", "mitigateLoanPrincipal",
    "mitigateLoanServiceFee", "mitigateInterest", "mitigatePenalty",
    "periodOriginReceivableAmount", "periodActualRepayAmount", "periodMitigateAmount",
}


def reminder_days(value):
    text = str(value).strip()
    assert text in {"0", "1", "2"}, "仅支持任务参数 0/1/2，对应 T0/T-1/T-2；不猜测其他参数"
    return int(text)


def server_business_clock(response, timezone):
    assert response.status_code == 200 and "html" in response.headers.get("Content-Type", "").lower(), "服务端未返回业务日期页面"
    match = re.search(r'var\s+__cn\s*=\s*"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"', response.text)
    assert match and response.headers.get("Date"), "服务端缺少商户当前时间或 HTTP Date，禁止使用电脑时间代替"
    wall = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(timezone))
    http_time = parsedate_to_datetime(response.headers["Date"])
    assert http_time.tzinfo is not None and abs((wall - http_time).total_seconds()) <= 120, "服务端业务时区或时间不一致，停止改期"
    return wall


def validate_job(job, config):
    for field in ("id", "beanName", "methodName", "dbGroupId", "targetGroup"):
        expected = config["jobId"] if field == "id" else config[field]
        assert str(job.get(field)) == str(expected), f"定时任务 {field} 不符合配置，停止操作"
    reminder_days(job.get("params"))


def read_job(session, config):
    # Match by identity and handler, not a mutable human-readable remark.
    body = checked_json(session.post(ADMIN_URL + "/system/job/list", data={
        "beanName": config["beanName"], "methodName": config["methodName"],
        "pageNum": 1, "pageSize": 500}, timeout=TIMEOUT), "读取预提醒任务")
    assert int(body.get("total", 0)) <= 500, "任务结果超出分页，停止而非截断查找"
    rows = [r for r in body.get("rows", []) if r.get("id") == config["jobId"]]
    assert len(rows) == 1, "没有唯一匹配的预提醒定时任务"
    validate_job(rows[0], config)
    return rows[0]


def get_plans(session, contract_id):
    body = checked_json(session.post(ADMIN_URL + "/loan/contract/getRepaymentPlan",
        data={"contractId": contract_id}, timeout=TIMEOUT), "读取合同还款计划")
    rows = body.get("rows", [])
    assert rows and int(body.get("total", -1)) == len(rows), "还款计划缺失或分页不完整"
    assert all(row.get("contractId") == contract_id for row in rows), "还款计划包含其他合同"
    assert len({r["id"] for r in rows}) == len(rows) and len({int(r["period"]) for r in rows}) == len(rows), "还款计划 ID 或期次重复"
    return sorted(rows, key=lambda r: int(r["period"]))


def select_period(plans, requested=None):
    eligible = [r for r in plans if str(r.get("repayStatus")) == "1"]
    assert eligible, "合同没有未还期次"
    first = min(eligible, key=lambda r: int(r["period"]))
    if requested:
        assert str(first["period"]) == str(requested), "只能选择最早未还期次，不能跳过前序欠款"
    assert not first.get("actualRepayDate") and not first.get("actualTotalAmount"), "所选期次已有还款，不适用本场景"
    return first


def date_payload(plans, period, due_date):
    # Same protocol as the contract page: all rows minus display-only accounting fields.
    rows = [{k: copy.deepcopy(v) for k, v in row.items() if k not in DISPLAY_FIELDS} for row in plans]
    selected = [r for r in rows if int(r["period"]) == int(period)]
    assert len(selected) == 1 and str(selected[0]["repayStatus"]) == "1", "待修改期次不唯一或不是未还状态"
    for row in rows:
        if int(row["period"]) < int(period):
            assert row["repayTimeStr"] < due_date.isoformat(), "目标日期不晚于前一期，不能修改"
        if int(row["period"]) > int(period):
            assert row["repayTimeStr"] > due_date.isoformat(), "目标日期不早于后一期，不能修改"
    selected[0]["repayTimeStr"] = due_date.isoformat()
    return {"repayData": json.dumps(rows, ensure_ascii=False, separators=(",", ":"))}


class JobEditForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.fields = {}
        self.select = None
        self.multiple = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form":
            self.active = attrs.get("id") == "form-job-edit"
        if not self.active or "disabled" in attrs:
            return
        if tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")
        elif tag == "select":
            self.select = attrs.get("name")
            self.multiple = "multiple" in attrs
        elif tag == "option" and self.select:
            if "selected" in attrs or (not self.multiple and self.select not in self.fields):
                self.fields[self.select] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False
        elif tag == "select":
            self.select = None


def job_edit_payload(html, job, days):
    parser = JobEditForm()
    parser.feed(html)
    fields = parser.fields
    for key in ("id", "beanName", "methodName", "cronExpression", "params", "remark", "dbGroupId", "targetGroup"):
        assert str(fields.get(key)) == str(job[key]), f"任务编辑页 {key} 与列表不一致"
    fields["params"] = str(reminder_days(days))
    return fields


class PrealertAttempt:
    def __init__(self, session):
        self.session = session
        self.plan_sent = self.job_edit_sent = self.run_sent = False

    def save_plan(self, payload):
        assert not self.plan_sent, "本次改期已经发送，禁止自动重发"
        self.plan_sent = True
        return checked_json(self.session.post(ADMIN_URL + "/loan/contract/saveRepaymentPlan",
            data=payload, timeout=(10, 120), allow_redirects=False), "保存应还日期")

    def edit_job(self, payload):
        assert not self.job_edit_sent, "本次任务参数修改已经发送，禁止自动重发"
        self.job_edit_sent = True
        return checked_json(self.session.post(ADMIN_URL + "/system/job/edit",
            data=payload, timeout=TIMEOUT, allow_redirects=False), "保存预提醒任务参数")

    def run(self, job_id):
        assert not self.run_sent, "本次定时任务已经触发，禁止自动重复执行"
        self.run_sent = True
        return checked_json(self.session.post(ADMIN_URL + "/system/job/run",
            data={"id": job_id}, timeout=(10, 120), allow_redirects=False), "执行预提醒定时任务")


def target_date(clock, days):
    return clock.date() + timedelta(days=reminder_days(days))
