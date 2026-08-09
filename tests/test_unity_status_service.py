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


def test_unity_status_service_has_explicit_read_only_ports_and_no_root_facades() -> None:
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
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name in METHODS
        for node in tree.body
    )
    assert not any(
        (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_UNITY_STATUS"
                for target in node.targets
            )
        )
        or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_UNITY_STATUS"
        )
        for node in tree.body
    )
    assert "STOPGAP: Migration-only owner for the three root compatibility facades below." not in dashboard_source


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
    assert offline["causeCode"] == "unity_core_contract_invalid"
    assert offline["tools"] == {
        "ok": False,
        "reachable": False,
        "totalTools": 0,
        "vrcForgeToolsCount": 0,
        "missingRequiredVrcForgeTools": ["vrc_alpha", "vrc_beta"],
        "error": "offline",
    }
    assert missing["error"] == "No Unity project is selected."
    assert missing["causeCode"] == "unity_project_not_selected"
    assert missing["projectPath"] == ""
    assert missing["tools"]["missingRequiredVrcForgeTools"] == ["vrc_alpha", "vrc_beta"]


def test_settings_project_path_overrides_the_persisted_selected_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SelectedA"
    requested = tmp_path / "RequestedB"
    observed: list[Path] = []

    class FakeCoreClient:
        def __init__(self, project_root: Path, *, timeout_seconds: int) -> None:
            assert timeout_seconds == 7
            observed.append(project_root)

        def list_tools(self, *, exposure_layer: str) -> list[dict[str, str]]:
            assert exposure_layer == "execution"
            return [{"name": "vrc_alpha"}, {"name": "vrc_beta"}]

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    status = make_service(selected_project=str(selected)).build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=7, unity_project_path=str(requested))
    )

    assert observed == [requested]
    assert status["projectPath"] == str(requested).replace("\\", "/")


def test_gateway_status_and_tools_handlers_forward_the_requested_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requested = tmp_path / "RequestedB"
    observed: list[str] = []

    def fake_snapshot(_self, settings=None, project_root=None):
        assert project_root is None
        observed.append(str(settings.unity_project_path))
        return {"projectPath": str(settings.unity_project_path), "tools": {"ok": True}}

    monkeypatch.setattr(UnityStatusService, "build_unity_status_snapshot", fake_snapshot)
    status = dashboard_server.AGENT_GATEWAY._tools["vrcforge_unity_status"].handler(  # noqa: SLF001
        {"projectPath": str(requested)}
    )
    tools = dashboard_server.AGENT_GATEWAY._tools["vrcforge_unity_tools"].handler(  # noqa: SLF001
        {"projectPath": str(requested)}
    )

    assert observed == [str(requested), str(requested)]
    assert status["projectPath"] == str(requested)
    assert tools == {"ok": True}


def test_validation_environment_uses_the_requested_project_for_every_component(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SelectedA"
    requested = tmp_path / "RequestedB"
    settings = SimpleNamespace(unity_project_path=str(requested), unity_mcp_timeout_seconds=5)
    status = {"projectPath": str(requested), "connected": True}
    observed: dict[str, object] = {}

    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "selected_project_path", str(selected))
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: settings)

    def fake_snapshot(_self, observed_settings=None, project_root=None):
        observed["statusSettings"] = observed_settings
        observed["statusProject"] = project_root
        return status

    def fake_components(
        observed_settings,
        *,
        selected_project_path=None,
        unity_status_override=None,
    ):
        observed["healthSettings"] = observed_settings
        observed["healthProject"] = selected_project_path
        observed["healthStatus"] = unity_status_override
        return {
            name: {"status": "ok", "detail": str(requested)}
            for name in (
                "unityPluginInstalled",
                "mcpPackageConfigured",
                "unityMcpBridgeReachable",
                "unityMcpInstance",
                "vrcForgeUnityTools",
            )
        }

    monkeypatch.setattr(UnityStatusService, "build_unity_status_snapshot", fake_snapshot)
    monkeypatch.setattr(dashboard_server, "build_health_components", fake_components)

    result = dashboard_server.validation_environment_status_sync({"projectPath": str(requested)})

    assert observed == {
        "statusSettings": settings,
        "statusProject": requested,
        "healthSettings": settings,
        "healthProject": str(requested),
        "healthStatus": status,
    }
    assert result["unityStatus"] is status
    assert set(result["components"]) == {
        "unityPluginInstalled",
        "mcpPackageConfigured",
        "unityMcpBridgeReachable",
        "unityMcpInstance",
        "vrcForgeUnityTools",
    }


def test_http_error_mapping_uses_stable_retryability_instead_of_core_error_text() -> None:
    temporary = dashboard_server.UnityMcpError(
        "Core is starting.",
        cause_code="unity_core_starting",
        retryable=True,
    )
    terminal = dashboard_server.UnityMcpError(
        "Core package is incomplete.",
        cause_code="unity_core_package_incomplete",
    )

    assert dashboard_server.to_http_exception(temporary).status_code == 503
    assert dashboard_server.to_http_exception(terminal).status_code == 400


def test_dashboard_unity_status_service_is_constructed_with_frozen_ports() -> None:
    assert isinstance(dashboard_server.UNITY_STATUS, UnityStatusService)
    assert dashboard_server.UNITY_STATUS._ports.required_tools == tuple(dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS)
