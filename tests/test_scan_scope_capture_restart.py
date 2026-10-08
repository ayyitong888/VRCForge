import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from agent_scan_result_store import ScanResultStore
from agent_scan_scope_budget import ScopeCaptureBudget
from test_scan_scope_capture_budget import avatar_target, captures, setup_core


def test_store_restart_does_not_repeat_known_timed_out_avatar(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    args = {"avatarPath": avatar_target(state, uuid.uuid4().hex)}
    root = tmp_path / "store"
    first = ScanResultStore(root).start(tmp_path, "vrcforge_scan_wardrobe", args)
    second = ScanResultStore(root).start(tmp_path, "vrcforge_scan_avatar_items", args)
    assert first["proof"]["verified"] is second["proof"]["verified"] is False
    assert second["proof"]["reason"] == "capture_suppressed:unity_core_timeout"
    assert "token" not in second["proof"]
    assert len(captures(state)) == 1


@pytest.mark.parametrize("change", ["instance", "object", "project", "store"])
def test_restart_suppression_does_not_cross_identity(monkeypatch, tmp_path, change):
    state = setup_core(monkeypatch)
    name = avatar_target(state, uuid.uuid4().hex)
    root, project = tmp_path / "store", tmp_path
    args = {"avatarPath": name}
    ScanResultStore(root).start(project, "vrcforge_scan_wardrobe", args)
    if change == "instance":
        state["instance"] = uuid.uuid4().hex
    elif change == "object":
        avatar_target(state, name)
    elif change == "project":
        project = tmp_path / uuid.uuid4().hex
    else:
        root = tmp_path / uuid.uuid4().hex
    ScanResultStore(root).start(project, "vrcforge_scan_wardrobe", args)
    assert len(captures(state)) == 2


def request_context():
    return {"instanceId": uuid.uuid4().hex, "captureRootIdentity": [["avatar", uuid.uuid4().hex, uuid.uuid4().hex]]}, {
        "scanTool": "vrcforge_scan_wardrobe", "arguments": {"avatarPath": uuid.uuid4().hex},
    }


def unverified(reason="unity_core_timeout"):
    return {"schema": "vrcforge.scan_scope.v1", "verified": False, "reason": reason}


def test_fresh_process_reads_only_negative_budget(tmp_path):
    cache = tmp_path / "budget.sqlite3"
    info, request = request_context()
    ScopeCaptureBudget(cache).capture(tmp_path, info, request, unverified)
    # Pytest owns this 10-second child and captured pipe; local temp budget only,
    # no Core/provider/network communication or credentials are used.
    code = """
import json, sys
from agent_scan_scope_budget import ScopeCaptureBudget
def forbidden():
    raise AssertionError('Known slow capture must not run after process restart')
result = ScopeCaptureBudget(sys.argv[1]).capture(sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4]), forbidden)
assert result['verified'] is False and 'token' not in result
assert result['reason'] == 'capture_suppressed:unity_core_timeout'
print('negative-only restart suppression')
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(cache), str(tmp_path), json.dumps(info), json.dumps(request)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "negative-only restart suppression"


@pytest.mark.parametrize("reason", ["core_unavailable", "invalid_scope_response", "scope_capture_identity_unavailable"])
def test_other_transient_or_invalid_failures_are_not_persisted(tmp_path, reason):
    cache = tmp_path / "budget.sqlite3"
    info, request = request_context()
    first = ScopeCaptureBudget(cache).capture(tmp_path, info, request, lambda: unverified(reason))
    expected = {"verified": True, "token": "scope_" + uuid.uuid4().hex}
    second = ScopeCaptureBudget(cache).capture(tmp_path, info, request, lambda: expected)
    assert first["verified"] is False
    assert second == expected
    assert not cache.exists()


def test_valid_proof_is_never_negative_cached(tmp_path):
    cache = tmp_path / "budget.sqlite3"
    info, request = request_context()
    for _ in range(2):
        proof = {"verified": True, "token": "scope_" + uuid.uuid4().hex}
        assert ScopeCaptureBudget(cache).capture(tmp_path, info, request, lambda: proof) == proof
    assert not cache.exists()


@pytest.mark.parametrize("broken", ["directory", "corrupt"])
def test_cache_failure_does_not_block_original_operation(tmp_path, broken):
    cache = tmp_path / "budget.sqlite3"
    if broken == "directory":
        cache.mkdir()
    else:
        cache.write_bytes(b"not sqlite")
    info, request = request_context()
    result = ScopeCaptureBudget(cache).capture(tmp_path, info, request, unverified)
    assert result == unverified()
    valid = {"verified": True, "token": "scope_" + uuid.uuid4().hex}
    assert ScopeCaptureBudget(cache).capture(tmp_path, info, request, lambda: valid) == valid


def test_persisted_failures_are_bounded_and_store_no_scope_or_credentials(tmp_path):
    cache = tmp_path / "budget.sqlite3"
    budget = ScopeCaptureBudget(cache)
    for _ in range(129):
        info, request = request_context()
        budget.capture(tmp_path, info, request, unverified)
    connection = sqlite3.connect(cache)
    try:
        rows = connection.execute("SELECT cache_key, reason FROM capture_failures").fetchall()
    finally:
        connection.close()
    assert len(rows) == 128
    assert all(len(key) == 64 and set(key) <= set("0123456789abcdef") and reason == "unity_core_timeout"
               for key, reason in rows)
    assert str(tmp_path).encode() not in cache.read_bytes()
    expected = hashlib.sha256(json.dumps(ScopeCaptureBudget._key(tmp_path, info, request), separators=(",", ":")).encode()).hexdigest()
    assert any(key == expected for key, _ in rows)


def test_old_core_still_has_zero_unknown_calls_after_restart(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    state["available"] = False
    for _ in range(2):
        proof = ScanResultStore(tmp_path / "store").start(tmp_path, "vrcforge_scan_wardrobe", {"avatarPath": uuid.uuid4().hex})["proof"]
        assert proof["verified"] is False and proof["reason"] == "capability_unavailable"
    assert state["calls"] == []


def test_healthy_b_scope_still_captures_after_restart(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    state["fail"] = False
    args = {"avatarPath": avatar_target(state, uuid.uuid4().hex)}
    for _ in range(2):
        proof = ScanResultStore(tmp_path / "store").start(tmp_path, "vrcforge_scan_wardrobe", args)["proof"]
        assert proof["verified"] is True and proof["dependencyCoverage"] == "complete"
    assert len(captures(state)) == 2
