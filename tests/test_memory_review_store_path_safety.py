from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agent_memory_store
from memory_consolidation import MemoryConsolidator, MemoryReviewStore
from memory_consolidation_sources import admit_memory_source, resolve_memory_scope


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")


def _candidate_store(tmp_path: Path) -> tuple[MemoryReviewStore, Path, dict[str, Any]]:
    project = tmp_path / "Project"
    project.mkdir()
    scope = resolve_memory_scope(
        "project",
        str(project),
        authorized_project_roots=[str(project)],
    )
    source = admit_memory_source(
        {
            "sourceType": "user_chat",
            "sourceId": "chat_path_safety",
            "sourceRevision": "1",
            "role": "user",
            "status": "completed",
            "signalKind": "preference",
            "text": "Prefer bounded review evidence.",
            "projectRoot": str(project),
        },
        scope=scope,
    )
    assert source is not None
    store_path = tmp_path / "review.json"
    store = MemoryReviewStore(store_path, tmp_path / "audit.jsonl")

    def provider(payload: dict[str, Any]) -> dict[str, Any]:
        source_row = payload["sources"][0]
        return {
            "candidates": [
                {
                    "kind": "preference",
                    "text": "Prefer bounded review evidence.",
                    "sourceIds": [source_row["sourceId"]],
                    "confidenceFactors": ["explicit_user_intent"],
                }
            ]
        }

    result = MemoryConsolidator(store).run(
        mode="suggest_only",
        sources=[source],
        expected_revision=0,
        provider=provider,
    )
    return store, store_path, result


def test_primary_symlink_fails_closed_without_rewriting_external_target(tmp_path: Path) -> None:
    external = tmp_path / "outside.json"
    original = b'external-target-must-stay-unchanged\n'
    external.write_bytes(original)
    store_path = tmp_path / "review.json"
    _symlink_or_skip(store_path, external)

    store = MemoryReviewStore(store_path, tmp_path / "audit.jsonl")
    with pytest.raises(OSError, match="link or reparse"):
        store.snapshot()

    assert external.read_bytes() == original
    assert store_path.is_symlink()


def test_atomic_write_rejects_primary_symlink_without_rewriting_target(tmp_path: Path) -> None:
    external = tmp_path / "outside.json"
    original = b'external-atomic-write-target\n'
    external.write_bytes(original)
    store_path = tmp_path / "review.json"
    _symlink_or_skip(store_path, external)

    with pytest.raises(OSError, match="link or reparse"):
        MemoryReviewStore._atomic_write(
            store_path,
            MemoryReviewStore._default_state(),
        )

    assert external.read_bytes() == original
    assert store_path.is_symlink()


def test_primary_reparse_attribute_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "review.json"
    store_path.write_text("{}", encoding="utf-8")
    original_lstat = agent_memory_store.os.lstat

    def reparse_lstat(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> Any:
        metadata = original_lstat(path, *args, **kwargs)
        if os.path.normcase(os.path.abspath(os.fspath(path))) == os.path.normcase(str(store_path.absolute())):
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=0x400,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
            )
        return metadata

    monkeypatch.setattr(agent_memory_store.os, "lstat", reparse_lstat)
    with pytest.raises(OSError, match="link or reparse"):
        MemoryReviewStore(store_path, tmp_path / "audit.jsonl").snapshot()


def test_primary_directory_is_rejected_as_non_regular(tmp_path: Path) -> None:
    store_path = tmp_path / "review.json"
    store_path.mkdir()
    with pytest.raises(OSError, match="regular file"):
        MemoryReviewStore(store_path, tmp_path / "audit.jsonl").snapshot()


@pytest.mark.parametrize("copy_kind", ["backup", "temporary"])
def test_managed_copy_symlink_fails_closed_without_unlinking_or_rewriting_target(
    tmp_path: Path,
    copy_kind: str,
) -> None:
    external = tmp_path / "outside.json"
    original = b'external-managed-copy-target\n'
    external.write_bytes(original)
    store_path = tmp_path / "review.json"
    backup = tmp_path / "review.backup.json"
    if copy_kind == "backup":
        _symlink_or_skip(backup, external)
        store = MemoryReviewStore(
            store_path,
            tmp_path / "audit.jsonl",
            backup_paths=[backup],
        )
        linked_path = backup
    else:
        linked_path = tmp_path / f".{store_path.name}.{os.getpid()}.deadbeef.tmp"
        _symlink_or_skip(linked_path, external)
        store = MemoryReviewStore(store_path, tmp_path / "audit.jsonl")

    with pytest.raises(OSError, match="link or reparse"):
        store.snapshot()

    assert external.read_bytes() == original
    assert linked_path.is_symlink()


@pytest.mark.parametrize("copy_kind", ["backup", "temporary"])
def test_managed_copy_directory_is_rejected_as_non_regular(
    tmp_path: Path,
    copy_kind: str,
) -> None:
    store_path = tmp_path / "review.json"
    backup = tmp_path / "review.backup.json"
    if copy_kind == "backup":
        backup.mkdir()
        store = MemoryReviewStore(
            store_path,
            tmp_path / "audit.jsonl",
            backup_paths=[backup],
        )
    else:
        temporary = tmp_path / f".{store_path.name}.{os.getpid()}.deadbeef.tmp"
        temporary.mkdir()
        store = MemoryReviewStore(store_path, tmp_path / "audit.jsonl")

    with pytest.raises(OSError, match="regular file"):
        store.snapshot()


def test_open_handle_and_path_identity_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "review.json"
    store_path.write_text(
        json.dumps(MemoryReviewStore._default_state(), sort_keys=True),
        encoding="utf-8",
    )
    original_lstat = agent_memory_store.os.lstat
    target_calls = 0

    def swapped_lstat(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> Any:
        nonlocal target_calls
        metadata = original_lstat(path, *args, **kwargs)
        if os.path.normcase(os.path.abspath(os.fspath(path))) != os.path.normcase(str(store_path.absolute())):
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
    store = MemoryReviewStore(store_path, tmp_path / "audit.jsonl")
    with pytest.raises(OSError, match="changed while it was being opened"):
        store._load_path(store_path, absent_ok=False)
    assert target_calls == 3


def test_permanent_erase_rejects_late_atomic_fragment_with_candidate_prose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, store_path, result = _candidate_store(tmp_path)
    candidate = result["candidates"][0]
    candidate_id = candidate["candidateId"]
    prose = candidate["proposedText"]
    pre_erase_bytes = store_path.read_bytes()
    fragment = tmp_path / f".{store_path.name}.{os.getpid()}.feedface.tmp"
    original_atomic_write = store._atomic_write
    injected = False

    def atomic_write_with_late_fragment(path: Path, payload: dict[str, Any]) -> None:
        nonlocal injected
        original_atomic_write(path, payload)
        if Path(path) == store_path and not injected:
            fragment.write_bytes(pre_erase_bytes)
            injected = True

    monkeypatch.setattr(store, "_atomic_write", atomic_write_with_late_fragment)
    with pytest.raises(OSError, match="candidate content"):
        store.physical_erase(candidate_id, expected_revision=result["revision"])

    assert fragment.exists()
    assert prose in fragment.read_text(encoding="utf-8")
    assert prose not in store_path.read_text(encoding="utf-8")

    monkeypatch.setattr(store, "_atomic_write", original_atomic_write)
    resumed = store.physical_erase(
        candidate_id,
        expected_revision=store.snapshot(include_internal=True)["revision"],
    )
    assert resumed["alreadyAbsent"] is True
    assert not fragment.exists()
    assert prose not in store_path.read_text(encoding="utf-8")
