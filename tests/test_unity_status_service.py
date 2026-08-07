from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import dashboard_server
import pytest
import unity_status_service
from unity_mcp_core_client import UnityMcpCoreError
from unity_status_service import UnityStatusPorts, UnityStatusService


ROOT = Path(__file__).parents[1]
METHODS = {
    "build_unity_status_snapshot",
    "build_vrcforge_mcp_core_unavailable_status",
    "build_vrcforge_mcp_core_status",
}


def make_service(*, selected_project: str = "", core_installed=lambda _project: True) -> UnityStatusService:
    return UnityStatusService(
        UnityStatusPorts(
            load_settings=lambda: SimpleNamespace(unity_mcp_timeout_seconds=10),
            selected_project_path=lambda: selected_project,
            normalize_path=lambda value: str(Path(value)).replace("\\", "/") if value else "",
            core_installed=core_installed,
            required_tools=("vrc_alpha", "vrc_beta"),
        )
    )


def test_unity_status_service_has_explicit_read_only_ports_and_narrow_root_facades() -> None:
    service_source = (ROOT / "unity_status_service.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    service = next(node for node in ast.parse(service_source).body if isinstance(node, ast.ClassDef) and node.name == "UnityStatusService")
    implementation_names = {node.name for node in service.body if isinstance(node, ast.FunctionDef) and node.name != "__init__"}
    assert implementation_names == METHODS
    assert "dashboard_server import" not in service_source
    assert "sys.modules" not in service_source
    assert "_host" not in service_source
    assert "__getattr__" not in service_source
    assert UnityStatusService.__slots__ == ("_ports",)
    assert "Thread(" not in service_source
    assert "Lock(" not in service_source

    tree = ast.parse(dashboard_source)
    expected_calls = {
        "build_unity_status_snapshot": "build_unity_status_snapshot",
        "build_vrcforge_mcp_core_unavailable_status": "build_vrcforge_mcp_core_unavailable_status",
        "build_vrcforge_mcp_core_status": "build_vrcforge_mcp_core_status",
    }
    for name, method_name in expected_calls.items():
        facade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        assert len(facade.body) == 1
        returned = facade.body[0]
        assert isinstance(returned, ast.Return)
        assert isinstance(returned.value, ast.Call)
        assert isinstance(returned.value.func, ast.Attribute)
        assert returned.value.func.attr == method_name


def test_unity_status_service_projects_existing_core_schema_with_fake_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, int, str]] = []

    class FakeCoreClient:
        def __init__(self, project_root: Path, *, timeout_seconds: int) -> None:
            self.project_root = project_root
            self.timeout_seconds = timeout_seconds

        def list_tools(self, *, exposure_layer: str) -> list[dict[str, str]]:
            calls.append((self.project_root, self.timeout_seconds, exposure_layer))
            return [{"name": "vrc_beta"}, {"name": "vrc_alpha"}, {"name": ""}, {"other": "ignored"}]

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    project = tmp_path / "Project"
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=99),
        project,
    )

    assert calls == [(project, 10, "execution")]
    assert status == {
        "connected": True,
        "mcpServerReachable": True,
        "unityInstanceRegistered": True,
        "selectedInstanceMatched": True,
        "host": "127.0.0.1",
        "port": 0,
        "instance": "project-scoped",
        "projectPath": str(project).replace("\\", "/"),
        "activeInstance": {
            "projectPath": str(project.resolve()),
            "transport": "vrcforge-mcp-core",
            "cliSelectorStable": True,
            "cliInstanceId": "project-scoped",
        },
        "instances": [{
            "projectPath": str(project.resolve()),
            "transport": "vrcforge-mcp-core",
            "cliSelectorStable": True,
            "cliInstanceId": "project-scoped",
        }],
        "activeInstanceCount": 1,
        "tools": {
            "ok": True,
            "reachable": True,
            "connected": True,
            "host": "127.0.0.1",
            "port": 0,
            "instance": "project-scoped",
            "totalTools": 2,
            "defaultToolsCount": 0,
            "vrcForgeToolsCount": 2,
            "toolNames": ["vrc_alpha", "vrc_beta"],
            "vrcForgeToolNames": ["vrc_alpha", "vrc_beta"],
            "missingRequiredVrcForgeTools": [],
            "onlyDefaultTools": False,
            "output": "",
            "parsed": None,
            "error": "",
        },
        "mcpHealth": {"ok": True, "protocolVersion": "2026-07-28", "transport": "vrcforge-mcp-core"},
        "unityMcpPackageVersion": "vrcforge-core-2026-07-28",
        "vrcForgeToolsRegistered": True,
        "missingRequiredVrcForgeTools": [],
        "output": "",
        "parsed": None,
        "error": "",
    }


def test_unity_status_service_preserves_core_error_and_missing_project_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class OfflineCoreClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def list_tools(self, *, exposure_layer: str) -> list[dict[str, str]]:
            raise UnityMcpCoreError("offline")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", OfflineCoreClient)
    project = tmp_path / "Project"
    offline = make_service().build_unity_status_snapshot(SimpleNamespace(unity_mcp_timeout_seconds=5), project)
    missing = make_service(selected_project="", core_installed=lambda _project: False).build_unity_status_snapshot()

    assert offline["error"] == "offline"
    assert offline["tools"] == {
        "ok": False,
        "reachable": False,
        "totalTools": 0,
        "vrcForgeToolsCount": 0,
        "missingRequiredVrcForgeTools": ["vrc_alpha", "vrc_beta"],
        "error": "offline",
    }
    assert missing["error"] == "No Unity project is selected."
    assert missing["projectPath"] == ""
    assert missing["tools"]["missingRequiredVrcForgeTools"] == ["vrc_alpha", "vrc_beta"]


def test_dashboard_unity_status_service_is_constructed_with_frozen_ports() -> None:
    assert isinstance(dashboard_server._UNITY_STATUS, UnityStatusService)
    assert dashboard_server._UNITY_STATUS._ports.required_tools == tuple(dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS)
