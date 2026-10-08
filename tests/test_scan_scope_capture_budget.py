import uuid

import agent_scan_scope_client as scope
from agent_scan_result_store import ScanResultStore


def setup_core(monkeypatch):
    # These tests isolate negative-budget ownership; admission has its own suite.
    monkeypatch.setattr(scope, "capture_admission_reason", lambda _request: None)
    state = {"instance": uuid.uuid4().hex, "calls": [], "fail": True, "available": True,
             "targets": {}, "single_target": False}

    class Timeout(RuntimeError):
        cause_code = "unity_core_timeout"

    class Core:
        def __init__(self, *args, **kwargs):
            pass

        def core_info(self):
            return {"instanceId": state["instance"], "scanScope": {
                "schema": scope.SCAN_SCOPE_SCHEMA, "implementation": "scan-scope-v1",
                "tools": list(scope.SCAN_SCOPE_TOOLS),
            } if state["available"] else None}

        def call_tool(self, name, arguments):
            state["calls"].append((name, arguments))
            if name == "vrc_get_execution_targets":
                targets = list(state["targets"].values())
                data = targets[0] if state["single_target"] and len(targets) == 1 else {"targets": targets}
                return {"structuredContent": {"success": True, "data": data}}
            if state["fail"]:
                raise Timeout("scope capture timed out")
            return {"structuredContent": {"success": True, "data": {
                "schema": scope.SCAN_SCOPE_SCHEMA, "verified": True,
                "token": "scope_" + uuid.uuid4().hex, "editorSession": str(uuid.uuid4()),
                "assetPaths": [], "capturedAt": "2026-10-06T00:00:00Z",
                "dependencyCoverage": "complete",
            }}}

    monkeypatch.setattr(scope, "UnityMcpCoreClient", Core)
    return state


def avatar_target(state, name):
    guid = uuid.uuid4().hex
    state["targets"][name] = {"scene": {"guid": guid}, "avatar": {
        "name": name, "exactHierarchyPath": name,
        "globalObjectId": "GlobalObjectId_V1-2-" + guid + "-" + str(uuid.uuid4().int % (2**63)) + "-0",
    }}
    return name


def captures(state):
    return [call for call in state["calls"] if call[0] == scope.CAPTURE_SCAN_SCOPE_TOOL]


def test_failed_avatar_scope_is_not_recaptured_by_equivalent_tools(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    store = ScanResultStore(tmp_path / "store")
    avatar = avatar_target(state, "Avatar-" + uuid.uuid4().hex)
    first = store.start(tmp_path, "vrcforge_scan_wardrobe", {"avatar_path": avatar})
    second = store.start(tmp_path, "vrcforge_scan_avatar_items", {"avatarPath": avatar})
    third = store.start(tmp_path, "vrcforge_scan_avatar_controls", {"avatar_path": avatar})

    assert first["proof"]["verified"] is False
    assert second["proof"]["verified"] is False
    assert third["proof"]["verified"] is False
    assert len(captures(state)) == 1
    assert second["proof"]["reason"] == "capture_suppressed:unity_core_timeout"
    assert captures(state)[0][1]["arguments"]["avatarPath"] == avatar


def test_healthy_other_scope_and_existing_proof_validation_are_not_suppressed(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    store = ScanResultStore(tmp_path / "store")
    avatar = avatar_target(state, "Avatar-" + uuid.uuid4().hex)
    store.start(tmp_path, "vrcforge_scan_wardrobe", {"avatarPath": avatar})
    state["fail"] = False
    other = avatar_target(state, "Other-" + uuid.uuid4().hex)
    first = store.start(tmp_path, "vrcforge_scan_wardrobe", {"avatarPath": other})
    second = store.start(tmp_path, "vrcforge_scan_avatar_items", {"avatarPath": other})

    assert first["proof"]["verified"] and second["proof"]["verified"]
    assert len(captures(state)) == 3
    client = scope.AgentScanScopeClient(tmp_path)
    client.capture_budget = store._capture_budget
    client.validate("scope_" + uuid.uuid4().hex)
    assert state["calls"][-1][0] == scope.VALIDATE_SCAN_SCOPE_TOOL


def test_editor_instance_change_allows_a_new_probe(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    store = ScanResultStore(tmp_path / "store")
    args = {"avatarPath": avatar_target(state, "Avatar-" + uuid.uuid4().hex)}
    store.start(tmp_path, "vrcforge_scan_wardrobe", args)
    store.start(tmp_path, "vrcforge_scan_wardrobe", args)
    state["instance"] = uuid.uuid4().hex
    store.start(tmp_path, "vrcforge_scan_wardrobe", args)
    assert len(captures(state)) == 2


def test_same_path_new_scene_object_is_a_different_scope(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    store = ScanResultStore(tmp_path / "store")
    name = avatar_target(state, "Avatar-" + uuid.uuid4().hex)
    args = {"avatarPath": name}
    store.start(tmp_path, "vrcforge_scan_wardrobe", args)
    avatar_target(state, name)
    store.start(tmp_path, "vrcforge_scan_wardrobe", args)
    assert len(captures(state)) == 2


def test_old_core_never_probes_unknown_tools(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    state["available"] = False
    store = ScanResultStore(tmp_path / "store")
    for _ in range(2):
        result = store.start(tmp_path, "vrcforge_scan_wardrobe", {"avatarPath": uuid.uuid4().hex})
        assert result["proof"]["verified"] is False
        assert result["proof"]["reason"] == "capability_unavailable"
    assert state["calls"] == []


def test_native_single_avatar_target_preserves_healthy_capture(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    state.update(fail=False, single_target=True)
    store = ScanResultStore(tmp_path / "store")
    name = avatar_target(state, "Avatar-" + uuid.uuid4().hex)
    result = store.start(tmp_path, "vrcforge_scan_wardrobe", {"avatarPath": name})
    assert result["proof"]["verified"] is True
    assert len(captures(state)) == 1


def test_ambiguous_actual_identity_is_never_probed_or_reused(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    state["fail"] = False
    store = ScanResultStore(tmp_path / "store")
    name = avatar_target(state, "Avatar-" + uuid.uuid4().hex)
    other = avatar_target(state, "Other-" + uuid.uuid4().hex)
    state["targets"][other]["avatar"].update(name=name, exactHierarchyPath=name)
    result = store.start(tmp_path, "vrcforge_scan_wardrobe", {"avatarPath": name})
    assert result["proof"]["verified"] is False
    assert result["proof"]["reason"] == "scope_capture_identity_unavailable"
    assert captures(state) == []


def test_replaced_asset_guid_does_not_inherit_previous_failure(monkeypatch, tmp_path):
    state = setup_core(monkeypatch)
    asset = tmp_path / "Assets" / (uuid.uuid4().hex + ".controller")
    asset.parent.mkdir()
    asset.write_text("test controller", encoding="utf-8")
    metadata = asset.with_name(asset.name + ".meta")
    metadata.write_text("guid: " + uuid.uuid4().hex, encoding="utf-8")
    args = {"controllerPath": asset.relative_to(tmp_path).as_posix()}
    store = ScanResultStore(tmp_path / "store")
    store.start(tmp_path, "vrcforge_scan_fx_animator", args)
    store.start(tmp_path, "vrcforge_scan_fx_animator", args)
    metadata.write_text("guid: " + uuid.uuid4().hex, encoding="utf-8")
    store.start(tmp_path, "vrcforge_scan_fx_animator", args)
    assert len(captures(state)) == 2
