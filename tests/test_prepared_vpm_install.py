from __future__ import annotations

from pathlib import Path
import json

import pytest

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, prepared_call, prepared_evidence


def _identity(path: Path) -> dict:
    return {"identity": {"path": str(path.resolve())}, "sha256": "a" * 64}


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    return project


def test_vpm_preparer_freezes_fixed_cli_version_and_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"; cli.write_bytes(b"x")
    monkeypatch.setattr(dashboard_server, "locate_vpm_package_managers", lambda: [{"name": "vrc-get", "path": str(cli), "supportsCommandInstall": True}])
    monkeypatch.setattr(dashboard_server, "_select_package_install_strategy", lambda *_args: {"commandInstaller": {"name": "vrc-get", "path": str(cli)}})
    monkeypatch.setattr(dashboard_server, "_sealed_vpm_file_identity", _identity)
    monkeypatch.setattr(dashboard_server, "_vpm_cli_version_and_package_info", lambda *_args: ("1.9.1", {"versions": [{"version": "1.2.3"}]}))
    prepared, _ = dashboard_server.prepare_vpm_package_install_request({"projectPath": str(project), "packageId": "com.example.package"}, None)
    assert prepared_call(prepared)[1]["argv"][-2:] == ["com.example.package", "1.2.3"]
    assert prepared_evidence(prepared)["packageVersion"] == "1.2.3"
    assert prepared["packageVersion"] == "1.2.3"


def test_vpm_preparer_rejects_repository_and_reserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_vpm_package_install_request({"projectPath": str(project), "packageId": "com.example.package", PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)
    monkeypatch.setattr(dashboard_server, "locate_vpm_package_managers", lambda: [])
    with pytest.raises(RuntimeError, match="Repository changes"):
        dashboard_server.prepare_vpm_package_install_request({"projectPath": str(project), "packageId": "com.example.package", "repository": "https://example.invalid"}, None)


def test_vpm_handler_is_not_a_fake_core_execution_target() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_install_vpm_package"]  # noqa: SLF001
    assert handler.request_preparer is dashboard_server.prepare_vpm_package_install_request
    assert handler.requires_approved_execution_context is False
    assert "vrcforge_install_vpm_package" not in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS


def test_vpm_semver_selects_stable_or_exact_prerelease() -> None:
    info = {"versions": [
        {"version": "1.9.0-beta.2"},
        {"version": "1.9.0-beta.10"},
        {"version": "1.8.9"},
        {"version": "1.9.0", "yanked": True},
        {"version": "2.0"},
    ]}
    assert dashboard_server._select_sealed_vpm_version(info, "", False) == "1.8.9"
    assert dashboard_server._select_sealed_vpm_version(info, "", True) == "1.9.0-beta.10"
    assert dashboard_server._select_sealed_vpm_version(info, "1.9.0-beta.2", True) == "1.9.0-beta.2"
    with pytest.raises(RuntimeError, match="unavailable"):
        dashboard_server._select_sealed_vpm_version(info, "1.9.0-beta.2", False)


def _prepare_real_vpm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    version: str = "1.2.3",
) -> tuple[Path, dict]:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    monkeypatch.setattr(dashboard_server, "locate_vpm_package_managers", lambda: [{"name": "vrc-get", "path": str(cli), "supportsCommandInstall": True}])
    monkeypatch.setattr(dashboard_server, "_select_package_install_strategy", lambda *_args: {"commandInstaller": {"name": "vrc-get", "path": str(cli)}})
    monkeypatch.setattr(dashboard_server, "_vpm_cli_version_and_package_info", lambda *_args: ("1.9.1", {"versions": [{"version": version}]}))
    prepared, _ = dashboard_server.prepare_vpm_package_install_request({"projectPath": str(project), "packageId": "com.example.package"}, None)
    return project, prepared


def _process_result(returncode: int = 0):
    return dashboard_server.BoundedProcessResult(returncode, "ok", "", False, False)


def test_vpm_execution_requires_exact_vpm_manifest_readback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, prepared = _prepare_real_vpm(monkeypatch, tmp_path)

    def run(_argv, **_kwargs):
        (project / "Packages" / "vpm-manifest.json").write_text(
            json.dumps({"dependencies": {"com.example.package": "1.2.3"}, "locked": {"com.example.package": {"version": "1.2.3"}}}),
            encoding="utf-8",
        )
        return _process_result()

    monkeypatch.setattr(dashboard_server, "_run_vpm_process", run)
    result = dashboard_server.install_vpm_package_sync(prepared)
    assert result["ok"] is True
    assert result["vpmManifestReadback"] == {
        "packageId": "com.example.package",
        "version": "1.2.3",
        "source": "vpm-manifest.locked",
        "embeddedPackage": None,
    }


def test_vpm_prestate_drift_blocks_before_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, prepared = _prepare_real_vpm(monkeypatch, tmp_path)
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{"drift":"1.0.0"}}', encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "_run_vpm_process", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("process must not run")))
    result = dashboard_server.install_vpm_package_sync(prepared)
    assert result["ok"] is False
    assert result["recovery"]["commitState"] == "not_started"


def test_vpm_nonzero_exit_is_conservatively_unknown_and_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_path, prepared = _prepare_real_vpm(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard_server, "_run_vpm_process", lambda *_args, **_kwargs: _process_result(7))
    result = dashboard_server.install_vpm_package_sync(prepared)
    assert result["ok"] is False
    assert result["recovery"] == {
        "checkpointMustBeRestoredOnlyIfUserChooses": True,
        "committed": True,
        "commitState": "unknown",
    }


def test_vpm_wrong_readback_requires_checkpoint_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, prepared = _prepare_real_vpm(monkeypatch, tmp_path)

    def run(_argv, **_kwargs):
        (project / "Packages" / "vpm-manifest.json").write_text(
            json.dumps({"dependencies": {"com.example.package": "9.9.9"}, "locked": {"com.example.package": {"version": "9.9.9"}}}),
            encoding="utf-8",
        )
        return _process_result()

    monkeypatch.setattr(dashboard_server, "_run_vpm_process", run)
    result = dashboard_server.install_vpm_package_sync(prepared)
    assert result["ok"] is False
    assert result["recovery"]["committed"] is True
    assert result["recovery"]["commitState"] == "unknown"
