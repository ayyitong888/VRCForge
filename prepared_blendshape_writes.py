"""Small, dependency-free primitives for sealed Blendshape write requests.

The dashboard owns Unity reads and the gateway owns approval authority.  This
module deliberately owns only canonical evidence comparison and the
process-lifetime undo lock, so it cannot become a second execution path.
"""

from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import Any


BLENDSHAPE_UNDO_LOCK = RLock()


def canonical_sha256(value: Any) -> str:
    """Hash JSON-compatible evidence in one stable representation."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Blendshape execution evidence must be JSON-compatible.") from exc
    return hashlib.sha256(encoded).hexdigest()


def require_exact_evidence(expected: Any, actual: Any, label: str) -> None:
    """Fail closed when an approval-time fact differs at execution time."""
    if canonical_sha256(expected) != canonical_sha256(actual):
        raise RuntimeError(f"Prepared Blendshape {label} drifted after approval.")
