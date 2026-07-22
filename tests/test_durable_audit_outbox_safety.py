from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agent_memory_store
import durable_audit_outbox as audit_module
from durable_audit_outbox import DurableMetadataAudit


SCHEMA = "vrcforge.test_metadata_audit.v1"


def _audit(tmp_path: Path) -> DurableMetadataAudit:
    return DurableMetadataAudit(
        tmp_path / "audit.jsonl",
        schema=SCHEMA,
        allowed_fields={"event", "count"},
    )


def _encoded(row: dict[str, Any]) -> bytes:
    return (
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")


def test_stage_commit_flush_and_append_remain_exactly_once(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    first = audit.stage({"event": "candidate_finished", "count": 1})
    second = audit.stage({"event": "candidate_finished", "count": 1})
    assert first["eventId"] == second["eventId"]
    assert len(audit.pending()) == 1

    assert audit.commit_staged(second) is True
    assert audit.commit_staged(second) is True
    assert not audit.outbox_path.exists()
    assert audit.append({"event": "candidate_finished", "count": 1}) is True

    rows = [json.loads(line) for line in audit.audit_path.read_text(encoding="utf-8").splitlines()]
    assert [row["eventId"] for row in rows] == [first["eventId"]]


@pytest.mark.parametrize("damage", ["malformed", "bad_digest", "unsupported_field"])
def test_invalid_outbox_never_moves_any_row_into_permanent_audit(
    tmp_path: Path,
    damage: str,
) -> None:
    audit = _audit(tmp_path)
    row = audit.prepare({"event": "candidate_finished", "count": 1})
    if damage == "malformed":
        payload = b'{"eventId":\n'
    else:
        damaged = dict(row)
        if damage == "bad_digest":
            damaged["count"] = 2
        else:
            damaged["unexpected"] = "blocked"
        payload = _encoded(damaged)
    outbox_path = tmp_path / "audit.outbox.jsonl"
    outbox_path.write_bytes(payload)

    assert audit.flush() is False
    assert not (tmp_path / "audit.jsonl").exists()
    with pytest.raises(ValueError):
        audit.pending()
    assert outbox_path.read_bytes() == payload


def test_valid_outbox_prefix_is_not_partially_flushed_before_later_damage(
    tmp_path: Path,
) -> None:
    audit = _audit(tmp_path)
    valid = audit.prepare({"event": "valid_prefix", "count": 1})
    damaged = audit.prepare({"event": "damaged_suffix", "count": 2})
    damaged["count"] = 3
    payload = _encoded(valid) + _encoded(damaged)
    outbox_path = tmp_path / "audit.outbox.jsonl"
    outbox_path.write_bytes(payload)

    assert audit.flush() is False
    assert not (tmp_path / "audit.jsonl").exists()
    assert outbox_path.read_bytes() == payload


def test_oversized_outbox_line_fails_closed_before_audit_append(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    outbox_path = tmp_path / "audit.outbox.jsonl"
    payload = b"x" * (audit_module.MAX_AUDIT_LINE_BYTES + 1)
    outbox_path.write_bytes(payload)

    assert audit.flush() is False
    assert not (tmp_path / "audit.jsonl").exists()
    with pytest.raises(ValueError, match="oversized row"):
        audit.pending()


def test_oversized_permanent_audit_blocks_append_without_mutation(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    original = b"x" * (audit_module.MAX_AUDIT_LINE_BYTES + 1)
    audit_path.write_bytes(original)

    assert audit.append_prepared(audit.prepare({"event": "safe", "count": 1})) is False
    assert audit_path.read_bytes() == original


def test_oversized_outbox_file_fails_closed_before_reading_rows(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    outbox_path = tmp_path / "audit.outbox.jsonl"
    with outbox_path.open("wb") as handle:
        handle.seek(audit_module.MAX_AUDIT_FILE_BYTES)
        handle.write(b"x")

    assert audit.flush() is False
    assert not (tmp_path / "audit.jsonl").exists()
    with pytest.raises(ValueError, match="size limit"):
        audit.pending()


def test_outbox_row_count_limit_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_module, "MAX_AUDIT_ROWS", 2)
    audit = _audit(tmp_path)
    rows = [
        audit.prepare({"event": f"event_{index}", "count": index})
        for index in range(3)
    ]
    (tmp_path / "audit.outbox.jsonl").write_bytes(b"".join(_encoded(row) for row in rows))

    assert audit.flush() is False
    assert not (tmp_path / "audit.jsonl").exists()
    with pytest.raises(ValueError, match="row limit"):
        audit.pending()


def test_invalid_permanent_audit_blocks_append_without_changing_bytes(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    original = b'{"eventId":"audit_invalid"}\n'
    audit_path.write_bytes(original)

    assert audit.append_prepared(audit.prepare({"event": "safe", "count": 1})) is False
    assert audit_path.read_bytes() == original


@pytest.mark.parametrize("target_kind", ["audit", "outbox"])
def test_symlink_target_is_never_read_rewritten_or_unlinked(
    tmp_path: Path,
    target_kind: str,
) -> None:
    audit = _audit(tmp_path)
    external = tmp_path / "outside.jsonl"
    original = b'external-target-must-remain-unchanged\n'
    external.write_bytes(original)
    linked = tmp_path / ("audit.jsonl" if target_kind == "audit" else "audit.outbox.jsonl")
    _symlink_or_skip(linked, external)
    row = audit.prepare({"event": "safe", "count": 1})

    if target_kind == "audit":
        assert audit.append_prepared(row) is False
    else:
        assert audit.flush() is False
        assert audit._rewrite_outbox([row]) is False

    assert external.read_bytes() == original
    assert linked.is_symlink()


@pytest.mark.parametrize("target_kind", ["audit", "outbox"])
def test_directory_target_is_rejected_as_non_regular(
    tmp_path: Path,
    target_kind: str,
) -> None:
    audit = _audit(tmp_path)
    target = tmp_path / ("audit.jsonl" if target_kind == "audit" else "audit.outbox.jsonl")
    target.mkdir()
    row = audit.prepare({"event": "safe", "count": 1})

    if target_kind == "audit":
        assert audit.append_prepared(row) is False
    else:
        assert audit.flush() is False
        assert audit._rewrite_outbox([row]) is False

    assert target.is_dir()


@pytest.mark.parametrize("target_kind", ["audit", "outbox"])
def test_reparse_attribute_is_rejected_without_changing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    audit = _audit(tmp_path)
    target = tmp_path / ("audit.jsonl" if target_kind == "audit" else "audit.outbox.jsonl")
    row = audit.prepare({"event": "safe", "count": 1})
    original = _encoded(row) if target_kind == "outbox" else b""
    target.write_bytes(original)
    original_lstat = agent_memory_store.os.lstat

    def reparse_lstat(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> Any:
        metadata = original_lstat(path, *args, **kwargs)
        if os.path.normcase(os.path.abspath(os.fspath(path))) == os.path.normcase(str(target.absolute())):
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=0x400,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
            )
        return metadata

    monkeypatch.setattr(agent_memory_store.os, "lstat", reparse_lstat)
    if target_kind == "audit":
        assert audit.append_prepared(row) is False
    else:
        assert audit.flush() is False

    assert target.read_bytes() == original


def test_open_handle_path_identity_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = _audit(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_bytes(_encoded(audit.prepare({"event": "safe", "count": 1})))
    original_lstat = agent_memory_store.os.lstat
    target_calls = 0

    def swapped_lstat(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> Any:
        nonlocal target_calls
        metadata = original_lstat(path, *args, **kwargs)
        if os.path.normcase(os.path.abspath(os.fspath(path))) != os.path.normcase(str(audit_path.absolute())):
            return metadata
        target_calls += 1
        if target_calls != 3:
            return metadata
        return SimpleNamespace(
            st_mode=stat.S_IFREG | stat.S_IRUSR | stat.S_IWUSR,
            st_file_attributes=int(getattr(metadata, "st_file_attributes", 0) or 0),
            st_dev=int(getattr(metadata, "st_dev", 1) or 1),
            st_ino=int(getattr(metadata, "st_ino", 1) or 1) + 1,
        )

    monkeypatch.setattr(agent_memory_store.os, "lstat", swapped_lstat)
    with pytest.raises(OSError, match="changed while it was being opened"):
        audit._read(audit_path, label="Durable audit ledger")
    assert target_calls == 3
