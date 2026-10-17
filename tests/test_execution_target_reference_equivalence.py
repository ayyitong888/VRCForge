"""Regression coverage for internal Agent calls using public ExecutionTarget refs.

The registered dashboard handlers remain real; only their Unity-facing ports are
replaced so the tests never connect to or mutate a Unity project.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
from agent_gateway import AgentGateway
from tests.test_execution_target import _assert_no_project_path
from execution_target import (
    EXECUTION_TARGET_SCHEMA,
    ExecutionTargetBindingRegistry,
    ExecutionTargetError,
    canonical_namespace,
    process_start_time,
    project_identity,
)


_REGISTERED_TOOL_NAMES = (
    "vrcforge_scan_materials",
    "vrcforge_scan_fx_animator",
    "vrcforge_preview_manage_fx_animator",
    "vrcforge_scan_animation_bindings",
    "vrcforge_get_property",
)


def _live_target(project_root: Path, *, scope: str) -> dict:
    project_root.mkdir(parents=True, exist_ok=True)
    assets = project_root / "Assets"
    assets.mkdir(exist_ok=True)
    scene = assets / "Main.unity"
    scene.write_bytes(b"%YAML 1.1\n--- !u!1 &1\n")
    guid = "0123456789abcdef0123456789abcdef"
    scene.with_suffix(".unity.meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8"
    )
    root = str(project_root.resolve())
    start = process_start_time(os.getpid())
    descriptor_dir = project_root / "Library" / "VRCForge"
    descriptor_dir.mkdir(parents=True, exist_ok=True)
    (descriptor_dir / "mcp-core.json").write_text(
        json.dumps({
            "projectPath": root,
            "projectId": project_identity(root),
            "processId": os.getpid(),
            "processStartTime": start,
            "instanceId": "fixture-core",
        }),
        encoding="utf-8",
    )
    target = {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": scope,
        "project": {"root": root, "projectId": project_identity(root)},
        "editor": {
            "unityPid": os.getpid(),
            "processStartTime": start,
            "coreInstanceId": "fixture-core",
        },
    }
    if scope in {"scene", "avatar", "object", "component"}:
        stat = scene.stat()
        target["scene"] = {
            "assetPath": "Assets/Main.unity",
            "absolutePath": str(scene.resolve()),
            "guid": guid,
            "revision": str(621355968000000000 + stat.st_mtime_ns // 100),
            "digest": hashlib.sha256(scene.read_bytes()).hexdigest(),
        }
    if scope in {"avatar", "object", "component"}:
        target["avatar"] = {
            "globalObjectId": "GlobalObjectId_V1-2-avatar",
            "exactHierarchyPath": "Root/Avatar",
        }
    target["namespace"] = canonical_namespace(target)
    return target


def _gateway_with_registered_handlers(tmp_path: Path, monkeypatch) -> AgentGateway:
    source = dashboard_server.AGENT_GATEWAY
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    for name in _REGISTERED_TOOL_NAMES:
        registered = source._tools[name]
        gateway.register_tool(
            registered.name,
            registered.description,
            registered.category,
            registered.handler,
            write=registered.write,
            advanced=registered.advanced,
            requires_user_activation=registered.requires_user_activation,
        )
        assert gateway._tools[name].handler is registered.handler
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(
        gateway, "ensure_config", lambda: SimpleNamespace(allow_write_requests=True)
    )
    return gateway


def _patch_dashboard_io(monkeypatch, tmp_path: Path, unity_calls: list[tuple]) -> None:
    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda params: params)
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )
    monkeypatch.setattr(
        dashboard_server,
        "build_dashboard_artifact_path",
        lambda prefix, _avatar_path, suffix: tmp_path / f"{prefix}.{suffix}",
    )
    monkeypatch.setattr(dashboard_server, "write_dashboard_json_artifact", lambda *_args: None)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *_args, **_kwargs: None)

    def invoke(_settings, tool_name, arguments, execution_context=None, **_kwargs):
        unity_calls.append((
            tool_name,
            copy.deepcopy(arguments),
            copy.deepcopy(execution_context),
        ))
        return SimpleNamespace(payload={
            "structuredContent": {
                "success": True,
                "data": {"ok": True, "status": "ok", "layers": [], "clips": []},
            },
        })

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)


def _call_as_runtime(gateway: AgentGateway, tool_name: str, arguments: dict) -> dict:
    return gateway.runtime_skills.execute(
        tool_name,
        arguments,
        "reference-equivalence-fixture",
        owner_id="fixture-owner",
    )


def _assert_envelope_and_reference_match(
    gateway: AgentGateway,
    *,
    tool_name: str,
    arguments: dict,
    target: dict,
    project_root: Path,
    session_id: str,
    observed_calls: list[tuple],
) -> None:
    with gateway.resource_context(f"runtime:{session_id}"):
        reference = gateway.bind_execution_target(
            target, project_root=str(project_root)
        )["executionTarget"]
        envelope_result = _call_as_runtime(
            gateway,
            tool_name,
            {**copy.deepcopy(arguments), "executionTarget": copy.deepcopy(target)},
        )
        reference_result = _call_as_runtime(
            gateway,
            tool_name,
            {**copy.deepcopy(arguments), "executionTarget": copy.deepcopy(reference)},
        )

    assert envelope_result["status"] == "executed", envelope_result
    assert reference_result["status"] == "executed", reference_result
    assert len(observed_calls) == 2
    assert observed_calls[0] == observed_calls[1]


@pytest.mark.parametrize("explicit_avatar_path", [False, True])
def test_internal_material_index_ref_matches_avatar_envelope(
    tmp_path: Path, monkeypatch, explicit_avatar_path: bool
) -> None:
    project_root = tmp_path / "unity-project"
    target = _live_target(project_root, scope="avatar")
    unity_calls: list[tuple] = []
    domain_calls: list[tuple] = []
    _patch_dashboard_io(monkeypatch, tmp_path, unity_calls)
    monkeypatch.setattr(
        dashboard_server,
        "DASHBOARD_RUNTIME",
        SimpleNamespace(current_avatar_path="Root/Avatar"),
    )

    def scan_domain(_settings, avatar_path, **kwargs):
        domain_calls.append((avatar_path, copy.deepcopy(kwargs)))
        return {
            "materials": [{
                "material_id": "material-fixture",
                "material_name": "FixtureMaterial",
                "renderer_path": "Root/Avatar/Body",
                "shader_name": "Fixture/Shader",
            }],
            "summary": {"materialCount": 1},
        }

    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", scan_domain)
    gateway = _gateway_with_registered_handlers(tmp_path, monkeypatch)
    arguments = {
        "projectPath": str(project_root),
        "indexOnly": True,
        **({"avatarPath": "Root/Avatar"} if explicit_avatar_path else {}),
    }

    _assert_envelope_and_reference_match(
        gateway,
        tool_name="vrcforge_scan_materials",
        arguments=arguments,
        target=target,
        project_root=project_root,
        session_id=f"material-{explicit_avatar_path}",
        observed_calls=domain_calls,
    )

    assert domain_calls[0][0] == "Root/Avatar"
    assert domain_calls[0][1] == {"material_ids": None, "include_textures": True}
    assert unity_calls == []


@pytest.mark.parametrize(
    ("tool_name", "arguments", "core_tool", "expected_core_arguments", "expected_context"),
    [
        (
            "vrcforge_scan_fx_animator",
            {"controllerPath": "Assets/FX/Detached.controller"},
            "vrc_scan_fx_animator",
            {
                "avatarPath": "",
                "outputPath": "",
                "controllerPath": "Assets/FX/Detached.controller",
                "refreshAssets": False,
            },
            None,
        ),
        (
            "vrcforge_preview_manage_fx_animator",
            {
                "controllerPath": "Assets/FX/Detached.controller",
                "action": "update_state",
                "layerName": "FX",
                "stateName": "Idle",
                "preview": True,
            },
            "vrc_manage_fx_animator",
            {
                "controllerPath": "Assets/FX/Detached.controller",
                "action": "update_state",
                "layerName": "FX",
                "stateName": "Idle",
                "preview": True,
            },
            {"lane": "app_preview"},
        ),
        (
            "vrcforge_scan_animation_bindings",
            {
                "controllerPath": "Assets/FX/Detached.controller",
                "clipPaths": ["Assets/FX/Idle.anim"],
            },
            "vrc_scan_animation_bindings",
            {
                "avatarPath": "",
                "outputPath": "",
                "controllerPath": "Assets/FX/Detached.controller",
                "clipPaths": ["Assets/FX/Idle.anim"],
                "includeAllProjectClips": False,
                "maxClips": 300,
                "includeBindingDetails": False,
                "maxKeysPerBinding": 256,
                "refreshAssets": False,
            },
            None,
        ),
    ],
)
def test_internal_project_asset_reference_matches_full_envelope(
    tmp_path: Path,
    monkeypatch,
    tool_name: str,
    arguments: dict,
    core_tool: str,
    expected_core_arguments: dict,
    expected_context: dict | None,
) -> None:
    project_root = tmp_path / "unity-project"
    target = _live_target(project_root, scope="project")
    unity_calls: list[tuple] = []
    _patch_dashboard_io(monkeypatch, tmp_path, unity_calls)
    gateway = _gateway_with_registered_handlers(tmp_path, monkeypatch)

    _assert_envelope_and_reference_match(
        gateway,
        tool_name=tool_name,
        arguments={"projectPath": str(project_root), **arguments},
        target=target,
        project_root=project_root,
        session_id=f"asset-{tool_name}",
        observed_calls=unity_calls,
    )

    expected_call = (core_tool, expected_core_arguments, expected_context)
    assert unity_calls == [expected_call, expected_call]


@pytest.mark.parametrize(
    ("scope", "tool_name", "tool_arguments"),
    [
        ("avatar", "vrcforge_scan_materials", {"indexOnly": True}),
        (
            "project",
            "vrcforge_scan_fx_animator",
            {"controllerPath": "Assets/FX/Detached.controller"},
        ),
    ],
)
def test_resource_snapshot_uses_bound_scope_and_keeps_paths_private(
    tmp_path: Path,
    monkeypatch,
    scope: str,
    tool_name: str,
    tool_arguments: dict,
) -> None:
    project_root = tmp_path / "unity-project"
    target = _live_target(project_root, scope=scope)
    unity_calls: list[tuple] = []
    _patch_dashboard_io(monkeypatch, tmp_path, unity_calls)
    if scope == "avatar":
        monkeypatch.setattr(
            dashboard_server,
            "DASHBOARD_RUNTIME",
            SimpleNamespace(current_avatar_path="Root/Avatar"),
        )
        monkeypatch.setattr(
            dashboard_server,
            "scan_shader_materials_direct",
            lambda *_args, **_kwargs: {
                "materials": [{
                    "material_id": "material-fixture",
                    "material_name": "FixtureMaterial",
                    "renderer_path": "Root/Avatar/Body",
                    "shader_name": "Fixture/Shader",
                }],
                "summary": {"materialCount": 1},
            },
        )
    gateway = _gateway_with_registered_handlers(tmp_path, monkeypatch)
    session = f"resource-{scope}"
    with gateway.resource_context(f"runtime:{session}"):
        binding = gateway.bind_execution_target(target, project_root=str(project_root))
        reference = binding["executionTarget"]
        actual = _call_as_runtime(
            gateway,
            tool_name,
            {
                "projectPath": str(project_root),
                **tool_arguments,
                "executionTarget": reference,
            },
        )
        assert actual["status"] == "executed", actual
        resource_result = {
            "ok": True,
            "status": "executed",
            "requestId": f"resource-{scope}-operation",
            "executionTarget": reference,
            "executionTargetDigest": binding["executionTargetDigest"],
            "result": actual["result"],
            "outcome": {"status": "ok"},
        }
        gateway.publish_mcp_tool_result_resource(
            tool_name,
            {
                "projectPath": str(project_root),
                "executionTarget": reference,
                "scope": "component",
            },
            resource_result,
            source_mode="internal_agent",
        )
        resources = gateway.list_mcp_resources()["resources"]
        snapshot = next(
            item for item in resources
            if item["_meta"]["resourceType"] == "unity_snapshot"
        )
        snapshot_content = gateway.read_mcp_resource(snapshot["uri"])["structuredContent"]
        identity_content = gateway.read_mcp_resource(
            resource_result["resources"]["identityLockUri"]
        )["structuredContent"]

    assert snapshot["uri"].startswith(f"vrcforge://snapshot/{scope}/")
    assert snapshot_content["identity"]["executionTarget"] == reference
    assert identity_content["data"]["exactExecutionTarget"] == reference
    _assert_no_project_path(snapshot_content, project_root)
    _assert_no_project_path(identity_content, project_root)


def test_stale_scene_result_publishes_bound_snapshot_then_next_call_rejects_drift(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / "unity-project"
    target = _live_target(project_root, scope="avatar")
    scene = project_root / "Assets" / "Main.unity"
    unity_calls: list[tuple] = []
    domain_calls: list[str] = []
    _patch_dashboard_io(monkeypatch, tmp_path, unity_calls)
    monkeypatch.setattr(
        dashboard_server,
        "DASHBOARD_RUNTIME",
        SimpleNamespace(current_avatar_path="Root/Avatar"),
    )

    def scan_domain(_settings, avatar_path, **_kwargs):
        domain_calls.append(avatar_path)
        return {
            "materials": [{
                "material_id": "material-fixture",
                "material_name": "FixtureMaterial",
                "renderer_path": "Root/Avatar/Body",
                "shader_name": "Fixture/Shader",
            }],
            "summary": {"materialCount": 1},
        }

    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", scan_domain)
    gateway = _gateway_with_registered_handlers(tmp_path, monkeypatch)
    session = "stale-scene-resource"
    with gateway.resource_context(f"runtime:{session}"):
        binding = gateway.bind_execution_target(target, project_root=str(project_root))
        reference = binding["executionTarget"]
        arguments = {
            "projectPath": str(project_root),
            "indexOnly": True,
            "executionTarget": reference,
        }
        actual = _call_as_runtime(gateway, "vrcforge_scan_materials", arguments)
        assert actual["status"] == "executed", actual

        scene.write_bytes(scene.read_bytes() + b"# changed after the successful scan\n")
        resource_result = {
            "ok": True,
            "status": "executed",
            "requestId": "successful-scan-before-drift",
            "executionTarget": reference,
            "executionTargetDigest": binding["executionTargetDigest"],
            "result": actual["result"],
            "outcome": {"status": "ok"},
        }
        gateway.publish_mcp_tool_result_resource(
            "vrcforge_scan_materials",
            arguments,
            resource_result,
            source_mode="internal_agent",
        )
        resources = gateway.list_mcp_resources()["resources"]
        snapshot = next(
            item for item in resources
            if item["_meta"]["resourceType"] == "unity_snapshot"
        )
        snapshot_content = gateway.read_mcp_resource(snapshot["uri"])["structuredContent"]

        next_call = _call_as_runtime(gateway, "vrcforge_scan_materials", arguments)

    assert snapshot["uri"].startswith("vrcforge://snapshot/avatar/")
    assert snapshot_content["identity"]["executionTarget"] == reference
    _assert_no_project_path(snapshot_content, project_root)
    assert next_call["status"] == "failed"
    assert "identity lock rejected" in next_call["error"].casefold()
    assert domain_calls == ["Root/Avatar"]
    assert unity_calls == []


@pytest.mark.parametrize("scope", ["project", "avatar"])
def test_internal_get_property_ref_keeps_component_requirement(
    tmp_path: Path, monkeypatch, scope: str
) -> None:
    project_root = tmp_path / f"unity-{scope}"
    target = _live_target(project_root, scope=scope)
    unity_calls: list[tuple] = []
    _patch_dashboard_io(monkeypatch, tmp_path, unity_calls)
    gateway = _gateway_with_registered_handlers(tmp_path, monkeypatch)
    with gateway.resource_context(f"runtime:property-{scope}"):
        reference = gateway.bind_execution_target(
            target, project_root=str(project_root)
        )["executionTarget"]
        result = _call_as_runtime(
            gateway,
            "vrcforge_get_property",
            {
                "projectPath": str(project_root),
                "gameObjectPath": "Root/Avatar/Body",
                "componentType": "SkinnedMeshRenderer",
                "propertyPath": "enabled",
                "executionTarget": reference,
            },
        )

    assert result["status"] == "failed"
    assert "identity lock rejected" in result["error"].casefold()
    assert unity_calls == []


@pytest.mark.parametrize("failure", ["wrong_project", "wrong_session", "unknown_ref", "core_drift"])
def test_internal_reference_rejections_happen_before_the_registered_handler(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    project_root = tmp_path / "unity-project"
    target = _live_target(project_root, scope="project")
    unity_calls: list[tuple] = []
    _patch_dashboard_io(monkeypatch, tmp_path, unity_calls)
    gateway = _gateway_with_registered_handlers(tmp_path, monkeypatch)
    owner_session = "reference-owner"
    with gateway.resource_context(f"runtime:{owner_session}"):
        reference = gateway.bind_execution_target(
            target, project_root=str(project_root)
        )["executionTarget"]

    call_root = project_root
    call_session = owner_session
    if failure == "wrong_project":
        call_root = tmp_path / "other-project"
        call_root.mkdir()
    elif failure == "wrong_session":
        call_session = "different-session"
    elif failure == "unknown_ref":
        reference = {
            "schema": "vrcforge.execution_target_ref.v1",
            "ref": "vrcforge-target-unknown-fixture",
        }
    elif failure == "core_drift":
        descriptor_path = project_root / "Library" / "VRCForge" / "mcp-core.json"
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        descriptor["instanceId"] = "replacement-core"
        descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    with gateway.resource_context(f"runtime:{call_session}"):
        result = _call_as_runtime(
            gateway,
            "vrcforge_scan_fx_animator",
            {
                "projectPath": str(call_root),
                "controllerPath": "Assets/FX/Detached.controller",
                "executionTarget": reference,
                "sessionId": owner_session,
                "_runtimeSessionId": owner_session,
            },
        )

    assert result["status"] == "failed"
    assert unity_calls == []


@pytest.mark.parametrize("scope", ["project", "avatar"])
def test_registry_explicit_component_requirement_still_rejects_broader_targets(
    tmp_path: Path, scope: str
) -> None:
    project_root = tmp_path / f"unity-{scope}"
    target = _live_target(project_root, scope=scope)
    registry = ExecutionTargetBindingRegistry()
    session_context = f"runtime:component-boundary-{scope}"
    reference = registry.bind(
        target,
        project_root=str(project_root),
        session_context=session_context,
    )["executionTarget"]

    with pytest.raises(ExecutionTargetError) as error:
        registry.resolve(
            reference,
            project_root=str(project_root),
            session_context=session_context,
            required_scope="component",
        )

    assert error.value.code == "identity_scope_insufficient"
