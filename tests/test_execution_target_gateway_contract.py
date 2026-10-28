from types import SimpleNamespace

import agent_gateway
import dashboard_server
import pytest


def test_bootstrap_tools_are_shared_core_reads_with_strict_public_schemas() -> None:
    names = {
        "vrcforge_list_execution_targets",
        "vrcforge_bind_execution_target",
        "vrcforge_refresh_execution_target",
    }
    assert names <= agent_gateway.EXTERNAL_MCP_READ_TOOL_BLOCKS["core"]
    assert names <= set(dashboard_server.AGENT_GATEWAY._tools)
    binder = dashboard_server.AGENT_GATEWAY._tools["vrcforge_bind_execution_target"]
    assert "discovers candidates internally" in binder.description
    assert "do not need to call vrcforge_list_execution_targets" in binder.description
    assert "two Avatars match" in binder.description
    bind_schema = agent_gateway.canonical_unity_read_tool_input_schema("vrcforge_bind_execution_target")
    assert bind_schema["required"] == ["projectPath", "scope"]
    assert "current Avatar directly" in bind_schema["properties"]["scope"]["description"]

    get_property = agent_gateway.canonical_unity_read_tool_input_schema("vrcforge_get_property")
    assert "executionTarget" in get_property["required"]
    assert "executionTarget" in get_property["properties"]
    ref_schema = get_property["properties"]["executionTarget"]["oneOf"][0]
    assert ref_schema["properties"]["schema"]["const"] == "vrcforge.execution_target_ref.v1"
    assert ref_schema["required"] == ["schema", "ref"]
    assert ref_schema["additionalProperties"] is False
    refresh = agent_gateway.canonical_unity_read_tool_input_schema("vrcforge_refresh_execution_target")
    assert refresh["required"] == ["projectPath", "executionTarget"]
    assert set(refresh["properties"]) == {"projectPath", "executionTarget"}
    assert refresh["properties"]["executionTarget"]["properties"]["schema"]["const"] == "vrcforge.execution_target_ref.v1"
    assert "executionTarget" not in agent_gateway.canonical_unity_read_tool_input_schema(
        "vrcforge_list_execution_targets"
    )["properties"]

    project_application_tools = {
        "vrcforge_create_project", "vrcforge_register_project", "vrcforge_register_project_catalog",
        "vrcforge_install_unity_core", "vrcforge_restore_unity_core", "vrcforge_rollback_project_catalog_registration",
        "vrcforge_rollback_project_lifecycle", "vrcforge_select_project", "vrcforge_restore_checkpoint",
    }
    for name, schema in agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.items():
        if "projectPath" not in schema.get("properties", {}):
            continue
        projected = agent_gateway.canonical_unity_write_tool_input_schema(name)
        assert "executionTarget" in projected["properties"]
        if name in project_application_tools:
            assert "executionTarget" not in projected.get("required", [])
        else:
            assert "executionTarget" in projected["required"]


def test_shared_descriptor_projects_optional_identity_extension_for_material_scan() -> None:
    for name in ("vrcforge_scan_materials", "vrcforge_scan_animation_bindings"):
        descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
            name, write=False
        )
        schema = descriptor["inputSchema"]
        assert schema["additionalProperties"] is False
        assert "executionTarget" in schema["properties"]
        assert "executionTarget" not in schema.get("required", [])


def test_list_execution_targets_accepts_one_core_issued_target(monkeypatch) -> None:
    target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": "project",
        "namespace": "vrcforge://projects/abc",
        "project": {"root": "D:/Unity/Avatar", "projectId": "abc"},
        "editor": {"unityPid": 123, "processStartTime": "1.0", "coreInstanceId": "core"},
    }
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, tool, arguments: SimpleNamespace(
            payload={"structuredContent": {"success": True, "data": target}}
        ),
    )
    result = dashboard_server.list_execution_targets_sync(
        {"projectPath": "D:/Unity/Avatar", "scope": "project"}
    )
    assert result["candidateCount"] == 1
    assert result["targets"] == [target]


def test_list_execution_targets_forwards_bounded_component_page(monkeypatch) -> None:
    observed = {}
    target = {"schema": "vrcforge.execution_target.v1", "scope": "component", "namespace": "vrcforge://projects/abc/components/id"}
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())

    def invoke(_settings, tool, arguments):
        observed.update({"tool": tool, "arguments": arguments})
        return SimpleNamespace(payload={"structuredContent": {"success": True, "data": {"targets": [target], "complete": True}}})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.list_execution_targets_sync(
        {
            "projectPath": "D:/Unity/Avatar",
            "scope": "component",
            "avatarGlobalObjectId": "avatar-id",
            "componentType": "UnityEngine.SkinnedMeshRenderer",
            "offset": 64,
            "maxItems": 32,
        }
    )
    assert observed == {
        "tool": "vrc_get_execution_targets",
        "arguments": {
            "scope": "component",
            "avatarGlobalObjectId": "avatar-id",
            "componentType": "UnityEngine.SkinnedMeshRenderer",
            "offset": 64,
            "maxItems": 32,
        },
    }
    assert result["candidateCount"] == 1


@pytest.mark.parametrize("field,value", [("offset", -1), ("maxItems", 0), ("maxItems", 129)])
def test_list_execution_targets_rejects_invalid_component_page(field, value) -> None:
    with pytest.raises(RuntimeError, match=field):
        dashboard_server.list_execution_targets_sync({"projectPath": "D:/Unity/Avatar", "scope": "component", field: value})


def test_bind_requires_exactly_one_candidate_and_uses_same_gateway_registry(monkeypatch) -> None:
    observed_discovery = []
    monkeypatch.setattr(
        dashboard_server,
        "list_execution_targets_sync",
        lambda params: observed_discovery.append(params) or {"targets": [{"scope": "avatar"}], "projectPath": "D:/Unity/Avatar"},
    )
    observed = {}
    reference = {"schema": "vrcforge.execution_target_ref.v1", "ref": "vrcforge-target-test-reference"}

    def bind(target, *, project_root):
        observed.update({"target": target, "projectRoot": project_root})
        return {"executionTarget": reference, "executionTargetDigest": "a" * 64, "scope": "project"}

    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "bind_execution_target", bind)
    result = dashboard_server.bind_execution_target_sync(
        {"projectPath": "D:/Unity/Avatar", "scope": "avatar"}
    )
    assert result["status"] == "bound"
    assert result["executionTarget"] == reference
    assert "executionTargetHandle" not in result
    assert "D:/Unity/Avatar" not in str(result)
    assert observed == {"target": {"scope": "avatar"}, "projectRoot": "D:/Unity/Avatar"}
    assert observed_discovery == [{"projectPath": "D:/Unity/Avatar", "scope": "avatar"}]


def test_refresh_accepts_public_reference_and_returns_no_target_paths(monkeypatch) -> None:
    reference = {"schema": "vrcforge.execution_target_ref.v1", "ref": "vrcforge-target-test-reference"}
    target = {
        "scope": "avatar",
        "project": {"root": "D:/Unity/Avatar", "projectId": "project-id"},
        "editor": {"unityPid": 42, "processStartTime": "1", "coreInstanceId": "core-id"},
        "scene": {"guid": "scene-guid", "absolutePath": "D:/Unity/Avatar/Assets/Main.unity"},
        "avatar": {"globalObjectId": "avatar-id"},
    }
    list_calls = []
    refresh_calls = []
    resolve_calls = []
    def resolve(supplied, **kwargs):
        assert supplied == reference
        resolve_calls.append(kwargs)
        return target
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "resolve_execution_target_reference", resolve)

    def list_targets(request):
        list_calls.append(request)
        return {"targets": [target], "projectPath": "D:/Unity/Avatar"}

    def refresh(supplied, observed, *, project_root):
        refresh_calls.append((supplied, observed, project_root))
        return {
            "executionTarget": reference,
            "executionTargetDigest": "b" * 64,
            "previousExecutionTargetDigest": "a" * 64,
            "changed": True,
            "lifetime": "gateway_process_or_explicit_refresh",
        }

    monkeypatch.setattr(dashboard_server, "list_execution_targets_sync", list_targets)
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "refresh_execution_target", refresh)
    result = dashboard_server.refresh_execution_target_sync(
        {"projectPath": "D:/Unity/Avatar", "executionTarget": reference}
    )

    assert list_calls == [{
        "projectPath": "D:/Unity/Avatar",
        "scope": "avatar",
        "avatarGlobalObjectId": "avatar-id",
    }]
    assert refresh_calls == [(reference, target, "D:/Unity/Avatar")]
    assert resolve_calls == [{
        "project_root": "D:/Unity/Avatar", "refresh_scene_snapshot": False,
    }]
    assert result["executionTarget"] == reference
    assert "D:/Unity/Avatar" not in str(result)
    assert "absolutePath" not in str(result)
    assert "executionTargetHandle" not in str(result)


@pytest.mark.parametrize("metadata", [
    {"totalCandidateCount": 2, "hasMore": True, "complete": False},
    {"totalCandidateCount": 2, "hasMore": False, "complete": True},
    {"hasMore": True},
    {"complete": False},
    {"ok": False},
])
def test_refresh_rejects_ambiguous_or_incomplete_discovery(monkeypatch, metadata) -> None:
    reference = {"schema": "vrcforge.execution_target_ref.v1", "ref": "vrcforge-target-test-reference"}
    target = {"scope": "project", "project": {"projectId": "project-id"}}
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "resolve_execution_target_reference",
                        lambda _ref, **_kwargs: target)
    monkeypatch.setattr(dashboard_server, "list_execution_targets_sync", lambda _request: {
        "targets": [target], "projectPath": "D:/Unity/Avatar", **metadata,
    })
    refresh_calls = []
    monkeypatch.setattr(
        dashboard_server.AGENT_GATEWAY,
        "refresh_execution_target",
        lambda *args, **kwargs: refresh_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="complete discovery"):
        dashboard_server.refresh_execution_target_sync(
            {"projectPath": "D:/Unity/Avatar", "executionTarget": reference}
        )
    assert refresh_calls == []


@pytest.mark.parametrize("metadata", [
    {"totalCandidateCount": 3568, "hasMore": True, "complete": False},
    {"totalCandidateCount": 3568, "hasMore": False, "complete": True},
    {"hasMore": True}, {"complete": False}, {"ok": False},
])
def test_binding_rejects_one_row_from_incomplete_or_multi_candidate_discovery(monkeypatch, metadata):
    monkeypatch.setattr(dashboard_server, "list_execution_targets_sync", lambda _params: {
        "targets": [{"scope": "component"}], "projectPath": "D:/Unity/Avatar", **metadata})
    calls = []
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "bind_execution_target",
                        lambda *args, **kwargs: calls.append(args) or {})
    with pytest.raises(RuntimeError, match="exact"):
        dashboard_server.bind_execution_target_sync({"scope": "component", "maxItems": 1})
    assert calls == []
