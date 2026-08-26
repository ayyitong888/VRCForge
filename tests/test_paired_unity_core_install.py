from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import EXTERNAL_MCP_WRITE_TOOL_BLOCKS


def _write_verified_payload(root: Path, *, integrity_valid: bool = True) -> Path:
    desktop = root / "VRCForge.exe"
    backend = root / "backend" / "vrcforge_backend.exe"
    source = root / "unity_plugin" / "Assets" / "VRCForge"
    mcp_root = source / "Editor" / "MCP"
    backend.parent.mkdir(parents=True)
    mcp_root.mkdir(parents=True)
    desktop.write_bytes(b"paired desktop")
    backend.write_bytes(b"paired backend")
    desktop_sha256 = hashlib.sha256(desktop.read_bytes()).hexdigest()
    backend_sha256 = hashlib.sha256(backend.read_bytes()).hexdigest()
    (mcp_root / "VRCForgeMcpToolContract.cs").write_text(
        'internal const string CoreIdentity = "vrcforge.unity-core";\n'
        'internal const string HandshakeProtocol = "vrcforge.core-handshake.v1";\n'
        'internal const string ProductVersion = "1.7.10";\n'
        'internal const string ToolContractVersion = "73";\n',
        encoding="utf-8",
    )
    (mcp_root / "VRCForgeMcpCoreServer.cs").write_text(
        'private const string MinimumProtocolVersion = "2026-07-28";\n'
        'private const string MaximumProtocolVersion = "2026-07-28";\n',
        encoding="utf-8",
    )
    (source / "new-core.txt").write_text("new", encoding="utf-8")
    (root / "payload-integrity.json").write_text(
        json.dumps(
            {
                "schema": "vrcforge.payload-integrity.v1",
                "files": {
                    "desktop": {
                        "relativePath": "VRCForge.exe",
                        "sha256": desktop_sha256,
                    },
                    "backend": {
                        "relativePath": "backend/vrcforge_backend.exe",
                        "sha256": backend_sha256 if integrity_valid else "0" * 64,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return source


def _write_project(root: Path) -> Path:
    (root / "Assets" / "VRCForge").mkdir(parents=True)
    (root / "Assets" / "VRCForge" / "old-core.txt").write_text("old", encoding="utf-8")
    (root / "Packages").mkdir()
    (root / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n",
        encoding="utf-8",
    )
    return root


def test_verified_unity_core_install_retains_previous_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )

    result = dashboard_server.install_verified_unity_core_sync(
        {"projectPath": str(project)}
    )

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["commitState"] == "full"
    assert result["restoreRequiresApproval"] is True
    assert (project / "Assets" / "VRCForge" / "new-core.txt").read_text(encoding="utf-8") == "new"
    signal_path = project / result["details"]["refreshSignal"]["assetPath"]
    source_signal_path = payload / "unity_plugin" / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"
    assert result["details"]["refreshSignal"]["contentChanged"] is False
    assert signal_path.stat().st_mtime_ns > source_signal_path.stat().st_mtime_ns
    backup = Path(result["backupPath"])
    assert backup.is_dir()
    assert (backup / "old-core.txt").read_text(encoding="utf-8") == "old"


def test_verified_unity_core_install_preserves_generated_and_unmanaged_project_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload)
    project = _write_project(tmp_path / "project")
    generated = project / "Assets" / "VRCForge" / "Generated"
    generated.mkdir()
    (generated / "FinalAvatar_BaseFX.controller").write_text("user generated FX", encoding="utf-8")
    (generated / "FinalAvatar_BaseFX.controller.meta").write_text("guid: 1" * 32, encoding="utf-8")
    user_extension = project / "Assets" / "VRCForge" / "Editor" / "UserExtension.txt"
    user_extension.parent.mkdir(exist_ok=True)
    user_extension.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )

    result = dashboard_server.install_verified_unity_core_sync({"projectPath": str(project)})

    assert result["ok"] is True
    assert (generated / "FinalAvatar_BaseFX.controller").read_text(encoding="utf-8") == "user generated FX"
    assert (generated / "FinalAvatar_BaseFX.controller.meta").read_text(encoding="utf-8") == "guid: 1" * 32
    assert user_extension.read_text(encoding="utf-8") == "keep me"
    preservation = result["details"]["unmanagedPreservation"]
    assert preservation["schema"] == "vrcforge.unity_core_unmanaged_preservation.v1"
    assert "Generated/FinalAvatar_BaseFX.controller" in preservation["paths"]
    assert "Generated/FinalAvatar_BaseFX.controller.meta" in preservation["paths"]
    assert "Editor/UserExtension.txt" in preservation["paths"]


def test_invalid_payload_integrity_is_rejected_before_project_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload, integrity_valid=False)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )

    with pytest.raises(RuntimeError, match="integrity does not match"):
        dashboard_server.install_verified_unity_core_sync(
            {"projectPath": str(project)}
        )

    assert (project / "Assets" / "VRCForge" / "old-core.txt").read_text(encoding="utf-8") == "old"
    assert not (project / ".vrcforge").exists()


def test_packaged_generic_project_install_requires_verified_core_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload, integrity_valid=False)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)

    with pytest.raises(RuntimeError, match="integrity does not match"):
        dashboard_server.install_vrcforge_into_unity_project(
            project,
            require_verified_source=True,
        )

    assert (project / "Assets" / "VRCForge" / "old-core.txt").read_text(encoding="utf-8") == "old"
    assert not (project / ".vrcforge").exists()


def test_verified_unity_core_install_is_one_external_project_write() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001
        "vrcforge_install_unity_core"
    ]
    assert handler.risk_level == "high"
    assert handler.handler is dashboard_server.install_verified_unity_core_sync
    assert handler.request_preparer is dashboard_server.prepare_verified_unity_core_install_request
    assert "vrcforge_install_unity_core" in EXTERNAL_MCP_WRITE_TOOL_BLOCKS["project"]


def test_failed_core_install_restores_previous_tree_and_retains_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    source = _write_verified_payload(payload)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )
    original_copy = dashboard_server._copy_tree_clean_with_meta
    calls = 0

    def fail_first_install_copy(source_path: Path, destination_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1 and source_path.resolve() == source.resolve():
            destination_path.mkdir(parents=True, exist_ok=True)
            (destination_path / "partial.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("simulated install copy failure")
        original_copy(source_path, destination_path)

    monkeypatch.setattr(dashboard_server, "_copy_tree_clean_with_meta", fail_first_install_copy)
    result = dashboard_server.install_verified_unity_core_sync({"projectPath": str(project)})

    assert result["ok"] is False
    assert result["commitState"] == "rolled_back"
    assert result["checkpointRecoveryRequired"] is False
    assert (project / "Assets" / "VRCForge" / "old-core.txt").read_text(encoding="utf-8") == "old"
    retained = list((project / ".vrcforge" / "backups").glob("VRCForge_*"))
    assert retained
    assert any((candidate / "old-core.txt").is_file() for candidate in retained)


def test_manual_core_restore_requires_exact_receipt_and_preserves_safety_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )
    install = dashboard_server.install_verified_unity_core_sync({"projectPath": str(project)})

    restored = dashboard_server.restore_unity_core_sync(
        {
            "projectPath": str(project),
            "backupPath": install["backupPath"],
            "backupSha256": install["backupSha256"],
            "installedSha256": install["installedSha256"],
        }
    )

    assert restored["ok"] is True
    assert restored["commitState"] == "full"
    assert (project / "Assets" / "VRCForge" / "old-core.txt").read_text(encoding="utf-8") == "old"
    safety = Path(restored["safetyBackupPath"])
    assert (safety / "new-core.txt").read_text(encoding="utf-8") == "new"
    assert Path(install["backupPath"]).is_dir()


def test_manual_core_restore_refuses_changed_installed_tree_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )
    install = dashboard_server.install_verified_unity_core_sync({"projectPath": str(project)})
    (project / "Assets" / "VRCForge" / "later-user-change.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(dashboard_server.AgentGatewayError, match="changed after the receipt"):
        dashboard_server.restore_unity_core_sync(
            {
                "projectPath": str(project),
                "backupPath": install["backupPath"],
                "backupSha256": install["backupSha256"],
                "installedSha256": install["installedSha256"],
            }
        )

    assert (project / "Assets" / "VRCForge" / "later-user-change.txt").read_text(encoding="utf-8") == "keep"
    assert not list((project / ".vrcforge" / "backups").glob("VRCForgePreRestore_*"))


def test_failed_manual_core_restore_compensates_from_its_own_safety_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _write_verified_payload(payload)
    project = _write_project(tmp_path / "project")
    monkeypatch.setattr(dashboard_server, "ROOT_DIR", payload)
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )
    install = dashboard_server.install_verified_unity_core_sync({"projectPath": str(project)})
    backup = Path(install["backupPath"]).resolve()
    target = (project / "Assets" / "VRCForge").resolve()
    original_copy = dashboard_server._copy_tree_clean_with_meta
    failed = False

    def fail_selected_backup_copy(source_path: Path, destination_path: Path) -> None:
        nonlocal failed
        if not failed and source_path.resolve() == backup and destination_path.resolve() == target:
            failed = True
            dashboard_server._remove_path_with_meta(destination_path)
            destination_path.mkdir(parents=True)
            (destination_path / "partial.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("simulated restore copy failure")
        original_copy(source_path, destination_path)

    monkeypatch.setattr(dashboard_server, "_copy_tree_clean_with_meta", fail_selected_backup_copy)
    result = dashboard_server.restore_unity_core_sync(
        {
            "projectPath": str(project),
            "backupPath": install["backupPath"],
            "backupSha256": install["backupSha256"],
            "installedSha256": install["installedSha256"],
        }
    )

    assert result["ok"] is False
    assert result["commitState"] == "rolled_back"
    assert result["manualRecoveryRequired"] is False
    assert (target / "new-core.txt").read_text(encoding="utf-8") == "new"
    assert Path(result["safetyBackupPath"]).is_dir()
    assert backup.is_dir()


def test_manual_core_restore_is_one_high_risk_external_project_write() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001
        "vrcforge_restore_unity_core"
    ]
    assert handler.risk_level == "high"
    assert handler.handler is dashboard_server.restore_unity_core_sync
    assert "vrcforge_restore_unity_core" in EXTERNAL_MCP_WRITE_TOOL_BLOCKS["project"]


@pytest.mark.parametrize(
    ("runtime_version", "is_compiling", "error_count", "expected_status"),
    [
        ("1.7.10", False, 0, "ready"),
        ("1.7.8", True, 0, "compiling"),
        ("1.7.8", False, 1, "compile_failed_old_assembly_retained"),
    ],
)
def test_core_upgrade_status_distinguishes_reload_wait_from_compile_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_version: str,
    is_compiling: bool,
    error_count: int,
    expected_status: str,
) -> None:
    project = _write_project(tmp_path / "project")
    descriptor = project / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")
    expected = {
        "coreIdentity": "vrcforge.unity-core",
        "coreVersion": "1.7.10",
        "handshakeProtocol": "vrcforge.core-handshake.v1",
        "toolContractVersion": "73",
        "protocolRange": {"minimum": "2026-07-28", "maximum": "2026-07-28"},
        "versionSource": "compiled_constant",
    }
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )
    monkeypatch.setattr(dashboard_server, "_resolve_install_source_assets", lambda: tmp_path / "source")
    monkeypatch.setattr(
        dashboard_server,
        "_verified_unity_core_source_identity",
        lambda _source: {"compiledIdentity": expected},
    )
    monkeypatch.setattr(
        dashboard_server,
        "probe_unity_mcp_core_diagnostics",
        lambda *_args, **_kwargs: {
            "coreInfo": {
                **expected,
                "coreVersion": runtime_version,
            },
            "compileResult": {
                "structuredContent": {
                    "data": {
                        "isCompiling": is_compiling,
                        "captureComplete": not is_compiling,
                        "errorCount": error_count,
                        "warningCount": 0,
                        "capturedAt": "2026-08-22T00:00:02+00:00",
                        "errors": [{"message": "error CS0165"}] if error_count else [],
                        "warnings": [],
                    }
                }
            },
            "transportError": "",
            "handshakeError": None,
        },
    )

    result = dashboard_server.core_upgrade_status_sync(
        {
            "projectPath": str(project),
            "installedAt": "2026-08-22T00:00:01+00:00",
        }
    )

    assert result["status"] == expected_status
    assert result["ready"] is (expected_status == "ready")
    assert result["runtimeIdentityMatchesTarget"] is (runtime_version == "1.7.10")
    assert result["consoleErrorCount"] == error_count


def test_core_upgrade_status_reports_descriptor_missing_without_throwing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path / "project")
    expected = {
        "coreIdentity": "vrcforge.unity-core",
        "coreVersion": "1.7.10",
        "handshakeProtocol": "vrcforge.core-handshake.v1",
        "toolContractVersion": "73",
        "protocolRange": {"minimum": "2026-07-28", "maximum": "2026-07-28"},
        "versionSource": "compiled_constant",
    }
    core_source = project / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"
    core_source.parent.mkdir(parents=True)
    core_source.write_text("// fixture", encoding="utf-8")
    monkeypatch.setattr(
        dashboard_server,
        "resolve_target_project",
        lambda value: str(Path(value).resolve()),
    )
    monkeypatch.setattr(dashboard_server, "_resolve_install_source_assets", lambda: tmp_path / "source")
    monkeypatch.setattr(
        dashboard_server,
        "_verified_unity_core_source_identity",
        lambda _source: {"compiledIdentity": expected},
    )
    monkeypatch.setattr(
        dashboard_server,
        "_latest_vrcforge_core_startup_diagnostic",
        lambda: {
            "schema": "vrcforge.core_startup_diagnostic.v1",
            "message": "InvalidOperationException: exact lane mismatch",
            "source": "unity_editor_log",
        },
    )
    monkeypatch.setattr(
        dashboard_server,
        "probe_unity_mcp_core_diagnostics",
        lambda *_args, **_kwargs: pytest.fail("descriptor-missing status must not route Core diagnostics"),
    )

    result = dashboard_server.core_upgrade_status_sync({"projectPath": str(project)})

    assert result["ok"] is True
    assert result["status"] == "core_start_failed"
    assert result["ready"] is False
    assert result["runtimeCoreInfo"] is None
    assert result["consoleCaptureComplete"] is False
    assert result["diagnostics"]["descriptorPresent"] is False
    assert result["diagnostics"]["coreSourcePresent"] is True
    assert result["diagnostics"]["startupDiagnostic"]["message"] == (
        "InvalidOperationException: exact lane mismatch"
    )
    assert result["diagnostics"]["toolRoutingStarted"] is False
    assert result["diagnostics"]["mutationStarted"] is False
    assert result["diagnostics"]["commitState"] == "not_started"
