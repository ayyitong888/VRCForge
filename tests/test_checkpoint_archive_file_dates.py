"""Imported files may carry timestamps outside the DOS ZIP date range."""
import os
import zipfile
from pathlib import Path

import pytest

from agent_gateway import AgentGateway


@pytest.mark.parametrize("scope", ["project", "local_state"])
@pytest.mark.parametrize("timestamp, zip_year", [(0, 1980), (7258118400, 2107)])
def test_checkpoint_keeps_file_bytes_with_out_of_range_dates(tmp_path, scope, timestamp, zip_year):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.checkpoint_recovery
    project = tmp_path / "Project"
    if scope == "project":
        source = project / "Assets" / "Imported.txt"
        member = "Assets/Imported.txt"
    else:
        source = service._local_state_checkpoint_roots()["skills"] / "fixture" / "SKILL.md"
        member = "skills/fixture/SKILL.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    content = b"Imported content must survive checkpointing.\x00\xff"
    source.write_bytes(content)
    os.utime(source, (timestamp, timestamp))
    original_mtime = source.stat().st_mtime_ns
    record = {"id": "ckpt_import_dates", "projectRoot": str(project), "status": "unavailable"}
    checkpoint = (
        service._create_archive_checkpoint(project, record)
        if scope == "project" else service._create_local_state_checkpoint(record)
    )
    assert checkpoint["ok"] is True, checkpoint.get("error")
    assert checkpoint["status"] == "ready"
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert archive.testzip() is None
        assert archive.read(member) == content
        assert archive.getinfo(member).date_time[0] == zip_year
    assert source.read_bytes() == content
    assert source.stat().st_mtime_ns == original_mtime


def test_archive_checkpoint_records_scope_bytes_and_independent_timing(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Assets" / "A.txt").write_text("A", encoding="utf-8")
    record = {"id": "ckpt_timing", "projectRoot": str(project), "status": "unavailable"}

    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(project, record)

    assert checkpoint["ok"] is True
    assert checkpoint["checkpointScope"] == {
        "kind": "unity_project_top_level",
        "pathspecs": ["Assets"],
    }
    assert checkpoint["fileCount"] == 1
    assert checkpoint["uncompressedBytes"] == 1
    assert checkpoint["archiveBytes"] == Path(checkpoint["archivePath"]).stat().st_size
    assert checkpoint["archiveElapsedMs"] >= 0
    assert checkpoint["archiveStartedAt"]
    assert checkpoint["archiveFinishedAt"]
