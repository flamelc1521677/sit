from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from common.payout_client import PayoutAttempt, build_payout_payload, database_time


def bill():
    return {"ID": "bill-test", "STATE": "2", "BIZ_ID": "biz-test", "CONTRACT_ID": "BOR-test",
            "SYSTEM_SOURCE_ID": "BORROWER", "TRADE_AMOUNT": Decimal("2178"),
            "TRADE_TIME": datetime(2026, 10, 16, 10, 56, 7), "MODIFY_TIME": datetime(2026, 10, 16, 10, 57)}


def html():
    inputs = {"id": "bill-test", "ids": "bill-test", "bizId": "biz-test", "contractId": "BOR-test",
              "systemSourceId": "BORROWER", "tradeTime": "2026-10-15 20:56:07",
              "completeTime": "2026-10-15 21:40:40", "arriTradeTime": ""}
    return '<form id="form-TransferBill-edit">' + ''.join(f'<input name="{key}" value="{value}">' for key, value in inputs.items()) + '<select name="state"><option value="1">成功</option><option value="2" selected>失败</option></select><textarea name="updateReason"></textarea></form>'


def test_time_uses_merchant_zone_not_computer_zone(monkeypatch):
    monkeypatch.setenv("PAYOUT_MERCHANT_TIMEZONE", "America/Mexico_City")
    monkeypatch.setenv("PAYOUT_DATABASE_TIMEZONE", "Asia/Shanghai")
    assert database_time("2026-10-15 20:56:07") == datetime(2026, 10, 16, 10, 56, 7)
    assert database_time("") is None


def test_payload_preserves_live_ids_and_trade_time():
    payload = build_payout_payload(html(), bill(), "2026-10-15 21:40:40")
    assert payload["id"] == payload["ids"] == "bill-test"
    assert payload["state"] == "1" and payload["tradeTime"] == "2026-10-15 20:56:07"
    assert payload["arriTradeTime"] == ""


def test_already_successful_bill_cannot_be_approved_again():
    row = bill()
    row["STATE"] = "1"
    with pytest.raises(AssertionError, match="已成功"):
        build_payout_payload(html(), row, "2026-10-15 21:40:40")


def test_wrong_bill_or_business_id_blocks_submit():
    with pytest.raises(AssertionError, match="bizId"):
        build_payout_payload(html().replace('value="biz-test"', 'value="other"'), bill(), "2026-10-15 21:40:40")


def test_concurrent_state_change_blocks_post():
    session = Mock()
    row = bill()
    attempt = PayoutAttempt(session, row, build_payout_payload(html(), row, "2026-10-15 21:40:40"))
    fresh = dict(row, STATE="1")
    with pytest.raises(AssertionError, match="发生变化"):
        attempt.execute(fresh)
    session.post.assert_not_called()


def test_post_once_even_if_api_fails():
    session = Mock()
    response = Mock(status_code=200, headers={"Content-Type": "application/json"})
    response.json.return_value = {"code": 500, "msg": "failure"}
    session.post.return_value = response
    row = bill()
    attempt = PayoutAttempt(session, row, build_payout_payload(html(), row, "2026-10-15 21:40:40"))
    with pytest.raises(AssertionError):
        attempt.execute(row)
    with pytest.raises(AssertionError, match="禁止自动重试"):
        attempt.execute(row)
    assert session.post.call_count == 1
