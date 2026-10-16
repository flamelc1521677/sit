"""Reserve one 16-digit test bank card number before sending a save request."""
import json
from pathlib import Path

from common.phone_sequence import locked
from common.state_store import write_json


def reserve_bank_card(path):
    path = Path(path)
    with locked(path.with_suffix(".lock")):
        number = json.loads(path.read_text(encoding="utf-8"))["next_card_number"] if path.exists() else "1254112541000002"
        if not isinstance(number, str) or len(number) != 16 or not number.isascii() or not number.isdigit():
            raise ValueError("银行卡状态必须为 16 位数字字符串")
        following = str(int(number) + 1).zfill(16)
        if len(following) != 16:
            raise ValueError("银行卡递增超出 16 位范围")
        write_json(path, {"next_card_number": following})
        return number
