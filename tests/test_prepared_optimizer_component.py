from __future__ import annotations

from pathlib import Path

import pytest

import dashboard_server as dashboard
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _params(project: Path) -> dict[str, object]:
    return {
        "projectPath": str(project),
        "avatarPath": "Avatar",
        "targetPath": "Avatar/Hat",
        "optimizerId": "meshia",
        "mode": "meshia_simplify",
        "componentType": "Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier",
        "profile": "pc_conservative",
        "options": {"rendererPath": "Avatar/Hat", "relativeVertexCount": 0.9},
    }


def _components() -> list[dict[str, object]]:
    return [
        {"type": "A"},
        {"type": "B"},
        {"type": "C"},
        {"fullName": "Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier", "id": "correct"},
    ]


def _aao_marker_params(project: Path) -> dict[str, object]:
    return {
        "projectPath": str(project),
        "avatarPath": "Avatar",
        "targetPath": "Avatar",
        "optimizerId": "aao",
        "mode": "aao_trace",
        "componentType": "Anatawa12.AvatarOptimizer.TraceAndOptimize",
        "profile": "pc_conservative",
        "options": {},
    }


def test_optimizer_preparer_freezes_existing_nonzero_index(monkeypatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    components = _components()
    monkeypatch.setattr(
        dashboard,
        "get_gameobject_sync",
        lambda _params: {"ok": True, "components": components},
    )

    prepared, _preview = dashboard.prepare_configure_optimizer_component_request(_params(project), {})
    calls = build_prepared_execution_plan(prepared)

    assert calls
    assert all(call[0] == "vrc_set_property" for call in calls)
    assert {call[1]["componentIndex"] for call in calls} == {3}


def test_optimizer_preparer_allows_one_bounded_marker_component_add(monkeypatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        dashboard,
        "get_gameobject_sync",
        lambda _params: {"ok": True, "components": [{"type": "Transform"}]},
    )

    prepared, _preview = dashboard.prepare_configure_optimizer_component_request(
        _aao_marker_params(project),
        {},
    )

    assert build_prepared_execution_plan(prepared) == [
        (
            "vrc_add_component",
            {
                "gameObjectPath": "Avatar",
                "componentType": "Anatawa12.AvatarOptimizer.TraceAndOptimize",
                "preview": False,
            },
        )
    ]


def test_optimizer_preparer_accepts_live_core_string_component_inventory(monkeypatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        dashboard,
        "get_gameobject_sync",
        lambda _params: {
            "ok": True,
            "components": [
                "UnityEngine.Transform",
                "UnityEngine.Animator",
                "VRC.Core.PipelineManager",
                "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor",
            ],
        },
    )

    prepared, _preview = dashboard.prepare_configure_optimizer_component_request(
        _aao_marker_params(project),
        {},
    )

    assert build_prepared_execution_plan(prepared) == [
        (
            "vrc_add_component",
            {
                "gameObjectPath": "Avatar",
                "componentType": "Anatawa12.AvatarOptimizer.TraceAndOptimize",
                "preview": False,
            },
        )
    ]


def test_optimizer_execution_rejects_reordered_components_before_write(monkeypatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    initial = _components()
    current = list(reversed(initial))
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(dashboard, "get_gameobject_sync", lambda _params: {"ok": True, "components": initial})
    prepared, _ = dashboard.prepare_configure_optimizer_component_request(_params(project), {})
    monkeypatch.setattr(dashboard, "get_gameobject_sync", lambda _params: {"ok": True, "components": current})
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", lambda *_args, **_kwargs: calls.append((_args[1], _args[2])) )

    with pytest.raises(RuntimeError, match="layout drifted"):
        dashboard.configure_optimizer_component_sync(prepared)
    assert calls == []


def test_optimizer_execution_stops_after_core_failure(monkeypatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    components = _components()
    invoked: list[str] = []
    monkeypatch.setattr(dashboard, "get_gameobject_sync", lambda _params: {"ok": True, "components": components})
    prepared, _ = dashboard.prepare_configure_optimizer_component_request(_params(project), {})

    def fail_first(_settings, tool_name, _arguments):
        invoked.append(tool_name)
        raise dashboard.UnityMcpError("Core rejected write")

    monkeypatch.setattr(dashboard, "invoke_unity_mcp", fail_first)
    with pytest.raises(dashboard.UnityMcpError):
        dashboard.configure_optimizer_component_sync(prepared)
    assert invoked == ["vrc_set_property"]


def test_optimizer_preparer_rejects_reserved_injection(tmp_path: Path) -> None:
    project = _project(tmp_path)
    arguments = _params(project)
    arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY] = {}
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard.prepare_configure_optimizer_component_request(arguments, {})


def test_optimizer_execution_rejects_readback_drift(monkeypatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    components = _components()
    monkeypatch.setattr(dashboard, "get_gameobject_sync", lambda _params: {"ok": True, "components": components})
    prepared, _ = dashboard.prepare_configure_optimizer_component_request(_params(project), {})
    monkeypatch.setattr(
        dashboard,
        "invoke_unity_mcp",
        lambda _settings, _tool, arguments: dashboard.McpResult(0, "", "", {"newValue": arguments["value"]}),
    )
    monkeypatch.setattr(
        dashboard,
        "read_component_property_sync",
        lambda _params: {"ok": True, "propertyValue": "different"},
    )
    with pytest.raises(RuntimeError, match="readback drifted"):
        dashboard.configure_optimizer_component_sync(prepared)
