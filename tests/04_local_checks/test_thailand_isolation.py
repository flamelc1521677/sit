"""Country isolation and duplicate prevention must hold before any remote write."""
import json
from unittest.mock import Mock

import pytest

from common.phone_sequence import reserve_phone
from thailand import client
from thailand.database_checks import ThailandDatabaseChecks


def test_thai_credentials_never_fall_back_to_mexico(monkeypatch):
    monkeypatch.setattr(client, 'user_value', lambda name: None)
    monkeypatch.setenv('SSO_USERNAME', 'ob4-mx')
    monkeypatch.setenv('SSO_PASSWORD', 'dummy-mexico')
    monkeypatch.delenv('TH_SSO_USERNAME', raising=False)
    monkeypatch.delenv('TH_SSO_PASSWORD', raising=False)
    with pytest.raises(AssertionError, match='TH_SSO_USERNAME'):
        client.credentials()


def test_thai_saved_credentials_win_without_changing_mexico(monkeypatch):
    saved = {'TH_SSO_USERNAME': 'OB5', 'TH_SSO_PASSWORD': 'dummy-thai'}
    monkeypatch.setattr(client, 'user_value', saved.get)
    monkeypatch.setenv('TH_CREDENTIAL_SOURCE', 'local')
    monkeypatch.setenv('SSO_USERNAME', 'ob4-mx')
    monkeypatch.setenv('TH_SSO_PASSWORD', 'stale-value')
    assert client.credentials() == ('OB5', 'dummy-thai')
    assert client.os.environ['SSO_USERNAME'] == 'ob4-mx'


def test_thai_credentials_cannot_mix_sources(monkeypatch):
    monkeypatch.setattr(client, 'user_value', {'TH_SSO_USERNAME': 'OB5'}.get)
    monkeypatch.setenv('TH_CREDENTIAL_SOURCE', 'local')
    monkeypatch.setenv('TH_SSO_PASSWORD', 'dummy-process')
    with pytest.raises(AssertionError):
        client.credentials()


def test_mexico_session_cannot_pass_thai_country_guard():
    with pytest.raises(AssertionError, match='泰国'):
        client.assert_thailand_page('<select name="appOwnership"><option value="ConbusloPre"></option></select>', client.configuration())


def test_thai_phone_increment_does_not_touch_mexico(tmp_path):
    mexico = tmp_path / 'phone.json'
    mexico.write_text('{"next_phone":"0658900999"}', encoding='utf-8')
    before = mexico.read_bytes()
    thai = tmp_path / 'thailand/phone.json'
    assert reserve_phone(thai, client.configuration()['firstPhone']) == '0650100001'
    assert reserve_phone(thai, client.configuration()['firstPhone']) == '0650100002'
    assert json.loads(thai.read_text())['next_phone'] == '0650100003'
    assert mexico.read_bytes() == before


@pytest.mark.parametrize('status', ['request_sent', 'api_success', 'api_error_unconfirmed'])
def test_unknown_create_stops_before_phone_or_http(tmp_path, monkeypatch, status):
    state = tmp_path / 'thai'
    state.mkdir()
    (state / 'add_user_attempts.json').write_text(json.dumps({'0650100001': {'status': status}}))
    monkeypatch.setattr(client, 'STATE', state)
    session, checks = Mock(), Mock()
    with pytest.raises(AssertionError, match='未确认'):
        client.create_user(session, checks)
    session.post.assert_not_called()
    session.get.assert_not_called()
    assert not (state / 'phone.json').exists()


def test_thai_schema_ignores_mexico_overrides(monkeypatch):
    monkeypatch.setenv('SIT_DB_BUSINESS_SCHEMA', 'off_biz_mex')
    checks = ThailandDatabaseChecks(Mock())
    assert checks.biz == 'off_biz_thailand'


def test_employee_option_cannot_change_owner():
    config = client.configuration()
    response = Mock(status_code=200, headers={'Content-Type': 'application/json'})
    response.json.return_value = {'code': 0, 'data': {'pagination': {'more': False}, 'results': [
        {'id': 65, 'lineAccount': config['employeeLine'], 'employeeUserId': 1590, 'employeeDeptId': 11807}]}}
    session = Mock()
    session.get.return_value = response
    with pytest.raises(AssertionError, match='OB5'):
        client.employee_option(session, config)


def test_failed_create_is_not_retried_or_replaced(tmp_path, monkeypatch):
    state = tmp_path / 'thai'
    monkeypatch.setattr(client, 'STATE', state)
    monkeypatch.setattr(client, 'employee_option', lambda *a: None)
    monkeypatch.setattr(client, 'find_users', lambda *a: {'total': 0, 'rows': []})
    response = Mock(status_code=200, headers={'Content-Type': 'application/json'})
    response.json.return_value = {'code': 500, 'msg': 'hashid required'}
    session = Mock()
    session.post.return_value = response
    with pytest.raises(AssertionError, match='hashid'):
        client.create_user(session, Mock())
    assert session.post.call_count == 1
    assert json.loads((state / 'phone.json').read_text())['next_phone'] == '0650100002'
    assert json.loads((state / 'add_user_attempts.json').read_text())['0650100001']['status'] == 'api_error_unconfirmed'
    with pytest.raises(AssertionError, match='未确认'):
        client.create_user(session, Mock())
    assert session.post.call_count == 1


@pytest.mark.parametrize('code,expected_calls', [(9999, 2), (401, 1)])
def test_only_captcha_failure_retries_authentication(monkeypatch, code, expected_calls):
    monkeypatch.setattr(client, 'credentials', lambda: ('OB5', 'dummy'))
    monkeypatch.setattr(client, 'assert_thailand_page', lambda *a: None)
    auth = Mock(side_effect=[AssertionError(f"业务登录失败：{{'code': {code}}}"), None])
    monkeypatch.setattr(client, 'authenticate_admin', auth)
    session = Mock()
    session.get.return_value.status_code = 200
    if code == 9999:
        client.authenticate_thailand(session, Mock())
    else:
        with pytest.raises(AssertionError):
            client.authenticate_thailand(session, Mock())
    assert auth.call_count == expected_calls
