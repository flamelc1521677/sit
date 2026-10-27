"""TH03: fixed Thai OCR image -> increment ID -> save -> increment KBANK card."""
from thailand.client import authenticate_thailand
from thailand.workflows import complete_profile


def test_thailand_identity_and_bank(session, database_checks):
    authenticate_thailand(session, database_checks)
    complete_profile(session, database_checks)
