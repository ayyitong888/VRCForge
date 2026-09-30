from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server
import data_root_migration
import vrcforge_runtime_paths as runtime_paths


REPO_ROOT = Path(__file__).resolve().parent.parent


def build_legacy_root(root: Path) -> None:
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "keep.log").write_text("log", encoding="utf-8")
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "keep.json").write_text("{}", encoding="utf-8")
    (root / "backups").mkdir(parents=True)
    (root / "backups" / "keep.zip").write_bytes(b"zip")
    (root / "project-indexes").mkdir(parents=True)
    (root / "project-indexes" / "index.json").write_text("{}", encoding="utf-8")
    (root / "signing").mkdir(parents=True)
    (root / "signing" / "key.pem").write_text("key", encoding="utf-8")
    (root / "project-cache.json").write_text("{}", encoding="utf-8")


def isolate_program_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)


# ---------------------------------------------------------------------------
# LOG_DIR / ARTIFACTS_DIR follow VRCFORGE_DATA_DIR
# ---------------------------------------------------------------------------


def test_log_and_artifacts_dirs_follow_data_dir_env(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    probe = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "import vrcforge_runtime_paths as runtime_paths\n"
        "assert runtime_paths.DATA_DIR == Path(sys.argv[1]).resolve()\n"
        "import dashboard_server\n"
        "result = {'log': str(dashboard_server.LOG_DIR),"
        " 'artifacts': str(dashboard_server.ARTIFACTS_DIR)}\n"
        "print('PROBE_RESULT:' + json.dumps(result))\n"
    )
    env = dict(os.environ)
    env["VRCFORGE_DATA_DIR"] = str(data_dir)
    env["LOCALAPPDATA"] = str(tmp_path / "localappdata")
    env.pop("VRCFORGE_LOG_DIR", None)
    env.pop("VRCFORGE_ARTIFACTS_DIR", None)
    env.pop("VRCFORGE_PRIMITIVE_LIVE_STDIN", None)

    completed = subprocess.run(
        [sys.executable, "-c", probe, str(data_dir)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("PROBE_RESULT:")
    )
    payload = json.loads(result_line[len("PROBE_RESULT:") :])
    assert Path(payload["log"]) == (data_dir / "logs").resolve()
    assert Path(payload["artifacts"]) == (data_dir / "artifacts").resolve()


# ---------------------------------------------------------------------------
# Startup migration entry
# ---------------------------------------------------------------------------


def test_startup_migration_entry_moves_legacy_items(
    tmp_path: Path, monkeypatch
) -> None:
    old_root = tmp_path / "legacy"
    data_dir = tmp_path / "data"
    build_legacy_root(old_root)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: old_root)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *a, **k: None)

    report = dashboard_server.migrate_legacy_runtime_data_on_startup()

    assert report is not None
    assert set(report["moved"]) == set(data_root_migration.LEGACY_RUNTIME_ITEMS)
    for name in data_root_migration.LEGACY_RUNTIME_ITEMS:
        assert (data_dir / name).exists()
        assert not (old_root / name).exists()
    assert (data_dir / data_root_migration.MARKER_NAME).exists()


def test_startup_migration_entry_skips_existing_target_entries(
    tmp_path: Path, monkeypatch
) -> None:
    old_root = tmp_path / "legacy"
    data_dir = tmp_path / "data"
    build_legacy_root(old_root)
    (data_dir / "backups").mkdir(parents=True)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: old_root)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *a, **k: None)

    report = dashboard_server.migrate_legacy_runtime_data_on_startup()

    assert "backups" in report["skipped"]
    assert (old_root / "backups" / "keep.zip").exists()
    assert (data_dir / "logs").exists()


def test_startup_migration_failure_logs_error_and_never_raises(
    tmp_path: Path, monkeypatch
) -> None:
    logged: list[tuple] = []

    def boom(*args, **kwargs) -> dict:
        raise OSError("move failed")

    monkeypatch.setattr(runtime_paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(data_root_migration, "migrate_legacy_runtime_data", boom)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *a, **k: logged.append((a, k)))

    report = dashboard_server.migrate_legacy_runtime_data_on_startup()

    assert report is None
    assert logged
    level, scope, message, data = logged[0][0]
    assert level == "error"
    assert scope == "data-root"
    assert "migration failed" in message
    assert (data or {}).get("error") == "move failed"


# ---------------------------------------------------------------------------
# GET /api/app/settings/data-root
# ---------------------------------------------------------------------------


def test_get_data_root_reports_path_writable_and_program_files(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", data_dir)

    payload = dashboard_server.read_agentic_app_data_root()

    assert payload["path"] == str(data_dir)
    assert payload["writable"] is True
    assert payload["inProgramFiles"] is False
    assert payload["migrated"] is False


def test_get_data_root_reports_migrated_when_marker_exists(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / data_root_migration.MARKER_NAME).write_text("{}", encoding="utf-8")
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", data_dir)

    payload = dashboard_server.read_agentic_app_data_root()

    assert payload["migrated"] is True


# ---------------------------------------------------------------------------
# POST /api/app/settings/data-root
# ---------------------------------------------------------------------------


def post_data_root(client, path_value: str):
    return client.post("/api/app/settings/data-root", json={"path": path_value})


def test_post_rejects_program_files_target(tmp_path: Path, monkeypatch) -> None:
    pf = tmp_path / "Program Files"
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", tmp_path / "current")
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: tmp_path / "legacy")
    registry_writes: list[str] = []
    monkeypatch.setattr(runtime_paths, "write_registry_data_dir", registry_writes.append)

    with TestClient(dashboard_server.app) as client:
        response = post_data_root(client, str(pf / "VRCForgeData"))

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "errors": ["data root must not be under Program Files"],
    }
    assert registry_writes == []


def test_post_rejects_relative_and_empty_paths(tmp_path: Path, monkeypatch) -> None:
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", tmp_path / "current")
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: tmp_path / "legacy")

    with TestClient(dashboard_server.app) as client:
        relative = post_data_root(client, "relative/data")
        empty = post_data_root(client, "")

    assert relative.status_code == 200
    assert relative.json() == {"ok": False, "errors": ["data root path must be absolute"]}
    assert empty.status_code == 200
    assert empty.json() == {"ok": False, "errors": ["data root path is required"]}


def test_post_accepts_valid_path_migrates_and_writes_registry(
    tmp_path: Path, monkeypatch
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target-data"
    build_legacy_root(current)
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", current)
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: tmp_path / "legacy")
    registry_writes: list[str] = []
    monkeypatch.setattr(runtime_paths, "write_registry_data_dir", registry_writes.append)

    with TestClient(dashboard_server.app) as client:
        response = post_data_root(client, str(target))

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert set(payload["migrated"]) == set(data_root_migration.LEGACY_RUNTIME_ITEMS)
    assert registry_writes == [str(target)]
    for name in data_root_migration.LEGACY_RUNTIME_ITEMS:
        assert (target / name).exists()
        assert not (current / name).exists()
    assert (target / data_root_migration.MARKER_NAME).exists()
    # The running process keeps the old DATA_DIR; the registry pointer only
    # takes effect after a restart.
    assert runtime_paths.DATA_DIR == current


def test_post_migration_failure_returns_contract_error_without_registry_write(
    tmp_path: Path, monkeypatch
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    target = tmp_path / "target-data"
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", current)
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: tmp_path / "legacy")
    registry_writes: list[str] = []
    monkeypatch.setattr(runtime_paths, "write_registry_data_dir", registry_writes.append)

    def boom(old_root, new_dir, **kwargs) -> dict:
        raise OSError("move failed")

    monkeypatch.setattr(data_root_migration, "migrate_legacy_runtime_data", boom)

    with TestClient(dashboard_server.app) as client:
        response = post_data_root(client, str(target))

    assert response.status_code == 200
    assert response.json() == {"ok": False, "errors": ["data root migration failed"]}
    assert registry_writes == []


# ---------------------------------------------------------------------------
# Doctor report carries dataRoot
# ---------------------------------------------------------------------------


def test_doctor_report_includes_data_root(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    isolate_program_files(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", data_dir)

    report = dashboard_server.read_agentic_app_doctor()

    assert report["schema"] == "vrcforge.doctor.v1"
    assert report["dataRoot"]["path"] == str(data_dir)
    assert report["dataRoot"]["writable"] is True
    assert report["dataRoot"]["inProgramFiles"] is False
    # Existing doctor fields stay intact.
    assert "checks" in report
    assert "sections" in report
    assert "selectedUnityEnvironment" in report
