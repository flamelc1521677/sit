import json
from unittest.mock import Mock

import pytest

from common.identity_client import latest_user


@pytest.mark.parametrize("stored_line,api_line,owner", [
    ("15666226611", "15666226611", "ob4-mx"),
    ("15666226611", "mx-1wp-15666226611", "ob4-mx"),
    ("ob5wa11111", "ob5wa11111", "ob5-mx"),
])
def test_latest_user_accepts_exact_line_or_known_display_name(tmp_path, stored_line, api_line, owner):
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"phone": "0658900209", "employeeLine": stored_line}))
    session = Mock()
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {"total": 1, "rows": [{
        "id": 862, "uuid": "fixture-uuid", "appOwnership": "fixture-app",
        "employeeLine": api_line, "employeeName": owner,
    }]}
    session.get.return_value.status_code = 200
    session.get.return_value.text = '<input name="uuid" value="fixture-uuid"><input name="appOwnership" value="fixture-app">'
    assert latest_user(session, state)[0]["id"] == 862


@pytest.mark.parametrize("api_line,owner", [
    ("mx-2wp-15666226612", "ob4-mx"),
    ("unknown-15666226611", "ob4-mx"),
    ("mx-1wp-15666226611", "ob5-mx"),
])
def test_latest_user_rejects_other_employee_or_line(tmp_path, api_line, owner):
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"phone": "0658900209", "employeeLine": "15666226611"}))
    session = Mock()
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {"total": 1, "rows": [{
        "id": 862, "uuid": "fixture-uuid", "appOwnership": "fixture-app",
        "employeeLine": api_line, "employeeName": owner,
    }]}
    with pytest.raises(AssertionError):
        latest_user(session, state)
    session.get.assert_not_called()
