import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from common.bank_client import build_bank_payload, parse_bank_cards
from common.bank_sequence import reserve_bank_card


def test_sequence_persists(tmp_path):
    path = tmp_path / "bank.json"
    assert reserve_bank_card(path) == "1254112541000002"
    assert reserve_bank_card(path) == "1254112541000003"
    assert json.loads(path.read_text())["next_card_number"] == "1254112541000004"


def test_concurrent_numbers_unique(tmp_path):
    path = tmp_path / "bank.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: reserve_bank_card(path), range(4)))
    assert sorted(values) == [f"125411254100000{n}" for n in range(2, 6)]


def test_invalid_state_never_resets(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text('{"next_card_number":1254112541000002}')
    with pytest.raises(ValueError):
        reserve_bank_card(path)


def test_existing_cards_are_preserved():
    user = {"uuid": "test-user", "appOwnership": "test-app"}
    cards = [{"id": 1, "bankNameId": "old", "bankCardPlainNum": "1111", "selectedFlag": "0"},
             {"id": 2, "bankNameId": "other", "bankCardPlainNum": "2222", "selectedFlag": "1"}]
    payload = build_bank_payload(user, cards, "1254112541000002")
    assert len(payload) == 2
    assert payload[0]["id"] == 1 and payload[0]["bankNameId"] == "37006"
    assert payload[1]["id"] == 2 and payload[1]["bankCardNum"] == "2222"
    assert payload[1]["bankNameId"] == "other" and payload[1]["selectedFlag"] == "1"
    assert cards[0]["bankCardPlainNum"] == "1111"


def test_missing_plaintext_stops_preservation():
    with pytest.raises(AssertionError, match="缺少可编辑明文"):
        build_bank_payload({"uuid": "u", "appOwnership": "app"},
                           [{"id": 1}, {"id": 2, "bankNameId": "37006"}], "1254112541000002")


def test_embedded_bank_list_is_parsed_without_javascript():
    cards = [{"id": 1, "bankNameId": "37006", "bankCardPlainNum": "1254112541000002"}]
    html = "var bankList = " + json.dumps(json.dumps(cards)) + ";"
    assert parse_bank_cards(html) == cards
