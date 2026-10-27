import json
from unittest.mock import Mock

import pytest

from common import state_store


def test_transient_windows_file_lock_retries_only_atomic_replace(tmp_path,monkeypatch):
    original=state_store.os.replace
    calls=[]
    def replace(source,target):
        calls.append((source,target))
        if len(calls)<3:
            raise PermissionError('temporary lock')
        original(source,target)
    monkeypatch.setattr(state_store.os,'replace',replace)
    monkeypatch.setattr(state_store.time,'sleep',Mock())
    target=tmp_path/'state.json'
    state_store.write_json(target,{'status':'api_success'})
    assert len(calls)==3 and len({str(c[0]) for c in calls})==1
    assert json.loads(target.read_text())=={'status':'api_success'}


def test_permanent_lock_preserves_old_state_and_raises(tmp_path,monkeypatch):
    target=tmp_path/'state.json'
    target.write_text('{"status":"request_sent"}')
    replacement=Mock(side_effect=PermissionError('locked'))
    monkeypatch.setattr(state_store.os,'replace',replacement)
    monkeypatch.setattr(state_store.time,'sleep',Mock())
    with pytest.raises(PermissionError):
        state_store.write_json(target,{'status':'api_success'})
    assert replacement.call_count==8
    assert json.loads(target.read_text())=={'status':'request_sent'}
    assert list(tmp_path.iterdir())==[target]
