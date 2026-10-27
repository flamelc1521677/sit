"""Explicit reject or return to credit review; never execute both on one loan."""
import os
from thailand.client import authenticate_thailand
from thailand.workflows import review_selected


def test_th_review_selected_alternative(session, database_checks):
    action = os.getenv('TH_APPROVAL_ACTION', '').strip()
    assert action in {'reject', 'close'}, '此入口必须明确指定reject或close'
    authenticate_thailand(session, database_checks, reviewer=True)
    review_selected(session, database_checks, action)
