from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

import agent_scan_result_store as store_module
from agent_scan_result_store import ScanResultStore
from agent_tool_result_reader import result_reference


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    asset = root / "Assets" / "Avatar" / "FX.controller"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"controller-v1")
    asset.with_name(asset.name + ".meta").write_text("guid: stable\n", encoding="utf-8")
    return root, asset


@pytest.fixture
def capture_data(project):
    root, asset = project
    step = {
        "index": 0,
        "actionId": "action-1",
        "tool": "vrcforge_scan_fx_animator",
        "status": "ok",
        "result": {"owner_model": {"text": "full original payload", "rows": [1, 2, 3]}},
    }
    arguments = {
        "controllerPath": "Assets/Avatar/FX.controller",
        "absolutePath": str(asset),
        "outsidePath": str(project[0].parent / "secret.txt"),
        "traversalPath": "Assets/../secret.txt",
    }
    return root, step, arguments


def store_at(tmp_path):
    return ScanResultStore(tmp_path / "store")


def capture(store, data, *, session="session-a", turn="turn-a"):
    project_root, step, arguments = data
    return store.capture(session, turn, str(project_root), step, arguments)


def test_capture_and_same_turn_lookup_preserve_full_step_and_owner_payload(tmp_path, capture_data):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)

    assert metadata["capturedAt"]
    assert metadata["scope"] == "current_turn"
    assert metadata["freshness"] == "unverified"
    assert metadata["persisted"] is True
    assert metadata["scanScope"]["tool"] == "vrcforge_scan_fx_animator"
    assert metadata["scanScope"]["assetPaths"] == ["Assets/Avatar/FX.controller"]
    assert metadata["scanScope"]["dependencyCoverage"] == "unverified"
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"]) == {
        "step": capture_data[1]
    }


def test_capture_is_idempotent_but_rejects_same_ref_with_different_snapshot(tmp_path, capture_data):
    store = store_at(tmp_path)
    first = capture(store, capture_data)
    second = capture(store, capture_data)
    assert second == first

    changed = dict(capture_data[1], result={"different": True})
    with pytest.raises(ValueError, match="collision"):
        store.capture("session-a", "turn-a", str(capture_data[0]), changed, capture_data[2])


def test_unknown_and_foreign_owner_or_project_are_not_disclosed(tmp_path, capture_data):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), "unknown") is None
    assert store.lookup("session-b", "turn-a", str(capture_data[0]), metadata["resultRef"]) is None
    other_project = tmp_path / "other"
    other_project.mkdir()
    assert store.lookup("session-a", "turn-a", str(other_project), metadata["resultRef"]) is None


def test_cross_turn_lookup_is_structured_expiry_not_failure(tmp_path, capture_data):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    result = store.lookup("session-a", "turn-b", str(capture_data[0]), metadata["resultRef"])
    assert result["expired"] is True
    assert result["reason"] == "core_scope_unavailable"
    assert result["rescanScope"] == metadata["scanScope"]
    assert result["capturedAt"] == metadata["capturedAt"]
    assert "ok" not in result


def test_process_restart_expires_before_cross_turn_check(tmp_path, capture_data, monkeypatch):
    first_store = store_at(tmp_path)
    metadata = capture(first_store, capture_data)
    monkeypatch.setattr(store_module, "_PROCESS_BOOT_ID", "new-process")
    restarted = store_at(tmp_path)
    result = restarted.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])
    assert result["expired"] is True
    assert result["reason"] == "runtime_observation_gap"


def test_asset_change_expires_terminally_even_after_bytes_are_restored(tmp_path, capture_data):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    asset = capture_data[0] / "Assets/Avatar/FX.controller"
    original = asset.read_bytes()
    asset.write_bytes(b"controller-v2")
    changed = store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])
    assert changed["expired"] is True
    assert changed["reason"] == "asset_fingerprint_changed"

    asset.write_bytes(original)
    still_expired = store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])
    assert still_expired["expired"] is True
    assert still_expired["reason"] == "asset_fingerprint_changed"


def test_project_write_invalidation_is_terminal_and_survives_restart(tmp_path, capture_data):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    asset = capture_data[0] / "Assets/Avatar/FX.controller"
    original = asset.read_bytes()
    store.invalidate(str(capture_data[0]), ["Assets/Avatar/FX.controller"], "write_started")
    asset.write_bytes(b"temporary mutation")
    asset.write_bytes(original)

    restarted = store_at(tmp_path)
    result = restarted.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])
    assert result["expired"] is True
    assert result["reason"] == "write_started"


def test_unscoped_project_write_invalidates_every_snapshot_in_project(tmp_path, capture_data):
    store = store_at(tmp_path)
    first = capture(store, capture_data)
    second_step = dict(capture_data[1], id="action-2")
    second = store.capture("session-b", "turn-2", str(capture_data[0]), second_step, capture_data[2])
    store.invalidate(str(capture_data[0]), None, "unknown_write_scope")
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), first["resultRef"])["reason"] == "unknown_write_scope"
    assert store.lookup("session-b", "turn-2", str(capture_data[0]), second["resultRef"])["reason"] == "unknown_write_scope"


def test_begin_mutation_expires_old_records_and_blocks_capture_until_end(tmp_path, capture_data):
    store = store_at(tmp_path)
    before = capture(store, capture_data)
    store.begin_mutation(str(capture_data[0]), "write_started")
    expired = store.lookup("session-a", "turn-a", str(capture_data[0]), before["resultRef"])
    assert expired["expired"] is True
    assert expired["reason"] == "write_started"

    during = store.capture("session-a", "turn-b", str(capture_data[0]), capture_data[1], capture_data[2])
    assert during["expired"] is True
    assert during["reason"] == "mutation_in_progress"
    assert store.lookup("session-a", "turn-b", str(capture_data[0]), during["resultRef"])["reason"] == "mutation_in_progress"

    store.end_mutation(str(capture_data[0]), "write_finished")
    after = capture(store, capture_data, turn="turn-c")
    assert store.lookup("session-a", "turn-c", str(capture_data[0]), after["resultRef"]) == {
        "step": capture_data[1]
    }
    assert store.lookup("session-a", "turn-b", str(capture_data[0]), during["resultRef"])["reason"] == "mutation_in_progress"


def test_pending_mutation_survives_store_restart_and_end_never_goes_negative(tmp_path, capture_data, monkeypatch):
    store = store_at(tmp_path)
    prior = capture(store, capture_data)
    store.begin_mutation(str(capture_data[0]), "shell_write_started")
    restarted_id = "restarted-process"
    monkeypatch.setattr(store_module, "_PROCESS_BOOT_ID", restarted_id)
    restarted = store_at(tmp_path)
    during = restarted.capture("session-a", "turn-b", str(capture_data[0]), capture_data[1], capture_data[2])
    assert during["expired"] is True
    assert during["reason"] == "mutation_in_progress"
    assert restarted.lookup("session-a", "turn-a", str(capture_data[0]), prior["resultRef"])["expired"] is True

    restarted.end_mutation(str(capture_data[0]), "shell_write_finished")
    restarted.end_mutation(str(capture_data[0]), "extra_end")
    after = capture(restarted, capture_data, turn="turn-c")
    assert restarted.lookup("session-a", "turn-c", str(capture_data[0]), after["resultRef"]) == {
        "step": capture_data[1]
    }


def test_nested_mutation_counter_keeps_project_blocked_until_all_ends(tmp_path, capture_data):
    store = store_at(tmp_path)
    store.begin_mutation(str(capture_data[0]), "outer")
    store.begin_mutation(str(capture_data[0]), "inner")
    store.end_mutation(str(capture_data[0]), "inner_done")
    metadata = capture(store, capture_data)
    assert metadata["expired"] is True
    assert metadata["reason"] == "mutation_in_progress"
    store.end_mutation(str(capture_data[0]), "outer_done")
    metadata = capture(store, capture_data, turn="turn-after")
    assert "expired" not in metadata


def test_empty_project_invalidation_and_mutation_are_global(tmp_path, capture_data):
    store = store_at(tmp_path)
    first = capture(store, capture_data)
    other_project = tmp_path / "other-project"
    other_asset = other_project / "Assets" / "FX.controller"
    other_asset.parent.mkdir(parents=True)
    other_asset.write_bytes(b"other")
    other_asset.with_name(other_asset.name + ".meta").write_text("guid: other\n", encoding="utf-8")
    other_step = dict(capture_data[1], id="other-project-action")
    second = store.capture("session-b", "turn-b", str(other_project), other_step, {"controllerPath": "Assets/FX.controller"})

    store.invalidate("", ["Assets/Unrelated.asset"], "unknown_project_write")
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), first["resultRef"])["reason"] == "unknown_project_write"
    assert store.lookup("session-b", "turn-b", str(other_project), second["resultRef"])["reason"] == "unknown_project_write"

    store.begin_mutation("", "unknown_shell_write")
    blocked = store.capture("session-b", "turn-c", str(other_project), other_step, {"controllerPath": "Assets/FX.controller"})
    assert blocked["expired"] is True
    assert blocked["reason"] == "mutation_in_progress"
    store.end_mutation("", "unknown_shell_finished")
    clear = store.capture("session-b", "turn-d", str(other_project), other_step, {"controllerPath": "Assets/FX.controller"})
    assert "expired" not in clear


def test_unverified_dependency_closure_expires_for_nonintersecting_project_write(tmp_path, capture_data):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    store.invalidate(str(capture_data[0]), ["Assets/Other/Material.mat"], "project_write")
    result = store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])
    assert result["expired"] is True
    assert result["reason"] == "project_write"


def test_missing_asset_fingerprint_expires_conservatively(tmp_path, capture_data):
    store = store_at(tmp_path)
    arguments = {"controllerPath": "Assets/Missing.controller"}
    metadata = store.capture("session-a", "turn-a", str(capture_data[0]), capture_data[1], arguments)
    result = store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])
    assert result["expired"] is True
    assert result["reason"] == "fingerprint_unavailable"


def test_untrusted_paths_are_not_fingerprinted_or_opened(tmp_path, capture_data):
    store = store_at(tmp_path)
    outside = capture_data[0].parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    metadata = capture(store, capture_data)
    assert metadata["scanScope"]["assetPaths"] == ["Assets/Avatar/FX.controller"]
    outside.write_text("changed secret", encoding="utf-8")
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"]) == {
        "step": capture_data[1]
    }


def test_delete_removes_only_requested_session(tmp_path, capture_data):
    store = store_at(tmp_path)
    own = capture(store, capture_data)
    other_step = dict(capture_data[1], id="other-action")
    other = store.capture("session-b", "turn-b", str(capture_data[0]), other_step, capture_data[2])
    store.delete("session-a")
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), own["resultRef"]) is None
    assert store.lookup("session-b", "turn-b", str(capture_data[0]), other["resultRef"]) == {"step": other_step}


def test_constructor_and_empty_delete_do_not_create_storage(tmp_path):
    root = tmp_path / "not-created"
    store = ScanResultStore(root)
    store.delete("missing")
    assert not root.exists()


def test_owned_reader_reference_is_preserved_with_original_project_spelling(tmp_path, capture_data):
    store = store_at(tmp_path)
    project_root = str(capture_data[0]) + "/."
    step = dict(capture_data[1])
    ref = result_reference("session-a", "turn-a", project_root, step)
    step["resultRead"] = {"resultRef": ref}
    metadata = store.capture("session-a", "turn-a", project_root, step, capture_data[2])
    assert metadata["resultRef"] == ref
    assert store.lookup("session-a", "turn-a", project_root, ref) == {"step": step}
    assert store.lookup("session-a", "turn-b", project_root, ref)["reason"] == "core_scope_unavailable"


def test_mismatched_owned_reader_reference_is_rejected(tmp_path, capture_data):
    store = store_at(tmp_path)
    step = dict(capture_data[1], resultRead={"resultRef": "result_" + "0" * 32})
    with pytest.raises(ValueError, match="reference"):
        store.capture("session-a", "turn-a", str(capture_data[0]), step, capture_data[2])


@pytest.mark.parametrize("mutation_project", ["same", "global"])
def test_completed_mutation_between_scan_and_capture_expires_opaque_result(tmp_path, capture_data, mutation_project):
    store = store_at(tmp_path)
    project_root = str(capture_data[0])
    generation = store.generation(project_root)
    writer = store_at(tmp_path)
    mutation_root = project_root if mutation_project == "same" else ""
    writer.begin_mutation(mutation_root, "write_started")
    writer.end_mutation(mutation_root, "write_finished")
    metadata = store.capture("session-a", "turn-a", project_root, capture_data[1], {}, expected_generation=generation)
    assert metadata["reason"] == "mutation_during_scan"
    assert store.lookup("session-a", "turn-a", project_root, metadata["resultRef"])["reason"] == "mutation_during_scan"
    after = store.capture("session-a", "turn-b", project_root, capture_data[1], {},
                          expected_generation=store.generation(project_root))
    assert "expired" not in after


def test_generation_sums_matching_project_and_global_epochs_only(tmp_path, capture_data):
    store = store_at(tmp_path)
    project_root = str(capture_data[0])
    assert store.generation(project_root) == 0
    assert not (tmp_path / "store").exists()
    store.begin_mutation(project_root, "local_begin")
    store.end_mutation(project_root, "local_end")
    assert store.generation(project_root) == 2
    store.begin_mutation("", "global_begin")
    store.end_mutation("", "global_end")
    assert store.generation(project_root) == 4
    store.begin_mutation(str(tmp_path / "unrelated"), "other_begin")
    assert store.generation(project_root) == 4
    assert store_at(tmp_path).generation(project_root) == 4


def test_capture_fingerprints_are_computed_with_shared_lock_and_transaction(tmp_path, capture_data, monkeypatch):
    lock = threading.RLock()
    store = ScanResultStore(tmp_path / "store", lock)
    original = store_module._fingerprints

    def checked(project_root, paths):
        assert lock._is_owned()
        connection = sqlite3.connect(store._path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                connection.execute("BEGIN IMMEDIATE")
        finally:
            connection.close()
        return original(project_root, paths)

    monkeypatch.setattr(store_module, "_fingerprints", checked)
    assert capture(store, capture_data)["persisted"] is True


@pytest.mark.parametrize("corrupt", ["not-json", json.dumps({"Assets/Avatar/FX.controller": {"asset": {"sha256": "bad"}}})])
def test_malformed_fingerprint_expires_conservatively(tmp_path, capture_data, corrupt):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    with sqlite3.connect(store._path) as connection:
        connection.execute("UPDATE snapshots SET fingerprints=? WHERE ref=?", (corrupt, metadata["resultRef"]))
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])["reason"] == "fingerprint_unavailable"


@pytest.mark.parametrize("missing", [False, True])
def test_changed_or_missing_meta_expires_current_turn(tmp_path, capture_data, missing):
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    meta = capture_data[0] / "Assets/Avatar/FX.controller.meta"
    if missing:
        meta.unlink()
    else:
        meta.write_text("guid: changed\n", encoding="utf-8")
    expected = "fingerprint_unavailable" if missing else "asset_fingerprint_changed"
    assert store.lookup("session-a", "turn-a", str(capture_data[0]), metadata["resultRef"])["reason"] == expected


def test_untrusted_paths_never_open_outside_project_assets(tmp_path, capture_data, monkeypatch):
    original = Path.open
    root = capture_data[0].resolve()

    def guarded(path, *args, **kwargs):
        assert path.resolve().is_relative_to(root / "Assets")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    store = store_at(tmp_path)
    metadata = capture(store, capture_data)
    assert store.lookup("session-a", "turn-a", str(root), metadata["resultRef"]) == {"step": capture_data[1]}
