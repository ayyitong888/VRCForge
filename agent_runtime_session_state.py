from __future__ import annotations

import copy
import json
import os
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Any, Callable
from context_compaction import normalize_compaction_recovery
from agent_native_history_store import NativeHistoryStore
from agent_scan_result_policy import (
    SCAN_RESULT_CONTINUATION_POLICY, SCAN_RESULT_CONTINUATION_POLICY_V2,
    is_scan_result_policy_message, restored_scan_result_policy,
)


@dataclass(frozen=True)
class AgentRuntimeSessionStatePorts:
    """Capabilities required by the in-memory runtime-session state owner.

    Optional storage is scoped to gateway-owned native history only, never
    approvals or execution. Lifetime and re-entrant lock belong to the gateway.
    """

    shared_state_lock: AbstractContextManager[Any]
    native_history_store: NativeHistoryStore | None = None
    discard_result_snapshots: Callable[[str], None] | None = None


class AgentRuntimeSessionState:
    """Own runtime sessions, cancellation markers, steer mailboxes, and stream identity."""

    MAX_STEER_MAILBOX = 20
    MAX_FINAL_RESPONSES = 8
    # Bound an individual provider receipt, not the accumulated local session.
    # Model request capacity is enforced by the planner's verified token guard.
    MAX_NATIVE_RESPONSE_BYTES = 2 * 1024 * 1024

    __slots__ = (
        "_ports",
        "_sessions",
        "_cancelled_targets",
        "_active_turns",
        "_finalizing_turns",
        "_final_responses",
        "_steer_mailboxes",
        "_steer_seen_ids",
        "_native_conversations",
        "_stream_context",
        "_user_adjustments",
        "_native_loaded",
    )

    def __init__(self, ports: AgentRuntimeSessionStatePorts) -> None:
        self._ports = ports
        self._sessions: dict[str, dict[str, Any]] = {}
        # Cancellation is scoped to one identity tuple.  A session-only target
        # is represented with empty turn/client fields and intentionally keeps
        # the supported "stop every turn in this session" behavior.
        self._cancelled_targets: set[tuple[str, str, str]] = set()
        self._active_turns: dict[tuple[str, str], str] = {}
        self._finalizing_turns: set[tuple[str, str]] = set()
        self._final_responses: dict[str, list[dict[str, Any]]] = {}
        self._steer_mailboxes: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._steer_seen_ids: dict[tuple[str, str], set[str]] = {}
        self._native_conversations: dict[str, dict[str, Any]] = {}
        self._stream_context = threading.local()
        self._user_adjustments: dict[tuple[str, str], dict[str, Any]] = {}
        self._native_loaded: set[str] = set()

    def _load_native(self, session_id: str, binding: str | None = None) -> None:
        current = self._native_conversations.get(session_id)
        if session_id in self._native_loaded and (binding is None or (current is not None and current["binding"] == binding)):
            return
        store = self._ports.native_history_store
        saved = store.load(session_id, binding) if store else None
        if saved is not None:
            native = saved.get("native")
            if not isinstance(native, dict):
                raise ValueError("Saved native history is invalid")
            normalized = self._validate_native_snapshot(native, binding=native.get("binding"))
            selections = saved.get("selections")
            if not isinstance(selections, dict) or any(
                not isinstance(block, str) or not block or
                (names is not None and (not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names)))
                for block, names in selections.items()
            ):
                raise ValueError("Saved native tool selections are invalid")
            self._native_conversations[session_id] = normalized
            session = self._sessions.setdefault(session_id, {"id": session_id, "turns": []})
            session["internalToolBlocks"] = sorted({"core", *selections})
            session["internalToolSelections"] = copy.deepcopy(selections)
        self._native_loaded.add(session_id)

    def _save_native(self, session_id: str, candidate: dict[str, Any]) -> None:
        store = self._ports.native_history_store
        if store:
            selections = (self._sessions.get(session_id) or {}).get("internalToolSelections") or {}
            store.save(session_id, candidate, {k: sorted(v) if v is not None else None for k, v in selections.items()})
        self._native_conversations[session_id] = candidate
        self._native_loaded.add(session_id)

    def _save_native_selection(self, session_id: str) -> None:
        current = self._native_conversations.get(session_id)
        if current is not None:
            self._save_native(session_id, current)

    @property
    def shared_state_lock(self) -> AbstractContextManager[Any]:
        return self._ports.shared_state_lock

    def configure_native_store(self, store: NativeHistoryStore) -> None:
        with self._ports.shared_state_lock:
            self.clear()
            self._ports = replace(self._ports, native_history_store=store)

    def clear(self) -> None:
        with self._ports.shared_state_lock:
            self._sessions.clear()
            self._cancelled_targets.clear()
            self._active_turns.clear()
            self._finalizing_turns.clear()
            self._final_responses.clear()
            self._steer_mailboxes.clear()
            self._steer_seen_ids.clear()
            self._native_conversations.clear()
            self._native_loaded.clear()
            self._user_adjustments.clear()

    def user_adjustment(self, session_id: str, project_root: str) -> dict[str, Any] | None:
        with self._ports.shared_state_lock:
            return copy.deepcopy(self._user_adjustments.get((session_id, os.path.normcase(os.path.abspath(project_root)))))

    def set_user_adjustment(self, session_id: str, project_root: str, value: dict[str, Any]) -> None:
        with self._ports.shared_state_lock:
            self._user_adjustments[(session_id, os.path.normcase(os.path.abspath(project_root)))] = copy.deepcopy(value)

    def session_count(self) -> int:
        with self._ports.shared_state_lock:
            return len(self._sessions)

    def discard_session(self, session_id: str) -> None:
        with self._ports.shared_state_lock:
            if self._ports.discard_result_snapshots:
                self._ports.discard_result_snapshots(session_id)
            if self._ports.native_history_store:
                self._ports.native_history_store.delete(session_id)
            self._native_loaded.discard(session_id)
            self._sessions.pop(session_id, None)
            self._native_conversations.pop(session_id, None)
            for key in [key for key in self._user_adjustments if key[0] == session_id]:
                self._user_adjustments.pop(key, None)
            for key in {key for key in (*self._active_turns.keys(), *self._finalizing_turns) if key[0] == session_id}:
                self._active_turns.pop(key, None)
                self._finalizing_turns.discard(key)
                self._steer_mailboxes.pop(key, None)
                self._steer_seen_ids.pop(key, None)
            self._cancelled_targets = {
                target for target in self._cancelled_targets if target[0] != session_id
            }
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
        # Complete local history must remain saveable after a tool has run.
        # Do not conflate its serialized size with the model context window.
        for message in snapshot.get("messages", []):
            encoded = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > cls.MAX_NATIVE_RESPONSE_BYTES:
                raise ValueError("Individual native message exceeds the size limit")

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
        allowed = {"role", "content", "tool_calls", "reasoning_content", "responses_output"}
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
        if "responses_output" in message:
            responses_output = message["responses_output"]
            if not isinstance(responses_output, list) or not responses_output or len(responses_output) > 256:
                raise ValueError("native Responses output items are invalid")
            response_calls: list[tuple[str, str, str]] = []
            output_text: list[str] = []
            for item in responses_output:
                if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                    raise ValueError("native Responses output item is invalid")
                item_type = item["type"]
                if item_type == "reasoning":
                    item_allowed = {"id", "type", "summary", "encrypted_content", "content", "status", "created_by"}
                    encrypted = item.get("encrypted_content")
                    content = item.get("content")
                    if set(item) - item_allowed or (
                        not isinstance(encrypted, str) and not isinstance(content, list)
                    ):
                        raise ValueError("native Responses encrypted reasoning item is invalid")
                    if "encrypted_content" in item and not isinstance(encrypted, str):
                        raise ValueError("native Responses encrypted reasoning item is invalid")
                    if "summary" in item and not isinstance(item["summary"], list):
                        raise ValueError("native Responses reasoning summary is invalid")
                    if "content" in item and not isinstance(item["content"], list):
                        raise ValueError("native Responses reasoning content is invalid")
                    if encrypted is None and any(
                        not isinstance(part, dict) or set(part) != {"type", "text"}
                        or part.get("type") != "reasoning_text" or not isinstance(part.get("text"), str)
                        for part in content
                    ):
                        raise ValueError("native Responses reasoning content is invalid")
                elif item_type == "message":
                    item_allowed = {"id", "type", "role", "content", "status", "created_by", "phase", "metadata"}
                    if set(item) - item_allowed or item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                        raise ValueError("native Responses assistant output item is invalid")
                    if "phase" in item and item["phase"] not in {"commentary", "final_answer"}:
                        raise ValueError("native Responses assistant phase is invalid")
                    if "metadata" in item and not isinstance(item["metadata"], dict):
                        raise ValueError("native Responses assistant metadata is invalid")
                    for content_item in item["content"]:
                        if not isinstance(content_item, dict) or content_item.get("type") not in {"output_text", "refusal"}:
                            raise ValueError("native Responses assistant content item is invalid")
                        content_type = content_item["type"]
                        if content_type == "output_text":
                            if set(content_item) - {"type", "text", "annotations", "logprobs"}:
                                raise ValueError("native Responses output text fields are invalid")
                            if "annotations" in content_item and not isinstance(content_item["annotations"], list):
                                raise ValueError("native Responses output annotations are invalid")
                            if "logprobs" in content_item and not isinstance(content_item["logprobs"], list):
                                raise ValueError("native Responses output logprobs are invalid")
                            text = content_item.get("text")
                        else:
                            if set(content_item) - {"type", "refusal"}:
                                raise ValueError("native Responses refusal fields are invalid")
                            text = content_item.get("refusal")
                        if not isinstance(text, str):
                            raise ValueError("native Responses assistant content text is invalid")
                        output_text.append(text)
                elif item_type == "function_call":
                    item_allowed = {"id", "type", "call_id", "name", "arguments", "namespace", "status", "created_by"}
                    if set(item) - item_allowed or ("namespace" in item and item["namespace"] != "vrcforge"):
                        raise ValueError("native Responses function call namespace or fields are invalid")
                    call_id = cls._native_required(item.get("call_id"), "Responses call id")
                    name = cls._native_required(item.get("name"), "Responses function name")
                    arguments = item.get("arguments")
                    if not isinstance(arguments, str) or any(existing[0] == call_id for existing in response_calls):
                        raise ValueError("native Responses function call is invalid")
                    response_calls.append((call_id, name, arguments))
                else:
                    raise ValueError("native Responses output item type is unsupported")
                for key in ("id", "status", "created_by"):
                    if key in item and not isinstance(item[key], str):
                        raise ValueError("native Responses output item metadata is invalid")
            expected_calls = [
                (call["id"], call["function"]["name"], call["function"]["arguments"])
                for call in calls
            ]
            if response_calls != expected_calls:
                raise ValueError("native Responses output calls do not match the assistant receipt")
            if "content" in message and "".join(output_text) != (message["content"] or ""):
                raise ValueError("native Responses output text does not match the assistant receipt")
            try:
                serialized = json.dumps(responses_output, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise ValueError("native Responses output items are not JSON-safe") from exc
            if len(serialized.encode("utf-8")) > cls.MAX_NATIVE_RESPONSE_BYTES:
                raise ValueError("native Responses output items exceed the size limit")
        return normalized

    @classmethod
    def _validate_native_snapshot(
        cls, snapshot: dict[str, Any], *, binding: str,
    ) -> dict[str, Any]:
        required = {"binding", "turnId", "messages", "activeTurnStart"}
        if not isinstance(snapshot, dict) or not required <= set(snapshot) or set(snapshot) - required - {"toolOrder", "compactionRecovery", "toolBlockIds"}:
            raise ValueError("native snapshot fields are invalid")
        if "toolBlockIds" in snapshot:
            ids = snapshot["toolBlockIds"]
            if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
                raise ValueError("native tool block snapshot is invalid")
        if "toolOrder" in snapshot:
            order = snapshot["toolOrder"]
            if not isinstance(order, list) or any(not isinstance(name, str) or not name.strip() for name in order) or len(set(order)) != len(order):
                raise ValueError("native tool order is invalid")
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
        scan_policies_seen: set[str] = set()
        for index, message in enumerate(messages):
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
            elif role == "system":
                if cls._is_native_runtime_state_message(message):
                    if pending:
                        raise ValueError("native runtime state cannot interrupt pending tool calls")
                    normalized_messages.append(copy.deepcopy(message))
                    continue
                if not is_scan_result_policy_message(message):
                    raise ValueError("native system message is not a host scan policy")
                if message["content"] in scan_policies_seen or pending or (
                        message["content"] == SCAN_RESULT_CONTINUATION_POLICY
                        and SCAN_RESULT_CONTINUATION_POLICY_V2 in scan_policies_seen):
                    raise ValueError("native scan policy position is invalid")
                scan_policies_seen.add(message["content"])
                normalized_messages.append(copy.deepcopy(message))
            else:
                raise ValueError("native snapshot role is invalid")
        normalized = {
            "binding": snapshot["binding"],
            "turnId": snapshot["turnId"],
            "messages": normalized_messages,
            "activeTurnStart": active_turn_start,
        }
        if "toolOrder" in snapshot:
            normalized["toolOrder"] = list(snapshot["toolOrder"])
        if "toolBlockIds" in snapshot:
            normalized["toolBlockIds"] = list(snapshot["toolBlockIds"])
        if "compactionRecovery" in snapshot:
            normalized["compactionRecovery"] = cls._merge_native_recoveries([], snapshot["compactionRecovery"])
        if normalized_messages[active_turn_start].get("role") != "user":
            raise ValueError("native active turn must begin with user message")
        cls._native_size_ok(normalized)
        return normalized

    @staticmethod
    def _merge_native_recoveries(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(incoming, list):
            raise ValueError("native compaction recovery must be a list")
        result = copy.deepcopy(existing)
        for value in incoming:
            normalized = normalize_compaction_recovery(value)
            if normalized not in result:
                result.append(normalized)
        return result

    def add_native_recoveries(self, session_id: str, *, binding: str, recoveries: list[dict[str, Any]]) -> None:
        with self._ports.shared_state_lock:
            current = self._native_conversations.get(session_id)
            if current is None or current["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            candidate = {**current, "compactionRecovery": self._merge_native_recoveries(current.get("compactionRecovery", []), recoveries)}
            self._native_size_ok(candidate)
            self._save_native(session_id, candidate)

    def begin_native_turn(
        self, session_id: str, *, binding: str, turn_id: str, message: str,
        initial_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        turn_id = self._native_required(turn_id, "turn id")
        message = self._native_required(message, "message")
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
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
                    self._save_native(session_id, candidate)
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
            self._save_native(session_id, candidate)
            return copy.deepcopy(candidate)

    def native_conversation(self, session_id: str, *, binding: str) -> dict[str, Any] | None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                return None
            return copy.deepcopy(snapshot)

    def order_native_tools(self, session_id: str, *, binding: str, visible_names: list[str]) -> list[str]:
        """Retain visible prior order; append newly exposed names, never permissions."""
        if any(not isinstance(name, str) or not name.strip() for name in visible_names) or len(set(visible_names)) != len(visible_names):
            raise ValueError("native visible tool names are invalid")
        with self._ports.shared_state_lock:
            current = self._native_conversations.get(session_id)
            if current is None or current["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            visible = set(visible_names)
            order = [name for name in current.get("toolOrder", []) if name in visible]
            retained = set(order)
            order.extend(name for name in visible_names if name not in retained)
            candidate = {**current, "toolOrder": order}
            self._native_size_ok(candidate)
            self._save_native(session_id, candidate)
            return list(order)

    def restore_native_conversation(
        self, session_id: str, *, binding: str, snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        normalized = self._validate_native_snapshot(snapshot, binding=binding)
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
            existing = self._native_conversations.get(session_id)
            if existing is None:
                self._save_native(session_id, normalized)
                return copy.deepcopy(normalized)
            if existing["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            current_messages = existing["messages"]
            incoming_messages = normalized["messages"]
            recoveries = self._merge_native_recoveries(existing.get("compactionRecovery", []), normalized.get("compactionRecovery", []))
            def restored(candidate: dict[str, Any]) -> dict[str, Any]:
                candidate = copy.deepcopy(candidate)
                if recoveries or "compactionRecovery" in candidate:
                    candidate["compactionRecovery"] = copy.deepcopy(recoveries)
                self._native_size_ok(candidate)
                self._save_native(session_id, candidate)
                return copy.deepcopy(candidate)
            if incoming_messages == current_messages and normalized["turnId"] == existing["turnId"]:
                return restored(existing)
            if (
                normalized["turnId"] == existing["turnId"]
                and existing["activeTurnStart"] <= normalized["activeTurnStart"]
                and existing["messages"][existing["activeTurnStart"]:]
                == incoming_messages[normalized["activeTurnStart"]:]
            ):
                return restored(existing)
            if len(incoming_messages) >= len(current_messages) and incoming_messages[:len(current_messages)] == current_messages:
                if len(incoming_messages) == len(current_messages):
                    raise ValueError("native snapshot conflicts with current turn")
                return restored(normalized)
            if len(current_messages) >= len(incoming_messages) and current_messages[:len(incoming_messages)] == incoming_messages:
                return restored(existing)
            raise ValueError("native snapshot conflicts with current conversation")

    @classmethod
    def native_compaction_projection(cls, snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # Every pending call (including approval/question waits) blocks replacement.
        if cls._native_pending(snapshot):
            raise ValueError("native compaction has pending calls")
        start = snapshot["activeTurnStart"]
        retained = [copy.deepcopy(item) for item in snapshot["messages"][start:]
                    if item["role"] == "user"]
        source = [copy.deepcopy(item) for index, item in enumerate(snapshot["messages"])
                  if index < start or item["role"] != "user"]
        if not retained or not source:
            raise ValueError("native compaction source is unavailable")
        retained.extend(restored_scan_result_policy(snapshot["messages"], retained))
        return source, retained

    def replace_native_compacted_history(
        self,
        session_id: str,
        *,
        binding: str,
        expected_snapshot: dict[str, Any],
        summary: str,
        recovery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        summary = self._native_required(summary, "summary")
        expected = self._validate_native_snapshot(expected_snapshot, binding=binding)
        with self._ports.shared_state_lock:
            current = self._native_conversations.get(session_id)
            if current is None or current["binding"] != binding or current != expected:
                raise ValueError("native snapshot compare-and-replace mismatch")
            _source, retained_messages = self.native_compaction_projection(current)
            candidate = {
                "binding": binding,
                "turnId": current["turnId"],
                "messages": [{"role": "assistant", "content": summary}] + retained_messages,
                "activeTurnStart": 1,
            }
            if "toolOrder" in current:
                candidate["toolOrder"] = list(current["toolOrder"])
            if recovery is not None or "compactionRecovery" in current:
                candidate["compactionRecovery"] = self._merge_native_recoveries(
                    current.get("compactionRecovery", []), [recovery] if recovery is not None else [])
            normalized = self._validate_native_snapshot(candidate, binding=binding)
            self._save_native(session_id, normalized)
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
            self._save_native(session_id, candidate)

    @staticmethod
    def _is_native_runtime_state_message(message: dict[str, Any]) -> bool:
        prefix = "Current runtime state (data): "
        if set(message) != {"role", "content"} or message.get("role") != "system":
            return False
        content = message.get("content")
        if not isinstance(content, str) or not content.startswith(prefix):
            return False
        try:
            state = json.loads(content[len(prefix):])
        except (TypeError, ValueError):
            return False
        required = {"exposureLayer", "projectContextActive", "allowedRuntimeActions", "availableToolNames"}
        optional = {"planMode", "modeConstraint", "projectPath", "loadedToolBlocks", "shellExecutor", "compactionRecoveryInformation"}
        if not isinstance(state, dict) or not required <= state.keys() or state.keys() - required - optional:
            return False
        if not isinstance(state["exposureLayer"], str) or state["exposureLayer"] not in {"planning", "execution"} or not isinstance(state["projectContextActive"], bool):
            return False
        for key in ("allowedRuntimeActions", "availableToolNames", "loadedToolBlocks", "compactionRecoveryInformation"):
            if key in state and (not isinstance(state[key], list) or any(not isinstance(item, str) for item in state[key])):
                return False
        return (all(isinstance(state[key], str) for key in ("modeConstraint", "projectPath") if key in state)
                and ("planMode" not in state or isinstance(state["planMode"], bool))
                and ("shellExecutor" not in state or isinstance(state["shellExecutor"], dict)))

    def append_native_runtime_state(
        self, session_id: str, *, binding: str, message: dict[str, Any],
    ) -> None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        if not self._is_native_runtime_state_message(message):
            raise ValueError("native runtime state must be host-built structured data")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if self._native_pending(snapshot):
                raise ValueError("native conversation has pending tool calls")
            candidate = copy.deepcopy(snapshot)
            candidate["messages"].append(copy.deepcopy(message))
            normalized = self._validate_native_snapshot(candidate, binding=binding)
            self._save_native(session_id, normalized)

    def append_native_scan_result_policy_once(
        self, session_id: str, *, binding: str, message: dict[str, Any],
    ) -> None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        if not is_scan_result_policy_message(message):
            raise ValueError("native scan policy must be the exact host constant")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if self._native_pending(snapshot):
                raise ValueError("native conversation has pending tool calls")
            if any(item == message for item in snapshot["messages"]):
                return
            candidate = copy.deepcopy(snapshot)
            candidate["messages"].append(copy.deepcopy(message))
            normalized = self._validate_native_snapshot(candidate, binding=binding)
            self._save_native(session_id, normalized)

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
            self._save_native(session_id, candidate)

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
            self._save_native(session_id, candidate)

    def freeze_native_tool_block_ids(self, session_id: str, *, binding: str, block_ids: list[str]) -> list[str]:
        """Keep the first discovery enum for this stored session/binding."""
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if "toolBlockIds" not in snapshot:
                candidate = copy.deepcopy(snapshot)
                candidate["toolBlockIds"] = sorted(set(block_ids))
                candidate = self._validate_native_snapshot(candidate, binding=binding)
                self._save_native(session_id, candidate)
                snapshot = candidate
            return list(snapshot["toolBlockIds"])

    def internal_tool_blocks(self, session_id: str) -> frozenset[str]:
        with self._ports.shared_state_lock:
            self._load_native(str(session_id or "").strip())
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
            self._load_native(session_id)
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
            self._save_native_selection(session_id)
            return frozenset(blocks)

    def internal_tool_selections(self, session_id: str) -> dict[str, list[str] | None]:
        with self._ports.shared_state_lock:
            self._load_native(session_id)
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
            self._load_native(session_id)
            session = self._sessions.get(session_id)
            blocks = {"core", *(str(item) for item in (session or {}).get("internalToolBlocks", []))}
            if block != "core":
                blocks.discard(block)
                if session is not None:
                    (session.get("internalToolSelections") or {}).pop(block, None)
            if session is not None:
                session["internalToolBlocks"] = sorted(blocks)
                self._save_native_selection(session_id)
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
            self._load_native(session_id)
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
                **(session or {}),
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
    ) -> bool:
        session_id = str(session_id or "").strip()
        turn_id = str(turn_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        with self._ports.shared_state_lock:
            if session_id and not (turn_id or client_turn_id):
                active = [
                    (key, active_turn_id)
                    for key, active_turn_id in self._active_turns.items()
                    if key[0] == session_id
                ]
                if active:
                    for (_active_session_id, active_client_turn_id), active_turn_id in active:
                        self._cancelled_targets.add(
                            (session_id, active_turn_id, active_client_turn_id)
                        )
                else:
                    # Preserve the supported "stop before the turn starts"
                    # behavior; the next turn consumes this session marker.
                    self._cancelled_targets.add((session_id, "", ""))
                return True

            # When one or more turn identities are supplied, all of them must
            # resolve to one active target.  A known partial collision is
            # rejected instead of allowing an OR match to cancel another turn;
            # an entirely unknown tuple is retained for a turn that is about
            # to begin.
            active_targets = [
                (active_session_id, active_turn_id, active_client_id)
                for (active_session_id, active_client_id), active_turn_id in self._active_turns.items()
                if active_turn_id and active_client_id
            ]
            matching = [
                target for target in active_targets
                if (not session_id or target[0] == session_id)
                and (not turn_id or target[1] == turn_id)
                and (not client_turn_id or target[2] == client_turn_id)
            ]
            colliding = [
                target for target in active_targets
                if (session_id and target[0] == session_id)
                or (turn_id and target[1] == turn_id)
                or (client_turn_id and target[2] == client_turn_id)
            ]
            if colliding and len(matching) != 1:
                return False
            if matching:
                self._cancelled_targets.add(matching[0])
            else:
                self._cancelled_targets.add((session_id, turn_id, client_turn_id))
            if session_id and client_turn_id:
                self._steer_mailboxes.pop((session_id, client_turn_id), None)
            return True

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
            if turn_id and active_turn_id is not None and active_turn_id != turn_id:
                return []
            undrained = copy.deepcopy(self._steer_mailboxes.get(key, []))
            if active_turn_id:
                self._finalizing_turns.add(key)
            self._active_turns.pop(key, None)
            self._steer_mailboxes.pop(key, None)
            self._steer_seen_ids.pop(key, None)
            # A completed turn must not leave cancellation/steer state behind
            # that could affect a later turn reusing either identifier.
            self._cancelled_targets = {
                target
                for target in self._cancelled_targets
                if not (
                    (target[1] or target[2])
                    and self._cancel_target_matches(
                        target,
                        session_id=session_id,
                        turn_id=turn_id,
                        client_turn_id=client_turn_id,
                    )
                )
            }
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

    def runtime_waiting_for_configuration(self) -> bool:
        """Only live-process waits hold settings; historical pending calls do not."""
        with self._ports.shared_state_lock:
            for session_id, entries in self._final_responses.items():
                if not entries:
                    continue
                item = entries[-1]
                if item.get("status") == "failed":
                    continue
                response = item.get("response") or {}
                if self.cancel_requested(session_id=session_id, turn_id=str(response.get("turnId") or response.get("turn_id") or ""),
                                         client_turn_id=str(item.get("clientTurnId") or "")):
                    continue
                plan = response.get("plan") or {}
                if plan.get("nextStep") in {"cancelled", "interrupted", "failed", "done"}:
                    continue
                if (plan.get("completionGate") or {}).get("reason") == "question_pending":
                    return True
                for field in ("write", "shell", "skill"):
                    payload = response.get(field) or {}
                    if payload.get("status") in {"approval_pending", "pending_approval", "pending", "running", "accepted", "queued"}:
                        return True
            return False

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

    def steer_pending(self, *, session_id: str, client_turn_id: str) -> bool:
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            return key in self._active_turns and bool(self._steer_mailboxes.get(key))

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
        session_id = str(session_id or "").strip()
        turn_id = str(turn_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        candidates = [item for item in (session_id, turn_id, client_turn_id) if item]
        if not candidates:
            return False
        with self._ports.shared_state_lock:
            return any(
                self._cancel_target_matches(
                    target,
                    session_id=session_id,
                    turn_id=turn_id,
                    client_turn_id=client_turn_id,
                )
                for target in self._cancelled_targets
            )

    def consume_cancel_request(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        client_turn_id: str = "",
    ) -> bool:
        session_id = str(session_id or "").strip()
        turn_id = str(turn_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        candidates = [item for item in (client_turn_id, turn_id, session_id) if item]
        if not candidates:
            return False
        with self._ports.shared_state_lock:
            matched = {
                target
                for target in self._cancelled_targets
                if self._cancel_target_matches(
                    target,
                    session_id=session_id,
                    turn_id=turn_id,
                    client_turn_id=client_turn_id,
                )
            }
            self._cancelled_targets.difference_update(matched)
            return bool(matched)

    @staticmethod
    def _cancel_target_matches(
        target: tuple[str, str, str],
        *,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
    ) -> bool:
        target_session, target_turn, target_client = target
        provided = sum(bool(item) for item in (session_id, turn_id, client_turn_id))
        if not provided:
            return False
        # A session-only marker intentionally applies to every later turn in
        # that session, including a full identity supplied when the turn begins.
        if target_session and not target_turn and not target_client:
            return bool(session_id and target_session == session_id)
        # A session-only query must not accidentally match a turn-specific
        # target; it means "stop the whole session" only when such a marker was
        # explicitly created.
        if provided == 1 and session_id:
            return False
        shared_identity = any(
            query and stored and query == stored
            for query, stored in (
                (session_id, target_session),
                (turn_id, target_turn),
                (client_turn_id, target_client),
            )
        )
        if not shared_identity:
            return False
        return (
            (not session_id or not target_session or target_session == session_id)
            and (not turn_id or not target_turn or target_turn == turn_id)
            and (not client_turn_id or not target_client or target_client == client_turn_id)
        )

    def set_stream_context(self, value: dict[str, str]) -> None:
        self._stream_context.value = dict(value)

    def clear_stream_context(self) -> None:
        self._stream_context.value = {}

    def stream_context(self) -> dict[str, str]:
        return dict(getattr(self._stream_context, "value", {}) or {})

    def next_provider_request_index(self) -> int | None:
        """Allocate on the bound runtime owner thread, independently of usage.

        The existing turn owner admits calls serially. Provider worker callbacks
        receive this value by closure; no cross-turn map or global counter exists.
        Unbound probes have no turn index. Request UUIDs remain globally unique.
        """
        context = self.stream_context()
        identity = tuple(context.get(key) or "" for key in ("sessionId", "turnId", "clientTurnId"))
        if not all(identity):
            return None
        if getattr(self._stream_context, "request_identity", None) != identity:
            self._stream_context.request_identity = identity
            self._stream_context.request_index = 0
        self._stream_context.request_index += 1
        return self._stream_context.request_index
