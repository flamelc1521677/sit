"""Write small local state files atomically without exposing credentials."""
import json
import os
import tempfile
import time
from pathlib import Path

from common.phone_sequence import locked

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows scanners/editors can briefly hold the destination without delete sharing.
        # Retry only this local atomic replacement; never retry a business request here.
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.1 * (attempt + 1), 0.5))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def record_latest_user(phone, employee_line, path=None):
    path = Path(path or ROOT / ".state" / "latest_user.json")
    with locked(path.with_suffix(".lock")):
        write_json(path, {"phone": phone, "employeeLine": employee_line})
