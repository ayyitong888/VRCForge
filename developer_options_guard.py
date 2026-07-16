from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable


DEVELOPER_CHALLENGE_SCHEMA = "vrcforge.developer_options_challenge.v1"
DEVELOPER_CHALLENGE_WAIT_MS = 5_000
DEVELOPER_CHALLENGE_TTL_MS = 60_000
_CHALLENGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


class DeveloperOptionsChallengeError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Challenge:
    created_at: float
    ready_at: float
    expires_at: float


class DeveloperOptionsGuard:
    """One-shot, monotonic five-second acknowledgement challenges."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] | None = None,
        wait_ms: int = DEVELOPER_CHALLENGE_WAIT_MS,
        ttl_ms: int = DEVELOPER_CHALLENGE_TTL_MS,
        max_pending: int = 32,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory or (lambda: secrets.token_urlsafe(32))
        self.wait_ms = max(DEVELOPER_CHALLENGE_WAIT_MS, int(wait_ms))
        self.ttl_ms = max(self.wait_ms + 1_000, int(ttl_ms))
        self.max_pending = max(1, int(max_pending))
        self._lock = Lock()
        self._pending: dict[str, _Challenge] = {}

    @staticmethod
    def valid_id(challenge_id: str) -> bool:
        return bool(_CHALLENGE_ID_RE.fullmatch(str(challenge_id or "")))

    def _prune_locked(self, now: float) -> None:
        for challenge_id, challenge in list(self._pending.items()):
            if challenge.expires_at < now:
                self._pending.pop(challenge_id, None)
        if len(self._pending) <= self.max_pending:
            return
        oldest = sorted(self._pending.items(), key=lambda item: item[1].created_at)
        for challenge_id, _ in oldest[: len(self._pending) - self.max_pending]:
            self._pending.pop(challenge_id, None)

    def create(self) -> dict[str, object]:
        with self._lock:
            now = float(self._clock())
            self._prune_locked(now)
            challenge_id = ""
            for _ in range(16):
                candidate = str(self._id_factory())
                if self.valid_id(candidate) and candidate not in self._pending:
                    challenge_id = candidate
                    break
            if not challenge_id:
                raise RuntimeError("Unable to allocate a Developer Options challenge.")
            ready_at = now + self.wait_ms / 1_000
            self._pending[challenge_id] = _Challenge(
                created_at=now,
                ready_at=ready_at,
                expires_at=now + self.ttl_ms / 1_000,
            )
            self._prune_locked(now)
            return {
                "ok": True,
                "schema": DEVELOPER_CHALLENGE_SCHEMA,
                "challengeId": challenge_id,
                "waitMs": self.wait_ms,
            }

    def cancel(self, challenge_id: str) -> bool:
        if not self.valid_id(challenge_id):
            return False
        with self._lock:
            return self._pending.pop(challenge_id, None) is not None

    def consume(self, challenge_id: str) -> None:
        if not self.valid_id(challenge_id):
            raise DeveloperOptionsChallengeError("Developer Options challenge is invalid or expired.")
        with self._lock:
            now = float(self._clock())
            self._prune_locked(now)
            challenge = self._pending.get(challenge_id)
            if challenge is None:
                raise DeveloperOptionsChallengeError("Developer Options challenge is invalid, expired, or already used.")
            if now < challenge.ready_at:
                remaining_ms = max(1, int(round((challenge.ready_at - now) * 1_000)))
                raise DeveloperOptionsChallengeError(
                    f"Developer Options warning is still active; wait {remaining_ms} ms before confirming."
                )
            if now > challenge.expires_at:
                self._pending.pop(challenge_id, None)
                raise DeveloperOptionsChallengeError("Developer Options challenge is invalid or expired.")
            self._pending.pop(challenge_id, None)


__all__ = [
    "DEVELOPER_CHALLENGE_SCHEMA",
    "DEVELOPER_CHALLENGE_TTL_MS",
    "DEVELOPER_CHALLENGE_WAIT_MS",
    "DeveloperOptionsChallengeError",
    "DeveloperOptionsGuard",
]
