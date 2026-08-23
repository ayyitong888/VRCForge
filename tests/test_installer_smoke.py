from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path
from subprocess import CompletedProcess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_installer_install_uninstall.py"
SMOKE_ID = "a" * 32


def load_installer_smoke():
    spec = importlib.util.spec_from_file_location("smoke_installer_install_uninstall", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_args(tmp_path: Path, installer: Path, **overrides: object) -> Namespace:
    values = {
        "installer": str(installer),
        "upgrade_installer": "",
        "scope": "isolated-smoke",
        "production_clean_confirmation": "",
        "smoke_id": SMOKE_ID,
        "install_dir": str(tmp_path / "Program Files" / f"VRCForge-Smoke-{SMOKE_ID}"),
        "user_data_root": str(tmp_path / "LocalAppData" / "VRCForge" / "installer-smoke" / SMOKE_ID),
        "timeout": 1.0,
        "backend_port": 8791,
        "dry_run": False,
        "allow_blocked": False,
        "artifacts_dir": "",
    }
    values.update(overrides)
    return Namespace(**values)


def test_default_user_data_root_matches_tauri_backend_contract(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    root = smoke.default_user_data_root()

    assert root == local_app_data / "VRCForge" / "agentic-app"
    step = smoke.user_data_root_step(root.resolve())
    assert step["ok"] is True
    assert step["matchesTauriAndBackendDefault"] is True
    assert step["legacyRoots"]["config"].endswith(str(Path("VRCForge") / "config"))


def test_user_data_root_override_is_valid_but_not_default(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    local_app_data = tmp_path / "LocalAppData"
    override = tmp_path / "custom-user-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    step = smoke.user_data_root_step(override.resolve(), override_used=True)

    assert step["ok"] is True
    assert step["overrideUsed"] is True
    assert step["matchesTauriAndBackendDefault"] is False
    assert step["expectedDefault"] == str((local_app_data / "VRCForge" / "agentic-app").resolve())


def test_default_install_dir_uses_windows_program_files_env(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    program_files = tmp_path / "ProgramFiles"
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramW6432", raising=False)

    assert smoke.default_install_dir() == program_files / "VRCForge"


def test_dry_run_writes_skipped_phase_evidence_without_admin_or_userdata_changes(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    installer.write_bytes(b"fake-installer")

    report = smoke.run_smoke(make_args(tmp_path, installer, dry_run=True))

    assert report["ok"] is True
    assert report["summary"]["status"] == "skipped"
    assert report["summary"]["phases"] == {
        "install": "skipped",
        "uninstall": "skipped",
        "upgrade": "skipped",
        "preservation": "skipped",
    }
    assert report["userData"]["root"] == str((local_app_data / "VRCForge" / "installer-smoke" / SMOKE_ID).resolve())
    assert report["userData"]["matchesSmokeScope"] is True
    assert report["smoke"]["id"] == SMOKE_ID
    assert not (local_app_data / "VRCForge" / "installer-smoke" / SMOKE_ID).exists()


def test_dry_run_allows_missing_installer_but_records_it(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    missing_installer = tmp_path / "missing.exe"

    report = smoke.run_smoke(make_args(tmp_path, missing_installer, dry_run=True))

    assert report["ok"] is True
    assert report["summary"]["status"] == "skipped"
    assert report["steps"][0]["name"] == "installer.exists"
    assert report["steps"][0]["ok"] is True
    assert report["steps"][0]["exists"] is False
    assert report["steps"][0]["required"] is False


def test_non_admin_report_marks_install_uninstall_blocked_and_upgrade_skipped(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(smoke, "is_admin", lambda: False)
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    installer.write_bytes(b"fake-installer")

    report = smoke.run_smoke(make_args(tmp_path, installer))

    assert report["ok"] is False
    assert report["summary"]["status"] == "blocked"
    assert report["summary"]["phases"]["install"] == "blocked"
    assert report["summary"]["phases"]["uninstall"] == "blocked"
    assert report["summary"]["phases"]["upgrade"] == "skipped"
    assert report["summary"]["phases"]["preservation"] == "skipped"
    assert "admin elevation" in report["summary"]["blockedReason"]


def test_nsis_install_command_keeps_custom_dir_unquoted_and_last(tmp_path):
    smoke = load_installer_smoke()
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    install_dir = tmp_path / "Program Files" / "VRCForge Smoke"

    command = smoke.nsis_install_command(installer, install_dir)

    assert command.startswith(f'"{installer}" /S ')
    assert command.endswith(f"/D={install_dir}")
    assert f'"/D={install_dir}"' not in command


def test_write_report_supports_custom_artifacts_dir(tmp_path):
    smoke = load_installer_smoke()
    report_dir = tmp_path / "reports"
    report = {
        "ok": True,
        "summary": {"status": "passed"},
    }

    path = smoke.write_report(report, str(report_dir))

    assert path.parent == report_dir.resolve()
    assert path.is_file()


def test_empty_directory_detection_only_accepts_empty_dirs(tmp_path):
    smoke = load_installer_smoke()
    empty_dir = tmp_path / "empty"
    filled_dir = tmp_path / "filled"
    empty_dir.mkdir()
    filled_dir.mkdir()
    (filled_dir / "file.txt").write_text("x", encoding="utf-8")

    assert smoke.is_empty_directory(empty_dir) is True
    assert smoke.is_empty_directory(filled_dir) is False
    assert smoke.is_empty_directory(tmp_path / "missing") is False


def test_runtime_settings_are_seeded_for_direct_installed_backend_probe(tmp_path):
    smoke = load_installer_smoke()
    config_dir = tmp_path / "user-data" / "config"
    config_dir.mkdir(parents=True)

    settings_path = smoke.ensure_runtime_settings(config_dir)

    assert settings_path == config_dir / "settings.json"
    payload = smoke.read_json_file(settings_path)
    assert payload["dashboard"]["project_roots"] == []
    assert payload["paths"]["blendshape_export"] == "Assets/VRCForge/blendshapes_export.json"


def test_runtime_settings_preserve_existing_user_data(tmp_path):
    smoke = load_installer_smoke()
    config_dir = tmp_path / "user-data" / "config"
    config_dir.mkdir(parents=True)
    settings_path = config_dir / "settings.json"
    settings_path.write_text('{"custom": true}\n', encoding="utf-8")

    assert smoke.ensure_runtime_settings(config_dir) == settings_path
    assert smoke.read_json_file(settings_path) == {"custom": True}


def test_failure_before_install_does_not_remove_preexisting_directory(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    installer.write_bytes(b"fake-installer")
    install_dir = tmp_path / "Program Files" / f"VRCForge-Smoke-{SMOKE_ID}"
    install_dir.mkdir(parents=True)
    marker = install_dir / "owned-by-user.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(smoke, "is_admin", lambda: True)

    report = smoke.run_smoke(make_args(tmp_path, installer, install_dir=str(install_dir)))

    assert report["ok"] is False
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not any(str(step["name"]).startswith("failure_cleanup") for step in report["steps"])


def test_health_failure_uninstalls_only_payload_created_by_smoke(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    installer.write_bytes(b"fake-installer")
    install_dir = tmp_path / "Program Files" / f"VRCForge-Smoke-{SMOKE_ID}"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(smoke, "is_admin", lambda: True)
    monkeypatch.setattr(smoke, "wait_for_health", lambda port, timeout, process=None: {})

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(smoke, "start_installed_backend", lambda args, install_root, user_data_root: FakeProcess())
    monkeypatch.setattr(
        smoke,
        "inspect_installed_shortcuts",
        lambda install_root, smoke_id, phase, timeout: {"name": f"shortcuts.{phase}", "ok": True},
    )

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, str) and f'"{installer.resolve()}"' in cmd:
            (install_dir / "backend").mkdir(parents=True)
            (install_dir / "dashboard").mkdir(parents=True)
            (install_dir / "VRCForge.exe").write_text("desktop", encoding="utf-8")
            (install_dir / "VERSION").write_text("1.0-test\n", encoding="utf-8")
            (install_dir / "backend" / "vrcforge_backend.exe").write_text("backend", encoding="utf-8")
            (install_dir / "dashboard" / "index.html").write_text("dashboard", encoding="utf-8")
            (install_dir / "Uninstall.exe").write_text("uninstall", encoding="utf-8")
            return CompletedProcess(cmd, 0, "", "")
        if isinstance(cmd, list) and Path(cmd[0]) == install_dir / "Uninstall.exe":
            for path in sorted(install_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            install_dir.rmdir()
            return CompletedProcess(cmd, 0, "", "")
        return CompletedProcess(cmd, 1, "", "unexpected command")

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    report = smoke.run_smoke(make_args(tmp_path, installer, install_dir=str(install_dir)))

    assert report["ok"] is False
    assert report["summary"]["phases"]["install"] == "passed"
    assert report["summary"]["phases"]["uninstall"] == "passed"
    assert any(step["name"] == "failure_cleanup.removed" and step["ok"] for step in report["steps"])
    assert not install_dir.exists()


def test_admin_upgrade_path_preserves_user_data_after_uninstall(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    local_app_data = tmp_path / "LocalAppData"
    install_dir = tmp_path / "Program Files" / f"VRCForge-Smoke-{SMOKE_ID}"
    first_installer = tmp_path / "old" / "VRCForge_Offline_Installer_x64.exe"
    upgrade_installer = tmp_path / "new" / "VRCForge_Offline_Installer_x64.exe"
    first_installer.parent.mkdir(parents=True)
    upgrade_installer.parent.mkdir(parents=True)
    first_installer.write_bytes(b"old")
    upgrade_installer.write_bytes(b"new")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(smoke, "is_admin", lambda: True)
    monkeypatch.setattr(smoke, "wait_for_health", lambda port, timeout, process=None: {"version": "0.9-test", "portableMode": True})

    class FakeProcess:
        pid = 5678

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(smoke, "start_installed_backend", lambda args, install_root, user_data_root: FakeProcess())
    monkeypatch.setattr(
        smoke,
        "inspect_installed_shortcuts",
        lambda install_root, smoke_id, phase, timeout: {"name": f"shortcuts.{phase}", "ok": True},
    )

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, str) and any(f'"{path.resolve()}"' in cmd for path in (first_installer, upgrade_installer)):
            assert cmd.endswith(f"/D={install_dir}")
            (install_dir / "backend").mkdir(parents=True, exist_ok=True)
            (install_dir / "dashboard").mkdir(parents=True, exist_ok=True)
            (install_dir / "VRCForge.exe").write_text("desktop", encoding="utf-8")
            (install_dir / "VERSION").write_text("0.9-test\n", encoding="utf-8")
            (install_dir / "backend" / "vrcforge_backend.exe").write_text("backend", encoding="utf-8")
            (install_dir / "dashboard" / "index.html").write_text("dashboard", encoding="utf-8")
            (install_dir / "Uninstall.exe").write_text("uninstall", encoding="utf-8")
            return CompletedProcess(cmd, 0, "", "")
        if isinstance(cmd, list) and Path(cmd[0]) == install_dir / "Uninstall.exe":
            assert kwargs["cwd"] == str(install_dir.parent)
            for path in sorted(install_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            install_dir.rmdir()
            return CompletedProcess(cmd, 0, "", "")
        return CompletedProcess(cmd, 1, "", "unexpected command")

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    report = smoke.run_smoke(
        make_args(
            tmp_path,
            upgrade_installer,
            upgrade_installer=str(first_installer),
            install_dir=str(install_dir),
        )
    )

    assert report["ok"] is True
    assert report["summary"]["phases"] == {
        "install": "passed",
        "uninstall": "passed",
        "upgrade": "passed",
        "preservation": "passed",
    }
    sentinel = Path(report["userData"]["sentinelPath"])
    assert sentinel.is_file()
    assert sentinel.parent == local_app_data / "VRCForge" / "installer-smoke" / SMOKE_ID
    assert not install_dir.exists()


def test_shortcut_contract_requires_installed_target_working_directory_and_icon(tmp_path):
    smoke = load_installer_smoke()
    install_dir = tmp_path / "Program Files" / "VRCForge"
    expected_target = install_dir / "VRCForge.exe"
    expected_icon = install_dir / "VRCForge.ico"
    payload = [
        {
            "kind": kind,
            "path": str(tmp_path / f"{kind}.lnk"),
            "exists": True,
            "targetPath": str(expected_target),
            "workingDirectory": str(install_dir),
            "iconLocation": f"{expected_icon}, 0",
        }
        for kind in ("desktop", "start-menu")
    ]

    passed = smoke.evaluate_shortcut_contract(payload, install_dir, phase="after_install")
    assert passed["ok"] is True
    assert all(entry["ok"] for entry in passed["entries"])

    payload[1]["iconLocation"] = ""
    failed = smoke.evaluate_shortcut_contract(payload, install_dir, phase="after_upgrade")
    assert failed["ok"] is False
    assert failed["entries"][1]["checks"]["iconPath"] is False
    assert failed["entries"][1]["checks"]["iconIndex"] is False


def test_production_start_menu_identity_uses_current_user_programs(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    app_data = tmp_path / "Roaming"
    monkeypatch.setenv("APPDATA", str(app_data))

    assert smoke.production_start_menu_dir() == (
        app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "VRCForge"
    )


def test_smoke_scope_rejects_missing_or_mismatched_identity(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    program_files = tmp_path / "Program Files"
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("ProgramW6432", raising=False)

    expected_install = program_files / f"VRCForge-Smoke-{SMOKE_ID}"
    expected_user_data = local_app_data / "VRCForge" / "installer-smoke" / SMOKE_ID
    assert smoke.smoke_scope_step(SMOKE_ID, expected_install.resolve(), expected_user_data.resolve())["ok"] is True
    assert smoke.smoke_scope_step("A" * 32, expected_install.resolve(), expected_user_data.resolve())["ok"] is False
    assert smoke.smoke_scope_step(SMOKE_ID, (program_files / "VRCForge").resolve(), expected_user_data.resolve())["ok"] is False
    assert smoke.smoke_scope_step(SMOKE_ID, expected_install.resolve(), (local_app_data / "VRCForge" / "agentic-app").resolve())["ok"] is False


def test_production_clean_scope_requires_exact_confirmation_paths_and_zero_existing_identity(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    program_files = tmp_path / "Program Files"
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(smoke, "production_identity_presence", lambda install_dir, user_data_root: [])
    install_dir = (program_files / "VRCForge").resolve()
    user_data_root = (local_app_data / "VRCForge" / "agentic-app").resolve()
    args = Namespace(production_clean_confirmation=smoke.PRODUCTION_CLEAN_CONFIRMATION)

    step = smoke.production_clean_scope_step(args, "", install_dir, user_data_root)

    assert step["ok"] is True
    assert step["productionIdentity"] is True
    assert step["existingIdentities"] == []
    assert smoke.production_clean_scope_step(Namespace(production_clean_confirmation=""), "", install_dir, user_data_root)["ok"] is False
    assert smoke.production_clean_scope_step(args, SMOKE_ID, install_dir, user_data_root)["ok"] is False
    monkeypatch.setattr(smoke, "production_identity_presence", lambda install_dir, user_data_root: ["install-dir:occupied"])
    assert smoke.production_clean_scope_step(args, "", install_dir, user_data_root)["ok"] is False


def test_production_clean_dry_run_records_exact_release_identity_without_mutation(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    program_files = tmp_path / "Program Files"
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(smoke, "production_identity_presence", lambda install_dir, user_data_root: [])
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    installer.write_bytes(b"strict-installer")

    report = smoke.run_smoke(
        make_args(
            tmp_path,
            installer,
            scope="production-clean",
            production_clean_confirmation=smoke.PRODUCTION_CLEAN_CONFIRMATION,
            smoke_id="",
            install_dir=str(program_files / "VRCForge"),
            user_data_root=str(local_app_data / "VRCForge" / "agentic-app"),
            dry_run=True,
        )
    )

    assert report["ok"] is True
    assert report["schema"] == "vrcforge.installer_install_uninstall_smoke.v2"
    assert report["scope"] == {
        "mode": "production-clean",
        "productionIdentity": True,
        "cleanEnvironmentConfirmed": True,
    }
    assert report["smoke"]["id"] == ""
    assert not (local_app_data / "VRCForge" / "agentic-app").exists()


def test_dry_run_rejects_an_unscoped_or_mismatched_smoke_target(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    installer = tmp_path / "VRCForge_Offline_Installer_x64.exe"
    installer.write_bytes(b"fake-installer")

    report = smoke.run_smoke(
        make_args(tmp_path, installer, smoke_id="", install_dir=str(tmp_path / "Program Files" / "VRCForge"), dry_run=True)
    )

    assert report["ok"] is False
    assert report["summary"]["status"] == "failed"
    assert report["steps"][-1]["name"] == "installer_smoke.error"
    assert "exact isolated smoke identity" in report["steps"][-1]["error"]
