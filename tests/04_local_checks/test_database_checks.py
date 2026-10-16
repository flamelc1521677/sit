from unittest.mock import Mock

import pytest

from common.database import Database, schema_name
from common.database_checks import DatabaseChecks


def checks_with_rows(rows):
    database = Mock()
    database.select.return_value = rows
    checks = DatabaseChecks(database)
    check = checks.check
    checks.check = lambda label, assertion: check(label, assertion, timeout=0)
    return checks


def test_mutation_is_blocked_before_execution():
    connection = Mock()
    with pytest.raises(ValueError, match="只允许 SELECT"):
        Database(connection).select("UPDATE anything SET status=1")
    connection.cursor.assert_not_called()


def test_schema_identifier_cannot_inject_sql(monkeypatch):
    monkeypatch.setenv("SIT_DB_BUSINESS_SCHEMA", "off_biz_mex`; DROP TABLE users;")
    with pytest.raises(ValueError):
        schema_name("SIT_DB_BUSINESS_SCHEMA", "off_biz_mex")


def test_disabled_login_account_fails():
    checks = checks_with_rows([{"USER_ID": 1, "STATUS": "1", "DEL_FLAG": "0"}])
    with pytest.raises(AssertionError, match="未启用"):
        checks.login_account("example")
    assert not checks.completed


@pytest.mark.parametrize("changes,message", [
    ({"LOGIN_NAME": "ob5-mx"}, "不属于指定账号"),
    ({"line_account": "other-line"}, "号码与固定配置不一致"),
    ({"status": "1"}, "未启用"),
    ({"STATUS": "1"}, "未启用"),
    ({"DEL_FLAG": "1"}, "已删除"),
])
def test_employee_line_rejects_wrong_owner_or_disabled_config(changes, message):
    row = {"LOGIN_NAME": "ob4-mx", "line_account": "15666226611",
           "status": "0", "STATUS": "0", "DEL_FLAG": "0"}
    row.update(changes)
    checks = checks_with_rows([row])
    with pytest.raises(AssertionError, match=message):
        checks.employee_line_config("56", "15666226611", "ob4-mx")
    assert not checks.completed


def test_employee_line_accepts_enabled_ob4_config():
    checks = checks_with_rows([{"LOGIN_NAME": "ob4-mx", "line_account": "15666226611",
                              "status": "0", "STATUS": "0", "DEL_FLAG": "0"}])
    checks.employee_line_config("56", "15666226611", "ob4-mx")
    assert checks.completed


def test_wrong_bank_is_rejected():
    checks = checks_with_rows([{"ID": 1, "BANK_CODE": "wrong"}])
    with pytest.raises(AssertionError, match="银行 ID"):
        checks.bank_card({"uuid": "test", "appOwnership": "app"}, "1254112541000002", {"id": 1})


def test_identity_cipher_must_change_after_save():
    checks = checks_with_rows([{"AADHAAR_NO": "unchanged"}])
    with pytest.raises(AssertionError, match="未更新"):
        checks.identity({"uuid": "test", "appOwnership": "app"}, "MAPA750101HMCRND05",
                        {"aadhaarNo": "MAPA750101HMCRND05"}, previous_cipher="unchanged")
