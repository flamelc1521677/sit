import pytest

from common.identity_client import verify_persisted_identity


def test_plaintext_must_match_requested_number():
    before = {"aadhaarNo": "MAPA920812HMCRND05", "fullName": "TEST NAME"}
    after = {"aadhaarNo": "MAPA750102HMCRND05", "fullName": "TEST NAME"}
    with pytest.raises(AssertionError, match="与请求不一致"):
        verify_persisted_identity(before, after, "MAPA750101HMCRND05")


def test_unchanged_ciphertext_is_rejected():
    info = {"aadhaarNo": "encrypted-old", "fullName": "TEST NAME"}
    with pytest.raises(AssertionError, match="未从 OCR 原号码更新"):
        verify_persisted_identity(info, dict(info), "MAPA750101HMCRND05")


def test_changed_ciphertext_preserves_name():
    before = {"aadhaarNo": "encrypted-old", "fullName": "TEST NAME"}
    after = {"aadhaarNo": "encrypted-new", "fullName": "TEST NAME"}
    verify_persisted_identity(before, after, "MAPA750101HMCRND05")
