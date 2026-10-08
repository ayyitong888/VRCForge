"""Run the production startup migration without starting the application."""
import ast
import json
from pathlib import Path

import pytest

import data_root_migration
import vrcforge_runtime_paths as runtime_paths


def startup_migration():
    source = Path(__file__).resolve().parents[1] / "dashboard_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "migrate_legacy_runtime_data_on_startup")
    namespace = {"runtime_paths": runtime_paths, "data_root_migration": data_root_migration,
                 "emit_log": lambda *args, **kwargs: None}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[function.name]()


@pytest.mark.parametrize("profile_exists", [False, True])
def test_explicit_profile_never_migrates_default_user_data(tmp_path, monkeypatch, profile_exists):
    default = tmp_path / "default-profile"
    profile = tmp_path / "test-profile"
    destination = tmp_path / "runtime-data"
    for name in ("logs", "artifacts"):
        (default / name).mkdir(parents=True)
        (default / name / "original.bin").write_bytes(b"preserve-default-contents")
        if profile_exists:
            (profile / name).mkdir(parents=True)
            (profile / name / "isolated.bin").write_bytes(b"isolated-contents")
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: default)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", destination)
    monkeypatch.setenv("VRCFORGE_USER_DATA_DIR", str(profile))

    report = startup_migration()

    assert report is not None
    assert report["oldRoot"] == str(profile.resolve())
    for name in ("logs", "artifacts"):
        assert (default / name / "original.bin").read_bytes() == b"preserve-default-contents"
        assert not (destination / name / "original.bin").exists()
        if profile_exists:
            assert (destination / name / "isolated.bin").read_bytes() == b"isolated-contents"
    if profile_exists:
        marker = json.loads((destination / data_root_migration.MARKER_NAME).read_text())
        assert marker["migratedFrom"] == str(profile.resolve())


def test_default_profile_migration_still_works(tmp_path, monkeypatch):
    default = tmp_path / "default-profile"
    (default / "artifacts").mkdir(parents=True)
    (default / "artifacts/history.json").write_bytes(b"history")
    destination = tmp_path / "runtime-data"
    monkeypatch.delenv("VRCFORGE_USER_DATA_DIR", raising=False)
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: default)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", destination)

    report = startup_migration()

    assert report["moved"] == ["artifacts"]
    assert (destination / "artifacts/history.json").read_bytes() == b"history"
