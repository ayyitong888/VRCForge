from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import dashboard_server
import primitive_basis_live_runtime as live_runtime


def configure_state(monkeypatch, project: Path, settings_path: Path) -> None:
    monkeypatch.setattr(
        dashboard_server.DASHBOARD_STATE,
        "selected_project_path",
        dashboard_server.normalize_path_string(str(project)),
    )
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "settings_path", settings_path)
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "unity_host", "127.0.0.1")
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "unity_port", 8080)
    monkeypatch.setattr(
        dashboard_server.DASHBOARD_STATE,
        "unity_instance",
        project.name,
    )


def fixed_settings(project: Path) -> SimpleNamespace:
    return SimpleNamespace(
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance=project.name,
        unity_mcp_timeout_seconds=30,
        unity_mcp_command=["fixed", "bridge"],
    )


def test_connection_freezes_project_transport_and_settings(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "FixtureProject"
    project.mkdir()
    settings_path = tmp_path / "settings.json"
    configure_state(monkeypatch, project, settings_path)
    settings = fixed_settings(project)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: settings)
    calls: list[tuple[object, str, dict[str, object]]] = []

    def invoke(call_settings, tool_name: str, arguments: dict[str, object]):
        calls.append((call_settings, tool_name, arguments))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={"data": {"ok": True}},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    connection = dashboard_server.PrimitiveBasisLiveUnityConnection()
    binding = connection.bind({"projectPath": str(project)})

    assert binding["frozen"] is True
    assert binding["projectPathDigest"] == live_runtime._hash_text(
        live_runtime._normalize_project_root(project)
    )
    assert connection.validate(
        {"connectionBindingDigest": binding["connectionBindingDigest"]}
    ) == binding
    connection._invoke_result("vrc_test_read", {})
    assert calls == [(settings, "vrc_test_read", {})]

    dashboard_server.DASHBOARD_STATE.unity_instance = "OtherProject"
    with pytest.raises(live_runtime.PrimitiveBasisLiveRuntimeError, match="connection changed"):
        connection._invoke_result("vrc_test_read", {})


def test_compile_status_returns_unwrapped_authoritative_payload(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "FixtureProject"
    project.mkdir()
    settings_path = tmp_path / "settings.json"
    configure_state(monkeypatch, project, settings_path)
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: fixed_settings(project),
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(_settings, tool: str, arguments: dict[str, object]):
        calls.append((tool, dict(arguments)))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "isCompiling": False,
                    "hasErrors": False,
                    "errorCount": 0,
                    "source": "compilation_pipeline",
                    "capturedAt": "2026-07-23T00:00:30Z",
                    "projectPathDigest": "1" * 64,
                    "unityProcessId": 2_000_000_000,
                    "unityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
                    "unityExecutableDigest": "2" * 64,
                }
            },
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    connection = dashboard_server.PrimitiveBasisLiveUnityConnection()
    connection.bind({"projectPath": str(project)})

    guard = {
        "expectedRunIdDigest": "3" * 64,
        "expectedProjectPathDigest": "1" * 64,
        "expectedUnityProcessId": 2_000_000_000,
        "expectedUnityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
        "expectedUnityExecutableDigest": "2" * 64,
    }
    payload = connection.read_compile_status({"maxErrors": 20, **guard})

    assert payload == {
        "ok": True,
        "isCompiling": False,
        "hasErrors": False,
        "errorCount": 0,
        "source": "compilation_pipeline",
        "capturedAt": "2026-07-23T00:00:30Z",
        "projectPathDigest": "1" * 64,
        "unityProcessId": 2_000_000_000,
        "unityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
        "unityExecutableDigest": "2" * 64,
        "exitCode": 0,
    }
    assert calls == [("vrc_get_compile_errors", {"maxErrors": 20, **guard})]


def test_checkpoint_callbacks_preserve_guarded_unity_identity(
    monkeypatch, tmp_path: Path
) -> None:
    project = tmp_path / "FixtureProject"
    project.mkdir()
    settings_path = tmp_path / "settings.json"
    configure_state(monkeypatch, project, settings_path)
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: fixed_settings(project),
    )
    guard = {
        "expectedRunIdDigest": "3" * 64,
        "expectedProjectPathDigest": "1" * 64,
        "expectedUnityProcessId": 2_000_000_000,
        "expectedUnityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
        "expectedUnityExecutableDigest": "2" * 64,
    }
    identity = {
        "projectPathDigest": "1" * 64,
        "unityProcessId": 2_000_000_000,
        "unityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
        "unityExecutableDigest": "2" * 64,
    }
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(_settings, tool: str, arguments: dict[str, object]):
        calls.append((tool, dict(arguments)))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={"data": {"ok": True, **identity}},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(
        dashboard_server,
        "PRIMITIVE_BASIS_LIVE_RUNTIME",
        SimpleNamespace(_component_arguments=lambda preview: {**guard}),
    )
    connection = dashboard_server.PrimitiveBasisLiveUnityConnection()
    connection.bind({"projectPath": str(project)})

    prepared = connection.prepare_checkpoint(project)
    reloaded = connection.reload_checkpoint(project)

    assert {key: prepared[key] for key in identity} == identity
    assert {key: reloaded[key] for key in identity} == identity
    expected_arguments = {"projectPath": str(project), **guard}
    assert calls == [
        ("vrc_prepare_checkpoint", expected_arguments),
        ("vrc_reload_after_checkpoint_restore", expected_arguments),
    ]


def test_live_status_route_is_absent_normally_and_bounded_when_active(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "PRIMITIVE_BASIS_LIVE_RUNTIME", None)
    with TestClient(dashboard_server.app) as client:
        inactive = client.get("/api/app/primitive-basis/live/model-part/status")
    assert inactive.status_code == 404

    fake = SimpleNamespace(
        status=lambda: {
            "ok": True,
            "schema": "vrcforge.primitive_basis_live_status.v1",
            "runId": "primitive-live-test",
            "state": "running",
            "receiptCount": 5,
            "approvalId": "approval-test",
            "checkpointId": "checkpoint-test",
            "restoreApprovalId": "",
            "projectBindingDigest": "1" * 64,
            "connectionBindingDigest": "2" * 64,
        }
    )
    monkeypatch.setattr(dashboard_server, "PRIMITIVE_BASIS_LIVE_RUNTIME", fake)
    with TestClient(dashboard_server.app) as client:
        active = client.get("/api/app/primitive-basis/live/model-part/status")

    assert active.status_code == 200
    assert active.json()["checkpointId"] == "checkpoint-test"
    assert "projectPath" not in active.text
