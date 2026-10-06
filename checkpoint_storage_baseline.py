"""Read-only measurement of the current ZIP checkpoint store.

The baseline deliberately inspects ZIP central-directory metadata only.  It
does not read member payloads, calculate new digests, rewrite the ledger, or
change checkpoint retention and restore behavior.  CRC32 plus member size is
reported as a *candidate* duplicate key; it is not proof of byte identity.
"""

from __future__ import annotations

import json
import hashlib
import os
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from collections.abc import Callable


def _regular_zip_files(store_dir: Path) -> list[Path]:
    if not store_dir.is_dir() or store_dir.is_symlink():
        return []
    files: list[Path] = []
    for path in store_dir.rglob("*.zip"):
        if path.is_symlink() or not path.is_file():
            continue
        files.append(path)
    return sorted(files, key=lambda item: str(item).casefold())


def _ledger_archive_metrics(ledger_path: Path, checkpoint_ids: set[str]) -> dict[str, Any]:
    elapsed_total = 0.0
    elapsed_count = 0
    matched = 0
    if not ledger_path.is_file() or ledger_path.is_symlink():
        return {
            "matchedArchiveRecordCount": 0,
            "archiveElapsedMsTotal": 0.0,
            "archiveElapsedMsCount": 0,
        }
    try:
        lines = ledger_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return {
            "matchedArchiveRecordCount": 0,
            "archiveElapsedMsTotal": 0.0,
            "archiveElapsedMsCount": 0,
        }
    seen: set[str] = set()
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        checkpoint_id = str(record.get("id") or "").strip()
        if checkpoint_id not in checkpoint_ids or checkpoint_id in seen:
            continue
        if record.get("strategy") not in {"archive", "project_chat_archive", "local_state_archive"}:
            continue
        seen.add(checkpoint_id)
        matched += 1
        try:
            elapsed = float(record.get("archiveElapsedMs"))
        except (TypeError, ValueError):
            elapsed = -1.0
        if elapsed >= 0:
            elapsed_total += elapsed
            elapsed_count += 1
    return {
        "matchedArchiveRecordCount": matched,
        "archiveElapsedMsTotal": round(elapsed_total, 3),
        "archiveElapsedMsCount": elapsed_count,
    }


def measure_checkpoint_storage(store_dir: Path, ledger_path: Path) -> dict[str, Any]:
    """Measure the existing checkpoint store without reading member payloads."""

    started = time.perf_counter()
    archives = _regular_zip_files(store_dir)
    archive_bytes = 0
    member_count = 0
    uncompressed_bytes = 0
    compressed_member_bytes = 0
    duplicate_groups: dict[tuple[int, int], list[int]] = defaultdict(list)
    invalid_archives = 0
    checkpoint_ids = {path.stem for path in archives}

    for path in archives:
        try:
            archive_bytes += path.stat().st_size
            with zipfile.ZipFile(path, "r") as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    member_count += 1
                    uncompressed_bytes += int(info.file_size)
                    compressed_member_bytes += int(info.compress_size)
                    duplicate_groups[(int(info.CRC), int(info.file_size))].append(int(info.compress_size))
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            invalid_archives += 1

    duplicate_candidate_groups = 0
    duplicate_candidate_members = 0
    estimated_dedup_savings = 0
    for compressed_sizes in duplicate_groups.values():
        if len(compressed_sizes) < 2:
            continue
        duplicate_candidate_groups += 1
        duplicate_candidate_members += len(compressed_sizes) - 1
        estimated_dedup_savings += sum(compressed_sizes[1:])

    ledger = _ledger_archive_metrics(ledger_path, checkpoint_ids)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "ok": invalid_archives == 0,
        "schema": "vrcforge.checkpoint_storage_baseline.v1",
        "measurement": {
            "archiveCount": len(archives),
            "archiveBytes": archive_bytes,
            "memberCount": member_count,
            "uncompressedBytes": uncompressed_bytes,
            "compressedMemberBytes": compressed_member_bytes,
            "containerOverheadBytes": max(0, archive_bytes - compressed_member_bytes),
            "duplicateCandidateGroups": duplicate_candidate_groups,
            "duplicateCandidateMembers": duplicate_candidate_members,
            "estimatedDedupSavingsBytes": estimated_dedup_savings,
            "invalidArchiveCount": invalid_archives,
            "measurementElapsedMs": elapsed_ms,
        },
        "recordedArchiveCost": ledger,
        "boundaries": {
            "payloadBytesRead": False,
            "contentDigestsComputed": False,
            "duplicateKey": "crc32+uncompressed_size_candidate_only",
            "dedupSavingsIsEstimate": True,
            "formatChanged": False,
            "retentionChanged": False,
            "restoreBehaviorChanged": False,
        },
    }


def verify_duplicate_candidates(
    store_dir: Path,
    *,
    max_read_bytes: int,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Verify CRC/size duplicate candidates with bounded streaming SHA-256 reads."""

    started = time.perf_counter()
    budget = max(0, int(max_read_bytes))
    candidates: dict[tuple[int, int], list[tuple[Path, zipfile.ZipInfo]]] = defaultdict(list)
    invalid_archives = 0
    for path in _regular_zip_files(store_dir):
        try:
            with zipfile.ZipFile(path, "r") as archive:
                for info in archive.infolist():
                    if not info.is_dir():
                        candidates[(int(info.CRC), int(info.file_size))].append((path, info))
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            invalid_archives += 1

    groups = [items for items in candidates.values() if len(items) > 1]
    groups.sort(key=lambda items: sum(int(info.file_size) for _, info in items))
    hashes: dict[tuple[int, int], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    members_by_archive: dict[Path, list[tuple[tuple[int, int], zipfile.ZipInfo]]] = defaultdict(list)
    for key, items in candidates.items():
        if len(items) > 1:
            for path, info in items:
                members_by_archive[path].append((key, info))
    bytes_read = 0
    verified_members = 0
    cancelled = False
    budget_exceeded = False
    for path, members in members_by_archive.items():
        if cancel_check is not None and cancel_check():
            cancelled = True
            break
        try:
            with zipfile.ZipFile(path, "r") as archive:
                for key, info in members:
                    if cancel_check is not None and cancel_check():
                        cancelled = True
                        break
                    size = int(info.file_size)
                    if bytes_read + size > budget:
                        budget_exceeded = True
                        break
                    digest = hashlib.sha256()
                    member_cancelled = False
                    with archive.open(info, "r") as source:
                        while chunk := source.read(1024 * 1024):
                            if cancel_check is not None and cancel_check():
                                cancelled = True
                                member_cancelled = True
                                break
                            digest.update(chunk)
                            bytes_read += len(chunk)
                    if member_cancelled:
                        break
                    hashes[key][digest.hexdigest()].append(int(info.compress_size))
                    verified_members += 1
                if cancelled or budget_exceeded:
                    break
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            invalid_archives += 1
            break
        if cancelled or budget_exceeded:
            break

    verified_groups = 0
    verified_duplicate_members = 0
    verified_savings = 0
    for group_hashes in hashes.values():
        for compressed_sizes in group_hashes.values():
            if len(compressed_sizes) < 2:
                continue
            verified_groups += 1
            verified_duplicate_members += len(compressed_sizes) - 1
            verified_savings += sum(compressed_sizes[1:])
    return {
        "ok": invalid_archives == 0 and not cancelled and not budget_exceeded,
        "schema": "vrcforge.checkpoint_storage_duplicate_verification.v1",
        "verifiedDuplicateCandidateGroups": verified_groups,
        "verifiedDuplicateCandidateMembers": verified_duplicate_members,
        "verifiedDedupSavingsBytes": verified_savings,
        "candidateGroupCount": len(groups),
        "verifiedMemberCount": verified_members,
        "bytesRead": bytes_read,
        "maxReadBytes": budget,
        "budgetExceeded": budget_exceeded,
        "cancelled": cancelled,
        "invalidArchiveCount": invalid_archives,
        "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
        "boundaries": {
            "streamed": True,
            "wholeMemberInMemory": False,
            "formatChanged": False,
            "retentionChanged": False,
            "restoreBehaviorChanged": False,
        },
    }
