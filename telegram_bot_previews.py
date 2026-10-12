"""Bounded byte snapshots from completed, approved Unity captures.

Owned by one Telegram service lifecycle. No file/URL input is accepted here;
the approved capture handler supplies bytes and execution identity. Restart
invalidates previews rather than falling back to another project's last image.
"""
from __future__ import annotations

import base64
import hashlib
import threading
import time


class TelegramPreviews:
    def __init__(self, *, max_entries: int = 8, ttl: float = 900, clock=time.monotonic) -> None:
        self._entries: dict[tuple[str, str], tuple[float, bytes, str]] = {}
        self._max, self._ttl, self._clock = max_entries, ttl, clock
        self._lock = threading.Lock()

    def record(self, *, project: str, session: str, approval: str, image: bytes) -> bool:
        if not project or not session or not approval or not isinstance(image, bytes):
            return False
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or not 8 < len(image) <= 8 * 1024 * 1024:
            return False
        with self._lock:
            key = (project, session)
            self._entries.pop(key, None)
            while len(self._entries) >= self._max:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (self._clock(), image, approval)
        return True

    def message(self, project: str, session: str) -> dict | None:
        with self._lock:
            key = (project, session)
            value = self._entries.get(key)
            if value is None:
                return None
            captured, image, approval = value
            if self._clock() - captured >= self._ttl:
                self._entries.pop(key, None)
                return None
            digest = hashlib.sha256(image).hexdigest()
            return {"text": f"已批准的 Unity 截图 · {approval}\n截图不代表改模验证已通过。",
                    "photo": base64.b64encode(image).decode("ascii"), "sha256": digest}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
