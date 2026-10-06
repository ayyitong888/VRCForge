import zipfile
from pathlib import Path

from agent_gateway import AgentGateway
from checkpoint_storage_baseline import verify_duplicate_candidates


def _zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)


def test_checkpoint_storage_baseline_aggregates_bytes_and_duplicate_candidates(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    root = gateway.checkpoint_store_dir / "fixture"
    payload = b"same payload" * 20
    _zip(root / "one.zip", [("Assets/A.txt", payload), ("Assets/unique.txt", b"one")])
    _zip(root / "two.zip", [("Assets/B.txt", payload)])
    gateway.checkpoint_log_path.parent.mkdir(parents=True, exist_ok=True)
    gateway.checkpoint_log_path.write_text(
        '{"id":"one","strategy":"archive","archiveElapsedMs":12.5}\n'
        '{"id":"two","strategy":"archive","archiveElapsedMs":7}\n',
        encoding="utf-8",
    )

    result = gateway.checkpoint_recovery.checkpoint_storage_baseline()
    measurement = result["measurement"]
    assert result["ok"] is True
    assert measurement["archiveCount"] == 2
    assert measurement["memberCount"] == 3
    assert measurement["uncompressedBytes"] == len(payload) * 2 + 3
    assert measurement["duplicateCandidateGroups"] == 1
    assert measurement["duplicateCandidateMembers"] == 1
    assert measurement["estimatedDedupSavingsBytes"] > 0
    assert result["recordedArchiveCost"]["matchedArchiveRecordCount"] == 2
    assert result["recordedArchiveCost"]["archiveElapsedMsTotal"] == 19.5


def test_checkpoint_storage_baseline_does_not_read_member_payloads(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    archive_path = gateway.checkpoint_store_dir / "fixture" / "one.zip"
    _zip(archive_path, [("Assets/A.txt", b"payload")])

    def fail_read(*_args, **_kwargs):
        raise AssertionError("baseline must not read ZIP member payloads")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_read)
    result = gateway.checkpoint_recovery.checkpoint_storage_baseline()
    assert result["ok"] is True
    assert result["boundaries"]["payloadBytesRead"] is False
    assert result["boundaries"]["contentDigestsComputed"] is False


def test_checkpoint_storage_baseline_reports_corrupt_archive_without_mutation(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    archive_path = gateway.checkpoint_store_dir / "fixture" / "broken.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"not a zip")
    before = archive_path.read_bytes()

    result = gateway.checkpoint_recovery.checkpoint_storage_baseline()
    assert result["ok"] is False
    assert result["measurement"]["invalidArchiveCount"] == 1
    assert archive_path.read_bytes() == before
    assert result["boundaries"]["formatChanged"] is False


def test_duplicate_verification_streams_with_read_budget_and_cancellation(tmp_path):
    root = tmp_path / "store"
    payload = b"candidate" * 128
    _zip(root / "one.zip", [("A", payload)])
    _zip(root / "two.zip", [("B", payload)])

    limited = verify_duplicate_candidates(root, max_read_bytes=len(payload) - 1)
    assert limited["ok"] is False
    assert limited["budgetExceeded"] is True
    assert limited["bytesRead"] == 0
    assert limited["verifiedDedupSavingsBytes"] == 0

    calls = 0
    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 0

    cancelled = verify_duplicate_candidates(root, max_read_bytes=len(payload) * 2, cancel_check=cancel)
    assert cancelled["ok"] is False
    assert cancelled["cancelled"] is True
    assert cancelled["bytesRead"] == 0
