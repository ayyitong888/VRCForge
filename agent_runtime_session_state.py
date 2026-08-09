from __future__ import annotations

import copy
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentRuntimeSessionStatePorts:
    """Capabilities required by the in-memory runtime-session state owner.

    The owner has no filesystem, network, provider, approval, or execution
    capability. Its lifetime is the containing AgentGateway instance, and it
    borrows that gateway's re-entrant state lock.
    """

    shared_state_lock: AbstractContextManager[Any]


class AgentRuntimeSessionState:
    """Own runtime sessions, cancellation markers, and turn-local stream identity."""

    __slots__ = ("_ports", "_sessions", "_cancelled_ids", "_stream_context")

    def __init__(self, ports: AgentRuntimeSessionStatePorts) -> None:
        self._ports = ports
        self._sessions: dict[str, dict[str, Any]] = {}
        self._cancelled_ids: set[str] = set()
        self._stream_context = threading.local()

    @property
    def shared_state_lock(self) -> AbstractContextManager[Any]:
        return self._ports.shared_state_lock

    def clear(self) -> None:
        with self._ports.shared_state_lock:
            self._sessions.clear()
            self._cancelled_ids.clear()

    def session_count(self) -> int:
        with self._ports.shared_state_lock:
            return len(self._sessions)

    def discard_session(self, session_id: str) -> None:
        with self._ports.shared_state_lock:
            self._sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._ports.shared_state_lock:
            session = self._sessions.get(session_id)
            return copy.deepcopy(session) if session is not None else None

    def session_summary(self, session_id: str) -> dict[str, Any]:
        with self._ports.shared_state_lock:
            session = self._sessions.get(session_id)
            if not session:
                return {"turnCount": 0, "restoredFromTranscript": False}
            return {
                "turnCount": len(session.get("turns", [])),
                "restoredFromTranscript": bool(session.get("restoredFromTranscript")),
            }

    def append_turn(self, session_id: str, *, now: str, updated_at: str, turn: dict[str, Any]) -> None:
        with self._ports.shared_state_lock:
            session = self._sessions.setdefault(
                session_id,
                {
                    "id": session_id,
                    "createdAt": now,
                    "updatedAt": now,
                    "turns": [],
                },
            )
            session["updatedAt"] = updated_at
            session["turns"].append(turn)

    def restore_session(self, session_id: str, history: list[dict[str, Any]], now: str) -> int:
        """Restore a missing in-memory session from the client transcript once."""

        if not session_id:
            return 0
        with self._ports.shared_state_lock:
            session = self._sessions.get(session_id)
            if session and session.get("turns"):
                return 0
            turns: list[dict[str, Any]] = []
            for index, entry in enumerate(history):
                text = str(entry.get("text") or entry.get("message") or "").strip()
                if not text:
                    continue
                role = str(entry.get("role") or "user").strip().lower()
                if role not in ("user", "agent"):
                    role = "user"
                turns.append(
                    {
                        "id": f"restored_{index:04d}",
                        "createdAt": str(entry.get("createdAt") or now),
                        "restored": True,
                        "role": role,
                        "message": text,
                    }
                )
            if not turns:
                return 0
            self._sessions[session_id] = {
                "id": session_id,
                "createdAt": now,
                "updatedAt": now,
                "restoredFromTranscript": True,
                "turns": turns,
            }
            return len(turns)

    def desktop_bootstrap_completed(self, session_id: str) -> bool:
        if not session_id:
            return False
        with self._ports.shared_state_lock:
            session = self._sessions.get(session_id)
            return bool(session and session.get("desktopBootstrapCompleted"))

    def record_desktop_bootstrap(
        self,
        session_id: str,
        *,
        now: str,
        status_summary: str,
        result_summary: Any,
    ) -> None:
        if not session_id:
            return
        with self._ports.shared_state_lock:
            session = self._sessions.setdefault(
                session_id,
                {"id": session_id, "createdAt": now, "updatedAt": now, "turns": []},
            )
            session["desktopBootstrapCompleted"] = True
            session["desktopBootstrapToolCalls"] = 1
            session["desktopBootstrapStatus"] = status_summary
            session["desktopBootstrapSummary"] = result_summary
            session["updatedAt"] = now

    def mark_cancel_requested(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        client_turn_id: str = "",
    ) -> None:
        with self._ports.shared_state_lock:
            if session_id and not (turn_id or client_turn_id):
                self._cancelled_ids.add(session_id)
            if turn_id:
                self._cancelled_ids.add(turn_id)
            if client_turn_id:
                self._cancelled_ids.add(client_turn_id)

    def cancel_requested(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        client_turn_id: str = "",
    ) -> bool:
        candidates = [item for item in (session_id, turn_id, client_turn_id) if item]
        if not candidates:
            return False
        with self._ports.shared_state_lock:
            return any(item in self._cancelled_ids for item in candidates)

    def consume_cancel_request(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        client_turn_id: str = "",
    ) -> bool:
        candidates = [item for item in (client_turn_id, turn_id, session_id) if item]
        if not candidates:
            return False
        with self._ports.shared_state_lock:
            matched = [item for item in candidates if item in self._cancelled_ids]
            for item in matched:
                self._cancelled_ids.discard(item)
            return bool(matched)

    def set_stream_context(self, value: dict[str, str]) -> None:
        self._stream_context.value = dict(value)

    def clear_stream_context(self) -> None:
        self._stream_context.value = {}

    def stream_context(self) -> dict[str, str]:
        return dict(getattr(self._stream_context, "value", {}) or {})
