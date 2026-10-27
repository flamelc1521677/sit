"""One precisely selected reminder -> fixed ob5 receiver. No bulk/filter allocation."""
import json
import re
from html.parser import HTMLParser

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json
from common.sso_login import TIMEOUT


def javascript_json(html, name):
    match = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*", html)
    assert match, f"转交表单缺少 {name}，停止操作"
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except ValueError:
        raise AssertionError(f"转交表单 {name} 不是有效 JSON，禁止执行网页脚本") from None
    return value


class AssignmentForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.names = set()
        self.op_type = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form":
            self.active = attrs.get("id") == "form-distributeOrTransferBills-add"
        if self.active and tag in {"input", "select"} and attrs.get("name"):
            self.names.add(attrs["name"])
            if attrs["name"] == "opType":
                self.op_type = attrs.get("value")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


def assignment_payload(html, before, receiver, config):
    assert config["receiverLogin"] == "ob5", "本场景接收人固定为 ob5"
    assert str(receiver["USER_ID"]) == config["receiverId"] and str(receiver["DEPT_ID"]) == config["departmentId"], "接收人或部门与固定配置不一致"
    assert receiver["LOGIN_NAME"] == config["receiverLogin"], "不能将相似名称当成固定接收人"
    assert config["distributionMode"] == "1" and config["opType"] == "2" and config["isAssistAllocation"] == "1", "仅支持单笔精准工单转交，不使用顺序分配或协催调配"
    assert str(before["IS_FINAL_STATE"]) == "0" and str(before["REPAYMENT_STATUS"]) == "1", "预提醒已结清或失效，不可转交"
    parser = AssignmentForm()
    parser.feed(html)
    names = {"distributionMode", "deptSelect", "deptIdList", "ownerDictList", "userName", "opType", "ids", "isAssistAllocation", "isFilter"}
    assert names <= parser.names and parser.op_type == "2", "转交表单协议不匹配"
    records = javascript_json(html, "records")
    assert isinstance(records, list) and len(records) == 1, "表单必须只包含一笔指定预提醒，禁止批量分单"
    row = records[0]
    for api, column in {"id": "ID", "contractId": "CONTRACT_ID", "period": "PERIOD", "merchantId": "MERCHANT_ID", "repaymentStatus": "REPAYMENT_STATUS"}.items():
        assert str(row.get(api)) == str(before[column]), f"转交表单 {api} 与目标数据库记录不一致"
    for api, column in {"userId": "USER_ID", "ownerName": "OWNER_NAME", "deptId": "DEPT_ID"}.items():
        assert str(row.get(api) or "") == str(before.get(column) or ""), "原归属已变化，停止覆盖他人分单"
    filters = javascript_json(html, "preReminderVo")
    assert isinstance(filters, dict), "预提醒筛选协议异常"
    payload = {"distributionMode": "1", "deptSelect": receiver["DEPT_NAME"],
        "deptIdList": config["departmentId"], "ownerDictList": config["receiverId"],
        "userName": config["receiverId"] + "-" + config["receiverLogin"],
        "opType": "2", "ids": before["ID"], "isFilter": "", "isAssistAllocation": "1"}
    for key, value in filters.items():
        if key == "params" or value is None:
            continue
        assert key not in payload, "筛选条件与精准分配参数重名，停止操作"
        payload[key] = ",".join(str(v) for v in value) if isinstance(value, list) else str(value)
    if payload.get("merchantIdList"):
        assert payload["merchantIdList"] == str(before["MERCHANT_ID"]), "转交筛选商户不匹配"
    assert payload["ids"] and "," not in payload["ids"], "必须提供单笔预提醒 ID，不能使用空 ID 批量筛选"
    return payload


def verify_receiver_option(session, receiver):
    response = session.post(ADMIN_URL + "/loan/remindBill/selectUsersByDeptList",
        data={"depts": str(receiver["DEPT_ID"]), "filterParam": ""}, timeout=TIMEOUT)
    assert response.status_code == 200 and "json" in response.headers.get("Content-Type", "").lower(), "读取可选接收人失败"
    rows = response.json()
    assert isinstance(rows, list), "接收人接口格式已变化，请核对"
    matches = [r for r in rows if str(r.get("userId")) == str(receiver["USER_ID"])
               and r.get("loginName") == receiver["LOGIN_NAME"] and str(r.get("deptId")) == str(receiver["DEPT_ID"])]
    assert len(matches) == 1, "ob5 不在指定部门的可选接收人列表中，停止分配"


class AssignmentAttempt:
    def __init__(self, session):
        self.session = session
        self.sent = False

    def execute(self, payload, before, fresh):
        assert not self.sent, "本次转交请求已经发送，禁止自动重发"
        assert payload.get("ids") == before["ID"] and fresh == before, "目标或原归属发生变化，停止提交"
        assert payload.get("distributionMode") == "1" and payload.get("opType") == "2", "禁止批量分单"
        self.sent = True
        return checked_json(self.session.post(ADMIN_URL + "/loan/preReminder/updateDistributeOrTransferBills",
            data=payload, timeout=(10, 120), allow_redirects=False), "预提醒精准转交")
