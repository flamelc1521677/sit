"""TH08: exact related payout, Bangkok time, DB success and three plans."""
from thailand.client import authenticate_thailand
from thailand.workflows import payout_success


def test_thailand_payout_success(session, database_checks):
    authenticate_thailand(session, database_checks)
    payout_success(session, database_checks)
