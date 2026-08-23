from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import dashboard_server
import primitive_basis_live_runtime as live_runtime
from unity_mcp_core_client import MODERN_PROTOCOL_VERSION, TRANSPORT_SCHEMA
from unity_mcp_tool_contract import (
    CORE_IDENTITY,
    EXPECTED_TOOL_COUNT,
    HANDSHAKE_PROTOCOL,
    PRODUCT_VERSION,
    TOOL_CONTRACT_VERSION,
)


def configure_state(monkeypatch, project: Path, settings_path: Path) -> None:
    raw_project_path = str(project.resolve())
    descriptor_path = project / "Library" / "VRCForge" / "mcp-core.json"
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(
        json.dumps(
            {
                "schema": TRANSPORT_SCHEMA,
                "transport": "tcp-newline-jsonrpc",
                "protocolVersion": MODERN_PROTOCOL_VERSION,
                "supportedProtocolVersions": [MODERN_PROTOCOL_VERSION],
                "minimumProtocolVersion": MODERN_PROTOCOL_VERSION,
                "maximumProtocolVersion": MODERN_PROTOCOL_VERSION,
                "coreIdentity": CORE_IDENTITY,
                "handshakeProtocol": HANDSHAKE_PROTOCOL,
                "productVersion": PRODUCT_VERSION,
                "toolContractVersion": TOOL_CONTRACT_VERSION,
                "authMode": "bearer-per-request",
                "executionPolicy": "read-direct-app-process-approved-writes",
                "host": "127.0.0.1",
                "port": 23456,
                "authToken": base64.b64encode(b"t" * 32).decode("ascii"),
                "instanceId": "core-instance-1",
                "processId": 12345,
                "projectPath": raw_project_path,
                "projectId": hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest(),
                "projectIdSource": "normalized_project_path_sha256",
                "toolCount": EXPECTED_TOOL_COUNT,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dashboard_server.DASHBOARD_STATE,
        "selected_project_path",
        dashboard_server.normalize_path_string(str(project)),
    )
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "settings_path", settings_path)
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "unity_host", "127.0.0.1")
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "unity_port", 0)
    monkeypatch.setattr(
        dashboard_server.DASHBOARD_STATE,
        "unity_instance",
        project.name,
    )


def fixed_settings(project: Path) -> SimpleNamespace:
    return SimpleNamespace(
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=0,
        unity_mcp_instance="",
        unity_mcp_timeout_seconds=30,
        unity_mcp_command=[],
    )


def test_connection_freezes_project_transport_and_settings(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "FixtureProject"
    project.mkdir()
    settings_path = tmp_path / "settings.json"
    configure_state(monkeypatch, project, settings_path)
    settings = fixed_settings(project)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: settings)
    calls: list[tuple[object, str, dict[str, object]]] = []

    def invoke(call_settings, tool_name: str, arguments: dict[str, object], **_kwargs):
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

    descriptor_path = project / "Library" / "VRCForge" / "mcp-core.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["instanceId"] = "core-instance-2"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
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

    def invoke(_settings, tool: str, arguments: dict[str, object], **_kwargs):
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


def test_reload_projection_keeps_only_safe_core_audit(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "FixtureProject"
    project.mkdir()
    settings_path = tmp_path / "settings.json"
    configure_state(monkeypatch, project, settings_path)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: fixed_settings(project))
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda *_args, **_kwargs: dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "structuredContent": {"ok": True, "reloaded": True},
                "_meta": {
                    "io.vrcforge/callAudit": {
                        "requestId": 77,
                        "toolName": "vrc_reload_primitive_basis_fixture",
                        "resultSummary": "complete",
                        "durationMs": 2.5,
                        "argumentKeys": ["expectedRunIdDigest"],
                        "inputSha256": "a" * 64,
                    }
                },
            },
        ),
    )
    connection = dashboard_server.PrimitiveBasisLiveUnityConnection()
    connection.bind({"projectPath": str(project)})
    payload = connection.reload_fixture({"expectedRunIdDigest": "secret-not-retained"})
    assert payload["_meta"]["io.vrcforge/callAudit"] == {
        "requestId": 77,
        "toolName": "vrc_reload_primitive_basis_fixture",
        "resultSummary": "complete",
        "durationMs": 2.5,
    }


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

    def invoke(_settings, tool: str, arguments: dict[str, object], **_kwargs):
        calls.append((tool, dict(arguments)))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "structuredContent": {
                    "success": True,
                    "data": {"ok": True, **identity},
                }
            },
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
    monkeypatch.setattr(dashboard_server, "PRIMITIVE_BASIS_LIVE_CONNECTION", connection)
    restore_prepared = dashboard_server.prepare_unity_checkpoint_restore_sync(project)
    restore_prepare = {
        "ok": True,
        "scenes": ["Assets/Fixture.unity", "Assets/Lighting.unity"],
        "activeScenePath": "Assets/Lighting.unity",
    }
    reloaded = connection.reload_checkpoint(project, restore_prepare)

    assert {key: prepared[key] for key in identity} == identity
    assert {key: restore_prepared[key] for key in identity} == identity
    assert {key: reloaded[key] for key in identity} == identity
    expected_arguments = {"projectPath": str(project), **guard}
    assert calls == [
        ("vrc_prepare_checkpoint", expected_arguments),
        (
            "vrc_reload_after_checkpoint_restore",
            {"projectPath": str(project), "phase": "prepare_restore", **guard},
        ),
        (
            "vrc_reload_after_checkpoint_restore",
            {
                "projectPath": str(project),
                    "phase": "reload",
                    "scenePaths": restore_prepare["scenes"],
                    "activeScenePath": restore_prepare["activeScenePath"],
                    "refreshAssets": False,
                    **guard,
                },
        ),
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
