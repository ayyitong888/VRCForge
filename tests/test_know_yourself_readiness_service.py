from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import dashboard_server
from know_yourself_readiness_service import KnowYourselfReadinessPorts, KnowYourselfReadinessService


ROOT = Path(__file__).parents[1]


def _doctor() -> dict[str, Any]:
    return {
        "schema": "vrcforge.doctor.v1",
        "checks": [
            {"id": check_id, "status": "warning" if check_id == "provider.test" else "ok"}
            for check_id in (
                "provider.configured", "provider.test", "unity.project_root", "unity.plugin",
                "package.vrchat_sdk", "unity.mcp.package",
            )
        ],
        "selectedUnityEnvironment": {"configured": True, "label": ".../Avatar"},
        "privatePath": r"C:\\private\\Avatar",
    }


def _unity(*, connected: bool = True) -> dict[str, Any]:
    return {
        "connected": connected,
        "unityInstanceRegistered": connected,
        "selectedInstanceMatched": connected,
        "activeInstanceCount": 1 if connected else 0,
        "vrcForgeToolsRegistered": connected,
        "missingRequiredVrcForgeTools": [],
        "projectPath": r"C:\\private\\Avatar",
        "instance": "project-scoped",
    }


def _tool_registry() -> dict[str, Any]:
    return {"schema": "vrcforge.tool_registry.v1", "tools": [
        {"name": "vrcforge_scan_project_index", "category": "project", "availableInMcp": True,
         "modelInvocable": True, "requiresApproval": False, "requiresCheckpoint": False, "advanced": False},
    ]}


def _skill_registry() -> dict[str, Any]:
    return {"schema": "vrcforge.skills.v1", "skills": [
        {"name": "know-yourself", "title": "Know Yourself", "description": "check",
         "category": "work-start", "source": "builtin", "skillType": "group", "enabled": True,
         "available": True, "permissionMode": "read_only", "riskLevel": "low",
         "whenToUse": "before work", "allowedTools": ["vrcforge_know_yourself"],
         "entrypointTool": "vrcforge_know_yourself", "validation": {"status": "ok", "reasons": []}},
    ]}


def _permission_state() -> dict[str, Any]:
    return {"executionMode": "approval", "perActionApproval": True, "autoApprove": False,
            "autoApproveDangerousRequiresApproval": False, "fullPermission": False,
            "allowWriteRequests": True}


def _service(*, unity: dict[str, Any] | None = None, process_error: bool = False, calls: list[dict[str, Any]] | None = None) -> KnowYourselfReadinessService:
    compile_calls = calls if calls is not None else []

    def processes() -> list[dict[str, Any]]:
        if process_error:
            raise RuntimeError("unavailable")
        return [{"processId": 42, "commandLine": r'-projectPath "C:\\private\\Avatar"'}]

    return KnowYourselfReadinessService(KnowYourselfReadinessPorts(
        load_settings_for_params=lambda params: {"request": params},
        build_unity_status=lambda _settings: unity or _unity(),
        build_doctor_report=_doctor,
        selected_project_path=lambda: r"C:\\private\\Avatar",
        unity_editor_path=lambda: __file__,
        parse_editor_version=lambda _path: "2022.3.22f1",
        list_running_unity_processes_strict=processes,
        process_matches_project=lambda _process, _project: True,
        read_compile_errors=lambda params: compile_calls.append(params) or {"ok": True, "result": {"exitCode": 0, "payload": {"data": {"hasErrors": False, "errorCount": 0, "isCompiling": False}}}},
        normalize_path=lambda value: value.replace("\\\\", "/"),
        build_tool_registry=_tool_registry,
        build_skill_registry=_skill_registry,
        permission_state=_permission_state,
        ensure_dict=lambda value: value if isinstance(value, dict) else {},
        normalize_bool=lambda value, default: str(value).strip().lower() in {"1", "true", "yes"}
        if value is not None
        else default,
    ))


def test_know_yourself_service_has_explicit_read_only_ports_and_single_root_facade() -> None:
    source = (ROOT / "know_yourself_readiness_service.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    service = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "KnowYourselfReadinessService")
    assert {node.name for node in service.body if isinstance(node, ast.FunctionDef) and node.name != "__init__"} == {"know_yourself_sync"}
    assert "dashboard_server import" not in source
    assert "agent_gateway import" not in source
    assert "sys.modules" not in source
    assert "_host" not in source
    assert "__getattr__" not in source
    assert "Lock(" not in source
    assert "Thread(" not in source
    assert KnowYourselfReadinessService.__slots__ == ("_ports",)

    facade = next(node for node in ast.parse(dashboard_source).body if isinstance(node, ast.FunctionDef) and node.name == "know_yourself_sync")
    assert len(facade.body) == 1
    assert isinstance(facade.body[0], ast.Return)
    assert isinstance(facade.body[0].value, ast.Call)
    assert isinstance(facade.body[0].value.func, ast.Attribute)
    assert facade.body[0].value.func.attr == "know_yourself_sync"
    assert "STOPGAP: Migration-only owner for the root Know Yourself compatibility facade below." in dashboard_source


def test_know_yourself_service_preserves_compile_gate_focus_scope_and_privacy() -> None:
    compile_calls: list[dict[str, Any]] = []
    report = _service(calls=compile_calls).know_yourself_sync({"editorFocusConfirmed": "true"})

    assert compile_calls == [{"editorFocusConfirmed": "true", "maxErrors": 20, "includeConsoleFallback": True}]
    assert report["readyForUnityWork"] is True
    assert report["projectContext"]["selectedProjectRunning"] is True
    scope = report["editorFocusGate"]["scope"]
    assert scope.startswith("focus-")
    assert report["editorFocusGate"]["acknowledgementValid"] is False
    assert r"C:\\private\\Avatar" not in json.dumps(report)


def test_know_yourself_service_skips_compile_without_live_core_and_preserves_strict_process_unknown() -> None:
    compile_calls: list[dict[str, Any]] = []
    offline = _service(unity=_unity(connected=False), calls=compile_calls).know_yourself_sync({})
    unknown = _service(process_error=True).know_yourself_sync({})

    assert compile_calls == []
    assert offline["liveReadback"]["compile"]["status"] == "not_checked"
    assert unknown["readyForUnityWork"] is False
    assert unknown["gaps"] == ["selected_unity_project_process_unknown"]


def test_dashboard_constructs_know_yourself_service_with_strict_process_port() -> None:
    assert isinstance(dashboard_server._KNOW_YOURSELF_READINESS, KnowYourselfReadinessService)
    assert dashboard_server._KNOW_YOURSELF_READINESS._ports.list_running_unity_processes_strict
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert "list_running_unity_processes(require_discovery_evidence=True)" in source
