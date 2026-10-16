import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from common.phone_sequence import reserve_phone


def test_preserves_zero_and_persists(tmp_path):
    state = tmp_path / "phone.json"
    assert reserve_phone(state) == "0658900201"
    assert reserve_phone(state) == "0658900202"
    assert json.loads(state.read_text())["next_phone"] == "0658900203"


def test_concurrent_reservations_are_unique(tmp_path):
    state = tmp_path / "phone.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        phones = list(pool.map(lambda _: reserve_phone(state), range(4)))
    assert sorted(phones) == ["0658900201", "0658900202", "0658900203", "0658900204"]


def test_corrupt_state_does_not_reset(tmp_path):
    state = tmp_path / "phone.json"
    state.write_text('{"next_phone": 658900201}')
    with pytest.raises(ValueError):
        reserve_phone(state)
