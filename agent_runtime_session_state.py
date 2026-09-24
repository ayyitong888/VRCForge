from __future__ import annotations

import copy
import json
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
    """Own runtime sessions, cancellation markers, steer mailboxes, and stream identity."""

    MAX_STEER_MAILBOX = 20
    MAX_FINAL_RESPONSES = 8
    MAX_NATIVE_CONVERSATION_BYTES = 2 * 1024 * 1024

    __slots__ = (
        "_ports",
        "_sessions",
        "_cancelled_ids",
        "_active_turns",
        "_finalizing_turns",
        "_final_responses",
        "_steer_mailboxes",
        "_steer_seen_ids",
        "_native_conversations",
        "_stream_context",
    )

    def __init__(self, ports: AgentRuntimeSessionStatePorts) -> None:
        self._ports = ports
        self._sessions: dict[str, dict[str, Any]] = {}
        self._cancelled_ids: set[str] = set()
        self._active_turns: dict[tuple[str, str], str] = {}
        self._finalizing_turns: set[tuple[str, str]] = set()
        self._final_responses: dict[str, list[dict[str, Any]]] = {}
        self._steer_mailboxes: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._steer_seen_ids: dict[tuple[str, str], set[str]] = {}
        self._native_conversations: dict[str, dict[str, Any]] = {}
        self._stream_context = threading.local()

    @property
    def shared_state_lock(self) -> AbstractContextManager[Any]:
        return self._ports.shared_state_lock

    def clear(self) -> None:
        with self._ports.shared_state_lock:
            self._sessions.clear()
            self._cancelled_ids.clear()
            self._active_turns.clear()
            self._finalizing_turns.clear()
            self._final_responses.clear()
            self._steer_mailboxes.clear()
            self._steer_seen_ids.clear()
            self._native_conversations.clear()

    def session_count(self) -> int:
        with self._ports.shared_state_lock:
            return len(self._sessions)

    def discard_session(self, session_id: str) -> None:
        with self._ports.shared_state_lock:
            self._sessions.pop(session_id, None)
            self._native_conversations.pop(session_id, None)
            for key in {key for key in (*self._active_turns.keys(), *self._finalizing_turns) if key[0] == session_id}:
                self._active_turns.pop(key, None)
                self._finalizing_turns.discard(key)
                self._steer_mailboxes.pop(key, None)
                self._steer_seen_ids.pop(key, None)
            self._final_responses.pop(session_id, None)

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

    @staticmethod
    def _native_required(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"native {field} must be non-empty")
        return value

    @classmethod
    def _native_size_ok(cls, snapshot: dict[str, Any]) -> None:
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > cls.MAX_NATIVE_CONVERSATION_BYTES:
            raise ValueError("Conversation context size exceeds 2MiB. Use Compact conversation or start a new conversation before retrying.")

    @staticmethod
    def _native_pending(snapshot: dict[str, Any]) -> set[str]:
        calls: set[str] = set()
        settled: set[str] = set()
        for message in snapshot.get("messages", []):
            if message.get("role") == "assistant":
                for call in message.get("tool_calls", []) or []:
                    calls.add(call["id"])
            elif message.get("role") == "tool":
                settled.add(message["tool_call_id"])
        return calls - settled

    @classmethod
    def _validate_native_assistant(cls, message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise ValueError("native assistant message must be an object")
        allowed = {"role", "content", "tool_calls", "reasoning_content"}
        if set(message) - allowed:
            raise ValueError("native assistant message has unsupported fields")
        if message.get("role") != "assistant":
            raise ValueError("native assistant role must be assistant")
        if "content" in message and message["content"] is not None and not isinstance(message["content"], str):
            raise ValueError("native assistant content must be string or null")
        if "reasoning_content" in message and message["reasoning_content"] is not None and not isinstance(message["reasoning_content"], str):
            raise ValueError("native reasoning_content must be string or null")
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list):
            raise ValueError("native tool_calls must be a list")
        seen: set[str] = set()
        normalized = copy.deepcopy(message)
        for call in calls:
            if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
                raise ValueError("native tool call shape is invalid")
            call_id = cls._native_required(call["id"], "call id")
            if call_id in seen:
                raise ValueError("native call id is duplicate")
            seen.add(call_id)
            if call["type"] != "function" or not isinstance(call["function"], dict) or set(call["function"]) != {"name", "arguments"}:
                raise ValueError("native tool call function shape is invalid")
            cls._native_required(call["function"]["name"], "function name")
            if not isinstance(call["function"]["arguments"], str):
                raise ValueError("native function arguments must be raw string")
        return normalized

    @classmethod
    def _validate_native_snapshot(
        cls, snapshot: dict[str, Any], *, binding: str,
    ) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or set(snapshot) != {"binding", "turnId", "messages", "activeTurnStart"}:
            raise ValueError("native snapshot fields are invalid")
        if snapshot["binding"] != binding:
            raise ValueError("native snapshot binding mismatch")
        cls._native_required(snapshot["binding"], "binding")
        cls._native_required(snapshot["turnId"], "turn id")
        messages = snapshot["messages"]
        if not isinstance(messages, list):
            raise ValueError("native snapshot messages must be a list")
        active_turn_start = snapshot["activeTurnStart"]
        if not isinstance(active_turn_start, int) or isinstance(active_turn_start, bool):
            raise ValueError("native active turn boundary is invalid")
        if not messages or active_turn_start < 0 or active_turn_start >= len(messages):
            raise ValueError("native active turn boundary is invalid")
        calls: set[str] = set()
        pending: set[str] = set()
        normalized_messages: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("native snapshot message must be an object")
            role = message.get("role")
            if role == "user":
                if set(message) != {"role", "content"} or not isinstance(message["content"], str) or not message["content"].strip():
                    raise ValueError("native user message shape is invalid")
                if pending:
                    raise ValueError("native snapshot has pending tool calls before user message")
                normalized_messages.append(copy.deepcopy(message))
            elif role == "assistant":
                normalized = cls._validate_native_assistant(message)
                if pending:
                    raise ValueError("native snapshot has pending tool calls before assistant message")
                for call in normalized.get("tool_calls", []) or []:
                    call_id = call["id"]
                    if call_id in calls:
                        raise ValueError("native call id is duplicate")
                    calls.add(call_id)
                    pending.add(call_id)
                normalized_messages.append(normalized)
            elif role == "tool":
                if set(message) != {"role", "tool_call_id", "content"} or not isinstance(message["content"], str):
                    raise ValueError("native tool message shape is invalid")
                call_id = cls._native_required(message["tool_call_id"], "call id")
                if call_id not in pending:
                    raise ValueError("native tool call is orphaned or already settled")
                pending.remove(call_id)
                normalized_messages.append(copy.deepcopy(message))
            else:
                raise ValueError("native snapshot role is invalid")
        normalized = {
            "binding": snapshot["binding"],
            "turnId": snapshot["turnId"],
            "messages": normalized_messages,
            "activeTurnStart": active_turn_start,
        }
        if normalized_messages[active_turn_start].get("role") != "user":
            raise ValueError("native active turn must begin with user message")
        cls._native_size_ok(normalized)
        return normalized

    def begin_native_turn(
        self, session_id: str, *, binding: str, turn_id: str, message: str,
        initial_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        turn_id = self._native_required(turn_id, "turn id")
        message = self._native_required(message, "message")
        with self._ports.shared_state_lock:
            existing = self._native_conversations.get(session_id)
            if existing is not None:
                pending = self._native_pending(existing)
                if pending:
                    raise ValueError("native conversation has pending tool calls")
                if existing["binding"] == binding and existing["turnId"] == turn_id:
                    return copy.deepcopy(existing)
                if existing["binding"] == binding:
                    candidate = copy.deepcopy(existing)
                    candidate["turnId"] = turn_id
                    candidate["activeTurnStart"] = len(candidate["messages"])
                    candidate["messages"].append({"role": "user", "content": message})
                    self._native_size_ok(candidate)
                    self._native_conversations[session_id] = candidate
                    return copy.deepcopy(candidate)
            history_messages: list[dict[str, Any]] = []
            for item in initial_history or []:
                if not isinstance(item, dict):
                    raise ValueError("native initial history item is invalid")
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                role = item.get("role")
                if role == "user":
                    history_messages.append({"role": "user", "content": text})
                elif role == "agent":
                    history_messages.append({"role": "assistant", "content": text})
                else:
                    continue
            candidate = {
                "binding": binding,
                "turnId": turn_id,
                "messages": history_messages + [{"role": "user", "content": message}],
                "activeTurnStart": len(history_messages),
            }
            self._native_size_ok(candidate)
            self._native_conversations[session_id] = candidate
            return copy.deepcopy(candidate)

    def native_conversation(self, session_id: str, *, binding: str) -> dict[str, Any] | None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                return None
            return copy.deepcopy(snapshot)

    def restore_native_conversation(
        self, session_id: str, *, binding: str, snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        normalized = self._validate_native_snapshot(snapshot, binding=binding)
        with self._ports.shared_state_lock:
            existing = self._native_conversations.get(session_id)
            if existing is None:
                self._native_conversations[session_id] = normalized
                return copy.deepcopy(normalized)
            if existing["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            current_messages = existing["messages"]
            incoming_messages = normalized["messages"]
            if incoming_messages == current_messages and normalized["turnId"] == existing["turnId"]:
                return copy.deepcopy(existing)
            if (
                normalized["turnId"] == existing["turnId"]
                and existing["activeTurnStart"] <= normalized["activeTurnStart"]
                and existing["messages"][existing["activeTurnStart"]:]
                == incoming_messages[normalized["activeTurnStart"]:]
            ):
                return copy.deepcopy(existing)
            if len(incoming_messages) >= len(current_messages) and incoming_messages[:len(current_messages)] == current_messages:
                if len(incoming_messages) == len(current_messages):
                    raise ValueError("native snapshot conflicts with current turn")
                self._native_conversations[session_id] = normalized
                return copy.deepcopy(normalized)
            if len(current_messages) >= len(incoming_messages) and current_messages[:len(incoming_messages)] == incoming_messages:
                return copy.deepcopy(existing)
            raise ValueError("native snapshot conflicts with current conversation")

    def replace_native_completed_prefix(
        self,
        session_id: str,
        *,
        binding: str,
        expected_snapshot: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        summary = self._native_required(summary, "summary")
        expected = self._validate_native_snapshot(expected_snapshot, binding=binding)
        with self._ports.shared_state_lock:
            current = self._native_conversations.get(session_id)
            if current is None or current["binding"] != binding or current != expected:
                raise ValueError("native snapshot compare-and-replace mismatch")
            cut = current["activeTurnStart"]
            if cut <= 0 or self._native_pending({"messages": current["messages"][:cut]}):
                raise ValueError("native completed prefix is unavailable")
            candidate = {
                "binding": binding,
                "turnId": current["turnId"],
                "messages": [{"role": "assistant", "content": summary}] + copy.deepcopy(current["messages"][cut:]),
                "activeTurnStart": 1,
            }
            normalized = self._validate_native_snapshot(candidate, binding=binding)
            self._native_conversations[session_id] = normalized
            return copy.deepcopy(normalized)

    def append_native_user(self, session_id: str, *, binding: str, message: str) -> None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        message = self._native_required(message, "message")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if self._native_pending(snapshot):
                raise ValueError("native conversation has pending tool calls")
            candidate = copy.deepcopy(snapshot)
            candidate["messages"].append({"role": "user", "content": message})
            self._native_size_ok(candidate)
            self._native_conversations[session_id] = candidate

    def append_native_assistant(self, session_id: str, *, binding: str, message: dict[str, Any]) -> None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        normalized = self._validate_native_assistant(message)
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if self._native_pending(snapshot):
                raise ValueError("native conversation has pending tool calls")
            existing_ids = {call["id"] for item in snapshot["messages"] for call in item.get("tool_calls", []) or []}
            if existing_ids.intersection(call["id"] for call in normalized.get("tool_calls", []) or []):
                raise ValueError("native call id is duplicate")
            candidate = copy.deepcopy(snapshot)
            candidate["messages"].append(normalized)
            self._native_size_ok(candidate)
            self._native_conversations[session_id] = candidate

    def settle_native_call(self, session_id: str, *, binding: str, call_id: str, content: str) -> None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        call_id = self._native_required(call_id, "call id")
        if not isinstance(content, str):
            raise ValueError("native tool content must be string")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            pending = self._native_pending(snapshot)
            all_calls = {call["id"] for item in snapshot["messages"] for call in item.get("tool_calls", []) or []}
            if call_id in all_calls and call_id not in pending:
                raise ValueError("native call is already settled")
            if call_id not in pending:
                raise ValueError("native call is unknown")
            candidate = copy.deepcopy(snapshot)
            candidate["messages"].append({"role": "tool", "tool_call_id": call_id, "content": content})
            self._native_size_ok(candidate)
            self._native_conversations[session_id] = candidate

    def internal_tool_blocks(self, session_id: str) -> frozenset[str]:
        with self._ports.shared_state_lock:
            session = self._sessions.get(str(session_id or "").strip())
            blocks = session.get("internalToolBlocks", []) if session else []
            return frozenset({"core", *(str(item) for item in blocks if str(item))})

    def load_internal_tool_block(self, session_id: str, block: str) -> frozenset[str]:
        return self.load_internal_tool_block_selected(session_id, block, None)

    def load_internal_tool_block_selected(
        self, session_id: str, block: str, tools: list[str] | None,
    ) -> frozenset[str]:
        session_id = str(session_id or "").strip()
        block = str(block or "").strip()
        if not session_id or not block:
            return frozenset({"core"})
        with self._ports.shared_state_lock:
            session = self._sessions.setdefault(
                session_id,
                {"id": session_id, "createdAt": "", "updatedAt": "", "turns": []},
            )
            blocks = {"core", *(str(item) for item in session.get("internalToolBlocks", []))}
            blocks.add(block)
            session["internalToolBlocks"] = sorted(blocks)
            selections = session.setdefault("internalToolSelections", {})
            if tools is None:
                selections[block] = None
            elif block not in selections:
                selections[block] = set(tools)
            elif selections[block] is not None:
                selections[block] = set(selections[block] or set()) | set(tools)
            # A previously whole-loaded block is monotonic: a later subset
            # request cannot hide tools already exposed in this session.
            return frozenset(blocks)

    def internal_tool_selections(self, session_id: str) -> dict[str, list[str] | None]:
        with self._ports.shared_state_lock:
            session = self._sessions.get(str(session_id or "").strip()) or {}
            return {
                block: (sorted(value) if value is not None else None)
                for block, value in (session.get("internalToolSelections") or {}).items()
            }

    def unload_internal_tool_block(self, session_id: str, block: str) -> frozenset[str]:
        session_id = str(session_id or "").strip()
        block = str(block or "").strip()
        if not session_id:
            return frozenset({"core"})
        with self._ports.shared_state_lock:
            session = self._sessions.get(session_id)
            blocks = {"core", *(str(item) for item in (session or {}).get("internalToolBlocks", []))}
            if block != "core":
                blocks.discard(block)
                if session is not None:
                    (session.get("internalToolSelections") or {}).pop(block, None)
            if session is not None:
                session["internalToolBlocks"] = sorted(blocks)
            return frozenset(blocks)

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
                active = [
                    (key, active_turn_id)
                    for key, active_turn_id in self._active_turns.items()
                    if key[0] == session_id
                ]
                if active:
                    for (_active_session_id, active_client_turn_id), active_turn_id in active:
                        if active_turn_id:
                            self._cancelled_ids.add(active_turn_id)
                        if active_client_turn_id:
                            self._cancelled_ids.add(active_client_turn_id)
                else:
                    # Preserve the supported "stop before the turn starts"
                    # behavior; the next turn consumes this session marker.
                    self._cancelled_ids.add(session_id)
            if turn_id:
                self._cancelled_ids.add(turn_id)
            if client_turn_id:
                self._cancelled_ids.add(client_turn_id)
            if session_id and client_turn_id:
                self._steer_mailboxes.pop((session_id, client_turn_id), None)

    def begin_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
    ) -> bool:
        if not session_id or not client_turn_id:
            return True
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            if key in self._active_turns:
                return False
            self._active_turns[key] = turn_id
            self._finalizing_turns.discard(key)
            self._steer_mailboxes.setdefault(key, [])
            return True

    def finish_turn(self, *, session_id: str, turn_id: str = "", client_turn_id: str) -> list[dict[str, Any]]:
        if not session_id or not client_turn_id:
            return []
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            active_turn_id = self._active_turns.get(key)
            if turn_id and active_turn_id != turn_id:
                return []
            undrained = copy.deepcopy(self._steer_mailboxes.get(key, []))
            if active_turn_id:
                self._finalizing_turns.add(key)
            self._active_turns.pop(key, None)
            self._steer_mailboxes.pop(key, None)
            self._steer_seen_ids.pop(key, None)
            # A completed turn must not leave cancellation/steer state behind
            # that could affect a later turn reusing either identifier.
            self._cancelled_ids.discard(client_turn_id)
            if active_turn_id:
                self._cancelled_ids.discard(active_turn_id)
            return undrained

    def owns_turn(self, *, session_id: str, turn_id: str, client_turn_id: str) -> bool:
        with self._ports.shared_state_lock:
            return self._active_turns.get((session_id, client_turn_id)) == turn_id

    def record_final_response(
        self,
        *,
        session_id: str,
        client_turn_id: str,
        status: str,
        response: dict[str, Any],
        error: str = "",
    ) -> None:
        """Keep a bounded, exact-turn response for same-process reconnects."""

        session_id = str(session_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        if not session_id or not client_turn_id or not isinstance(response, dict):
            return
        normalized_status = "failed" if str(status).strip().lower() == "failed" else "completed"
        item: dict[str, Any] = {
            "clientTurnId": client_turn_id,
            "status": normalized_status,
            "response": copy.deepcopy(response),
        }
        if error:
            item["error"] = str(error)[:400]
        key = (session_id, client_turn_id)
        with self._ports.shared_state_lock:
            self._finalizing_turns.discard(key)
            entries = self._final_responses.setdefault(session_id, [])
            entries[:] = [entry for entry in entries if entry.get("clientTurnId") != client_turn_id]
            entries.append(item)
            if len(entries) > self.MAX_FINAL_RESPONSES:
                del entries[:-self.MAX_FINAL_RESPONSES]

    def clear_finalizing_turn(self, *, session_id: str, client_turn_id: str) -> None:
        """Close a finalization marker when recovery bookkeeping cannot be stored."""

        with self._ports.shared_state_lock:
            self._finalizing_turns.discard((str(session_id or "").strip(), str(client_turn_id or "").strip()))

    def final_response(
        self, *, session_id: str, client_turn_id: str,
    ) -> dict[str, Any]:
        """Return one exact-turn recovery projection without exposing the transcript."""

        session_id = str(session_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        key = (session_id, client_turn_id)
        with self._ports.shared_state_lock:
            if key in self._active_turns or key in self._finalizing_turns:
                return {"ok": True, "sessionId": session_id, "clientTurnId": client_turn_id, "status": "running"}
            for item in reversed(self._final_responses.get(session_id, [])):
                if item.get("clientTurnId") != client_turn_id:
                    continue
                result = {
                    "ok": True,
                    "sessionId": session_id,
                    "clientTurnId": client_turn_id,
                    "status": item.get("status") or "completed",
                    "response": copy.deepcopy(item.get("response") or {}),
                }
                if item.get("error"):
                    result["error"] = item["error"]
                return result
        return {"ok": True, "sessionId": session_id, "clientTurnId": client_turn_id, "status": "missing"}

    def submit_steer(
        self,
        *,
        session_id: str,
        target_client_turn_id: str,
        input_id: str,
        message: str,
        followup_lane_id: str = "",
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()[:180]
        target_client_turn_id = str(target_client_turn_id or "").strip()[:180]
        input_id = str(input_id or "").strip()[:180]
        message = str(message or "").strip()[:4000]
        if not session_id or not target_client_turn_id or not input_id or not message:
            return {"accepted": False, "mode": "followup", "reason": "invalid_request"}
        with self._ports.shared_state_lock:
            key = (session_id, target_client_turn_id)
            if key not in self._active_turns:
                return {"accepted": False, "mode": "followup", "reason": "turn_not_active"}
            mailbox = self._steer_mailboxes.setdefault(key, [])
            seen_ids = self._steer_seen_ids.setdefault(key, set())
            if input_id in seen_ids:
                return {"accepted": True, "mode": "steer", "status": "accepted", "reason": "duplicate_input", "deduped": True, "queuedCount": len(mailbox)}
            # Keep the hot same-turn mailbox bounded. Overflow is not dropped:
            # the API atomically falls back to the durable follow-up lane.
            if len(mailbox) >= self.MAX_STEER_MAILBOX:
                return {"accepted": False, "mode": "followup", "reason": "mailbox_full"}
            mailbox_item = {
                "inputId": input_id,
                "message": message,
            }
            normalized_lane_id = str(followup_lane_id or "").strip()[:180]
            if normalized_lane_id:
                mailbox_item["followupLaneId"] = normalized_lane_id
            mailbox.append(mailbox_item)
            seen_ids.add(input_id)
            return {
                "accepted": True,
                "mode": "steer",
                "reason": "accepted",
                "queuedCount": len(mailbox),
            }

    def drain_steer(self, *, session_id: str, client_turn_id: str) -> list[dict[str, Any]]:
        if not session_id or not client_turn_id:
            return []
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            items = self._steer_mailboxes.get(key, [])
            self._steer_mailboxes[key] = []
            return copy.deepcopy(items)

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
