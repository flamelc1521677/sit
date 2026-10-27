"""TH 01: SSO result.success -> OAuth -> Thai country and account assertions."""
from thailand.client import authenticate_thailand


def test_thailand_login(session, database_checks):
    authenticate_thailand(session, database_checks)
