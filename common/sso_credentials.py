"""Resolve a complete SSO credential pair; saved local login wins over stale terminal input."""
import os

from common.environment import user_value


def sso_credentials(username=None, password=None):
    if username is not None or password is not None:
        assert username and password, "显式登录必须同时指定账号和密码"
        return username.strip(), password
    source = os.getenv("SSO_CREDENTIAL_SOURCE", "local").strip()
    assert source in {"local", "process"}, "SSO_CREDENTIAL_SOURCE 仅允许 local/process"
    saved_username, saved_password = user_value("SSO_USERNAME"), user_value("SSO_PASSWORD")
    if source == "local" and (saved_username or saved_password):
        assert saved_username and saved_password, "本机 SSO 登录配置不完整，请同时配置账号和密码"
        return saved_username.strip(), saved_password
    username, password = os.getenv("SSO_USERNAME"), os.getenv("SSO_PASSWORD")
    assert username and password, "未配置 SSO 登录信息：请配置本机当前用户的 SSO_USERNAME/SSO_PASSWORD"
    return username.strip(), password
