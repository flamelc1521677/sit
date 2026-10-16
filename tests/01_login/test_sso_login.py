"""场景 01：SSO 登录，验证 HTTP 200、code 200、msg result.success。"""
from common.sso_login import login
import os


def test_password_login(session, database_checks):
    login(session)
    database_checks.login_account(os.environ["SSO_USERNAME"])
