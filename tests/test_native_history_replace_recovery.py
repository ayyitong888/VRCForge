"""Local persistence retry must not repeat a model request or an executed tool."""
import json
from pathlib import Path

import pytest

import agent_native_history_store as history
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
from runtime_planner_service import RuntimePlannerService


def denied():
    error = PermissionError("sharing violation")
    error.winerror = 32
    return error


def test_load_closes_file_before_decoding(tmp_path, monkeypatch):
    store = history.NativeHistoryStore(tmp_path)
    store.save("s", {"binding": "b"}, {})
    loads = json.loads
    def decode(raw, *args, **kwargs):
        # On Windows replacing an open ordinary read handle is denied.
        store.save("s", {"binding": "b", "updated": True}, {})
        return loads(raw, *args, **kwargs)
    monkeypatch.setattr(history.json, "loads", decode)
    assert store.load("s", "b")["native"] == {"binding": "b"}


def test_temporary_replace_denial_retries_same_bytes(tmp_path, monkeypatch):
    replace = history.os.replace
    attempts = []
    def flaky(source, target):
        attempts.append((Path(source), Path(source).read_bytes()))
        if len(attempts) == 1:
            raise denied()
        return replace(source, target)
    monkeypatch.setattr(history.os, "replace", flaky)
    store = history.NativeHistoryStore(tmp_path)
    store.save("s", {"binding": "b"}, {})
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert store.load("s", "b")["native"] == {"binding": "b"}
    assert not list(tmp_path.glob(".native-*"))


def test_persistent_denial_is_bounded_and_classified_as_local(tmp_path, monkeypatch):
    attempts = []
    def blocked(*args):
        attempts.append(args)
        raise denied()
    monkeypatch.setattr(history.os, "replace", blocked)
    with pytest.raises(OSError) as caught:
        history.NativeHistoryStore(tmp_path).save("s", {"binding": "b"}, {})
    assert len(attempts) == 3
    assert RuntimePlannerService._planner_failure_code(caught.value) == "native_history_save_failed"
    assert not list(tmp_path.glob(".native-*"))


def test_post_tool_save_retry_keeps_one_tool_execution(tmp_path, monkeypatch):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read", "vrcforge_read_text_file", {"path": "readme.txt"}), finish,
    ])
    replace = history.os.replace
    failed = []
    def flaky(source, target):
        if invoked and not failed and Path(source).name.startswith(".native-"):
            failed.append(True)
            raise denied()
        return replace(source, target)
    monkeypatch.setattr(history.os, "replace", flaky)
    response = run(gateway, tmp_path)
    assert failed and len(invoked) == 1 and len(model.requests) == 2
    assert not response["plan"].get("plannerFailed")
