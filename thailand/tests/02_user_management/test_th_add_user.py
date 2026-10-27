"""TH 02: login -> OB5 Line precheck -> increment phone -> create -> DB proof."""
from thailand.client import authenticate_thailand, create_user


def test_thailand_add_user(session, database_checks):
    authenticate_thailand(session, database_checks)
    create_user(session, database_checks)
