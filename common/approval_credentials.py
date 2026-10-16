"""Resolve the independent reviewer without storing passwords in project files."""
import os


def configured_value(name):
    value = os.getenv(name)
    if not value and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            pass
    return value


def approval_credentials(requester):
    assert requester, "缺少借款发起账号 SSO_USERNAME"
    reviewer = configured_value("APPROVER_USERNAME") or ({"ob4-mx": "ob5-mx"}.get(requester))
    password = configured_value("APPROVER_PASSWORD")
    assert reviewer and password, "请配置独立审批账号 APPROVER_USERNAME/APPROVER_PASSWORD；完整流程会在新增用户前检查"
    assert reviewer.strip().casefold() != requester.strip().casefold(), "人审不能审核自己提交的借款，请使用独立审批账号"
    return reviewer.strip(), password
