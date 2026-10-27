"""TH07: only pass, by independent chengzi; already-paid system route is read-only."""
from thailand.client import authenticate_thailand
from thailand.workflows import review_pass


def test_thailand_review_pass(session, database_checks):
    authenticate_thailand(session, database_checks, reviewer=True)
    review_pass(session, database_checks)
