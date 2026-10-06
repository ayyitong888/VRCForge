from copy import deepcopy
import json
import sqlite3
import uuid

import agent_scan_rescan as rescan
import agent_scan_result_store as stores
from agent_tool_result_reader import (
    bind_tool_result_context,
    read_tool_result,
    result_continuation,
    retain_model_information,
)
from runtime_planner_service import sanitize_planner_observation_text
import pytest


def _save_scan(project, store, tool, arguments, result):
    step = {"index": 0, "actionId": uuid.uuid4().hex, "tool": tool,
            "status": "executed", "result": result}
    step["resultRead"] = result_continuation(
        "owner", "original", str(project), step, sanitize_planner_observation_text,
    )
    metadata = store.capture("owner", "original", project, step, arguments)
    step["scanSnapshot"] = metadata
    return step, metadata["resultRef"]


def test_changed_rescan_flows_through_public_reader_and_model_projection(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = stores.ScanResultStore(tmp_path / "store")
    arguments = {"controllerPath": "Assets/Avatar.controller"}
    old_result = {"ok": True, "count": 1, "rows": [{"name": "old"}]}
    step, ref = _save_scan(project, store, "vrcforge_scan_fx_animator", arguments, old_result)
    fresh = {"ok": True, "count": 1, "rows": [{"name": "PRIVATE_DETAIL_MARKER"}]}
    scan_calls = []

    def scan(*_):
        scan_calls.append(1)
        return deepcopy(fresh)

    monkeypatch.setattr(rescan, "direct_scan", scan)

    with bind_tool_result_context("owner", "next", str(project), [], snapshot_lookup=store.lookup):
        page = read_tool_result({"resultRef": ref}, sanitize=sanitize_planner_observation_text)
        replacement_page = read_tool_result(
            {"resultRef": page["replacementRef"], "jsonPointer": "/rows", "limit": 1},
            sanitize=sanitize_planner_observation_text,
        )

    assert page["status"] == "replaced"
    assert page["expired"] is True
    assert page["replacementSummary"]["detailsOmitted"] is True
    assert page["replacementRef"]
    assert "PRIVATE_DETAIL_MARKER" not in str(page)
    assert replacement_page["items"][0]["value"] == fresh["rows"][0]
    assert len(scan_calls) == 1
    replacement_raw = store._connection
    with replacement_raw() as db:
        replacement = json.loads(db.execute(
            "SELECT raw FROM snapshots WHERE ref=?", (page["replacementRef"],)
        ).fetchone()[0])["step"]
    assert "modelInformation" not in replacement
    assert "modelInformationRead" not in replacement
    legacy_helper_result = retain_model_information(
        "owner", "next", str(project), replacement,
        {"text": json.dumps(fresh), "sourceCompleteness": []},
        sanitize_planner_observation_text,
    )
    assert {"modelInformation", "modelInformationRead"}.issubset(legacy_helper_result)
    assert step["result"] == old_result


def test_nonwhitelisted_reference_stays_current_turn_only_in_public_reader(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = stores.ScanResultStore(tmp_path / "store")
    arguments = {"avatarPath": "Assets/Avatar.prefab"}
    step, ref = _save_scan(
        project, store, "vrcforge_scan_avatar_parameters", arguments,
        {"ok": True, "parameters": [{"name": "Param"}]},
    )
    monkeypatch.setattr(rescan, "direct_scan", lambda *_: (_ for _ in ()).throw(
        AssertionError("non-whitelisted scans must not be repeated")
    ))

    with bind_tool_result_context("owner", "next", str(project), [], snapshot_lookup=store.lookup):
        page = read_tool_result({"resultRef": ref}, sanitize=sanitize_planner_observation_text)

    assert page["status"] == "needs_rescan"
    assert page["reason"] == "current_turn_only"
    assert page["items"] == []
    assert step["result"]["parameters"][0]["name"] == "Param"


def test_capture_translates_sqlite_error_at_store_boundary(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = stores.ScanResultStore(tmp_path / "store")
    step = {"index": 0, "tool": "vrcforge_scan_fx_animator", "result": {"ok": True}}

    def unavailable(*, create=False):
        raise sqlite3.OperationalError("locked result store")

    monkeypatch.setattr(store, "_connection", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        store._capture("owner", "turn", project, step, {})
    with pytest.raises(ValueError, match="storage is unavailable"):
        store.capture("owner", "turn", project, step, {})


def test_receipt_reservation_failure_happens_before_rescan(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = stores.ScanResultStore(tmp_path / "store")
    args = {"controllerPath": "Assets/Avatar.controller"}
    _, ref = _save_scan(project, store, "vrcforge_scan_fx_animator", args,
                        {"ok": True, "count": 1})
    _, padding_ref = _save_scan(project, store, "vrcforge_scan_fx_animator", args,
                                {"ok": True, "count": 2})
    oversized = json.dumps({"padding": "x" * (16 * 1024 * 1024)}, separators=(",", ":"))
    with store._connection() as db:
        db.execute("INSERT INTO scan_rescan_checks VALUES (?, ?, ?, ?, ?)",
                   ("other-owner", "other-turn", padding_ref, 0, oversized))
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *_: calls.append(1) or {"ok": True, "count": 1})

    response = store.lookup("owner", "next", project, ref)

    assert response["reason"] == "comparison_store_unavailable"
    assert calls == []


def test_generation_change_between_cas_and_receipt_is_rejected(tmp_path, monkeypatch):
    result = {"ok": True, "count": 1}
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *_: calls.append(1) or deepcopy(result))

    def run_case(root, *, replay_old_receipt_helper):
        root.mkdir()
        project = root / "project"
        project.mkdir()
        store = stores.ScanResultStore(root / "store")
        args = {"controllerPath": "Assets/Avatar.controller"}
        _, ref = _save_scan(project, store, "vrcforge_scan_fx_animator", args, result)
        verifier = store._rescan
        original_owned = verifier._owned
        owned_calls = []

        def mutate_after_final_read(session, turn, project_root, result_ref):
            latest = original_owned(session, turn, project_root, result_ref)
            owned_calls.append(result_ref)
            if len(owned_calls) == 2:
                store.begin_mutation(project, "shell")
                store.end_mutation(project, "shell")
            return latest

        monkeypatch.setattr(verifier, "_owned", mutate_after_final_read)
        if replay_old_receipt_helper:
            new_receipt = verifier._receipt

            def old_receipt(session, turn, result_ref, generation, result=None, **kwargs):
                # The previous helper persisted the verdict without the project CAS.
                return new_receipt(session, turn, result_ref, generation, result)

            monkeypatch.setattr(verifier, "_receipt", old_receipt)
        response = store.lookup("owner", "next", project, ref)
        assert len(owned_calls) == 2
        return response

    legacy = run_case(tmp_path / "old", replay_old_receipt_helper=True)
    with pytest.raises(AssertionError):
        assert legacy.get("expired") is True and legacy.get("reason") == "mutation_during_verification"

    current = run_case(tmp_path / "new", replay_old_receipt_helper=False)
    assert current.get("expired") is True
    assert current.get("reason") == "mutation_during_verification"
    assert len(calls) == 2
