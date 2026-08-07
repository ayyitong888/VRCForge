from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_probe():
    path = REPO_ROOT / "scripts" / "diagnose_packaged_project_snapshot.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unity_fixture(tmp_path: Path) -> Path:
    probe = load_probe()
    return probe.create_disposable_unity_project(tmp_path / "runtime")


def snapshot(project: Path, *, selected: bool) -> dict:
    normalized = str(project.resolve()).replace("\\", "/")
    return {
        "selectedProjectPath": normalized if selected else "",
        "projects": [
            {
                "name": project.name,
                "path": normalized,
                "selected": selected,
                "source": "configured-root",
                "sources": ["configured-root", "manual"] if selected else ["configured-root"],
                "activeMcp": False,
                "sessionId": "",
                "cliInstanceId": "",
                "selectable": True,
            }
        ],
    }


def test_fixture_snapshot_requires_exactly_one_fixture_project(tmp_path: Path) -> None:
    probe = load_probe()
    fixture = unity_fixture(tmp_path)
    payload = snapshot(fixture, selected=True)

    assert probe.fixture_only_snapshot_matches(payload, fixture, selected=True) is True
    assert probe.normalized_path("") == ""
    payload["projects"].append({"name": "Host Unity", "path": "C:/real-project", "selected": False})
    assert probe.fixture_only_snapshot_matches(payload, fixture, selected=True) is False


def test_cache_and_selection_documents_require_schema_and_exact_fixture(tmp_path: Path) -> None:
    probe = load_probe()
    fixture = unity_fixture(tmp_path)
    cache_path = tmp_path / "user-data" / "project-cache.json"
    selection_path = tmp_path / "user-data" / "config" / "selected-project.json"
    cache_path.parent.mkdir(parents=True)
    selection_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps({"schema": probe.PROJECT_CACHE_SCHEMA, "snapshot": snapshot(fixture, selected=True)}),
        encoding="utf-8",
    )
    selection_path.write_text(
        json.dumps(
            {
                "schema": probe.PROJECT_SELECTION_SCHEMA,
                "selectedProjectPath": str(fixture.resolve()).replace("\\", "/"),
                "updatedAt": "2026-08-08T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    assert probe.project_cache_matches(cache_path, fixture, selected=True) is True
    assert probe.selection_document_matches(selection_path, fixture) is True
    selection_path.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    assert probe.selection_document_matches(selection_path, fixture) is False


def test_runtime_env_replaces_host_profile_and_config_scope(monkeypatch, tmp_path: Path) -> None:
    probe = load_probe()
    fixture = unity_fixture(tmp_path)
    monkeypatch.setenv("VRCFORGE_CONFIG_PATH", str(tmp_path / "host-config.json"))
    monkeypatch.setenv("VRCFORGE_DISABLE_APP_AUTH", "1")
    monkeypatch.setenv("VRCFORGE_UNITY_HOST", "host-unity.invalid")
    monkeypatch.setenv("APPDATA", str(tmp_path / "host-appdata"))

    env, user_data, cache_path, selection_path = probe.build_runtime_env(
        tmp_path / "package", tmp_path / "isolated-runtime", "session-token", fixture
    )

    assert Path(env["VRCFORGE_USER_DATA_DIR"]) == user_data
    assert Path(env["VRCFORGE_CONFIG_PATH"]).is_relative_to(tmp_path / "isolated-runtime")
    assert Path(env["APPDATA"]).is_relative_to(tmp_path / "isolated-runtime")
    assert Path(env["LOCALAPPDATA"]).is_relative_to(tmp_path / "isolated-runtime")
    assert Path(env["USERPROFILE"]).is_relative_to(tmp_path / "isolated-runtime")
    assert env["HOME"] == env["USERPROFILE"]
    assert env["VRCFORGE_APP_SESSION_TOKEN"] == "session-token"
    assert env["VRCFORGE_DESKTOP_EXECUTOR"] == "0"
    assert "VRCFORGE_DISABLE_APP_AUTH" not in env
    assert "VRCFORGE_UNITY_HOST" not in env
    settings = json.loads(Path(env["VRCFORGE_SETTINGS_PATH"]).read_text(encoding="utf-8"))
    assert settings["dashboard"]["project_roots"] == [str(fixture.parent)]
    assert cache_path.parent == user_data
    assert selection_path.parent == user_data / "config"


def test_auth_gate_requires_missing_and_wrong_tokens(monkeypatch) -> None:
    probe = load_probe()
    statuses = iter((401, 403))
    monkeypatch.setattr(probe, "request_status", lambda *_args, **_kwargs: next(statuses))
    assert probe.app_auth_rejects_missing_and_wrong_tokens("http://127.0.0.1:1") is True

    statuses = iter((200, 401))
    monkeypatch.setattr(probe, "request_status", lambda *_args, **_kwargs: next(statuses))
    assert probe.app_auth_rejects_missing_and_wrong_tokens("http://127.0.0.1:1") is False


def test_restart_recovery_uses_public_camel_case_state_contract(tmp_path: Path) -> None:
    probe = load_probe()
    fixture = unity_fixture(tmp_path)
    cache_path = tmp_path / "user-data" / "project-cache.json"
    selection_path = tmp_path / "user-data" / "config" / "selected-project.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"schema": probe.PROJECT_CACHE_SCHEMA, "snapshot": snapshot(fixture, selected=False)}),
        encoding="utf-8",
    )
    selection_path.write_text(
        json.dumps(
            {
                "schema": probe.PROJECT_SELECTION_SCHEMA,
                "selectedProjectPath": str(fixture.resolve()).replace("\\", "/"),
                "updatedAt": "2026-08-08T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    bootstrap = {
        "health": {
            "state": {"selectedProjectPath": str(fixture.resolve()).replace("\\", "/")}
        }
    }
    assert probe.restart_recovery_matches(
        bootstrap,
        snapshot(fixture, selected=False),
        cache_path,
        selection_path,
        fixture,
    ) is True
    bootstrap["health"]["state"] = {"selected_project_path": str(fixture.resolve())}
    assert probe.restart_recovery_matches(
        bootstrap,
        snapshot(fixture, selected=False),
        cache_path,
        selection_path,
        fixture,
    ) is False


def test_package_binding_requires_head_manifest_version_and_zip_digest(monkeypatch, tmp_path: Path) -> None:
    probe = load_probe()
    payload_zip = tmp_path / "VRCForge_Windows_x64_1.4.0.zip"
    payload_zip.write_bytes(b"fixture package")
    manifest_dir = tmp_path / "release"
    manifest_dir.mkdir()
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "current_head_commit", lambda: "abc123")
    digest = probe.sha256_file(payload_zip)
    (manifest_dir / "release-manifest.json").write_text(
        json.dumps(
            {
                "version": "1.4.0",
                "commit": "abc123",
                "artifacts": [{"name": payload_zip.name, "sha256": digest}],
            }
        ),
        encoding="utf-8",
    )
    # package_binding intentionally reads the normal release location below REPO_ROOT.
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "release").mkdir()
    (tmp_path / "dist" / "release" / "release-manifest.json").write_text(
        (manifest_dir / "release-manifest.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    binding = probe.package_binding("1.4.0", payload_zip)

    assert binding["ok"] is True
    assert binding["headCommit"] == "abc123"
    assert binding["payloadZipSha256"] == digest
    (tmp_path / "dist" / "release" / "release-manifest.json").write_text("{}", encoding="utf-8")
    assert probe.package_binding("1.4.0", payload_zip)["ok"] is False


def test_unpacked_root_must_match_every_payload_zip_file(tmp_path: Path) -> None:
    probe = load_probe()
    packaged_root = tmp_path / "package"
    (packaged_root / "backend").mkdir(parents=True)
    (packaged_root / "backend" / "vrcforge_backend.exe").write_bytes(b"backend")
    (packaged_root / "VERSION").write_text("1.4.0\n", encoding="utf-8")
    payload_zip = tmp_path / "payload.zip"
    with probe.zipfile.ZipFile(payload_zip, "w") as archive:
        archive.write(packaged_root / "backend" / "vrcforge_backend.exe", "backend/vrcforge_backend.exe")
        archive.write(packaged_root / "VERSION", "VERSION")
    assert probe.packaged_root_matches_zip(packaged_root, payload_zip) is True
    (packaged_root / "VERSION").write_text("old\n", encoding="utf-8")
    assert probe.packaged_root_matches_zip(packaged_root, payload_zip) is False


def test_session_token_is_scanned_in_payload_and_logs(tmp_path: Path) -> None:
    probe = load_probe()
    token = "project-snapshot-session-token"
    user_data = tmp_path / "user-data"
    (user_data / "logs").mkdir(parents=True)
    (tmp_path / "backend-first-stdout.log").write_text(token, encoding="utf-8")

    assert probe.contains_secret({"token": token}, {token}) is True
    assert probe.logs_exclude_secrets(tmp_path, user_data, {token}) is False
    assert probe.redact_text(f"error {token}", {token}) == "error <redacted>"


def test_probe_cli_exposes_payload_binding(monkeypatch) -> None:
    probe = load_probe()
    monkeypatch.setattr(
        probe.sys,
        "argv",
        ["diagnose_packaged_project_snapshot.py", "--payload-zip", "dist/release/example.zip"],
    )
    assert probe.parse_args().payload_zip == "dist/release/example.zip"
