from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import dashboard_server
from doctor_readiness_report_service import DoctorReadinessReportPorts, DoctorReadinessReportService


ROOT = Path(__file__).parents[1]


def _check(check_id: str, title: str, status: str, message: str, *_args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "message": message,
        "detail": kwargs.get("detail"),
        "fixable": bool(kwargs.get("fixable")),
    }


def _component_check(check_id: str, title: str, component: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    component = component if isinstance(component, dict) else {}
    return _check(
        check_id,
        title,
        str(component.get("status") or "unknown"),
        str(component.get("message") or "Check did not report a result."),
        *args,
        **kwargs,
    )


def _package_check(check_id: str, title: str, project: Path | None, package_ids: list[str], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return _check(check_id, title, "ok" if project else "unknown", ",".join(package_ids))


def _ports(*, calls: list[dict[str, Any]]) -> DoctorReadinessReportPorts:
    health = {
        "components": {
            "backend": {"status": "ok", "message": "Backend ready."},
            "selectedUnityProject": {"status": "ok", "message": "Project ready."},
            "unityPluginInstalled": {"status": "ok", "message": "Plugin ready."},
            "mcpPackageConfigured": {"status": "ok", "message": "Core ready."},
            "unityMcpBridgeReachable": {"status": "ok", "message": "Bridge ready."},
            "unityMcpInstance": {"status": "ok", "message": "Instance ready."},
            "vrcForgeUnityTools": {"status": "ok", "message": "Tools ready."},
            "providerConfigPresent": {"status": "ok", "message": "Provider ready."},
        },
        "projects": {"selectedProjectPath": r"C:\fixture\Avatar"},
    }
    return DoctorReadinessReportPorts(
        build_health=lambda: health,
        serialize_api_config=lambda: {"provider": "openai", "apiKeyRequired": True, "apiKeyPresent": True, "model": "model", "base_url": "https://example.invalid"},
        safe_agent_health=lambda: {"enabled": True, "requiresToken": True, "mcpUrl": "http://127.0.0.1:8757/mcp", "pendingApprovalCount": 0, "allowWriteRequests": False},
        safe_agent_manifest=lambda: {"writeTargets": ["request"]},
        safe_permission_state=lambda: {"allowWriteRequests": False},
        selected_project_path_from_health=lambda payload: str(payload["projects"]["selectedProjectPath"]),
        doctor_check=_check,
        doctor_check_from_component=_component_check,
        package_doctor_check=_package_check,
        status_from_counts=lambda errors, warnings: "error" if errors else "warning" if warnings else "ok",
        check_skill_registry=lambda: {"schema": "vrcforge.skills.v1", "count": 2, "errorCount": 0, "warningCount": 0},
        list_checkpoints=lambda params: calls.append(params) or {"ok": True, "count": 3},
        checkpoint_paths=lambda: (r"C:\state\checkpoints.jsonl", r"C:\state\checkpoints"),
        package_manager_status=lambda params: {"managers": [], "preferredCli": {"name": "vrc-get"}},
        merge_registered_checks=lambda checks: checks,
        doctor_summary=lambda checks: {
            "okCount": sum(check["status"] == "ok" for check in checks),
            "warningCount": sum(check["status"] == "warning" for check in checks),
            "errorCount": sum(check["status"] == "error" for check in checks),
            "unknownCount": sum(check["status"] == "unknown" for check in checks),
        },
        doctor_sections=lambda checks: [{"id": "fixture", "name": "Fixture", "summary": {"okCount": len(checks), "warningCount": 0, "errorCount": 0, "unknownCount": 0}, "checkIds": [check["id"] for check in checks]}],
        redact_local_path=lambda value: f".../{Path(value).name}",
        version=lambda: "1.4.0",
    )


def test_doctor_readiness_service_has_frozen_ports_and_no_root_facade() -> None:
    source = (ROOT / "doctor_readiness_report_service.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    service = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "DoctorReadinessReportService")
    methods = {node.name for node in service.body if isinstance(node, ast.FunctionDef) and node.name != "__init__"}
    assert methods == {"build_app_doctor_report"}
    assert "dashboard_server import" not in source
    assert "sys.modules" not in source
    assert "_host" not in source
    assert "__getattr__" not in source
    assert not any(
        (isinstance(node, ast.Import) and any(alias.name == "fastapi" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "fastapi")
        for node in ast.parse(source).body
    )
    assert "Lock(" not in source
    assert "Thread(" not in source
    assert DoctorReadinessReportService.__slots__ == ("_ports",)

    tree = ast.parse(dashboard_source)
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "build_app_doctor_report"
        for node in tree.body
    )
    assert "_DOCTOR_READINESS_REPORT" not in dashboard_source
    assert "STOPGAP: Migration-only owner for the root Doctor report facade below." not in dashboard_source


def test_doctor_readiness_service_projects_existing_schema_from_fake_ports() -> None:
    calls: list[dict[str, Any]] = []
    report = DoctorReadinessReportService(_ports(calls=calls)).build_app_doctor_report()

    assert calls == [{"projectRoot": r"C:\fixture\Avatar", "limit": 1}]
    assert report["schema"] == "vrcforge.doctor.v1"
    assert report["scope"] == "vrcforge.environment.v1"
    assert report["projectContentInspected"] is False
    assert report["version"] == "1.4.0"
    assert report["selectedUnityEnvironment"] == {"configured": True, "label": ".../Avatar"}
    assert [item["id"] for item in report["checks"]] == [
        "desktop.runtime", "backend.online", "unity.project_root", "unity.plugin", "unity.mcp.package",
        "unity.mcp.bridge", "unity.mcp.instance", "unity.tools", "package.vrchat_sdk",
        "package.modular_avatar", "package.vrcfury", "provider.configured", "provider.test",
        "provider.local_ollama", "agent.gateway", "skills.registry", "checkpoint.backend",
        "package.manager", "external.security_contract",
    ]
    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["provider.test"]["status"] == "unknown"
    assert by_id["external.security_contract"]["status"] == "warning"


def test_dashboard_constructs_doctor_report_service_with_frozen_ports() -> None:
    assert isinstance(dashboard_server.DOCTOR_READINESS_REPORT, DoctorReadinessReportService)
