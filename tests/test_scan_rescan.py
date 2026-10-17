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
    marker = store.begin_mutation(project, "shell")
    store.end_mutation(project, "shell", marker=marker)
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
        assert reply["replacementSummary"]["detailsOmitted"] is False
        assert reply["replacementSummary"]["inlineComplete"] is True
        assert reply["replacementSummary"]["fields"] == fresh
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


def test_avatar_items_continuation_reuses_same_page_and_full_snapshot_guard(setup, monkeypatch):
    project, store = setup
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    args = {"avatarPath": "Avatar-" + uuid.uuid4().hex, "maxItems": 37,
            "offset": 73, "expectedSnapshotDigest": digest}
    ref, step, _ = save(project, store, "vrcforge_scan_avatar_items", args)
    calls = []

    def scan(root, tool, arguments):
        calls.append(deepcopy(arguments))
        assert arguments == {**args, "outputPath": "", "refreshAssets": False}
        return deepcopy(step["result"])

    monkeypatch.setattr(rescan, "direct_scan", scan)
    assert store.lookup("owner", "next", project, ref)["verification"] == "observable_result"
    assert store.lookup("owner", "next", project, ref)["step"] == step
    assert len(calls) == 1


def test_avatar_items_digest_mismatch_expires_and_restarts_first_page(setup, monkeypatch):
    project, store = setup
    args = {"avatarPath": "Avatar-" + uuid.uuid4().hex, "maxItems": 37,
            "offset": 73, "expectedSnapshotDigest": uuid.uuid4().hex + uuid.uuid4().hex}
    ref, step, _ = save(project, store, "vrcforge_scan_avatar_items", args)
    calls = []

    def scan(root, tool, arguments):
        calls.append(deepcopy(arguments))
        if len(calls) == 1:
            assert arguments["offset"] == args["offset"]
            assert arguments["expectedSnapshotDigest"] == args["expectedSnapshotDigest"]
            raise ValueError("Avatar snapshot changed; expectedSnapshotDigest mismatch")
        assert arguments == {"avatarPath": args["avatarPath"], "maxItems": 37,
                             "offset": 0, "outputPath": "", "refreshAssets": False}
        return deepcopy(step["result"])

    monkeypatch.setattr(rescan, "direct_scan", scan)
    reply = store.lookup("owner", "next", project, ref)
    assert reply["expired"] is True
    assert reply["reason"] == "snapshot_digest_mismatch"
    assert reply["replacement"]["resultRef"] != ref
    assert store.lookup("owner", "next", project, ref) == reply
    assert len(calls) == 2


def test_owner_isolation_and_mutation_after_verification_never_rescans_again(setup, monkeypatch):
    project, store = setup
    ref, step, _ = save(project, store)
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: calls.append(args) or step["result"])
    assert store.lookup("foreign", "next", project, ref) is None
    assert not calls
    assert store.lookup("owner", "next", project, ref)["step"] == step
    marker = store.begin_mutation(project, "shell")
    store.end_mutation(project, "shell", marker=marker)
    assert store.lookup("owner", "next", project, ref)["reason"] == "mutation_after_verification"
    assert len(calls) == 1


def test_mutation_during_rescan_cannot_be_marked_valid(setup, monkeypatch):
    project, store = setup
    ref, step, _ = save(project, store)
    def scan(*args):
        marker = store.begin_mutation(project, "shell")
        store.end_mutation(project, "shell", marker=marker)
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


def inventory_payload():
    root = "Avatar-" + uuid.uuid4().hex
    return {"ok": True, "type": "avatar_items_snapshot", "requested_avatar_path": "",
            "scenes": ["Scene-" + uuid.uuid4().hex],
            "snapshotDigest": uuid.uuid4().hex + uuid.uuid4().hex,
            "items": [{"avatar_path": root, "object_path": root,
                       "item_id": uuid.uuid4().hex, "active_self": True},
                      {"avatar_path": root, "object_path": root + "/" + uuid.uuid4().hex,
                       "item_id": uuid.uuid4().hex, "active_self": False}]}


@pytest.mark.parametrize("selector", [{}, {"avatarPath": ""}, {"avatar_path": ""},
                                      {"avatarPath": "  "}])
def test_all_avatar_inventory_reuses_the_same_query_without_inventing_a_target(setup, monkeypatch, selector):
    project, store = setup
    args = {**selector, "maxItems": int(uuid.uuid4().hex[:4], 16) % 2000 + 1}
    payload = inventory_payload()
    ref, step, _ = save(project, store, "vrcforge_scan_avatar_items", args, payload)
    calls = []

    def scan(root, tool, arguments):
        calls.append(deepcopy(arguments))
        assert arguments == {"avatarPath": "", "maxItems": args["maxItems"],
                             "outputPath": "", "refreshAssets": False}
        return deepcopy(payload)

    monkeypatch.setattr(rescan, "direct_scan", scan)
    assert store.capture("owner", "first", project, step, args)["scope"] == "validated_scope"
    for _ in range(2):
        assert store.lookup("owner", "next", project, ref)["step"] == step
    restarted = stores.ScanResultStore(store._path.parent)
    assert restarted.lookup("owner", "next", project, ref)["step"] == step
    assert len(calls) == 1
    assert step["result"] == payload


@pytest.mark.parametrize("field", ["items", "scenes", "snapshotDigest"])
def test_all_avatar_inventory_changes_expire_publish_and_page_a_new_reference(setup, monkeypatch, field):
    project, store = setup
    payload = inventory_payload()
    args = {"maxItems": 2000}
    ref, step, _ = save(project, store, "vrcforge_scan_avatar_items", args, payload)
    fresh = deepcopy(payload)
    if field == "items":
        fresh["items"][1]["active_self"] = True
    elif field == "scenes":
        fresh["scenes"].append("Scene-" + uuid.uuid4().hex)
    else:
        fresh["snapshotDigest"] = uuid.uuid4().hex + uuid.uuid4().hex
    calls = []
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: calls.append(args) or deepcopy(fresh))
    with store._connection() as db:
        original = db.execute("SELECT raw FROM snapshots WHERE ref=?", (ref,)).fetchone()[0]
    with bind_tool_result_context("owner", "next", str(project), [], snapshot_lookup=store.lookup):
        reply = read_tool_result({"resultRef": ref}, sanitize=sanitize)
        assert reply["expired"] is True
        assert reply["reason"] == "observable_result_changed"
        replacement = read_tool_result({"resultRef": reply["replacementRef"],
                                        "jsonPointer": "/items", "limit": 2}, sanitize=sanitize)
        assert [item["value"] for item in replacement["items"]] == fresh["items"]
    assert len(calls) == 1
    with store._connection() as db:
        assert db.execute("SELECT raw FROM snapshots WHERE ref=?", (ref,)).fetchone()[0] == original
    assert step["result"] == payload


@pytest.mark.parametrize("invalid", [
    {"type": "unknown"}, {"requested_avatar_path": None},
    {"requested_avatar_path": "Avatar-" + uuid.uuid4().hex},
    {"scenes": []}, {"scenes": [None]}, {"scenes": "unknown"},
    {"snapshotDigest": None}, {"snapshotDigest": "x" * 64}, {"items": None},
])
def test_all_avatar_inventory_without_scope_evidence_remains_current_turn(setup, monkeypatch, invalid):
    project, store = setup
    payload = {**inventory_payload(), **invalid}
    args = {"maxItems": 2000}
    ref, step, _ = save(project, store, "vrcforge_scan_avatar_items", args, payload)
    assert store.capture("owner", "first", project, step, args)["scope"] == "current_turn"
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: pytest.fail("Unclear inventory must not rescan"))
    assert store.lookup("owner", "next", project, ref)["expired"] is True


@pytest.mark.parametrize("failure", ["ambiguous", "timeout", "missing_scope"])
def test_all_avatar_inventory_failed_verification_never_reuses_or_retries(setup, monkeypatch, failure):
    project, store = setup
    payload = inventory_payload()
    ref, _, _ = save(project, store, "vrcforge_scan_avatar_items", {"maxItems": 2000}, payload)
    calls = []

    def scan(*args):
        calls.append(args)
        if failure == "ambiguous":
            raise ValueError("Avatar hierarchy path is ambiguous across loaded scene objects")
        if failure == "timeout":
            raise TimeoutError()
        return {**payload, "requested_avatar_path": None}

    monkeypatch.setattr(rescan, "direct_scan", scan)
    for _ in range(2):
        reply = store.lookup("owner", "next", project, ref)
        assert reply["expired"] is True
        assert reply["reason"] == "comparison_unavailable"
        assert "replacement" not in reply
    assert len(calls) == 1


@pytest.mark.parametrize("method", ["core_scope", "observable_result"])
def test_reader_exposes_host_verification_without_changing_retained_business_data(setup, monkeypatch, method):
    project, store = setup
    ref, step, _ = save(project, store)
    calls = []
    if method == "core_scope":
        monkeypatch.setattr(store, "_lookup_scope", lambda *args, **kwargs: {"step": step, "scopeValidated": True})
        monkeypatch.setattr(rescan, "direct_scan", lambda *args: pytest.fail("Core proof must not rescan"))
    else:
        monkeypatch.setattr(rescan, "direct_scan", lambda *args: calls.append(args) or deepcopy(step["result"]))
    with store._connection() as db:
        before = db.execute("SELECT raw FROM snapshots WHERE ref=?", (ref,)).fetchone()[0]
    with bind_tool_result_context("owner", "next", str(project), [], snapshot_lookup=store.lookup):
        for _ in range(2):
            reply = read_tool_result({"resultRef": ref, "jsonPointer": "/rows"}, sanitize=sanitize)
            assert reply["freshness"] == "verified_on_read"
            assert reply["verification"] == method
            assert reply["expired"] is False
            assert reply["items"][0]["value"] == step["result"]["rows"][0]
    assert len(calls) == (method == "observable_result")
    with store._connection() as db:
        assert db.execute("SELECT raw FROM snapshots WHERE ref=?", (ref,)).fetchone()[0] == before


def test_reader_current_turn_receipt_does_not_claim_a_new_scope_verification(setup, monkeypatch):
    project, store = setup
    ref, _, _ = save(project, store)
    monkeypatch.setattr(rescan, "direct_scan", lambda *args: pytest.fail("Producing turn must not rescan"))
    with bind_tool_result_context("owner", "first", str(project), [], snapshot_lookup=store.lookup):
        reply = read_tool_result({"resultRef": ref}, sanitize=sanitize)
    assert reply["freshness"] == "captured_this_turn"
    assert reply["verification"] == "current_turn"
    assert reply["expired"] is False


@pytest.mark.parametrize("method", [None, "unverified", {"valid": True}])
def test_reader_never_promotes_unverified_or_business_supplied_freshness(setup, monkeypatch, method):
    project, store = setup
    payload = {"ok": True, "freshness": "verified_on_read", "verification": "core_scope"}
    ref, step, _ = save(project, store, payload=payload)
    lookup = lambda *args: {"step": step, "verification": method}
    with bind_tool_result_context("owner", "next", str(project), [], snapshot_lookup=lookup):
        reply = read_tool_result({"resultRef": ref}, sanitize=sanitize)
    assert "freshness" not in reply and "verification" not in reply
