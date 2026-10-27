"""Thai number isolation, target binding, reviewer separation and payout timezone."""
from datetime import datetime
from unittest.mock import Mock

import pytest

from common.payout_client import database_time
from thailand import client, workflows
from thailand.database_checks import ThailandDatabaseChecks


def test_thai_identity_and_bank_sequences_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(workflows, 'STATE', tmp_path / 'thai')
    assert workflows.reserve_number('identity_number', '1551500010011') == '1551500010011'
    assert workflows.reserve_number('identity_number', '1551500010011') == '1551500010012'
    assert workflows.reserve_number('bank_card', '102519931001') == '102519931001'
    assert workflows.reserve_number('bank_card', '102519931001') == '102519931002'
    assert not (tmp_path / 'identity_date.json').exists()


def test_bound_flow_refuses_changed_latest_user_before_http(tmp_path, monkeypatch):
    monkeypatch.setattr(workflows, 'STATE', tmp_path)
    workflows.write_json(tmp_path / 'latest_user.json', {'country': 'TH', 'uuid': 'other-user', 'user_id': 999})
    monkeypatch.setenv('TH_FLOW_USER_UUID', 'expected-user')
    monkeypatch.setenv('TH_FLOW_USER_ID', '100')
    session = Mock()
    with pytest.raises(AssertionError, match='绑定用户'):
        workflows.target_user(session, Mock())
    session.post.assert_not_called()


def test_bangkok_payout_conversion_ignores_mexico_environment(monkeypatch):
    monkeypatch.setenv('PAYOUT_MERCHANT_TIMEZONE', 'America/Mexico_City')
    assert database_time('2026-10-27 10:00:00', 'Asia/Bangkok', 'Asia/Shanghai') == datetime(2026, 10, 27, 11)


def test_reviewer_never_uses_mexico_approval_credentials(monkeypatch):
    monkeypatch.setattr(client, 'user_value', lambda name: None)
    monkeypatch.setenv('APPROVER_USERNAME', 'ob5-mx')
    monkeypatch.setenv('APPROVER_PASSWORD', 'dummy-mx')
    monkeypatch.delenv('TH_APPROVER_USERNAME', raising=False)
    monkeypatch.delenv('TH_APPROVER_PASSWORD', raising=False)
    with pytest.raises(AssertionError, match='TH_APPROVER_USERNAME'):
        client.reviewer_credentials()


def test_thai_reviewer_cannot_be_the_requester(monkeypatch):
    monkeypatch.setattr(client, 'credentials', lambda: ('chengzi', 'dummy'))
    monkeypatch.setattr(client, 'user_value', {'TH_APPROVER_USERNAME': 'chengzi', 'TH_APPROVER_PASSWORD': 'dummy-review'}.get)
    monkeypatch.setenv('TH_CREDENTIAL_SOURCE', 'local')
    with pytest.raises(AssertionError, match='独立账号'):
        client.reviewer_credentials()


def test_thai_phone_storage_and_display_masks_are_distinct(monkeypatch):
    database = Mock()
    database.select.return_value = [{'ID': 1, 'UUID': 'uuid', 'PHONE': 'cipher', 'PHONE_DESENSITIZE': '0*****0002', 'APP_OWNERSHIP': 'supma', 'EMPLOYEE_LINE': 'ob516772222', 'DEPT_USER_ID': '1593', 'DEPT_ID': 11804}]
    checks = ThailandDatabaseChecks(database)
    monkeypatch.setattr(checks, 'employee_line_config', lambda *a: None)
    user = {'id': 1, 'uuid': 'uuid', 'appOwnership': 'supma', 'phoneEncrypt': 'cipher', 'phoneDesensitize': '065****002'}
    checks.created_user(user, '0650100002', '65', 'ob516772222', 'ob5')
    assert checks.completed
    user['phoneEncrypt'] = 'another-person-cipher'
    with pytest.raises(AssertionError, match='密文'):
        checks.created_user(user, '0650100002', '65', 'ob516772222', 'ob5')


def test_confirmed_occupied_phone_skips_without_create(tmp_path, monkeypatch):
    monkeypatch.setattr(client, 'STATE', tmp_path)
    monkeypatch.setattr(client, 'employee_option', lambda *a: None)
    found = Mock(side_effect=[{'total': 1, 'rows': [{'id': 99}]}, {'total': 0, 'rows': []}])
    monkeypatch.setattr(client, 'find_users', found)
    response = Mock(status_code=200, headers={'Content-Type': 'application/json'})
    response.json.return_value = {'code': 500, 'msg': 'test rejection'}
    session = Mock()
    session.post.return_value = response
    with pytest.raises(AssertionError, match='拒绝'):
        client.create_user(session, Mock())
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs['data']['phone'] == '0650100002'
    records = workflows.load(tmp_path / 'add_user_attempts.json')
    assert records['0650100001']['status'] == 'occupied_existing'
    assert records['0650100001']['create_request_sent'] is False


def test_edited_identity_uses_compose_and_keeps_original_ocr(monkeypatch):
    database = Mock()
    database.select.return_value = [{'AADHAAR_NO': 'edited-cipher', 'ID_CARD_ENCRYPT': '1********0011', 'AADHAAR_NAME': 'Thai OCR name', 'OCR_FORT_PIC': 'image-key', 'OCR_ORIGINAL_CIPHER': 'original-ocr-cipher'}]
    checks = ThailandDatabaseChecks(database)
    monkeypatch.setattr(checks, 'check', lambda label, assertion: assertion())
    info = {'aadhaarNo': '1551500010011', 'fullName': 'Thai OCR name', '_saved_cipher': 'edited-cipher'}
    checks.identity({'uuid': 'u', 'appOwnership': 'supma'}, '1551500010011', info)
    assert 't_mgt_user_compose_info' in database.select.call_args.args[0]
    info['_saved_cipher'] = 'wrong-person-cipher'
    with pytest.raises(AssertionError, match='compose'):
        checks.identity({'uuid': 'u', 'appOwnership': 'supma'}, '1551500010011', info)


def test_thai_review_checks_real_amount_when_expected_amount_is_not_applicable(monkeypatch):
    checks = ThailandDatabaseChecks(Mock())
    monkeypatch.setattr(checks, 'check', lambda label, assertion: assertion())
    row = {'id': 'a', 'status': 1, 'version': 1, 'pre_repayment_plan_header_id': 'h', 'contract_amount': '3500', 'expected_amount': None, 'approval_reason': 'pass', 'auditor_id': '1572', 'audit_time': datetime(2026, 10, 27)}
    monkeypatch.setattr(checks, 'approval_record', lambda _: row)
    monkeypatch.setattr(checks, 'approval_logs', lambda _: [{'id': 'new-pass', 'from_status': 0, 'to_status': 1, 'action_type': 'PASS', 'operator_type': 'USER', 'operator_id': '1572'}])
    before = {'id': 'a', 'version': 0, 'pre_repayment_plan_header_id': 'h'}
    checks.approval_result(before, 'pass', 'pass', '3500', set(), 1572)
    row['contract_amount'] = '6000'
    with pytest.raises(AssertionError, match='合同金额'):
        checks.approval_result(before, 'pass', 'pass', '3500', set(), 1572)
