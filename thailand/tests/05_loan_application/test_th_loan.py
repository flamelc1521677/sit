"""TH05: trial first, submit exactly once, verify main loan and three plans."""
from thailand.client import authenticate_thailand
from thailand.workflows import submit_loan


def test_thailand_trial_then_submit(session, database_checks):
    authenticate_thailand(session, database_checks)
    submit_loan(session, database_checks)
