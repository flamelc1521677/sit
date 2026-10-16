"""Reserve the next real calendar date used in a synthetic test CURP."""
import json
from datetime import date, timedelta
from pathlib import Path

from common.phone_sequence import locked
from common.state_store import write_json


def reserve_identity(path):
    path = Path(path)
    with locked(path.with_suffix(".lock")):
        current = date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["next_date"]) if path.exists() else date(1975, 1, 1)
        # Stop before a 2-digit year could wrap and reuse previously allocated numbers.
        if not date(1975, 1, 1) <= current < date(2075, 1, 1):
            raise ValueError("证件日期超出 1975-01-01 至 2074-12-31 范围")
        document_no = "MAPA" + current.strftime("%y%m%d") + "HMCRND05"
        write_json(path, {"next_date": (current + timedelta(days=1)).isoformat()})
        return document_no, current
