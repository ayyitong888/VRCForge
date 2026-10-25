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


__all__ = ["replace_with_retry"]
