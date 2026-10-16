import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from common.identity_sequence import reserve_identity


def test_initial_number_and_persistence(tmp_path):
    path = tmp_path / "identity.json"
    assert reserve_identity(path) == ("MAPA750101HMCRND05", date(1975, 1, 1))
    assert reserve_identity(path)[0] == "MAPA750102HMCRND05"
    assert json.loads(path.read_text())["next_date"] == "1975-01-03"


@pytest.mark.parametrize("current,following", [
    ("1975-01-31", "1975-02-01"), ("1975-12-31", "1976-01-01"),
    ("1976-02-28", "1976-02-29"), ("1976-02-29", "1976-03-01"),
    ("1975-02-28", "1975-03-01")])
def test_calendar_boundaries(tmp_path, current, following):
    path = tmp_path / "identity.json"
    path.write_text(json.dumps({"next_date": current}))
    assert reserve_identity(path)[1].isoformat() == current
    assert json.loads(path.read_text())["next_date"] == following


def test_no_century_wrap(tmp_path):
    path = tmp_path / "identity.json"
    path.write_text('{"next_date":"2075-01-01"}')
    with pytest.raises(ValueError):
        reserve_identity(path)


def test_concurrent_dates_are_unique(tmp_path):
    path = tmp_path / "identity.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: reserve_identity(path)[0], range(4)))
    assert sorted(values) == [f"MAPA75010{day}HMCRND05" for day in range(1, 5)]
