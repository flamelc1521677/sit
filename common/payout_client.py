"""Mark one explicitly linked test payout successful; preserve live identifiers and times."""
import os
from datetime import datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json

ALLOWED_STATES = {"2", "4", "6"}


class PayoutForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.active = False
        self.select = None
        self.textarea = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("id") == "form-TransferBill-edit"
        if not self.active:
            return
        if tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")
        if tag == "select":
            self.select = attrs.get("name")
        if tag == "option" and self.select and (self.select not in self.fields or "selected" in attrs):
            self.fields[self.select] = attrs.get("value", "")
        if tag == "textarea":
            self.textarea = attrs.get("name")
            if self.textarea:
                self.fields[self.textarea] = ""

    def handle_data(self, data):
        if self.active and self.textarea:
            self.fields[self.textarea] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False
        elif tag == "select":
            self.select = None
        elif tag == "textarea":
            self.textarea = None


def database_time(local_time, merchant_timezone=None, database_timezone=None):
    if not local_time:
        return None
    value = datetime.strptime(local_time, "%Y-%m-%d %H:%M:%S")
    merchant = ZoneInfo(merchant_timezone or os.getenv("PAYOUT_MERCHANT_TIMEZONE", "America/Mexico_City"))
    database = ZoneInfo(database_timezone or os.getenv("PAYOUT_DATABASE_TIMEZONE", "Asia/Shanghai"))
    return value.replace(tzinfo=merchant).astimezone(database).replace(tzinfo=None)


def build_payout_payload(html, bill, page_now, merchant_timezone=None, database_timezone=None):
    parser = PayoutForm()
    parser.feed(html)
    fields = parser.fields
    for field, column in {"id": "ID", "ids": "ID", "systemSourceId": "SYSTEM_SOURCE_ID", "contractId": "CONTRACT_ID", "bizId": "BIZ_ID"}.items():
        assert fields.get(field) == str(bill[column]), f"放款表单 {field} 与目标单据不一致"
    assert bill["SYSTEM_SOURCE_ID"] == "BORROWER", "仅支持 BORROWER 来源放款"
    assert str(bill["STATE"]) in ALLOWED_STATES, "放款单已成功、已退票或状态不支持，禁止重复修改"
    assert fields.get("state") == str(bill["STATE"]), "放款状态在读取表单时已变化"
    trade_time = fields.get("tradeTime") or page_now
    complete_time = fields.get("completeTime") or page_now
    assert trade_time and complete_time, "页面未返回商户当前时间或执行时间"
    assert database_time(complete_time, merchant_timezone, database_timezone) >= database_time(trade_time, merchant_timezone, database_timezone), "放款完成时间早于执行时间"
    if bill.get("TRADE_TIME"):
        assert database_time(trade_time, merchant_timezone, database_timezone) == bill["TRADE_TIME"], "表单执行时间与数据库时间不一致，请检查商户时区"
    return {"id": bill["ID"], "ids": bill["ID"], "systemSourceId": "BORROWER",
            "contractId": bill["CONTRACT_ID"], "bizId": bill["BIZ_ID"], "state": "1",
            "tradeTime": trade_time, "completeTime": complete_time,
            "arriTradeTime": fields.get("arriTradeTime", ""),
            "updateReason": os.getenv("PAYOUT_UPDATE_REASON", fields.get("updateReason", ""))}


class PayoutAttempt:
    def __init__(self, session, bill, payload):
        self.session = session
        self.before = dict(bill)
        self.payload = dict(payload)
        self.sent = False

    def execute(self, fresh):
        assert not self.sent, "放款编辑请求已发送，禁止自动重试"
        for field in ("ID", "STATE", "BIZ_ID", "CONTRACT_ID", "TRADE_AMOUNT", "MODIFY_TIME"):
            assert fresh.get(field) == self.before.get(field), "放款单据在提交前发生变化，停止操作"
        assert str(fresh["STATE"]) in ALLOWED_STATES and self.payload["state"] == "1"
        assert self.payload["id"] == fresh["ID"] == self.payload["ids"], "禁止批量或错单修改"
        assert self.payload["contractId"] == fresh["CONTRACT_ID"] and self.payload["bizId"] == fresh["BIZ_ID"]
        self.sent = True
        response = self.session.post(ADMIN_URL + "/pay/borTransferBillMoco/edit", data=self.payload,
                                     timeout=(10, 120), allow_redirects=False)
        return checked_json(response, "放款审批通过")
