"""Existing MA instances use the domain writer, preserving exact update arguments."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from unity_execution_plans_workflows import build_workflow_execution_plan
from unity_mcp_core_client import UnityMcpCoreClient, UnityMcpCoreError
from unity_tool_schema_projection import (
    canonical_unity_read_tool_input_schema, canonical_unity_write_tool_input_schema,
)
from wardrobe_outfit_workflow_service import (
    build_add_modular_avatar_component_request,
    validate_add_modular_avatar_component_request,
    finalize_add_modular_avatar_component_verification,
)


def request():
    # Original rejected structured-list operation, with disposable identities.
    return {
        "gameObjectPath": "Avatar/Outfit", "avatarPath": "Avatar",
        "componentType": "nadena.dev.modular_avatar.core.ModularAvatarShapeChanger",
        "action": "update", "componentIndex": 1,
        "expectedComponentGlobalObjectId": "GlobalObjectId_V1-2-fixture-100-0",
        "fields": {"Shapes": [
            {"Object": {"referencePath": "Body"}, "ShapeName": name,
             "ChangeType": "Set", "Value": value}
            for name, value in (("Shrink_Sock_on", 0), ("Sock_squeeze", 0),
                                ("Garters_squeeze", 0), ("Foot_heel", 100), ("Shrink_Foot", 100))
        ]}, "saveScene": True,
    }


def test_preview_apply_and_frozen_plan_keep_exact_existing_instance():
    params = request()
    preview = build_add_modular_avatar_component_request(params, True)
    apply = build_add_modular_avatar_component_request(params, False)
    assert validate_add_modular_avatar_component_request(apply) is None
    assert preview == {**apply, "preview": True}
    assert apply == {**params, "preview": False}
    assert build_workflow_execution_plan("vrcforge_add_modular_avatar_component", params) == [
        ("vrc_add_modular_avatar_component", apply)]


def test_existing_instance_update_reuses_registered_scene_only_checkpoint(tmp_path, monkeypatch):
    import dashboard_server as server
    from test_scene_workflow_checkpoint_scope import _target

    root = tmp_path / "project"
    (root / "Assets").mkdir(parents=True)
    (root / "Assets/Main.unity").write_text("scene", encoding="utf-8")
    target = _target(root, "object")
    args = {**request(), "projectPath": str(root), "executionTarget": target}
    calls = []
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda _: {"ok": True, "targets": [deepcopy(target)]})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda project, **kw: calls.append(kw) or {"ok": True})
    result = server.AGENT_GATEWAY._write_handlers["vrcforge_add_modular_avatar_component"].checkpoint_prepare_handler(
        root, {**args, "_checkpointTargetTool": "vrcforge_add_modular_avatar_component"})
    assert result["ok"] is True, result
    assert result["archiveAssetPaths"] == ["Assets/Main.unity"]
    assert calls == [{"checkpoint_files": ["Assets/Main.unity"]}]


@pytest.mark.parametrize("change", [
    {"componentIndex": None}, {"componentIndex": True}, {"componentIndex": -1},
    {"expectedComponentGlobalObjectId": ""}, {"allowDuplicate": True}, {"fields": {}},
])
def test_update_rejects_missing_identity_or_empty_change(change):
    params = {**request(), **change}
    assert validate_add_modular_avatar_component_request(
        build_add_modular_avatar_component_request(params, False))["ok"] is False


def test_internal_external_preview_and_write_expose_same_update_fields():
    write = canonical_unity_write_tool_input_schema("vrcforge_add_modular_avatar_component")
    preview = canonical_unity_read_tool_input_schema("vrcforge_preview_add_modular_avatar_component")
    for key in ("action", "componentIndex", "expectedComponentGlobalObjectId"):
        assert write["properties"][key] == preview["properties"][key]
    assert write["properties"]["action"]["enum"] == ["add", "update"]


@pytest.mark.parametrize("change", [None, "identity", "index", "duplicate", "field"])
def test_update_readback_uses_requested_instance_and_rejects_wrong_result(change):
    args = request()
    receipt = {"ok": True, "updatedComponent": True, "addedComponent": False,
               "componentIndex": 1, "componentGlobalObjectId": args["expectedComponentGlobalObjectId"],
               "before": {"count": 2}, "after": {"count": 2}}
    observed = deepcopy(args["fields"]["Shapes"])
    if change == "identity": receipt["componentGlobalObjectId"] = "different"
    if change == "index": receipt["componentIndex"] = 0
    if change == "duplicate": receipt["addedComponent"] = True
    if change == "field": observed[-1]["Value"] = 0
    calls = []
    result = finalize_add_modular_avatar_component_verification(
        args, receipt,
        lambda _: {"ok": True, "present": True, "count": 2, "references": [],
                   "type": args["componentType"], "sceneDirty": False},
        lambda q: calls.append(q) or {"ok": True, "propertyValue": observed},
    )
    assert (result.get("verified") is True) is (change is None)
    if change is None:
        assert result["mutationApplied"] is True and result["commitState"] == "committed"
        assert calls[0]["componentIndex"] == 1
    else:
        assert result["ok"] is False


@pytest.mark.parametrize("contract", ["185", "184", "unknown", ""])
def test_old_core_cannot_silently_add_or_skip_an_update(contract):
    client = object.__new__(UnityMcpCoreClient)
    client._connection = SimpleNamespace(tool_contract_version=contract)
    client._request = lambda *a, **k: pytest.fail("Must reject before transport")
    with pytest.raises(UnityMcpCoreError, match="186"):
        client.call_tool("vrc_add_modular_avatar_component", request())


@pytest.mark.parametrize("contract,action", [("186", "update"), ("187", "update"), ("185", "add")])
def test_supported_update_and_original_add_reach_transport(contract, action):
    client = object.__new__(UnityMcpCoreClient)
    client._connection = SimpleNamespace(tool_contract_version=contract)
    calls = []
    client._request = lambda method, params, **kw: calls.append(params) or {
        "content": [{"type": "text", "text": "ok"}], "isError": False,
        "structuredContent": {"success": True}}
    args = {**request(), "action": action}
    client.call_tool("vrc_add_modular_avatar_component", args)
    assert calls == [{"name": "vrc_add_modular_avatar_component", "arguments": args}]
