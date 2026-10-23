from copy import deepcopy
from pathlib import Path
import ast

import pytest

from prefab_unpack_preview import PrefabUnpackPreviewError, prepare_unpack_prefab
from unity_execution_plans_scene import build_scene_execution_plan
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema
from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS
from mcp_tool_descriptor import identity_scope
from provider_tool_phases import PHASE_BY_TOOL


def values(tmp_path):
    target = {"schema": "vrcforge.execution_target.v1", "scope": "avatar",
              "project": {"root": str(tmp_path)},
              "avatar": {"exactHierarchyPath": "Avatar", "globalObjectId": "avatar-id"},
              "scene": {"absolutePath": str(tmp_path / "Assets/Main.unity")}}
    arguments = {"projectPath": str(tmp_path), "executionTarget": target,
                 "gameObjectPath": "Avatar/Outfit", "mode": "completely"}
    payload = {"action": "unpack_prefab", "preview": True, "gameObjectPath": "Avatar/Outfit",
               "unpackMode": "completely", "globalObjectId": "outfit-id", "prefabGuid": "1" * 32,
               "dependencyHash": "2" * 32, "scenePath": "Assets/Main.unity"}
    return arguments, payload


def test_static_read_preview_and_write_preparer_are_exposed():
    schema = canonical_unity_read_tool_input_schema("vrcforge_preview_unpack_prefab")
    assert any("vrcforge_preview_unpack_prefab" in names for names in EXTERNAL_MCP_READ_TOOL_BLOCKS.values())
    assert set(schema["required"]) == {"projectPath", "gameObjectPath", "executionTarget"}
    assert "preview" not in schema["properties"]
    assert identity_scope("vrcforge_preview_unpack_prefab") == "avatar"
    assert identity_scope("vrcforge_unpack_prefab", write=True) == "avatar"
    assert PHASE_BY_TOOL["unity_preview_unpack_prefab"] != "unknown"
    tree = ast.parse(Path("dashboard_server.py").read_text(encoding="utf-8"))
    registration = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name) and n.func.id == "register_write_handler"
                        and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "vrcforge_unpack_prefab")
    assert next(k.value.id for k in registration.keywords if k.arg == "request_preparer") == "prepare_unpack_prefab_request"


def test_actual_core_preview_is_readonly_and_all_guards_are_sealed(tmp_path):
    arguments, payload = values(tmp_path)
    before = deepcopy(arguments)
    calls = []
    prepared, preview = prepare_unpack_prefab(arguments, lambda request: calls.append(request) or payload)
    assert arguments == before
    assert calls == [{"gameObjectPath": "Avatar/Outfit", "mode": "completely", "preview": True,
                      "expectedScenePath": "Assets/Main.unity"}]
    assert preview["preview"] is True
    assert prepared["preview"] is False
    expected = {"expectedGlobalObjectId": "outfit-id", "expectedPrefabGuid": "1" * 32,
                "expectedAssetDependencyHash": "2" * 32, "expectedScenePath": "Assets/Main.unity"}
    assert all(prepared[key] == value for key, value in expected.items())
    plan = build_scene_execution_plan("vrcforge_unpack_prefab", prepared)
    assert plan == [("vrc_unpack_prefab", {"gameObjectPath": "Avatar/Outfit", "mode": "completely", "preview": False, **expected})]


def test_registered_read_and_approval_share_authoritative_preview(tmp_path, monkeypatch):
    import dashboard_server as host
    arguments, payload = values(tmp_path)
    actual_target = deepcopy(arguments["executionTarget"])
    reference = {"schema": "vrcforge.execution_target_ref.v1", "ref": "vrcforge-target-synthetic-preview"}
    arguments["executionTarget"] = reference
    resolutions, calls = [], []
    monkeypatch.setattr(host.AGENT_GATEWAY, "resolve_execution_target_reference", lambda target, **kwargs:
                        resolutions.append((target, kwargs)) or deepcopy(actual_target))
    monkeypatch.setattr(host, "_invoke_authoritative_unity_preview", lambda params, tool, request:
                        calls.append((tool, deepcopy(request))) or deepcopy(payload))
    registered = host.AGENT_GATEWAY._tools["vrcforge_preview_unpack_prefab"]
    assert registered.handler is host.preview_unpack_prefab_sync
    handler = host.AGENT_GATEWAY._write_handlers["vrcforge_unpack_prefab"]
    assert handler.request_preparer is host.prepare_unpack_prefab_request
    read = host.preview_unpack_prefab_sync(arguments)
    prepared, preview = handler.request_preparer(arguments, {"callerControlled": True})
    assert read == {"ok": True, "preview": preview}
    assert prepared["expectedPrefabGuid"] == payload["prefabGuid"]
    assert len(calls) == 2 and all(tool == "vrc_unpack_prefab" and request["preview"] is True for tool, request in calls)
    assert all(kwargs["required_scope"] == "avatar" for _, kwargs in resolutions)
    catalog = host._RuntimePlannerCatalog().read("planning", project_context_active=True)
    names = {name for tool in (*catalog.visible_tools, *catalog.routable_tools) for name in (tool.name, tool.runtime_name)}
    assert "unity_preview_unpack_prefab" in names


@pytest.mark.parametrize("key,value", [("preview", False), ("action", "wrong"), ("gameObjectPath", "Other"),
    ("unpackMode", "outermost"), ("scenePath", "Assets/Other.unity"), ("globalObjectId", ""),
    ("prefabGuid", ""), ("dependencyHash", "invalid")])
def test_malformed_or_drifted_preview_cannot_prepare_write(tmp_path, key, value):
    arguments, payload = values(tmp_path)
    payload[key] = value
    with pytest.raises(PrefabUnpackPreviewError):
        prepare_unpack_prefab(arguments, lambda _: payload)


@pytest.mark.parametrize("key", ["expectedGlobalObjectId", "expectedPrefabGuid", "expectedAssetDependencyHash", "expectedScenePath"])
def test_explicit_guard_drift_is_not_overwritten(tmp_path, key):
    arguments, payload = values(tmp_path)
    arguments[key] = "previous identity"
    with pytest.raises(PrefabUnpackPreviewError, match="drifted"):
        prepare_unpack_prefab(arguments, lambda _: payload)


def test_nonprefab_core_rejection_keeps_noop_evidence(tmp_path):
    arguments, _ = values(tmp_path)
    payload = {"success": False, "message": "Target is not the outermost root of a prefab instance; nothing to unpack."}
    with pytest.raises(PrefabUnpackPreviewError, match="nothing to unpack") as error:
        prepare_unpack_prefab(arguments, lambda _: payload)
    assert error.value.raw_result == payload


@pytest.mark.parametrize("change", ["other_avatar", "other_project", "unsaved_scene", "replaced_avatar"])
def test_exact_avatar_project_scene_identity_is_required(tmp_path, change):
    arguments, payload = values(tmp_path)
    if change == "other_avatar": arguments["gameObjectPath"] = "Other/Outfit"
    if change == "other_project": arguments["projectPath"] = str(tmp_path / "other")
    if change == "unsaved_scene": arguments["executionTarget"]["scene"]["absolutePath"] = ""
    if change == "replaced_avatar":
        arguments["gameObjectPath"] = "Avatar"
        payload["gameObjectPath"] = "Avatar"
    with pytest.raises(PrefabUnpackPreviewError):
        prepare_unpack_prefab(arguments, lambda _: payload)
