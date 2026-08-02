"""Small, dependency-free state guards for prepared shader tuning writes.

The dashboard owns Unity scans and calls; this module owns neither authority
nor I/O.  Its sole purpose is to make approval-time evidence and the
process-lifetime shader undo stack comparable without creating another write
route.
"""

from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import Any


SHADER_UNDO_LOCK = RLock()


def canonical_sha256(value: Any) -> str:
    """Return a stable digest for JSON-compatible execution evidence."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Shader execution evidence must be JSON-compatible.") from exc
    return hashlib.sha256(encoded).hexdigest()


def require_exact_evidence(expected: Any, actual: Any, label: str) -> None:
    """Reject a prepared action when a live prerequisite has changed."""
    if canonical_sha256(expected) != canonical_sha256(actual):
        raise RuntimeError(f"Prepared shader {label} drifted after approval.")
