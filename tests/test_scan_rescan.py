from copy import deepcopy
import json
import sqlite3
import uuid

import pytest

import agent_scan_rescan as rescan
import agent_scan_result_store as stores
from agent_tool_result_reader import bind_tool_result_context, read_tool_result, result_continuation


def sanitize(value, limit=2000):
    return str(value)[:limit]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    project = tmp_path / ("project-" + uuid.uuid4().hex)
    project.mkdir()
    monkeypatch.setattr(stores, "AgentScanScopeClient", lambda _: type("Legacy", (), {
        "capture": lambda *args: {"verified": False, "reason": "capability_unavailable"},
        "validate": lambda *args: {"valid": False, "reason": "capability_unavailable"},
    })())
    store = stores.ScanResultStore(tmp_path / "store")
    return project, store


def save(project, store, tool="vrcforge_scan_fx_animator", arguments=None, payload=None):
    arguments = arguments or {"avatarPath": "Avatar-" + uuid.uuid4().hex}
    if tool.endswith("animation_bindings"):
        arguments.setdefault("bindingView", "details")
    payload = payload or {"ok": True, "rows": [{"guid": uuid.uuid4().hex, "values": [0, 1]}],
                          "complete": False, "nextRequest": {"expectedSnapshotDigest": "a" * 64}}
    step = {"index": 0, "actionId": uuid.uuid4().hex, "tool": tool, "status": "ok", "result": payload}
    step["resultRead"] = result_continuation("owner", "first", str(project), step, sanitize)
    start = store.start(project, tool, arguments)
    metadata = store.capture("owner", "first", project, step, arguments, expected_generation=start)
    return metadata["resultRef"], step, arguments


@pytest.mark.parametrize("tool", sorted(rescan.WHITELIST))
def test_all_whitelisted_references_reuse_complete_immutable_result_once_per_turn(setup, monkeypatch, tool):
    project, store = setup
    ref, step, arguments = save(project, store, tool)
    calls = []
    def scan(root, name, args):
        calls.append((root, name, args))
        assert args["outputPath"] == ""
        assert args.get("refreshAssets", False) is False
        return deepcopy(step["result"])
    monkeypatch.setattr(rescan, "direct_scan", scan)
    for _ in range(3):
        response = store.lookup("owner", "next", project, ref)
        assert response["step"] == step
        assert response["verification"] == "observable_result"
    assert len(calls) == 1
    # A backend restart still uses the receipt in this turn, never a second scan.
    restarted = stores.ScanResultStore(store._path.parent)
    assert restarted.lookup("owner", "next", project, ref)["step"] == step
    assert len(calls) == 1
    assert store.lookup("owner", "later", project, ref)["step"] == step
    assert len(calls) == 2


@pytest.mark.parametrize("tool", sorted(rescan.WHITELIST))
def test_first_turn_paging_does_not_repeat_a_fresh_scan(setup, monkeypatch, tool):
    project, store = setup
    ref, step, _ = save(project, store, tool)
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: pytest.fail("Fresh current-turn result must not rescan"))
    for _ in range(3):
        reply = store.lookup("owner", "first", project, ref)
        assert reply["step"] == step
        assert reply["verification"] == "current_turn"
    store.begin_mutation(project, "shell")
    store.end_mutation(project, "shell")
    assert store.lookup("owner", "first", project, ref)["reason"] == "mutation_after_verification"


def test_first_turn_after_unobserved_restart_still_requires_comparison(setup, monkeypatch):
    project, store = setup
    ref, step, _ = save(project, store)
    monkeypatch.setattr(stores, "_PROCESS_BOOT_ID", uuid.uuid4().hex)
    restarted = stores.ScanResultStore(store._path.parent)
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: calls.append(args) or deepcopy(step["result"]))
    reply = restarted.lookup("owner", "first", project, ref)
    assert reply["verification"] == "observable_result"
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["guid", "values", "complete", "nextRequest"])
def test_every_business_field_and_semantic_order_changes_expire_and_publish_new_ref(setup, monkeypatch, field):
    project, store = setup
    ref, step, _ = save(project, store)
    fresh = deepcopy(step["result"])
    if field == "guid":
        fresh["rows"][0]["guid"] = uuid.uuid4().hex
    elif field == "values":
        fresh["rows"][0]["values"].reverse()
    elif field == "complete":
        fresh["complete"] = True
    else:
        fresh["nextRequest"]["expectedSnapshotDigest"] = "b" * 64
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: calls.append(args) or fresh)
    with sqlite3.connect(store._path) as db:
        original = db.execute("SELECT raw FROM snapshots WHERE ref=?", (ref,)).fetchone()[0]
    with bind_tool_result_context("owner", "next", str(project), [], snapshot_lookup=store.lookup):
        reply = read_tool_result({"resultRef": ref}, sanitize=sanitize)
        assert reply["expired"] is True
        assert reply["status"] == "replaced"
        assert reply["replacementRef"] != ref
        assert reply["replacementSummary"]["detailsOmitted"] is True
        again = read_tool_result({"resultRef": ref}, sanitize=sanitize)
        assert again["replacementRef"] == reply["replacementRef"]
    assert len(calls) == 1
    with sqlite3.connect(store._path) as db:
        assert db.execute("SELECT raw FROM snapshots WHERE ref=?", (ref,)).fetchone()[0] == original
        replacement = json.loads(db.execute("SELECT raw FROM snapshots WHERE ref=?", (reply["replacementRef"],)).fetchone()[0])
        assert replacement["step"]["result"] == fresh


def test_normalization_removes_only_outer_call_noise_not_business_id_or_nested_time():
    left = {"ok": True, "requestId": "one", "jsonPath": "latest-a.json", "generatedAt": "one",
            "rows": [{"id": "business", "operationId": "semantic", "generatedAt": "business-time"}]}
    right = deepcopy(left)
    right.update(requestId="two", jsonPath="latest-b.json", generatedAt="two")
    assert rescan.business_digest(left) == rescan.business_digest(right)
    right["rows"][0]["operationId"] = "another"
    assert rescan.business_digest(left) != rescan.business_digest(right)
    with pytest.raises(ValueError):
        rescan.business_digest({"ok": False})


@pytest.mark.parametrize("kind,prefix", [("fx_animator_snapshot", "fx"), ("avatar_items_snapshot", "items")])
def test_known_scan_batch_identity_is_outer_noise_but_object_identity_remains(kind, prefix):
    payload = {"ok": True, "type": kind, "id": prefix + "_20261006_010000", "created_at": "batch-time-a",
               "rows": [{"id": "asset-guid", "created_at": "semantic-time"}]}
    fresh = deepcopy(payload)
    fresh.update(id=prefix + "_20261006_010001", created_at="batch-time-b")
    assert rescan.business_digest(payload) == rescan.business_digest(fresh)
    fresh["rows"][0]["id"] = "new-guid"
    assert rescan.business_digest(payload) != rescan.business_digest(fresh)
    fresh = deepcopy(payload)
    fresh["id"] = "business-id"
    assert rescan.business_digest(payload) != rescan.business_digest(fresh)


@pytest.mark.parametrize("error", [TimeoutError(), ValueError("ambiguous target"), OSError("Core offline")])
def test_failed_comparison_never_valid_and_never_retries_in_same_turn(setup, monkeypatch, error):
    project, store = setup
    ref, _, _ = save(project, store)
    calls = []
    def fail(*args):
        calls.append(args)
        raise error
    monkeypatch.setattr(rescan, "direct_scan", fail)
    for _ in range(2):
        reply = store.lookup("owner", "next", project, ref)
        assert reply["expired"] is True
        assert reply["reason"] == "comparison_unavailable"
    assert len(calls) == 1


def test_scope_proof_fast_path_avoids_scan_and_scope_failure_falls_back(setup, monkeypatch):
    project, store = setup
    ref, step, _ = save(project, store)
    monkeypatch.setattr(store, "_lookup_scope", lambda *args, **kw: {"step": step, "scopeValidated": True})
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: pytest.fail("Proven unchanged scope must not scan"))
    assert store.lookup("owner", "next", project, ref)["verification"] == "core_scope"
    monkeypatch.setattr(store, "_lookup_scope", lambda *args, **kw: {"expired": True, "reason": "domain_reload_observation_gap"})
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: deepcopy(step["result"]))
    assert store.lookup("owner", "later", project, ref)["verification"] == "observable_result"


def test_digest_mismatch_expires_and_restarts_first_read_preserving_selection(setup, monkeypatch):
    project, store = setup
    args = {"avatarPath": "Root", "bindingView": "details", "bindingSelectors": [{"path": "Body"}],
            "expectedSnapshotDigest": "a" * 64, "clipOffset": 2, "bindingOffset": 3, "keyOffset": 4}
    ref, step, _ = save(project, store, "vrcforge_scan_animation_bindings", args)
    calls = []
    def scan(root, tool, arguments):
        calls.append(deepcopy(arguments))
        if "expectedSnapshotDigest" in arguments:
            raise ValueError("Animation snapshot changed; restart the summary/index before continuing.")
        return deepcopy(step["result"])
    monkeypatch.setattr(rescan, "direct_scan", scan)
    reply = store.lookup("owner", "next", project, ref)
    assert reply["expired"] is True
    assert reply["reason"] == "snapshot_digest_mismatch"
    assert reply["replacement"]["resultRef"] != ref
    assert calls[1]["bindingSelectors"] == args["bindingSelectors"]
    assert all(calls[1][field] == 0 for field in ("clipOffset", "bindingOffset", "keyOffset"))
    assert "expectedSnapshotDigest" not in calls[1]
    store.lookup("owner", "next", project, ref)
    assert len(calls) == 2


def test_owner_isolation_and_mutation_after_verification_never_rescans_again(setup, monkeypatch):
    project, store = setup
    ref, step, _ = save(project, store)
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: calls.append(args) or step["result"])
    assert store.lookup("foreign", "next", project, ref) is None
    assert not calls
    assert store.lookup("owner", "next", project, ref)["step"] == step
    store.begin_mutation(project, "shell")
    store.end_mutation(project, "shell")
    assert store.lookup("owner", "next", project, ref)["reason"] == "mutation_after_verification"
    assert len(calls) == 1


def test_mutation_during_rescan_cannot_be_marked_valid(setup, monkeypatch):
    project, store = setup
    ref, step, _ = save(project, store)
    def scan(*args):
        store.begin_mutation(project, "shell")
        store.end_mutation(project, "shell")
        return step["result"]
    monkeypatch.setattr(rescan, "direct_scan", scan)
    assert store.lookup("owner", "next", project, ref)["reason"] == "mutation_during_verification"


def test_large_scan_preserves_all_content_and_uses_comparison_not_partial_fingerprints(setup, monkeypatch):
    project, store = setup
    payload = {"ok": True, "rows": [{"assetPath": "Assets/" + uuid.uuid4().hex + ".asset"}
                                    for _ in range(stores.MAX_ASSET_PATHS + 12)]}
    ref, step, _ = save(project, store, payload=payload)
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: deepcopy(payload))
    reply = store.lookup("owner", "next", project, ref)
    assert reply["step"]["result"] == payload
    assert reply["verification"] == "observable_result"
    with sqlite3.connect(store._path) as db:
        row = db.execute("SELECT raw, scope, fingerprints FROM snapshots WHERE ref=?", (ref,)).fetchone()
        assert json.loads(row[0])["step"] == step
        assert len(json.loads(row[1])["assetPaths"]) == len(payload["rows"])
        assert json.loads(row[2]) == {}


def test_nonwhite_and_legacy_animation_remain_current_turn(setup):
    project, store = setup
    for tool, args in [("vrcforge_scan_parameters", {"avatarPath": "Root"}),
                       ("vrcforge_scan_animation_bindings", {"avatarPath": "Root"})]:
        step = {"index": 0, "actionId": uuid.uuid4().hex, "tool": tool, "result": {"ok": True}}
        metadata = store.capture("owner", "first", project, step, args)
        assert store.lookup("owner", "next", project, metadata["resultRef"])["reason"] == "current_turn_only"


def test_unknown_target_does_not_advertise_cross_turn_reuse(setup, monkeypatch):
    project, store = setup
    step = {"index": 0, "actionId": uuid.uuid4().hex, "tool": "vrcforge_scan_wardrobe", "result": {"ok": True}}
    metadata = store.capture("owner", "first", project, step, {})
    assert metadata["scope"] == "current_turn"
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: pytest.fail("Unknown scope must not be scanned"))
    assert store.lookup("owner", "next", project, metadata["resultRef"])["expired"] is True
