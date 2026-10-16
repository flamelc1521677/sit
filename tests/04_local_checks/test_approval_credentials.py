import pytest

from common import approval_credentials as credentials


def test_ob4_defaults_to_ob5(monkeypatch):
    monkeypatch.setattr(credentials, "configured_value", lambda name: "fixture-password" if name == "APPROVER_PASSWORD" else None)
    assert credentials.approval_credentials("ob4-mx") == ("ob5-mx", "fixture-password")


def test_missing_approver_password_stops_preflight(monkeypatch):
    monkeypatch.setattr(credentials, "configured_value", lambda name: None)
    with pytest.raises(AssertionError, match="独立审批账号"):
        credentials.approval_credentials("ob4-mx")


def test_same_account_override_is_rejected(monkeypatch):
    monkeypatch.setattr(credentials, "configured_value", lambda name: " OB4-MX " if name == "APPROVER_USERNAME" else "fixture-password")
    with pytest.raises(AssertionError, match="不能审核自己"):
        credentials.approval_credentials("ob4-mx")
