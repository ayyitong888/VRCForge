from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path
from subprocess import CompletedProcess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_installer_install_uninstall.py"


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
        "install_dir": str(tmp_path / "Program Files" / "VRCForge"),
        "user_data_root": "",
        "timeout": 1.0,
        "backend_port": 8791,
        "dry_run": False,
        "allow_blocked": False,
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


def test_dry_run_writes_skipped_phase_evidence_without_admin_or_userdata_changes(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
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
    assert report["userData"]["root"] == str((local_app_data / "VRCForge" / "agentic-app").resolve())
    assert not (local_app_data / "VRCForge" / "agentic-app").exists()


def test_dry_run_allows_missing_installer_but_records_it(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
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


def test_admin_upgrade_path_preserves_user_data_after_uninstall(tmp_path, monkeypatch):
    smoke = load_installer_smoke()
    local_app_data = tmp_path / "LocalAppData"
    install_dir = tmp_path / "Program Files" / "VRCForge"
    first_installer = tmp_path / "old" / "VRCForge_Offline_Installer_x64.exe"
    upgrade_installer = tmp_path / "new" / "VRCForge_Offline_Installer_x64.exe"
    first_installer.parent.mkdir(parents=True)
    upgrade_installer.parent.mkdir(parents=True)
    first_installer.write_bytes(b"old")
    upgrade_installer.write_bytes(b"new")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(smoke, "is_admin", lambda: True)
    monkeypatch.setattr(smoke, "wait_for_health", lambda port, timeout: {"version": "0.9-test", "portableMode": True})

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(smoke, "start_installed_backend", lambda args, install_root, user_data_root: FakeProcess())

    def fake_run(cmd, **kwargs):
        if Path(cmd[0]) in {first_installer.resolve(), upgrade_installer.resolve()}:
            (install_dir / "backend").mkdir(parents=True, exist_ok=True)
            (install_dir / "dashboard").mkdir(parents=True, exist_ok=True)
            (install_dir / "VRCForge.exe").write_text("desktop", encoding="utf-8")
            (install_dir / "backend" / "vrcforge_backend.exe").write_text("backend", encoding="utf-8")
            (install_dir / "dashboard" / "index.html").write_text("dashboard", encoding="utf-8")
            (install_dir / "Uninstall.exe").write_text("uninstall", encoding="utf-8")
            return CompletedProcess(cmd, 0, "", "")
        if Path(cmd[0]) == install_dir / "Uninstall.exe":
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
    assert sentinel.parent == local_app_data / "VRCForge" / "agentic-app"
    assert not install_dir.exists()
