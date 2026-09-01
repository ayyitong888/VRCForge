from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import agent_checkpoint_recovery
from agent_gateway import AgentGateway


def _project(root: Path) -> Path:
    project = root / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Assets" / "avatar.txt").write_text("avatar", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3", encoding="utf-8"
    )
    return project


def _archive_checkpoint(root: Path) -> tuple[AgentGateway, dict[str, object]]:
    gateway = AgentGateway(root / "config.json", root / "audit")
    project = _project(root)
    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(
        project,
        {
            "schema": "vrcforge.checkpoint.v1",
            "id": "responsive-checkpoint",
            "createdAt": "2026-08-26T00:00:00+00:00",
            "targetTool": "vrcforge_set_property",
            "status": "unavailable",
            "projectRoot": str(project),
        },
    )
    assert checkpoint["ok"] is True
    return gateway, checkpoint


def test_repeated_archive_previews_validate_crc_once_until_archive_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, checkpoint = _archive_checkpoint(tmp_path)
    calls = 0
    real_testzip = zipfile.ZipFile.testzip

    def count_testzip(archive: zipfile.ZipFile) -> str | None:
        nonlocal calls
        calls += 1
        return real_testzip(archive)

    monkeypatch.setattr(agent_checkpoint_recovery.zipfile.ZipFile, "testzip", count_testzip)

    first = gateway.checkpoint_recovery.preview_restore_checkpoint(
        {"checkpointId": checkpoint["id"]}
    )
    second = gateway.checkpoint_recovery.preview_restore_checkpoint(
        {"checkpointId": checkpoint["id"]}
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert calls == 1

    with zipfile.ZipFile(Path(str(checkpoint["archivePath"])), "a") as archive:
        archive.writestr("Assets/another.txt", "another")

    changed = gateway.checkpoint_recovery.preview_restore_checkpoint(
        {"checkpointId": checkpoint["id"]}
    )
    assert changed["ok"] is True
    assert calls == 2


def test_changed_checkpoint_metadata_invalidates_archive_validation_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, checkpoint = _archive_checkpoint(tmp_path)
    calls = 0
    real_testzip = zipfile.ZipFile.testzip

    def count_testzip(archive: zipfile.ZipFile) -> str | None:
        nonlocal calls
        calls += 1
        return real_testzip(archive)

    monkeypatch.setattr(agent_checkpoint_recovery.zipfile.ZipFile, "testzip", count_testzip)

    assert gateway.checkpoint_recovery._checkpoint_available(checkpoint)["ok"] is True
    assert gateway.checkpoint_recovery._checkpoint_available(checkpoint)["ok"] is True
    updated = {**checkpoint, "pathspecs": ["Assets"]}
    assert gateway.checkpoint_recovery._checkpoint_available(updated)["ok"] is True
    assert calls == 2


def test_missing_archive_is_listed_unavailable_and_preview_never_opens_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, checkpoint = _archive_checkpoint(tmp_path)
    Path(str(checkpoint["archivePath"])).unlink()

    def unexpected_zip(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("A missing archive must fail before any ZIP read.")

    monkeypatch.setattr(agent_checkpoint_recovery.zipfile, "ZipFile", unexpected_zip)

    listed = gateway.checkpoint_recovery.list_checkpoints()
    assert listed["count"] == 1
    assert listed["checkpoints"][0]["status"] == "unavailable"
    assert listed["checkpoints"][0]["available"] is False
    assert listed["checkpoints"][0]["availabilityReasonCode"] == "checkpoint_archive_missing"

    preview = gateway.checkpoint_recovery.preview_restore_checkpoint(
        {"checkpointId": checkpoint["id"]}
    )
    assert preview["ok"] is False
    assert preview["status"] == "unavailable"
    assert preview["reasonCode"] == "checkpoint_archive_missing"
    assert "missing" in preview["error"]
    assert str(tmp_path) not in json.dumps(preview["error"])
    assert preview["nextAction"]


def test_missing_pathspec_is_unavailable_before_archive_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, checkpoint = _archive_checkpoint(tmp_path)
    incomplete = {**checkpoint, "pathspecs": []}

    def unexpected_zip(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Incomplete scope must fail before any ZIP read.")

    monkeypatch.setattr(agent_checkpoint_recovery.zipfile, "ZipFile", unexpected_zip)

    result = gateway.checkpoint_recovery._checkpoint_available(incomplete)
    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["reasonCode"] == "checkpoint_scope_metadata_invalid"


def test_checkpoint_list_reuses_parsed_log_until_log_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, checkpoint = _archive_checkpoint(tmp_path)
    log_path = gateway.checkpoint_recovery._ports.checkpoint_log_path()
    calls = 0
    real_read_bytes = Path.read_bytes

    def count_log_reads(path: Path) -> bytes:
        nonlocal calls
        if path == log_path:
            calls += 1
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_log_reads)

    first = gateway.checkpoint_recovery.list_checkpoints()
    second = gateway.checkpoint_recovery.list_checkpoints()
    assert first["count"] == second["count"] == 1
    assert calls == 1

    updated = {**checkpoint, "id": "newer-checkpoint"}
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(updated) + "\n")

    refreshed = gateway.checkpoint_recovery.list_checkpoints()
    assert refreshed["count"] == 2
    assert calls == 2


def test_checkpoint_list_never_crc_scans_available_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, _checkpoint = _archive_checkpoint(tmp_path)

    def unexpected_testzip(_archive: zipfile.ZipFile) -> str | None:
        raise AssertionError("Listing checkpoints must not CRC-scan archive payloads.")

    monkeypatch.setattr(agent_checkpoint_recovery.zipfile.ZipFile, "testzip", unexpected_testzip)

    assert gateway.checkpoint_recovery.list_checkpoints()["count"] == 1
    assert gateway.checkpoint_recovery.list_checkpoints()["count"] == 1
