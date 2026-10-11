from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import dashboard_server
import pytest
import unity_status_service
from unity_mcp_core_client import UnityMcpCoreError
from unity_status_service import UnityStatusPorts, UnityStatusService


ROOT = Path(__file__).parents[1]
METHODS = {
    "readiness",
    "build_unity_tools_snapshot",
    "build_unity_status_snapshot",
    "build_vrcforge_mcp_core_unavailable_status",
    "build_vrcforge_mcp_core_status",
}


def make_service(
    *,
    selected_project: str = "",
    core_installed=lambda _project: True,
    dependency_preflight=None,
) -> UnityStatusService:
    return UnityStatusService(
        UnityStatusPorts(
            load_settings=lambda: SimpleNamespace(unity_mcp_timeout_seconds=10),
            selected_project_path=lambda: selected_project,
            normalize_path=lambda value: str(Path(value)).replace("\\", "/") if value else "",
            core_installed=core_installed,
            required_tools=("vrc_alpha", "vrc_beta"),
            dependency_preflight=dependency_preflight,
        )
    )


def test_missing_bridge_propagates_to_bootstrap_without_claiming_connected(monkeypatch, tmp_path):
    status = make_service(selected_project=str(tmp_path)).build_unity_status_snapshot()
    assert status["connected"] is False
    assert status["readiness"]["ready"] is False
    assert status["readiness"]["blockerCode"] == "unity_core_unavailable"
    monkeypatch.setattr(dashboard_server, "CURRENT_UNITY_STATUS", status)
    monkeypatch.setattr(dashboard_server, "PROVIDER_CONFIGURATION", SimpleNamespace(serialize_app_api_config=lambda: {}))
    monkeypatch.setattr(dashboard_server, "PROJECT_SNAPSHOT_SELECTION", SimpleNamespace(project_snapshot_payload=lambda **_: {}))
    monkeypatch.setattr(dashboard_server, "serialize_dashboard_state", lambda: {})
    monkeypatch.setattr(dashboard_server, "build_external_agent_connection_component", lambda: {"status": "unknown"})
    components = dashboard_server.build_bootstrap_app_health(agent_health={})["components"]
    assert components["vrcForgeUnityTools"]["message"] == "VRCForge Unity tools are not ready."
    assert components["unityMcpInstance"]["message"] == "Selected Unity project is not connected."
    assert components["unityMcpBridgeReachable"]["status"] == "warning"


def test_missing_project_dependencies_block_before_core_connection(monkeypatch, tmp_path):
    packages = tmp_path / "Packages"
    packages.mkdir()
    (packages / "manifest.json").write_text('{"dependencies": {}}', encoding="utf-8")
    calls = []

    def fake_core_installed(_project):
        calls.append("core_installed")
        return False

    class UnexpectedCoreClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("dependency blocker must precede Core connection")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", UnexpectedCoreClient)

    status = make_service(
        core_installed=fake_core_installed,
        dependency_preflight=lambda _project: {
            "ready": False,
            "status": "error",
            "blockerCode": "unity_vrchat_sdk_missing",
            "missing": ["vrchat_sdk", "newtonsoft"],
            "message": "Install the VRChat Avatar SDK and Unity Newtonsoft Json before connecting.",
        },
    ).build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path
    )

    assert calls == ["core_installed"]
    assert status["connected"] is False
    assert status["blockerCode"] == "unity_vrchat_sdk_missing"
    assert status["dependencyPreflight"]["missing"] == ["vrchat_sdk", "newtonsoft"]


def test_dashboard_unity_status_reports_dependency_blocker_before_core(monkeypatch, tmp_path):
    packages = tmp_path / "Packages"
    packages.mkdir()
    (packages / "manifest.json").write_text('{"dependencies": {}}', encoding="utf-8")

    class UnexpectedCoreClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("dependency blocker must precede Core connection")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", UnexpectedCoreClient)
    status = dashboard_server.UNITY_STATUS.build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path
    )

    assert status["blockerCode"] == "unity_vrchat_sdk_missing"
    assert status["dependencyPreflight"]["missing"] == ["vrchat_sdk", "newtonsoft"]


def test_healthy_core_path_is_not_blocked_by_package_metadata_only(monkeypatch, tmp_path):
    calls = []

    class FakeCoreClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            calls.append("core_info")
            return {"coreVersion": "1.8.8"}

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    status = make_service(
        dependency_preflight=lambda _project: {
            "ready": False,
            "status": "error",
            "blockerCode": "unity_newtonsoft_missing",
            "missing": ["newtonsoft"],
            "message": "Required package metadata was not detected: Unity Newtonsoft Json.",
        },
    ).build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path
    )

    assert calls == ["core_info"]
    assert status["connected"] is True
    assert status["readiness"]["ready"] is True
    assert status["blockerCode"] == ""
    assert status["dependencyPreflight"]["blockerCode"] == "unity_newtonsoft_missing"


def test_core_failure_promotes_dependency_blocker_when_core_files_are_present(monkeypatch, tmp_path):
    class FailingCoreClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            raise UnityMcpCoreError("compile failed")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FailingCoreClient)
    status = make_service(
        dependency_preflight=lambda _project: {
            "ready": False,
            "status": "error",
            "blockerCode": "unity_newtonsoft_missing",
            "missing": ["newtonsoft"],
            "message": "Required package metadata was not detected: Unity Newtonsoft Json.",
        },
    ).build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path
    )

    assert status["connected"] is False
    assert status["blockerCode"] == "unity_newtonsoft_missing"
    assert status["readiness"]["blockerCode"] == "unity_newtonsoft_missing"
    assert status["causeCode"] == "unity_newtonsoft_missing"


def test_bootstrap_surfaces_dependency_blocker_instead_of_bridge_only_message(monkeypatch):
    monkeypatch.setattr(
        dashboard_server,
        "CURRENT_UNITY_STATUS",
        {
            "connected": False,
            "executionReady": False,
            "blockerCode": "unity_vrchat_sdk_missing",
            "error": "Required package metadata was not detected: VRChat Avatar SDK, Unity Newtonsoft Json.",
            "readiness": {"ready": False, "blockerCode": "unity_vrchat_sdk_missing"},
            "dependencyPreflight": {
                "ready": False,
                "status": "error",
                "blockerCode": "unity_vrchat_sdk_missing",
                "missing": ["vrchat_sdk", "newtonsoft"],
                "message": "Required package metadata was not detected: VRChat Avatar SDK, Unity Newtonsoft Json.",
            },
            "tools": {},
        },
    )
    monkeypatch.setattr(dashboard_server, "PROVIDER_CONFIGURATION", SimpleNamespace(serialize_app_api_config=lambda: {}))
    monkeypatch.setattr(dashboard_server, "PROJECT_SNAPSHOT_SELECTION", SimpleNamespace(project_snapshot_payload=lambda **_: {}))
    monkeypatch.setattr(dashboard_server, "serialize_dashboard_state", lambda: {})
    monkeypatch.setattr(dashboard_server, "build_external_agent_connection_component", lambda: {"status": "unknown"})

    components = dashboard_server.build_bootstrap_app_health(agent_health={})["components"]

    assert components["unityMcpBridgeReachable"]["status"] == "error"
    assert "VRChat Avatar SDK" in components["unityMcpBridgeReachable"]["message"]
    assert components["vrcForgeUnityTools"]["detail"]["dependencyPreflight"]["blockerCode"] == "unity_vrchat_sdk_missing"


def test_dependency_preflight_does_not_call_missing_package_metadata_missing(tmp_path):
    project = tmp_path / "Project"
    (project / "Packages").mkdir(parents=True)

    result = dashboard_server.build_unity_dependency_preflight(project)

    assert result["status"] == "unknown"
    assert result["ready"] is None
    assert result["blockerCode"] == ""
    assert result["missing"] == []
    assert result["unknown"] == ["vrchat_sdk", "newtonsoft"]


def test_dependency_preflight_does_not_call_unreadable_manifest_missing(tmp_path):
    project = tmp_path / "Project"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    (packages / "manifest.json").write_text("{broken", encoding="utf-8")

    result = dashboard_server.build_unity_dependency_preflight(project)

    assert result["status"] == "unknown"
    assert result["missing"] == []
    assert result["unknown"] == ["vrchat_sdk", "newtonsoft"]


def test_dependency_preflight_reports_known_missing_packages_and_dashboard_message(tmp_path):
    project = tmp_path / "Project"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    (packages / "manifest.json").write_text('{"dependencies": {"com.unity.ugui": "1.0.0"}}', encoding="utf-8")

    result = dashboard_server.build_unity_dependency_preflight(project)

    assert result["status"] == "error"
    assert result["ready"] is False
    assert result["blockerCode"] == "unity_vrchat_sdk_missing"
    assert result["missing"] == ["vrchat_sdk", "newtonsoft"]
    assert result["checks"]["vrchatSdk"]["status"] == "missing"
    assert result["checks"]["newtonsoft"]["status"] == "missing"


def test_dependency_preflight_uses_newtonsoft_blocker_when_sdk_is_present(tmp_path):
    project = tmp_path / "Project"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    (packages / "manifest.json").write_text(
        '{"dependencies": {"com.vrchat.avatars": "3.7.0", "com.vrchat.base": "3.7.0"}}',
        encoding="utf-8",
    )

    result = dashboard_server.build_unity_dependency_preflight(project)

    assert result["blockerCode"] == "unity_newtonsoft_missing"
    assert result["missing"] == ["newtonsoft"]
    assert result["checks"]["vrchatSdk"]["status"] == "installed"


def test_dependency_preflight_requires_base_with_avatar_sdk_before_ready(tmp_path):
    project = tmp_path / "Project"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    manifest = {
        "dependencies": {
            "com.vrchat.avatars": "3.7.0",
            "com.unity.nuget.newtonsoft-json": "3.0.2",
        }
    }
    manifest_path = packages / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    before = dashboard_server.build_unity_dependency_preflight(project)
    assert before["status"] == "error"
    assert before["ready"] is False
    assert before["blockerCode"] == "unity_vrchat_sdk_missing"
    assert before["missing"] == ["vrchat_sdk"]
    assert before["checks"]["vrchatSdk"]["installed"] is False

    manifest["dependencies"]["com.vrchat.base"] = "3.7.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    after = dashboard_server.build_unity_dependency_preflight(project)
    assert after["status"] == "ok"
    assert after["ready"] is True
    assert after["blockerCode"] == ""
    assert after["missing"] == []
    assert after["checks"]["vrchatSdk"]["installed"] is True


def test_dependency_preflight_requires_avatar_sdk_when_only_base_sdk_is_present(tmp_path):
    project = tmp_path / "Project"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    manifest = {
        "dependencies": {
            "com.vrchat.base": "3.7.0",
            "com.unity.nuget.newtonsoft-json": "3.0.2",
        }
    }
    manifest_path = packages / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = dashboard_server.build_unity_dependency_preflight(project)
    assert result["status"] == "error"
    assert result["ready"] is False
    assert result["blockerCode"] == "unity_vrchat_sdk_missing"
    assert result["checks"]["vrchatSdk"]["status"] == "missing"

    manifest["dependencies"]["com.vrchat.avatars"] = "3.7.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    recovered = dashboard_server.build_unity_dependency_preflight(project)
    assert recovered["status"] == "ok"
    assert recovered["ready"] is True
    assert recovered["checks"]["vrchatSdk"]["status"] == "installed"


def test_core_inventory_is_diagnostic_not_an_agent_callable_catalog(monkeypatch, tmp_path):
    names = ["vrc_create_gameobject", *[f"vrc_diagnostic_{index}" for index in range(96)]]

    class FakeCoreClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_tools(self, *, exposure_layer):
            assert exposure_layer == "execution"
            return [{"name": name} for name in names]

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    monkeypatch.setattr(UnityStatusService, "build_unity_status_snapshot", lambda *_args: {
        "connected": True, "projectPath": str(tmp_path), "tools": {"ok": True},
    })
    result = make_service().build_unity_tools_snapshot()
    assert result["vrcForgeToolNames"] == names
    assert result["toolNameNamespace"] == "unity_core"
    assert result["agentCallable"] is False
    observation = dashboard_server.RUNTIME_PLANNER.native_result_observation({
        "tool": "vrcforge_unity_tools", "kind": "skill", "status": "executed", "result": result,
    })
    assert '"toolNameNamespace":"unity_core"' in observation["observation"]
    assert '"agentCallable":false' in observation["observation"]


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
    calls: list[tuple[Path, int]] = []

    class FakeCoreClient:
        def __init__(self, project_root: Path, *, timeout_seconds: int) -> None:
            self.project_root = project_root
            self.timeout_seconds = timeout_seconds

        def core_info(self) -> dict[str, str]:
            calls.append((self.project_root, self.timeout_seconds))
            return {
                "schema": "vrcforge.core_info.v1",
                "coreIdentity": "vrcforge.unity-core",
                "coreVersion": "1.7.10",
                "instanceId": "instance-a",
                "projectId": "project-a",
            }

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    project = tmp_path / "Project"
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=99),
        project,
    )

    assert calls == [(project, 10)]
    assert status == {
        "connected": True,
        "executionReady": True,
        "blockerCode": "",
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
            "totalTools": 0,
            "defaultToolsCount": 0,
            "vrcForgeToolsCount": 0,
            "toolNames": [],
            "vrcForgeToolNames": [],
            "missingRequiredVrcForgeTools": [],
            "inspectionMode": "core_version_only",
            "inspectionSkipped": True,
            "onlyDefaultTools": False,
            "output": "",
            "parsed": None,
            "error": "",
        },
        "mcpHealth": {
            "ok": True,
            "protocolVersion": "2026-07-28",
            "transport": "vrcforge-mcp-core",
            "version": "1.7.10",
        },
        "unityMcpPackageVersion": "vrcforge-core-2026-07-28",
        "coreVersion": "1.7.10",
        "coreVersionMatched": True,
        "vrcForgeToolsRegistered": True,
        "missingRequiredVrcForgeTools": [],
        "readiness": {
            "ready": True,
            "blockerCode": "",
            "inspectionMode": "core_version_only",
            "inspectionSkipped": True,
        },
        "output": "",
        "parsed": None,
        "error": "",
    }


def test_unity_status_binds_core_version_without_listing_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Path, int]] = []

    class FakeCoreClient:
        def __init__(self, project_root: Path, *, timeout_seconds: int) -> None:
            self.project_root = project_root
            self.timeout_seconds = timeout_seconds

        def core_info(self) -> dict[str, object]:
            calls.append(("core_info", self.project_root, self.timeout_seconds))
            return {
                "schema": "vrcforge.core_info.v1",
                "coreIdentity": "vrcforge.unity-core",
                "coreVersion": "1.7.10",
                "instanceId": "instance-version-only",
                "projectId": "project-version-only",
            }

        def list_tools(self, *, exposure_layer: str) -> list[dict[str, str]]:
            raise AssertionError(f"connection status must not inspect tools ({exposure_layer})")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    project = tmp_path / "Project"
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=8),
        project,
    )

    assert calls == [("core_info", project, 8)]
    assert status["connected"] is True
    assert status["executionReady"] is True
    assert status["coreVersion"] == "1.7.10"
    assert status["coreVersionMatched"] is True
    assert status["tools"]["inspectionMode"] == "core_version_only"
    assert status["tools"]["inspectionSkipped"] is True
    assert status["missingRequiredVrcForgeTools"] == []


def test_unity_status_blocks_execution_when_core_reports_compile_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeCoreClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def core_info(self) -> dict[str, object]:
            return {
                "schema": "vrcforge.core_info.v1",
                "coreIdentity": "vrcforge.unity-core",
                "coreVersion": "1.8.0",
                "compileSnapshot": {
                    "isCompiling": False,
                    "captureComplete": True,
                    "hasErrors": True,
                    "hasWarnings": True,
                    "errorCount": 1,
                    "warningCount": 3,
                    "source": "compilation_pipeline",
                    "capturedAt": "2026-09-23T21:57:41.4491432Z",
                },
            }

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=8), tmp_path / "Project"
    )

    assert status["connected"] is True
    assert status["executionReady"] is False
    assert status["blockerCode"] == "unity_compile_errors"
    assert status["readiness"]["ready"] is False
    assert status["readiness"]["blockerCode"] == "unity_compile_errors"


@pytest.mark.parametrize(
    "compile_snapshot",
    [
        {
            "isCompiling": False,
            "captureComplete": True,
            "hasErrors": False,
            "errorCount": 0,
            "warningCount": 3,
            "source": "compilation_pipeline",
            "capturedAt": "2026-09-23T21:57:41.4491432Z",
        },
        {"hasErrors": True, "errorCount": 1},
    ],
)
def test_unity_status_keeps_warnings_and_unknown_compile_snapshot_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    compile_snapshot: dict[str, object],
) -> None:
    class FakeCoreClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def core_info(self) -> dict[str, object]:
            return {"coreVersion": "1.8.0", "compileSnapshot": compile_snapshot}

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=8), tmp_path / "Project"
    )

    assert status["executionReady"] is True
    assert status["readiness"]["ready"] is True


def test_unity_status_service_preserves_core_error_and_missing_project_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class OfflineCoreClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def core_info(self) -> dict[str, str]:
            raise UnityMcpCoreError("offline")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", OfflineCoreClient)
    project = tmp_path / "Project"
    offline = make_service().build_unity_status_snapshot(SimpleNamespace(unity_mcp_timeout_seconds=5), project)
    missing = make_service(selected_project="", core_installed=lambda _project: False).build_unity_status_snapshot()

    assert offline["error"] == "offline"
    assert offline["executionReady"] is False
    assert offline["blockerCode"] == "unity_core_contract_invalid"
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
    assert missing["executionReady"] is False
    assert missing["blockerCode"] == "unity_project_not_selected"
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

        def core_info(self) -> dict[str, str]:
            return {"coreVersion": "1.7.10"}

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    status = make_service(selected_project=str(selected)).build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=7, unity_project_path=str(requested))
    )

    assert observed == [requested]
    assert status["projectPath"] == str(requested).replace("\\", "/")


def test_unity_status_keeps_connection_but_blocks_execution_for_exact_reload_dialog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeCoreClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def core_info(self) -> dict[str, str]:
            return {"coreVersion": "1.7.10"}

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", FakeCoreClient)
    monkeypatch.setattr(
        unity_status_service,
        "probe_unity_reload_dialog",
        lambda project: {
            "projectPath": str(Path(project).resolve()),
            "blocked": True,
            "blockerCode": "unity_editor_reload_dialog",
            "dialog": {"title": "Unity", "reloadLabel": "reload"},
        },
    )
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path / "Project"
    )
    assert status["connected"] is True
    assert status["executionReady"] is False
    assert status["blockerCode"] == "unity_editor_reload_dialog"
    assert status["editorBlocker"]["projectPath"].endswith("Project")


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
