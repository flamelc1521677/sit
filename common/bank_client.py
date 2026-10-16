"""Save the primary bank card and preserve the user's other bank cards."""
import json
import os
import re
from pathlib import Path

from common.admin_client import ADMIN_URL
from common.bank_sequence import reserve_bank_card
from common.identity_client import checked_json
from common.sso_login import TIMEOUT
from common.state_store import ROOT, write_json


def parse_bank_cards(html):
    # The page embeds a JSON array inside a JSON-escaped string literal.
    match = re.search(r'var\s+bankList\s*=\s*("(?:[^"\\]|\\.)*")\s*;', html)
    assert match, "用户详情未包含银行卡列表，停止保存"
    cards = json.loads(json.loads(match.group(1)))
    assert isinstance(cards, list), "银行卡列表格式错误"
    return cards


def read_bank_cards(session, user):
    response = session.get(ADMIN_URL + "/user/user/detail/" + str(user["id"]),
                           params={"appOwnership": user["appOwnership"]}, timeout=TIMEOUT)
    assert response.status_code == 200, "银行卡详情读取失败"
    cards = parse_bank_cards(response.text)
    for card in cards:
        assert card.get("uuid") == user["uuid"], "银行卡 UUID 与目标用户不一致"
        assert card.get("appOwnership") == user["appOwnership"], "银行卡应用归属不一致"
    return cards


def build_bank_payload(user, cards, card_number):
    payload = []
    # Preserve all existing rows, IDs and flags; plaintext is what the page submits in edit mode.
    for card in cards:
        row = {key: card[key] for key in ("id", "accountType", "selectedFlag", "verityStatus") if key in card}
        row.update({"uuid": user["uuid"], "appOwnership": user["appOwnership"],
                    "bankNameId": str(card.get("bankNameId", "")),
                    "bankCardNum": card.get("bankCardPlainNum", "")})
        payload.append(row)
    if not payload:
        payload.append({"uuid": user["uuid"], "appOwnership": user["appOwnership"], "selectedFlag": "0"})
    payload[0].update({"bankNameId": "37006", "bankCardNum": card_number})
    for row in payload:
        assert row.get("bankCardNum") and row.get("bankNameId"), "其他银行卡缺少可编辑明文或银行 ID，停止保存"
    return payload


def save_and_verify_bank_card(session, user, phone, database_checks):
    print("[银行卡 1/3] 读取当前银行卡，保留其他卡记录", flush=True)
    existing = read_bank_cards(session, user)
    # Validate preservation before consuming a sequence number.
    build_bank_payload(user, existing, "1254112541000002")
    state = Path(os.getenv("BANK_CARD_STATE_FILE", str(ROOT / ".state" / "bank_card.json")))
    card_number = reserve_bank_card(state)
    payload = build_bank_payload(user, existing, card_number)
    print(f"[银行卡 2/3] 保存银行 37006，卡号尾号 {card_number[-4:]}；下一号码已持久化", flush=True)
    response = session.post(ADMIN_URL + "/user/user/saveBankCardInfo", json=payload,
                            timeout=TIMEOUT, allow_redirects=False)
    saved = checked_json(response, "银行卡保存")
    assert any(word in str(saved.get("msg", "")) for word in ("成功", "success")), "银行卡保存响应缺少成功文案"
    result = {"user_id": user["id"], "phone": phone, "bankNameId": "37006", "card_last4": card_number[-4:],
              "code": saved.get("code"), "msg": saved.get("msg"), "readback_verified": False}
    write_json(ROOT / ".state" / "last_bank_result.json", result)
    actual = read_bank_cards(session, user)
    matches = [card for card in actual if str(card.get("bankNameId")) == "37006"
               and card.get("bankCardPlainNum") == card_number]
    assert len(matches) == 1, "银行卡回查未唯一匹配提交的银行和卡号"
    if existing:
        assert str(matches[0].get("id")) == str(existing[0].get("id")), "第一张银行卡记录 ID 发生变化"
    others = {str(card["id"]): card for card in actual if card.get("id")}
    for old in existing[1:]:
        persisted = others.get(str(old["id"]))
        assert persisted, "其他银行卡记录丢失"
        for key in ("bankNameId", "bankCardPlainNum", "selectedFlag"):
            assert str(persisted.get(key)) == str(old.get(key)), f"其他银行卡的 {key} 被修改"
    database_checks.bank_card(user, card_number, matches[0])
    result["readback_verified"] = True
    write_json(ROOT / ".state" / "last_bank_result.json", result)
    print(f"[银行卡 3/3] 保存及回查通过：code={saved.get('code')}，msg={saved.get('msg')}", flush=True)
    return result
