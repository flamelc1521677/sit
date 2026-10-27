"""Explicit Thai repeat application; no implicit review or payout."""
import os
from thailand.client import authenticate_thailand
from thailand.workflows import submit_loan


def test_th_repeat_loan(session, database_checks):
    assert os.getenv('TH_REPEAT_USER_ID') and os.getenv('TH_REPEAT_REQUEST_ID') and os.getenv('TH_TARGET_PHONE'), '复借必须指定用户ID、手机号及唯一申请标识'
    authenticate_thailand(session, database_checks)
    submit_loan(session, database_checks)
