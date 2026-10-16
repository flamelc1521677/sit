"""Establish the offadmin session through the SSO OAuth callback."""
import os
from urllib.parse import parse_qs, urlsplit

from common.sso_login import BASE_URL, TIMEOUT, login

ADMIN_URL = os.getenv("ADMIN_BASE_URL", "https://saas-offadmin.suxi-test.com").rstrip("/")


def authenticate_admin(session, username=None, password=None):
    print("[后台 1/3] 获取 offadmin OAuth 授权地址", flush=True)
    response = session.get(ADMIN_URL + "/login/oauth2/authorize",
                           params={"redirect": "/index?lang=zh_CN"},
                           timeout=TIMEOUT, allow_redirects=False)
    assert response.status_code in (302, 303), "后台未返回 OAuth 授权跳转"
    authorize_url = response.headers.get("Location", "")
    parsed = urlsplit(authorize_url)
    assert parsed.scheme == "https" and parsed.netloc == urlsplit(BASE_URL).netloc, "OAuth 授权域名不符合配置"
    assert parsed.path == "/sso/oauth2/authorize", "OAuth 授权路径不符合预期"
    client_id = parse_qs(parsed.query)["client_id"][0]
    login(session, client_id, username=username, password=password)
    print("[后台 2/3] 完成 OAuth 授权回调", flush=True)
    response = session.get(authorize_url, timeout=TIMEOUT)
    assert response.status_code == 200, "OAuth 回调失败"
    assert urlsplit(response.url).netloc == urlsplit(ADMIN_URL).netloc, "OAuth 未回到后台"
    assert urlsplit(response.url).path == "/index", "OAuth 未进入后台首页"
    session.headers.update({"Origin": ADMIN_URL, "Referer": ADMIN_URL + "/user/user",
                            "X-Requested-With": "XMLHttpRequest"})
    print("[后台 3/3] 后台登录完成", flush=True)
