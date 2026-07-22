from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import agent_memory_store as memory_store_module
from agent_memory_store import AgentMemoryStore, MAX_MEMORY_JSONL_LINE_BYTES


def _store(tmp_path: Path, log_path: Path | None = None) -> AgentMemoryStore:
    return AgentMemoryStore(
        log_path or tmp_path / "agent-memory.jsonl",
        tmp_path / "memory-audit.jsonl",
    )


def _event(memory_id: str, **values: object) -> bytes:
    payload: dict[str, object] = {
        "schema": "vrcforge.agent_memory.v1",
        "event": "memory_created",
        "status": "active",
        "memoryId": memory_id,
        "createdAt": "2026-07-22T00:00:00+00:00",
        "updatedAt": "2026-07-22T00:00:00+00:00",
        **values,
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def test_projection_streams_large_log_and_preserves_early_active_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "agent-memory.jsonl"
    with log_path.open("wb") as handle:
        handle.write(_event("mem_early", text="Keep the first durable value.", kind="preference"))
        for index in range(12_000):
            handle.write(
                _event(
                    "mem_early",
                    event="memory_updated",
                    updatedAt=f"2026-07-22T00:{index // 60 % 60:02d}:{index % 60:02d}+00:00",
                )
            )
        handle.write(b'{"memoryId":"mem_oversized","text":"')
        handle.write(b"x" * (MAX_MEMORY_JSONL_LINE_BYTES + 64 * 1024))
        handle.write(b"\n")
        handle.write(_event("mem_late", text="A later durable value.", kind="fact"))

    projected = _store(tmp_path, log_path).project()

    assert set(projected) == {"mem_early", "mem_late"}
    assert projected["mem_early"]["text"] == "Keep the first durable value."
    assert projected["mem_early"]["kind"] == "preference"
    assert projected["mem_late"]["text"] == "A later durable value."


def test_primary_store_directory_fails_closed_for_read_append_rewrite_and_erase(tmp_path: Path) -> None:
    log_path = tmp_path / "agent-memory.jsonl"
    log_path.mkdir()
    store = _store(tmp_path, log_path)

    with pytest.raises(OSError, match="regular file"):
        store.project()
    with pytest.raises(OSError, match="regular file"):
        store._append_jsonl(log_path, {"memoryId": "mem_one"})
    with pytest.raises(OSError, match="regular file"):
        store._atomic_rewrite(log_path, b"replacement\n")
    with pytest.raises(OSError, match="regular file"):
        store.physical_erase("mem_one")


def test_primary_store_link_never_reads_or_changes_external_target(tmp_path: Path) -> None:
    external = tmp_path / "external.jsonl"
    original = _event("mem_external", text="External content must remain unchanged.")
    external.write_bytes(original)
    log_path = tmp_path / "agent-memory.jsonl"
    try:
        log_path.symlink_to(external)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"File links are unavailable in this environment: {exc}")
    store = _store(tmp_path, log_path)

    with pytest.raises(OSError, match="link or reparse point"):
        store.project()
    with pytest.raises(OSError, match="link or reparse point"):
        store._append_jsonl(log_path, {"memoryId": "mem_new"})
    with pytest.raises(OSError, match="link or reparse point"):
        store._atomic_rewrite(log_path, b"replacement\n")
    with pytest.raises(OSError, match="link or reparse point"):
        store.physical_erase("mem_external")

    assert external.read_bytes() == original
    assert log_path.is_symlink()


def test_primary_store_windows_reparse_attribute_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "agent-memory.jsonl"
    log_path.write_bytes(_event("mem_one", text="Safe before attribute injection."))
    real_lstat = os.lstat

    class ReparseMetadata:
        def __init__(self, wrapped: os.stat_result) -> None:
            self._wrapped = wrapped
            self.st_file_attributes = (
                int(getattr(wrapped, "st_file_attributes", 0) or 0)
                | memory_store_module._FILE_ATTRIBUTE_REPARSE_POINT
            )

        def __getattr__(self, name: str) -> object:
            return getattr(self._wrapped, name)

    def injected_lstat(path: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
        metadata = real_lstat(path, *args, **kwargs)
        if Path(path) == log_path:
            return ReparseMetadata(metadata)  # type: ignore[return-value]
        return metadata

    monkeypatch.setattr(memory_store_module.os, "lstat", injected_lstat)

    with pytest.raises(OSError, match="link or reparse point"):
        _store(tmp_path, log_path).project()
