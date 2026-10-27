import pytest

from common import sso_credentials as credentials


@pytest.fixture
def local_login(monkeypatch):
    monkeypatch.delenv("SSO_CREDENTIAL_SOURCE", raising=False)
    monkeypatch.delenv("SSO_USERNAME", raising=False)
    monkeypatch.delenv("SSO_PASSWORD", raising=False)
    values = {"SSO_USERNAME": "ob4-mx", "SSO_PASSWORD": "local-test-secret"}
    monkeypatch.setattr(credentials, "user_value", values.get)
    return values


def test_saved_pair_wins_over_stale_terminal_password(local_login, monkeypatch):
    monkeypatch.setenv("SSO_USERNAME", "incorrect-user")
    monkeypatch.setenv("SSO_PASSWORD", "incorrect-terminal-secret")
    assert credentials.sso_credentials() == ("ob4-mx", "local-test-secret")


def test_saved_login_works_without_process_environment(local_login):
    assert credentials.sso_credentials() == ("ob4-mx", "local-test-secret")


def test_explicit_reviewer_is_not_replaced_by_local_requester(local_login):
    assert credentials.sso_credentials("ob5-mx", "reviewer-test-secret") == ("ob5-mx", "reviewer-test-secret")


def test_partial_explicit_login_cannot_mix_passwords(local_login):
    with pytest.raises(AssertionError, match="同时指定"):
        credentials.sso_credentials("ob5-mx")


def test_partial_saved_login_cannot_mix_process_credentials(local_login, monkeypatch):
    local_login.pop("SSO_PASSWORD")
    monkeypatch.setenv("SSO_PASSWORD", "terminal-secret")
    with pytest.raises(AssertionError, match="配置不完整"):
        credentials.sso_credentials()


def test_explicit_process_mode_supports_intentional_override(local_login, monkeypatch):
    monkeypatch.setenv("SSO_CREDENTIAL_SOURCE", "process")
    monkeypatch.setenv("SSO_USERNAME", "another-account")
    monkeypatch.setenv("SSO_PASSWORD", "process-test-secret")
    assert credentials.sso_credentials() == ("another-account", "process-test-secret")


def test_no_saved_login_can_fall_back_to_process(local_login, monkeypatch):
    local_login.clear()
    monkeypatch.setenv("SSO_USERNAME", "process-user")
    monkeypatch.setenv("SSO_PASSWORD", "process-test-secret")
    assert credentials.sso_credentials() == ("process-user", "process-test-secret")


def test_missing_login_fails_without_password_in_error(local_login):
    local_login.clear()
    with pytest.raises(AssertionError, match="未配置 SSO"):
        credentials.sso_credentials()
