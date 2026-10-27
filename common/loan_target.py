"""Optional explicit phone for an existing user's loan chain, without rewriting latest_user."""
import json
import os
import re

from common.identity_client import latest_user, lookup_user_by_phone
from common.state_store import ROOT


def loan_target_phone():
    explicit = os.getenv("LOAN_TARGET_PHONE", "").strip()
    phone = explicit or json.loads((ROOT / ".state/latest_user.json").read_text(encoding="utf-8"))["phone"]
    assert re.fullmatch(r"\d{10}", phone), "借款目标手机号必须为 10 位数字"
    return phone


def loan_target_user(session):
    if os.getenv("LOAN_TARGET_PHONE", "").strip():
        phone = loan_target_phone()
        return lookup_user_by_phone(session, phone, os.getenv("LOAN_TARGET_APP_OWNERSHIP")), phone, ""
    return latest_user(session, ROOT / ".state/latest_user.json")
