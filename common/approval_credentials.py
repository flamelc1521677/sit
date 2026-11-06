"""Resolve the independent reviewer without storing passwords in project files."""
import json
from common.environment import configured_value


def approval_credentials(requester, approval=None):
    assert requester, "缺少借款发起账号 SSO_USERNAME"
    reviewer = configured_value("APPROVER_USERNAME") or ({"ob4-mx": "ob5-mx"}.get(requester))
    password = configured_value("APPROVER_PASSWORD")
    if approval and str(approval.get("review_assignment_managed") or "0") == "1" and (approval.get("assigned_reviewer_name") or approval.get("assigned_reviewer_id")):
        assigned = str(approval.get("assigned_reviewer_name") or "").strip()
        assert assigned and approval.get("assigned_reviewer_id"), "订单缺少已分配复审人，停止审批"
        passwords = json.loads(configured_value("MX_REVIEWER_PASSWORDS") or "{}")
        assert isinstance(passwords, dict), "本机复审账号配置必须为映射"
        password = passwords.get(assigned)
        if not password and reviewer and assigned.casefold() == reviewer.strip().casefold():
            password = configured_value("APPROVER_PASSWORD")
        reviewer = assigned
        assert password, f"未配置订单复审人 {assigned} 的本机凭据，停止审批"

    assert reviewer and password, "请配置独立审批账号 APPROVER_USERNAME/APPROVER_PASSWORD；完整流程会在新增用户前检查"
    assert reviewer.strip().casefold() != requester.strip().casefold(), "人审不能审核自己提交的借款，请使用独立审批账号"
    return reviewer.strip(), password
