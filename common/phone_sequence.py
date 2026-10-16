"""Durable phone reservation, protected against concurrent local test processes."""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def reserve_phone(state_path, start="0658900201"):
    """Persist the next phone BEFORE submitting a create request; never roll back."""
    path = Path(state_path)
    with locked(path.with_suffix(".lock")):
        phone = json.loads(path.read_text(encoding="utf-8"))["next_phone"] if path.exists() else start
        if not isinstance(phone, str) or len(phone) != 10 or not phone.isascii() or not phone.isdigit():
            raise ValueError("手机号状态必须是 10 位数字字符串；请检查状态文件")
        next_phone = str(int(phone) + 1).zfill(10)
        if len(next_phone) != 10:
            raise ValueError("手机号递增超出 10 位范围")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump({"next_phone": next_phone}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
        return phone
