from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import dashboard_server
from agent_gateway import AgentGateway


def _checkpoint_fixture(tmp_path: Path):
    project = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    (project / "Assets" / "fixture.txt").write_text("checkpoint", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1", encoding="utf-8"
    )
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    checkpoint = gateway.approval_transactions.create_manual_project_checkpoint(
        {"projectPath": str(project)}
    )
    assert checkpoint["ok"] is True, checkpoint
    gateway.checkpoint_recovery.checkpoint_restore_prepare_handler = (
        dashboard_server.prepare_unity_checkpoint_restore_sync
    )
    gateway.checkpoint_recovery.checkpoint_restore_handler = (
        dashboard_server.reload_unity_checkpoint_sync
    )
    return gateway.checkpoint_recovery, project, checkpoint


def _known_other_editor(*, require_discovery_evidence: bool) -> list[dict[str, object]]:
    assert require_discovery_evidence is True
    return [{
        "processId": 7001,
        "commandLine": '"Unity.exe" -projectPath "D:\\other\\KnownProject"',
    }]


def test_dashboard_restore_uses_disk_only_after_proving_editor_closed(tmp_path, monkeypatch):
    service, project, checkpoint = _checkpoint_fixture(tmp_path)
    monkeypatch.setattr(dashboard_server, "PRIMITIVE_BASIS_LIVE_CONNECTION", None, raising=False)
    monkeypatch.setattr(dashboard_server, "list_running_unity_processes", _known_other_editor)
    monkeypatch.setattr(
        dashboard_server, "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=20, unity_mcp_retries=1),
    )
    monkeypatch.setattr(
        dashboard_server, "invoke_unity_mcp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(dashboard_server.UnityMcpError("Core is offline")),
    )
    (project / "Assets" / "fixture.txt").write_text("changed on disk", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{"changed":true}}', encoding="utf-8")

    preview = service.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
    assert preview["ok"] is True
    assert len(preview["changedFiles"]) == 2

    result = service.restore_checkpoint({
        "checkpointId": checkpoint["id"], "confirmRestore": True,
    })

    assert result["ok"] is True, result
    assert result["status"] == "restored"
    assert (project / "Assets" / "fixture.txt").read_text(encoding="utf-8") == "checkpoint"
    assert (project / "Packages" / "manifest.json").read_text(encoding="utf-8") == '{"dependencies":{}}'
    reload_result = result["unityReload"]
    assert reload_result["status"] == "editor_closed_disk_only"
    assert reload_result["editorReloaded"] is False
    assert reload_result["readbackVerified"] is False
    assert reload_result["editorReopenRequired"] is True
    reload_check = next(
        check for check in result["rollbackCoverageAudit"]["checks"]
        if check["id"] == "unity_reload_after_restore"
    )
    assert reload_check["status"] == "todo"
    assert reload_check["editorReopenRequired"] is True
    assert any(todo["id"] == "reopen_unity_after_restore" for todo in result["rollbackCoverageAudit"]["todos"])


def test_dashboard_restore_does_not_treat_unknown_unity_process_as_offline(tmp_path, monkeypatch):
    service, project, checkpoint = _checkpoint_fixture(tmp_path)
    monkeypatch.setattr(dashboard_server, "PRIMITIVE_BASIS_LIVE_CONNECTION", None, raising=False)
    monkeypatch.setattr(dashboard_server, "list_running_unity_processes", lambda **_: [{
        "processId": 7002, "commandLine": '"Unity.exe" -some-flag',
    }])
    monkeypatch.setattr(
        dashboard_server, "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=20, unity_mcp_retries=1),
    )
    monkeypatch.setattr(
        dashboard_server, "invoke_unity_mcp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(dashboard_server.UnityMcpError("Core is offline")),
    )
    changed = project / "Assets" / "fixture.txt"
    changed.write_text("preserve while editor ownership is unknown", encoding="utf-8")

    result = service.restore_checkpoint({
        "checkpointId": checkpoint["id"], "confirmRestore": True,
    })

    assert result["ok"] is False
    assert result["status"] == "restore_prepare_failed"
    assert "Core is offline" in result["unityRestorePrepare"]["error"]
    assert result["unityRestorePrepare"].get("editorReloaded") is not False
    assert changed.read_text(encoding="utf-8") == "preserve while editor ownership is unknown"


def test_dashboard_restore_keeps_online_core_path_when_target_editor_is_running(tmp_path, monkeypatch):
    service, project, _checkpoint = _checkpoint_fixture(tmp_path)
    monkeypatch.setattr(dashboard_server, "PRIMITIVE_BASIS_LIVE_CONNECTION", None, raising=False)
    monkeypatch.setattr(dashboard_server, "list_running_unity_processes", lambda **_: [{
        "processId": 7003,
        "commandLine": f'"Unity.exe" -projectPath "{project}"',
    }])
    settings = SimpleNamespace(unity_mcp_timeout_seconds=20, unity_mcp_retries=1)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: settings)
    calls = []
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    monkeypatch.setattr(
        dashboard_server,
        "normalize_unity_checkpoint_result",
        lambda _result, _root: {"ok": False, "error": "simulated live Core unavailable"},
    )

    result = dashboard_server.prepare_unity_checkpoint_restore_sync(project)

    assert calls
    assert result["ok"] is False
    assert result["error"] == "simulated live Core unavailable"


def test_dashboard_restore_fails_closed_when_process_discovery_is_unavailable(tmp_path, monkeypatch):
    def fail_discovery(*, require_discovery_evidence):
        assert require_discovery_evidence is True
        raise dashboard_server.UnityProcessDiscoveryUnavailable("fixture discovery failure")

    monkeypatch.setattr(dashboard_server, "list_running_unity_processes", fail_discovery)

    result = dashboard_server._checkpoint_restore_editor_is_definitely_closed(tmp_path)

    assert result is False
