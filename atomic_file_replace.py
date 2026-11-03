"""Bounded retry for atomic file replaces on Windows.

os.replace on Windows fails with WinError 5/32/33 when an antivirus scanner,
a search indexer, or a short-lived reader holds the target or the temporary
file. Those holders release within milliseconds, so a short bounded retry on
exactly those sharing violations removes a whole class of transient
persistence failures without masking real permission problems.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# WinError 5 (ACCESS_DENIED), 32 (SHARING_VIOLATION), 33 (LOCK_VIOLATION).
_SHARING_VIOLATIONS = frozenset({5, 32, 33})
_RETRY_DELAYS_SECONDS = (0.025, 0.05, 0.1, 0.2)


def replace_with_retry(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """os.replace with a bounded retry for transient Windows sharing violations."""
    last_error: OSError | None = None
    for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _SHARING_VIOLATIONS:
                raise
            last_error = exc
            if attempt < len(_RETRY_DELAYS_SECONDS):
                time.sleep(_RETRY_DELAYS_SECONDS[attempt])
    assert last_error is not None
    raise last_error


# Append-only JSONL audit ledgers grow without bound (approvals reached 2GB
# because single records run ~1MB), so every full-file scan turns into a cold
# multi-gigabyte read. Rotation keeps the active file bounded; readers that
# need history fall back to the numbered generations, newest first.
JSONL_ROTATE_MAX_BYTES = 64 * 1024 * 1024
JSONL_ROTATE_GENERATIONS = 2


def iter_jsonl_generations(
    path: os.PathLike[str] | Path,
    *,
    generations: int = JSONL_ROTATE_GENERATIONS,
) -> list[Path]:
    """Return the active ledger plus its rotated generations, newest first."""
    active = Path(path)
    return [active] + [active.with_name(f"{active.name}.{gen}") for gen in range(1, generations + 1)]


def rotate_jsonl_if_large(
    path: os.PathLike[str] | Path,
    *,
    max_bytes: int | None = None,
    generations: int = JSONL_ROTATE_GENERATIONS,
) -> bool:
    """Rotate an append-only JSONL ledger once it exceeds max_bytes.

    Best-effort: auditing must never fail because housekeeping did. The caller
    is expected to hold the ledger's append lock so rotation cannot race a
    concurrent append.
    """
    active = Path(path)
    try:
        if max_bytes is None:
            max_bytes = JSONL_ROTATE_MAX_BYTES
        if not active.exists() or active.stat().st_size < max_bytes:
            return False
        active.with_name(f"{active.name}.{generations}").unlink(missing_ok=True)
        for gen in range(generations - 1, 0, -1):
            source = active.with_name(f"{active.name}.{gen}")
            if source.exists():
                replace_with_retry(source, active.with_name(f"{active.name}.{gen + 1}"))
        replace_with_retry(active, active.with_name(f"{active.name}.1"))
        return True
    except OSError:
        return False


__all__ = ["replace_with_retry", "iter_jsonl_generations", "rotate_jsonl_if_large"]
