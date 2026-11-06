"""Mutually exclusive manual-review outcomes. No default action or automatic retry."""
from decimal import Decimal
from html.parser import HTMLParser
import re

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json

ACTIONS = {
    "pass": {"label": "人审通过", "reason_field": "approvalReason", "status": 1, "log_action": "PASS"},
    "reject": {"label": "人审拒绝", "reason_field": "rejectReason", "status": 2, "log_action": "REJECT"},
    "close": {"label": "退回信审", "reason_field": "reason", "status": 3, "log_action": "CLOSE"},
}


class ApprovalBusinessError(AssertionError):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"人审接口拒绝：code={code}，msg={message}")


def verify_reviewer_context(approval, operator_id, login_name):
    # creator_id may be the assigned sales owner, not the account that submitted the loan.
    requester = approval.get("submitted_by")
    assert requester, "缺少实际借款发起账号记录，请提供 LOAN_REQUESTER_USERNAME 后再核对"
    assert operator_id and login_name, "缺少审批操作账号"
    assert requester.strip().casefold() != login_name.strip().casefold(), "人审不能审核自己提交的借款，请使用独立审批账号"
    if approval.get("submitter_id"):
        assert str(approval["submitter_id"]) != str(operator_id), "人审不能审核自己提交的借款"
    if str(approval.get("review_assignment_managed") or "0") == "1" and (approval.get("assigned_reviewer_id") or approval.get("assigned_reviewer_name")):
        assert str(approval.get("assigned_reviewer_id")) == str(operator_id), "操作账号不是订单分配复审人"
        assert str(approval.get("assigned_reviewer_name") or "").casefold() == login_name.casefold(), "复审账号名称不一致"
        assert str(approval.get("submitter_id")) != str(operator_id), "人审不能审核自己提交的借款"



def verify_previous_attempt(previous, action, login_name, version, approval=None):
    if previous is None:
        return
    assignment_rejection = (
        approval and str(approval.get("review_assignment_managed") or "0") == "1"
        and previous.get("status") == "api_rejected"
        and str(previous.get("code")) == "500"
        and previous.get("msg") == "仅订单分配复审人可审批"
        and str(approval.get("status")) == "0"
        and approval.get("assigned_reviewer_name") == login_name)
    assert ((previous.get("status") == "self_approval_rejected" or assignment_rejection)
            and previous.get("attempted_by") != login_name
            and previous.get("action") == action
            and previous.get("approval_version") == version), "此审批已有请求尝试记录，禁止自动重试；请先核对实际状态"


def action_spec(action):
    assert action in ACTIONS, "必须明确选择 APPROVAL_ACTION=pass、reject 或 close；不默认审批"
    return ACTIONS[action]


def build_approval_payload(approval, action, reason, amount):
    spec = action_spec(action)
    assert approval.get("id"), "审批 ID 缺失"
    assert str(approval.get("approval_type")) == "1", "仅支持发起借款审批，禁止操作放款改卡审批"
    assert str(approval.get("status")) == "0", "审批记录不是待审批状态，禁止重复或变更审批结果"
    reason = str(reason).strip()
    assert reason and len(reason) <= 500, "审批原因必须为 1 至 500 字"
    amount = str(amount).strip()
    assert re.fullmatch(r"\d{1,16}(\.\d{1,2})?", amount) and Decimal(amount) > 0, "用户期望额度必须为正数且最多两位小数"
    # Require an explicit expected amount matching this loan; never reuse another loan amount.
    assert Decimal(str(approval["contract_amount"])) == Decimal(amount), "审批期望额度与目标借款合同金额不一致，请核对配置"
    return {"id": approval["id"], spec["reason_field"]: reason, "expectedAmount": amount}


class ApprovalDetail(HTMLParser):
    def __init__(self):
        super().__init__()
        self.approval_id = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("id") == "approvalId":
            self.approval_id = attrs.get("value")


def verify_detail(html, approval_id):
    parser = ApprovalDetail()
    parser.feed(html)
    assert parser.approval_id == approval_id, "审批详情与目标审批 ID 不一致，或登录已失效"


class ApprovalAttempt:
    def __init__(self, session, approval, action, reason, amount):
        self.session = session
        self.approval = dict(approval)
        self.action = action
        self.payload = build_approval_payload(approval, action, reason, amount)
        self.sent = False

    def execute(self, fresh):
        assert not self.sent, "同一次审批只能发送一次，禁止重试或切换结果"
        assert fresh["id"] == self.approval["id"] and str(fresh["status"]) == "0", "审批已被其他操作处理，停止提交"
        assert fresh["version"] == self.approval["version"], "审批版本已变化，停止提交"
        assert build_approval_payload(fresh, self.action, self.payload[ACTIONS[self.action]["reason_field"]], self.payload["expectedAmount"]) == self.payload, "审批参数与提交前快照不一致"
        self.sent = True
        response = self.session.post(ADMIN_URL + "/borrower/payoutApproval/" + self.action,
                                     data=self.payload, timeout=(10, 120), allow_redirects=False)
        if response.status_code == 200 and "json" in response.headers.get("Content-Type", "").lower():
            body = response.json()
            if str(body.get("code")) != "0":
                raise ApprovalBusinessError(body.get("code"), body.get("msg"))
        return checked_json(response, ACTIONS[self.action]["label"])
