from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Any, Callable
from agent_handoff_graph import handoff_view, parse_handoff_graph
from context_compaction import MODEL_HANDOFF_PREFIX, handoff_text_payload, normalize_compaction_recovery
from agent_general_no_progress import LOOP_REMINDER
from agent_native_handoff_blocks import (
    handoff_attachments, handoff_block_messages, handoff_provider_messages, project_handoff_blocks,
    handoff_previous_reply, handoff_references, refresh_handoff_transactions, user_handoff_references,
)
from agent_native_history_store import NativeHistoryStore
from agent_output_truncation import CONTINUE_MESSAGE, OUTPUT_FEEDBACK_PREFIX, is_output_continuation_control
from agent_renderer_correction_history import retained_renderer_corrections
from agent_runtime_input import RuntimeInputSource, classify_history, classify_steer
from agent_user_path_context import confirmed_human_indices, human_indices, human_user_projection, missing_human_history
from agent_scan_result_policy import (
    SCAN_RESULT_CONTINUATION_POLICY, SCAN_RESULT_CONTINUATION_POLICY_V2,
    is_scan_result_policy_message, restored_scan_result_policy,
)
from responses_native_protocol import public_assistant_messages

NATIVE_RUNTIME_STATE_PREFIX = "Current runtime state (data): "
NATIVE_RUNTIME_CONTEXT_PREFIX = "Runtime context snapshot (data, not user instructions or authorization): "


def model_handoff_payload(entry: Mapping[str, object]) -> str | None:
    if entry.get("role") not in {"agent", "assistant"} or entry.get("tool_calls"):
        return None
    text = entry.get("text", entry.get("content"))
    return handoff_text_payload(text) if isinstance(text, str) else None


def model_handoff_continuation(entry: Mapping[str, object], *, todo_items=(), handoff_snapshot=None) -> str | None:
    payload = model_handoff_payload(entry)
    if payload is None or (graph := parse_handoff_graph(payload)) is None:
        return None
    projection = {**graph, **handoff_view(graph, todo_items=todo_items)}
    if handoff_snapshot and handoff_snapshot.get("handoffArchive"):
        from agent_handoff_operations import native_handoff_package_inputs

        if package_inputs := native_handoff_package_inputs(handoff_snapshot, graph):
            projection["packageInputsByPreflightNode"] = package_inputs
    return MODEL_HANDOFF_PREFIX + json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def native_handoff_message_sources(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    humans = set(human_indices(snapshot))
    revision = len(snapshot.get("compactionRecovery", ()))
    stored = snapshot.get("handoffArchive", {})
    inputs = {key: {field: copy.deepcopy(item[field]) for field in ("id", "source", "text", "references") if field in item}
              for key, item in stored.items() if item["source"] == RuntimeInputSource.USER}
    for index, item in enumerate(snapshot["messages"]):
        if item.get("role") != "user" or item.get("hidden") is True:
            continue
        text = item.get("content")
        source = item.get("runtimeInputSource")
        if index in humans:
            source = RuntimeInputSource.USER
            text = item.get("handoffSourceText", text)
        elif source in {RuntimeInputSource.PARENT_AGENT, RuntimeInputSource.AGENT_RELAY,
                        RuntimeInputSource.GOAL, RuntimeInputSource.EXTERNAL_AGENT, RuntimeInputSource.RETRY}:
            try:
                quoted = json.loads(text.partition(": ")[2])
            except ValueError:
                continue
            if not isinstance(quoted, dict) or quoted.get("source") != source:
                continue
            text = quoted.get("text")
        else:
            continue
        if isinstance(text, str) and (text.strip() or item.get("handoffAttachments")):
            message_id = str(item.get("messageId") or f"native:{snapshot['turnId']}:{revision}:{index}")
            references = user_handoff_references(item, message_id)
            inputs.setdefault(message_id, {"id": message_id, "source": RuntimeInputSource(source).value, "text": text,
                                           **({"references": references} if references else {})})
    return list(inputs.values())


def _last_native_public_reply(messages: list[dict[str, Any]]) -> tuple[int, str] | None:
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if item.get("role") != "assistant" or model_handoff_payload(item) is not None:
            continue
        if public := public_assistant_messages(item):
            return index, public[-1]["text"]
        calls = item.get("tool_calls") or []
        if not calls and isinstance(item.get("content"), str) and item["content"].strip():
            return index, item["content"]
        for call in reversed(calls):
            function = call["function"]
            if function["name"] != "vrcforge_runtime_action":
                continue
            try:
                arguments = json.loads(function["arguments"])
            except ValueError:
                continue
            if not isinstance(arguments, dict):
                continue
            reply = arguments.get("reply")
            if isinstance(reply, str) and reply.strip() and (
                arguments.get("action") in {"reply", "correct"}
                or arguments.get("action") == "shell" and str(arguments.get("shell_command") or "").strip()
            ):
                return index, reply
    return None


def _pending_suffix(messages: list[dict[str, Any]]) -> int:
    last_assistant = next((index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "assistant"), len(messages))
    if last_assistant == len(messages):
        return last_assistant
    calls = {call["id"] for call in messages[last_assistant].get("tool_calls") or []}
    settled = {item["tool_call_id"] for item in messages[last_assistant + 1:] if item.get("role") == "tool"}
    return last_assistant if calls - settled else len(messages)


def inject_native_handoff_context(messages: list[dict[str, Any]], current_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundary = _pending_suffix(messages)
    return [*messages[:boundary], *current_context, *messages[boundary:]]


def _native_task_boundaries(snapshot: dict[str, Any]) -> list[int]:
    humans = set(human_indices(snapshot))
    task_sources = {RuntimeInputSource.PARENT_AGENT, RuntimeInputSource.GOAL,
                    RuntimeInputSource.EXTERNAL_AGENT, RuntimeInputSource.RETRY}
    return [index for index, item in enumerate(snapshot["messages"]) if index in humans
            or (item.get("role") == "user" and item.get("runtimeInputSource") in task_sources)]


def _native_input_message(message: str, source: RuntimeInputSource) -> dict[str, str]:
    return {
        "role": "user", "content": source.model_text(message),
        **({"runtimeInputSource": source.value} if not source.is_human else {}),
    }


def _historical_native_user(item: dict[str, Any]) -> dict[str, Any]:
    text = item["text"]
    message = {"role": "user", "content": text}
    if isinstance(item.get("messageId"), str) and item["messageId"]:
        message["messageId"] = item["messageId"]
    if isinstance(item.get("sourceText"), str) and item["sourceText"] != text:
        message["handoffSourceText"] = item["sourceText"]
    if isinstance(item.get("handoffAttachments"), list) and item["handoffAttachments"]:
        message["handoffAttachments"] = handoff_attachments(item["handoffAttachments"])
    return message


def _relay_input_id(value: object) -> str:
    return value if isinstance(value, str) and classify_steer(value) is RuntimeInputSource.AGENT_RELAY else ""


def native_runtime_state_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if message.get("role") == "system" and isinstance(content, str) and content.startswith(NATIVE_RUNTIME_STATE_PREFIX):
        return content
    return ""


def _native_runtime_state_boundary(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return message.get("role") == "user" and not (
        isinstance(content, str) and content.startswith(NATIVE_RUNTIME_CONTEXT_PREFIX)
    )


def project_native_runtime_state_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first state of each run at its original cache-prefix position."""
    projected = []
    previous = ""
    for message in messages:
        if _native_runtime_state_boundary(message):
            previous = ""
        state = native_runtime_state_content(message)
        if state:
            if state == previous:
                continue
            previous = state
            # Older runs embedded prior answers in the privileged state message.
            # Split only the model projection and preserve the saved transcript.
            if '"questionTask"' in state:
                try:
                    payload = json.loads(state[len(NATIVE_RUNTIME_STATE_PREFIX):])
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get("questionTask"), dict):
                    quoted = {"questionTask": payload.pop("questionTask")}
                    projected.append({**message, "content": NATIVE_RUNTIME_STATE_PREFIX + json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":"))})
                    projected.append({"role": "user", "content": NATIVE_RUNTIME_CONTEXT_PREFIX + json.dumps(
                        quoted, ensure_ascii=False, separators=(",", ":"))})
                    continue
        projected.append(message)
    return projected


def native_model_history(messages: list[dict[str, Any]], *, handoff_snapshot=None) -> list[dict[str, Any]]:
    """Translate saved authorship at the shared provider boundary, including older hidden host notes."""
    if any(model_handoff_payload(item) is not None for item in messages):
        # A compacted transcript has one authoritative runtime snapshot.
        # Keep storage append-only and project the latest state before user
        # history. Filtering only host system records preserves tool pairs,
        # output continuation controls, and all later user messages.
        latest_state = next((item for item in reversed(messages) if native_runtime_state_content(item)), None)
        if latest_state is not None:
            messages = [latest_state, *(item for item in messages if not native_runtime_state_content(item))]
    projected = []
    todos = []
    for item in reversed(messages):
        content = item.get("content")
        if (item.get("role") != "user" or item.get("handoffUser") or item.get("messageId")
                or not isinstance(content, str) or not content.startswith(NATIVE_RUNTIME_CONTEXT_PREFIX)):
            continue
        try:
            payload = json.loads(content[len(NATIVE_RUNTIME_CONTEXT_PREFIX):])
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("activeTodo"), list):
            todos = payload["activeTodo"]
            break
    for item in handoff_provider_messages(messages):
        record = {key: value for key, value in item.items()
                  if key not in {"runtimeInputSource", "messageId", "handoffUser", "handoffOmittedCallId",
                                 "handoffSourceText", "handoffAttachments", "handoffReferences"}}
        content = record.get("content")
        if item.get("handoffUser"):
            prefix = f"[User message {item['messageId']}]\n"
            if isinstance(content, str):
                record["content"] = prefix + content
            elif isinstance(content, list):
                record["content"] = [{"type": "text", "text": prefix}, *content]
        if handoff := model_handoff_continuation(record, todo_items=todos, handoff_snapshot=handoff_snapshot):
            record["content"] = handoff
        if record.get("role") == "user" and record.get("hidden") is True and isinstance(content, str):
            if content in (LOOP_REMINDER, CONTINUE_MESSAGE):
                record = {"role": "system", "content": content}
            elif not content.startswith(OUTPUT_FEEDBACK_PREFIX):
                record.pop("hidden")
                if not item.get("runtimeInputSource"):
                    record["content"] = RuntimeInputSource.RUNTIME.model_text(content)
        projected.append(record)
    return project_native_runtime_state_history(projected)


@dataclass(frozen=True)
class AgentRuntimeSessionStatePorts:
    """Capabilities required by the in-memory runtime-session state owner.

    Optional storage is scoped to gateway-owned native history only, never
    approvals or execution. Lifetime and re-entrant lock belong to the gateway.
    """

    shared_state_lock: AbstractContextManager[Any]
    native_history_store: NativeHistoryStore | None = None
    discard_result_snapshots: Callable[[str], None] | None = None
    recover_native_results: Callable[[str, dict[str, Any], set[str]], dict[str, str]] | None = None


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
        "_stopped_core_dispatches",
        "_admitted_core_writes",
        "_active_turns",
        "_active_plan_turns",
        "_waiting_turns",
        "_subagent_parent_turns",
        "_finalizing_turns",
        "_exclusive_turns",
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
        self._cancelled_targets: dict[tuple[str, str, str], str] = {}
        self._stopped_core_dispatches: set[tuple[str, str, str]] = set()
        self._admitted_core_writes: set[tuple[str, str, str]] = set()
        self._active_turns: dict[tuple[str, str], str] = {}
        self._active_plan_turns: set[tuple[str, str]] = set()
        self._waiting_turns: dict[tuple[str, str], tuple[str, threading.Event]] = {}
        self._subagent_parent_turns: dict[tuple[str, str], tuple[str, str, str]] = {}
        self._finalizing_turns: set[tuple[str, str]] = set()
        self._exclusive_turns: set[tuple[str, str]] = set()
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
            self._stopped_core_dispatches.clear()
            self._admitted_core_writes.clear()
            self._active_turns.clear()
            self._active_plan_turns.clear()
            for _turn, cancelled in self._waiting_turns.values():
                cancelled.set()
            self._waiting_turns.clear()
            self._subagent_parent_turns.clear()
            self._finalizing_turns.clear()
            self._exclusive_turns.clear()
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

    def checkpoint_choice(self, session_id: str) -> dict[str, Any] | None:
        """Return this live session's explicit checkpoint setting, if any."""
        with self._ports.shared_state_lock:
            session = self._sessions.get(str(session_id or "").strip())
            choice = session.get("checkpointChoice") if session else None
            return copy.deepcopy(choice) if isinstance(choice, dict) else None

    def set_checkpoint_choice(self, session_id: str, choice: dict[str, Any]) -> None:
        """Store a user-validated checkpoint setting with the owning session."""
        key = str(session_id or "").strip()
        if not key or not isinstance(choice, dict) or choice.get("mode") not in {"skip", "require"}:
            raise ValueError("Checkpoint choice requires a session and mode skip or require")
        with self._ports.shared_state_lock:
            session = self._sessions.setdefault(key, {"id": key, "turns": []})
            session["checkpointChoice"] = copy.deepcopy(choice)

    def pending_obligations(self, session_id: str) -> list[dict[str, Any]]:
        """Unmet task obligations carried from the previous turn, if any."""
        with self._ports.shared_state_lock:
            session = self._sessions.get(str(session_id or "").strip())
            obligations = session.get("pendingObligations") if session else None
            return copy.deepcopy(obligations) if isinstance(obligations, list) else []

    def set_pending_obligations(self, session_id: str, obligations: list[dict[str, Any]]) -> None:
        """Store the next turn's seeded obligations (empty list clears)."""
        key = str(session_id or "").strip()
        bounded: list[dict[str, Any]] = []
        if isinstance(obligations, list):
            for item in obligations:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip()[:32]
                tool = str(item.get("tool") or "").strip()[:160]
                if not kind or not tool:
                    continue
                try:
                    generation = max(0, min(int(item.get("generation") or 0), 1000))
                except (TypeError, ValueError):
                    generation = 0
                bounded.append({
                    "kind": kind,
                    "tool": tool,
                    "verificationProfile": str(item.get("verificationProfile") or "").strip()[:80],
                    "generation": generation,
                    **({"target": copy.deepcopy(item["target"])}
                       if isinstance(item.get("target"), dict) and item["target"] else {}),
                })
        if not key:
            return
        with self._ports.shared_state_lock:
            session = self._sessions.setdefault(key, {"id": key, "turns": []})
            if bounded:
                session["pendingObligations"] = copy.deepcopy(bounded)
            else:
                session.pop("pendingObligations", None)

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
            for key in [key for key in self._waiting_turns if key[0] == session_id]:
                self._waiting_turns.pop(key)[1].set()
            for key in [key for key in self._user_adjustments if key[0] == session_id]:
                self._user_adjustments.pop(key, None)
            for key in {key for key in (*self._active_turns.keys(), *self._finalizing_turns) if key[0] == session_id}:
                self._active_turns.pop(key, None)
                self._active_plan_turns.discard(key)
                self._finalizing_turns.discard(key)
                self._exclusive_turns.discard(key)
                self._steer_mailboxes.pop(key, None)
                self._steer_seen_ids.pop(key, None)
            self._subagent_parent_turns = {
                key: parent for key, parent in self._subagent_parent_turns.items() if key[0] != session_id
            }
            self._cancelled_targets = {
                target: reason for target, reason in self._cancelled_targets.items() if target[0] != session_id
            }
            self._stopped_core_dispatches = {target for target in self._stopped_core_dispatches if target[0] != session_id}
            self._admitted_core_writes = {target for target in self._admitted_core_writes if target[0] != session_id}
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
            native_message = {key: value for key, value in message.items()
                              if key not in {"handoffAttachments", "handoffSourceText", "handoffReferences"}}
            encoded = json.dumps(native_message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
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
        allowed = {"role", "content", "tool_calls", "reasoning_content", "responses_output", "gemini_parts", "anthropic_content", "provider_protocol"}
        if set(message) - allowed:
            raise ValueError("native assistant message has unsupported fields")
        if message.get("role") != "assistant":
            raise ValueError("native assistant role must be assistant")
        if "content" in message and message["content"] is not None and not isinstance(message["content"], str):
            raise ValueError("native assistant content must be string or null")
        if "reasoning_content" in message and message["reasoning_content"] is not None and not isinstance(message["reasoning_content"], str):
            raise ValueError("native reasoning_content must be string or null")
        for field in ("gemini_parts", "anthropic_content"):
            if field in message and (not isinstance(message[field], list) or any(not isinstance(item, dict) for item in message[field])):
                raise ValueError("native provider blocks must be object lists")
        if "provider_protocol" in message and message["provider_protocol"] not in {"chat_completions", "responses", "messages", "generate_content"}:
            raise ValueError("native provider protocol is invalid")
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
        if not isinstance(snapshot, dict) or not required <= set(snapshot) or set(snapshot) - required - {
            "toolOrder", "compactionRecovery", "handoffMessageSources", "toolBlockIds", "bindingToolDefinition",
            "humanUserMessageIndices", "activeDispatchCallId", "adoptedSubAgentTaskIds", "relayInputIds",
            "handoffArchive", "handoffLastReplyId", "handoffLastReplyIndex",
        }:
            raise ValueError("native snapshot fields are invalid")
        if "activeDispatchCallId" in snapshot and not isinstance(snapshot["activeDispatchCallId"], str):
            raise ValueError("native dispatch marker must be a string")
        if "bindingToolDefinition" in snapshot:
            definition = snapshot["bindingToolDefinition"]
            if definition is not None and (not isinstance(definition, dict)
                    or set(definition) != {"name", "description", "parameters"}
                    or not isinstance(definition["name"], str) or not definition["name"]
                    or not isinstance(definition["description"], str)
                    or not isinstance(definition["parameters"], dict)):
                raise ValueError("native binding tool definition is invalid")
        if "toolBlockIds" in snapshot:
            ids = snapshot["toolBlockIds"]
            if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
                raise ValueError("native tool block snapshot is invalid")
        if "adoptedSubAgentTaskIds" in snapshot:
            ids = snapshot["adoptedSubAgentTaskIds"]
            if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
                raise ValueError("native adopted sub-agent task IDs are invalid")
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
                if (set(message) - {"role", "content", "hidden", "runtimeInputSource", "messageId",
                                    "handoffUser", "handoffOmittedCallId", "handoffSourceText", "handoffAttachments",
                                    "handoffReferences"}
                        or not {"role", "content"} <= set(message)
                        or ("hidden" in message and message["hidden"] is not True)
                        or not isinstance(message["content"], str) or not message["content"].strip()):
                    raise ValueError("native user message shape is invalid")
                if "messageId" in message:
                    cls._native_required(message["messageId"], "message id")
                if "handoffUser" in message and (message["handoffUser"] is not True or "messageId" not in message):
                    raise ValueError("native projected user message is invalid")
                if "handoffOmittedCallId" in message:
                    cls._native_required(message["handoffOmittedCallId"], "handoff transaction call id")
                if "handoffSourceText" in message and not isinstance(message["handoffSourceText"], str):
                    raise ValueError("native handoff source text is invalid")
                if "handoffAttachments" in message and (not isinstance(message["handoffAttachments"], list)
                        or handoff_attachments(message["handoffAttachments"]) != message["handoffAttachments"]):
                    raise ValueError("native handoff attachment records are invalid")
                if "handoffReferences" in message and (not isinstance(message["handoffReferences"], list)
                        or handoff_references(message["handoffReferences"]) != message["handoffReferences"]):
                    raise ValueError("native handoff references are invalid")
                if "runtimeInputSource" in message and (
                    not isinstance(message["runtimeInputSource"], str)
                    or RuntimeInputSource(message["runtimeInputSource"]).is_human
                ):
                    raise ValueError("native runtime input source is invalid")
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
                if is_output_continuation_control(message):
                    if pending:
                        raise ValueError("output continuation cannot interrupt pending tool calls")
                    normalized_messages.append(copy.deepcopy(message))
                    continue
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
        if "humanUserMessageIndices" in snapshot:
            indices = snapshot["humanUserMessageIndices"]
            if (not isinstance(indices, list) or any(type(index) is not int or index < 0
                    or index >= len(messages) or messages[index].get("role") != "user"
                    or "runtimeInputSource" in messages[index] for index in indices)
                    or indices != sorted(set(indices))):
                raise ValueError("native human user provenance is invalid")
            normalized["humanUserMessageIndices"] = list(indices)
        relay_ids = snapshot.get("relayInputIds", [])
        if not isinstance(relay_ids, list) or any(not _relay_input_id(item) for item in relay_ids):
            raise ValueError("native relay input identities are invalid")
        if relay_ids:
            normalized["relayInputIds"] = sorted(set(relay_ids))
        if "bindingToolDefinition" in snapshot:
            normalized["bindingToolDefinition"] = copy.deepcopy(snapshot["bindingToolDefinition"])
        if "toolOrder" in snapshot:
            normalized["toolOrder"] = list(snapshot["toolOrder"])
        if "toolBlockIds" in snapshot:
            normalized["toolBlockIds"] = list(snapshot["toolBlockIds"])
        if "adoptedSubAgentTaskIds" in snapshot:
            normalized["adoptedSubAgentTaskIds"] = list(snapshot["adoptedSubAgentTaskIds"])
        if "compactionRecovery" in snapshot:
            normalized["compactionRecovery"] = cls._merge_native_recoveries([], snapshot["compactionRecovery"])
        if "handoffMessageSources" in snapshot:
            sources = snapshot["handoffMessageSources"]
            if (not isinstance(sources, dict) or any(not isinstance(key, str) or not key
                    or not isinstance(value, dict) or set(value) != {"id", "source", "text"}
                    or value["id"] != key or value["source"] not in RuntimeInputSource._value2member_map_
                    or not isinstance(value["text"], str) for key, value in sources.items())):
                raise ValueError("native handoff message sources are invalid")
            normalized["handoffMessageSources"] = copy.deepcopy(sources)
        if "handoffArchive" in snapshot:
            archive = snapshot["handoffArchive"]
            if (not isinstance(archive, dict) or any(not isinstance(key, str) or not key
                    or not isinstance(value, dict) or not {"id", "source", "text"} <= set(value)
                    or set(value) - {"id", "source", "text", "attachments", "references"}
                    or value["id"] != key or value["source"] not in ("user", "assistant", "tool", "attachment")
                    or not isinstance(value["text"], str)
                    or ("attachments" in value and (not isinstance(value["attachments"], list)
                        or handoff_attachments(value["attachments"]) != value["attachments"]))
                    or ("references" in value and (not isinstance(value["references"], list)
                        or handoff_references(value["references"]) != value["references"]))
                    for key, value in archive.items())):
                raise ValueError("native handoff archive is invalid")
            normalized["handoffArchive"] = copy.deepcopy(archive)
        if "handoffLastReplyId" in snapshot or "handoffLastReplyIndex" in snapshot:
            reply_id, reply_index = snapshot.get("handoffLastReplyId"), snapshot.get("handoffLastReplyIndex")
            if (not isinstance(reply_id, str) or normalized.get("handoffArchive", {}).get(reply_id, {}).get("source") != "assistant"
                    or type(reply_index) is not int or not 0 <= reply_index < len(messages)
                    or messages[reply_index].get("role") != "assistant"):
                raise ValueError("native handoff last reply is invalid")
            normalized["handoffLastReplyId"] = reply_id
            normalized["handoffLastReplyIndex"] = reply_index
        if "activeDispatchCallId" in snapshot:
            normalized["activeDispatchCallId"] = snapshot["activeDispatchCallId"]
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

    @staticmethod
    def _new_adopted_subagent_message(entry: dict[str, Any], adopted_ids: list[str]) -> dict[str, Any] | None:
        task_id = entry.get("subAgentTaskId")
        text = entry.get("text")
        if (entry.get("role") != "agent" or not isinstance(task_id, str) or not task_id
                or task_id in adopted_ids or not isinstance(text, str) or not text.strip()):
            return None
        adopted_ids.append(task_id)
        return {"role": "assistant", "content": text}

    def begin_native_turn(
        self, session_id: str, *, binding: str, turn_id: str, message: str,
        initial_history: list[dict[str, Any]] | None = None,
        input_source: RuntimeInputSource = RuntimeInputSource.USER,
        input_id: str = "",
        source_text: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        references: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        turn_id = self._native_required(turn_id, "turn id")
        message = self._native_required(message, "message")
        input_message = _native_input_message(message, input_source)
        if input_source.is_human:
            if input_id:
                input_message["messageId"] = input_id
            if source_text is not None and source_text != message:
                input_message["handoffSourceText"] = source_text
            if attachments:
                input_message["handoffAttachments"] = handoff_attachments(attachments)
            if references:
                input_message["handoffReferences"] = handoff_references(references)
        current_relay_id = _relay_input_id(input_id) if input_source is RuntimeInputSource.AGENT_RELAY else ""
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
            existing = self._native_conversations.get(session_id)
            if existing is not None:
                pending = self._native_pending(existing)
                if pending:
                    # One shared implementation with request-time normalization:
                    # restore durable applied results, then settle orphans with
                    # the honest tri-state (unknown / not_executed). Reentrant
                    # shared_state_lock makes this safe inside this helper.
                    self.normalize_native_pending(session_id, binding=binding)
                    existing = self._native_conversations.get(session_id) or existing
                if existing["binding"] == binding and existing["turnId"] == turn_id:
                    return copy.deepcopy(existing)
                if existing["binding"] == binding:
                    candidate = copy.deepcopy(existing)
                    relay_ids = set(candidate.get("relayInputIds", []))
                    if current_relay_id and current_relay_id in relay_ids:
                        raise ValueError("Agent relay was already delivered to this session.")
                    indices = confirmed_human_indices(candidate, initial_history or [])
                    archive = candidate.get("handoffArchive", {})
                    source_rows = [item for item in initial_history or [] if isinstance(item, dict)
                                   and classify_history(item).is_human and isinstance(item.get("text"), str)]
                    original_rows = [{**item, "text": item.get("sourceText") or item["text"]} for item in source_rows]
                    original_matches: dict[str, list[dict[str, Any]]] = {}
                    for item, original in zip(source_rows, original_rows):
                        if projected := human_user_projection({"role": "user", "content": original["text"]}):
                            original_matches.setdefault(projected["content"], []).append(item)
                    missing_prefix, missing_tail = missing_human_history(
                        original_rows, [{**item, "content": archive.get(item.get("messageId"), {}).get(
                            "text", item.get("handoffSourceText") or item["content"])}
                            for index in indices if (item := candidate["messages"][index])],
                    )
                    for segment in (missing_prefix, missing_tail):
                        for index, projected in enumerate(segment):
                            originals = original_matches.get(projected["content"], [])
                            if len(originals) == 1:
                                segment[index] = _historical_native_user(originals[0])
                    if missing_prefix:
                        candidate["messages"][0:0] = missing_prefix
                    offset = len(missing_prefix)
                    if "handoffLastReplyIndex" in candidate:
                        candidate["handoffLastReplyIndex"] += offset
                    human_indices = [index + offset for index in indices]
                    if missing_tail:
                        tail_start = len(candidate["messages"])
                        candidate["messages"].extend(missing_tail)
                        human_indices.extend(range(tail_start, tail_start + len(missing_tail)))
                    candidate["humanUserMessageIndices"] = sorted([*range(offset), *human_indices])
                    adopted_ids = candidate.get("adoptedSubAgentTaskIds", [])
                    new_relay_notes = []
                    for item in initial_history or []:
                        if not isinstance(item, dict):
                            continue
                        if classify_history(item) is RuntimeInputSource.AGENT_RELAY:
                            relay_id = _relay_input_id(item.get("clientTurnId"))
                            text = item.get("text")
                            if (relay_id and relay_id != current_relay_id and relay_id not in relay_ids
                                    and isinstance(text, str) and text.strip()):
                                new_relay_notes.append(_native_input_message(text, RuntimeInputSource.AGENT_RELAY))
                                relay_ids.add(relay_id)
                        if report := self._new_adopted_subagent_message(item, adopted_ids):
                            candidate["messages"].append(report)
                    if adopted_ids:
                        candidate["adoptedSubAgentTaskIds"] = adopted_ids
                    candidate["turnId"] = turn_id
                    candidate["activeTurnStart"] = len(candidate["messages"])
                    if input_source.is_human:
                        candidate["humanUserMessageIndices"].append(len(candidate["messages"]))
                    candidate["messages"].append(input_message)
                    candidate["messages"].extend(new_relay_notes)
                    if current_relay_id:
                        relay_ids.add(current_relay_id)
                    if relay_ids:
                        candidate["relayInputIds"] = sorted(relay_ids)
                    self._native_size_ok(candidate)
                    self._save_native(session_id, candidate)
                    return copy.deepcopy(candidate)
            history_messages: list[dict[str, Any]] = []
            history_humans: list[int] = []
            relay_ids: set[str] = set()
            adopted_ids: list[str] = []
            for item in initial_history or []:
                if not isinstance(item, dict):
                    raise ValueError("native initial history item is invalid")
                if "subAgentTaskId" in item:
                    if report := self._new_adopted_subagent_message(item, adopted_ids):
                        history_messages.append(report)
                    continue
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                role = item.get("role")
                if role == "user":
                    history_humans.append(len(history_messages))
                    history_messages.append(_historical_native_user(item))
                elif role == "runtime":
                    source = classify_history(item)
                    relay_id = _relay_input_id(item.get("clientTurnId")) if source is RuntimeInputSource.AGENT_RELAY else ""
                    if relay_id:
                        if relay_id == current_relay_id or relay_id in relay_ids:
                            continue
                        relay_ids.add(relay_id)
                    history_messages.append(_native_input_message(text, source))
                elif role == "agent":
                    history_messages.append({"role": "assistant", "content": text})
                else:
                    continue
            candidate = {
                "binding": binding,
                "turnId": turn_id,
                "messages": history_messages + [input_message],
                "activeTurnStart": len(history_messages),
                "humanUserMessageIndices": history_humans + ([len(history_messages)] if input_source.is_human else []),
            }
            if adopted_ids:
                candidate["adoptedSubAgentTaskIds"] = adopted_ids
            if current_relay_id:
                relay_ids.add(current_relay_id)
            if relay_ids:
                candidate["relayInputIds"] = sorted(relay_ids)
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

    def native_handoff_archive(self, session_id: str, *, binding: str, message_id: str) -> dict[str, Any] | None:
        """Resolve a handoff pointer in the owning session and route."""
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
            native = self._native_conversations.get(session_id)
            if native is None or native["binding"] != binding:
                return None
            return copy.deepcopy(native.get("handoffArchive", {}).get(message_id) or
                                 native.get("handoffMessageSources", {}).get(message_id))

    def freeze_native_conversation(self, approval_id: str, session_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Persist the exact approval snapshot under an immutable approval key."""
        with self._ports.shared_state_lock:
            store = self._ports.native_history_store
            if store is None:
                raise ValueError("native history store is unavailable")
            selections = self.internal_tool_selections(session_id)
            return store.save_approval(approval_id, session_id, copy.deepcopy(snapshot), selections)

    def frozen_native_conversation(self, reference: dict[str, Any]) -> dict[str, Any] | None:
        with self._ports.shared_state_lock:
            store = self._ports.native_history_store
            value = store.load_approval(reference) if store is not None else None
            return copy.deepcopy(value.get("native")) if value is not None else None

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
            message_sources = {**normalized.get("handoffMessageSources", {}), **existing.get("handoffMessageSources", {})}
            def restored(candidate: dict[str, Any]) -> dict[str, Any]:
                candidate = copy.deepcopy(candidate)
                if recoveries or "compactionRecovery" in candidate:
                    candidate["compactionRecovery"] = copy.deepcopy(recoveries)
                if message_sources:
                    candidate["handoffMessageSources"] = copy.deepcopy(message_sources)
                self._native_size_ok(candidate)
                self._save_native(session_id, candidate)
                return copy.deepcopy(candidate)
            if incoming_messages == current_messages and normalized["turnId"] == existing["turnId"]:
                return restored(existing)
            existing_open = _pending_suffix(current_messages)
            incoming_open = _pending_suffix(incoming_messages)
            if (existing["turnId"] == normalized["turnId"] and existing_open < len(current_messages)
                    and incoming_open < len(incoming_messages) and any(map(model_handoff_payload, current_messages))
                    and existing.get("activeDispatchCallId") == normalized.get("activeDispatchCallId")
                    and (existing.get("handoffArchive") or
                         current_messages[existing["activeTurnStart"]] == incoming_messages[normalized["activeTurnStart"]])
                    and current_messages[existing_open:] == incoming_messages[incoming_open:]):
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
    def native_handoff_tail(cls, snapshot: dict[str, Any]) -> int:
        messages = snapshot["messages"]
        start = snapshot["activeTurnStart"]
        pending = cls._native_pending(snapshot)
        settled = [index for index in range(start, len(messages)) if messages[index].get("role") == "assistant"
                   and not any(call["id"] in pending for call in messages[index].get("tool_calls") or [])]
        # A finished user turn stays indivisible. An open one can contain
        # enough completed model decisions to compact a settled prefix.
        open_turn = bool(pending or (settled and messages[settled[-1]].get("tool_calls"))
                         or is_output_continuation_control(messages[-1]))
        if open_turn and len(settled) >= 6:
            return settled[-3]
        prior = [index for index in _native_task_boundaries(snapshot) if index < start]
        return prior[-2] if len(prior) >= 2 else (prior[0] if prior else start)

    @classmethod
    def _native_handoff_indices(cls, snapshot: dict[str, Any], tail_from: int) -> list[int]:
        messages = snapshot["messages"]
        start = snapshot["activeTurnStart"]
        task_boundaries = _native_task_boundaries(snapshot)
        prior = [index for index in task_boundaries if index < start]
        prior_tail = prior[-2] if len(prior) >= 2 else (prior[0] if prior else start)
        first = task_boundaries[0] if task_boundaries else start
        task_indices = set(task_boundaries)
        def in_tail(index: int) -> bool:
            return index >= tail_from or (tail_from > start and prior_tail <= index < start)
        return [index for index, item in enumerate(messages) if index in {first, start}
                or (index >= min(start, tail_from) and item.get("runtimeInputSource") == RuntimeInputSource.AGENT_RELAY)
                or is_scan_result_policy_message(item)
                or (in_tail(index) and (index in task_indices or item.get("role") in {"assistant", "tool"}
                                        or is_output_continuation_control(item)) and model_handoff_payload(item) is None)]

    @classmethod
    def native_handoff_projection(cls, snapshot: dict[str, Any], *, character_budget: int, measure: Callable[[str], int] = len,
                                  preserve_user_text: bool = False,
                                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        messages = snapshot["messages"]
        pending_from = _pending_suffix(messages)
        active = snapshot["activeTurnStart"]
        humans = [index for index in human_indices(snapshot) if not messages[index].get("hidden")]
        if pending_from == len(messages):
            human_set = set(humans)
            last_decision = next((index for index in range(len(messages) - 1, active, -1)
                                  if messages[index]["role"] == "assistant" or index in human_set
                                  or messages[index].get("runtimeInputSource")), len(messages))
            if last_decision < len(messages) and messages[last_decision].get("tool_calls"):
                pending_from = last_decision
        transaction_end = next((index for index in range(pending_from + 1, len(messages))
                                if messages[index]["role"] != "tool"), len(messages))
        revision = len(snapshot.get("compactionRecovery", ()))
        user_sources = {index: (messages[index].get("messageId") or f"native:{snapshot['turnId']}:{revision}:{index}")
                        for index in humans}
        task_indices = set()
        if active not in user_sources:
            task_indices.add(active)
        for index, item in enumerate(messages[:pending_from]):
            if item.get("runtimeInputSource") == RuntimeInputSource.PARENT_AGENT:
                task_indices.add(index)
                break
        task_indices.update(index for index in range(active, pending_from)
                            if messages[index].get("runtimeInputSource") == RuntimeInputSource.AGENT_RELAY)
        last_reply = _last_native_public_reply(messages)
        blocks = project_handoff_blocks(snapshot, humans=humans, archive_indices=user_sources, pending_from=pending_from,
                                        transaction_end=transaction_end,
                                        task_indices=task_indices, last_reply=last_reply,
                                        policy_indices={index for index, item in enumerate(messages) if is_scan_result_policy_message(item)},
                                        continuation_index=len(messages) - 1 if is_output_continuation_control(messages[-1]) else None,
                                        character_budget=character_budget, measure=measure, preserve_user_text=preserve_user_text)
        inputs_by_id = {row["id"]: row for row in blocks["taskInputs"]}
        previous_boundary = 0
        for index in humans:
            if preceding := _last_native_public_reply(messages[previous_boundary:index]):
                inputs_by_id[user_sources[index]]["previousReply"] = handoff_previous_reply(
                    blocks["messageArchive"], snapshot["turnId"], previous_boundary + preceding[0], preceding[1], measure)
            previous_boundary = index + 1
        for row in native_handoff_message_sources(snapshot):
            if row["source"] != RuntimeInputSource.USER:
                if row["source"] == RuntimeInputSource.PARENT_AGENT and all(
                    item["source"] != RuntimeInputSource.PARENT_AGENT for item in blocks["taskInputs"]
                ):
                    blocks["taskInputs"].insert(0, row)
                else:
                    blocks["taskInputs"].append(row)
        for index in task_indices:
            if source_id := messages[index].get("messageId"):
                blocks["messages"][blocks["sourceIndices"][index]]["messageId"] = source_id
            elif messages[index].get("runtimeInputSource"):
                blocks["messages"][blocks["sourceIndices"][index]]["messageId"] = f"native:{snapshot['turnId']}:{revision}:{index}"
        source = []
        for index, message in enumerate(messages):
            if index in blocks["sourceIndices"] and index not in user_sources:
                continue
            if message["role"] not in {"user", "assistant", "tool"} or "handoffOmittedCallId" in message:
                continue
            if message["role"] == "user" and str(message["content"]).startswith(NATIVE_RUNTIME_CONTEXT_PREFIX):
                continue
            if index in user_sources:
                message = {**message, "content": blocks["messages"][blocks["sourceIndices"][index]]["content"], "humanUser": True}
            elif last_reply and index == last_reply[0] and not message.get("tool_calls"):
                continue
            source.append(copy.deepcopy(message))
        return source, blocks

    @classmethod
    def native_compaction_projection(cls, snapshot: dict[str, Any], *, tail_from: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if tail_from is not None:
            messages = snapshot["messages"]
            retained_indices = set(cls._native_handoff_indices(snapshot, tail_from))
            humans = set(human_indices(snapshot))
            retained = [copy.deepcopy(item) for index, item in enumerate(messages) if index in retained_indices]
            archived = [{**copy.deepcopy(item), **({"humanUser": True} if index in humans else {})}
                        for index, item in enumerate(messages) if index not in retained_indices
                        and (item.get("role") in {"assistant", "tool"}
                             or (item.get("role") == "user" and not str(item.get("content")).startswith(NATIVE_RUNTIME_CONTEXT_PREFIX)))]
            return archived, retained
        # Every pending call (including approval/question waits) blocks replacement.
        if cls._native_pending(snapshot):
            raise ValueError("native compaction has pending calls")
        start = snapshot["activeTurnStart"]
        known_humans = set(human_indices(snapshot))
        active = snapshot["messages"][start]
        active_program_input = bool(active.get("runtimeInputSource"))
        active_runtime_indices = {
            index for index in range(start, len(snapshot["messages"]))
            if snapshot["messages"][index].get("runtimeInputSource") == RuntimeInputSource.AGENT_RELAY
        }
        if active_program_input:
            active_runtime_indices.add(start)
        retained = []
        for index, item in enumerate(snapshot["messages"]):
            if index in active_runtime_indices:
                retained.append(copy.deepcopy(item))
            elif index in known_humans and (projected := human_user_projection(item)) is not None:
                retained.append(projected)
        source = project_native_runtime_state_history([
            {**copy.deepcopy(item), **({"humanUser": True} if index in known_humans else {})}
            for index, item in enumerate(snapshot["messages"])
            if index < start or (index not in known_humans and index not in active_runtime_indices)
        ])
        if not retained or not source:
            raise ValueError("native compaction source is unavailable")
        retained.extend(restored_scan_result_policy(snapshot["messages"], retained))
        retained.extend(retained_renderer_corrections(snapshot["messages"]))
        from responses_native_protocol import is_hidden_continuation
        if is_hidden_continuation(snapshot["messages"]):
            retained.append({"role": "system", "content": CONTINUE_MESSAGE})
        return source, retained

    def replace_native_compacted_history(
        self,
        session_id: str,
        *,
        binding: str,
        expected_snapshot: dict[str, Any],
        summary: str,
        recovery: dict[str, Any] | None = None,
        current_context: list[dict[str, Any]] | None = None,
        tail_from: int | None = None,
        handoff_blocks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        summary = self._native_required(summary, "summary")
        expected = self._validate_native_snapshot(expected_snapshot, binding=binding)
        with self._ports.shared_state_lock:
            current = self._native_conversations.get(session_id)
            if current is None or current["binding"] != binding or current != expected:
                raise ValueError("native snapshot compare-and-replace mismatch")
            if handoff_blocks is None:
                _source, retained_messages = self.native_compaction_projection(current, tail_from=tail_from)
            else:
                retained_messages = handoff_blocks["messages"]
            known_humans = human_indices(current)
            if current_context:
                if any(item.get("role") != "user"
                       or not str(item.get("content")).startswith(NATIVE_RUNTIME_CONTEXT_PREFIX) for item in current_context):
                    raise ValueError("native compaction context was not emitted by the current owner")
            kept_indices = self._native_handoff_indices(current, tail_from) if tail_from is not None else None
            if handoff_blocks is None:
                active_turn_start = (1 + kept_indices.index(current["activeTurnStart"]) if kept_indices is not None else
                    1 + sum(index < current["activeTurnStart"] and human_user_projection(current["messages"][index]) is not None for index in known_humans))
            else:
                active_turn_start = handoff_blocks["activeTurnStart"] + len(current_context or [])
            candidate = {
                "binding": binding,
                "turnId": current["turnId"],
                "messages": handoff_block_messages(handoff_blocks, summary=summary, current_context=current_context)
                    if handoff_blocks is not None else [{"role": "assistant", "content": summary}]
                    + inject_native_handoff_context(retained_messages, current_context or []),
                "activeTurnStart": active_turn_start,
                "humanUserMessageIndices": ([index + 1 for index, original in enumerate(kept_indices) if original in known_humans]
                    if kept_indices is not None else [index + 1 for index, item in enumerate(retained_messages)
                        if item["role"] == "user" and not item.get("runtimeInputSource")]),
            }
            if handoff_blocks is not None:
                from agent_handoff_operations import native_handoff_package_selector_archive

                offset = len(current_context or [])
                candidate["humanUserMessageIndices"] = [index + offset for index in handoff_blocks["humanUserMessageIndices"]]
                candidate["handoffArchive"] = {
                    **copy.deepcopy(handoff_blocks["messageArchive"]), **native_handoff_package_selector_archive(current),
                }
                if handoff_blocks["lastReplyId"]:
                    candidate["handoffLastReplyId"] = handoff_blocks["lastReplyId"]
                    candidate["handoffLastReplyIndex"] = handoff_blocks["lastReplyIndex"] + offset + 1
            for key in ("toolOrder", "toolBlockIds", "bindingToolDefinition", "adoptedSubAgentTaskIds", "relayInputIds", "activeDispatchCallId"):
                if key in current:
                    candidate[key] = copy.deepcopy(current[key])
            if recovery is not None or "compactionRecovery" in current:
                candidate["compactionRecovery"] = self._merge_native_recoveries(
                    current.get("compactionRecovery", []), [recovery] if recovery is not None else [])
            if (payload := handoff_text_payload(summary)) and (graph := parse_handoff_graph(payload)):
                referenced = {item["id"] for node in graph["nodes"].values() for item in node.get("evidence", ())
                              if item["type"] == "message"}
                sources = {**{item["id"]: {key: item[key] for key in ("id", "source", "text")}
                              for item in native_handoff_message_sources(current)},
                           **current.get("handoffMessageSources", {})}
                selected_sources = {key: item for key, item in sources.items() if key in referenced}
                if selected_sources:
                    candidate["handoffMessageSources"] = selected_sources
            normalized = self._validate_native_snapshot(candidate, binding=binding)
            if self._native_pending(current) != self._native_pending(normalized):
                raise ValueError("native compaction would change pending calls")
            self._save_native(session_id, normalized)
            return copy.deepcopy(normalized)

    def append_native_user(self, session_id: str, *, binding: str, message: str, human: bool = True,
                           hidden: bool = False, input_source: RuntimeInputSource | None = None,
                           input_id: str = "", attachments: list[dict[str, Any]] | None = None,
                           source_text: str | None = None, references: list[dict[str, str]] | None = None) -> int | None:
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        message = self._native_required(message, "message")
        record = _native_input_message(message, input_source) if input_source is not None else {
            "role": "user", "content": message,
        }
        if input_source is not None:
            human = input_source.is_human
        relay_id = _relay_input_id(input_id) if input_source is RuntimeInputSource.AGENT_RELAY else ""
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if self._native_pending(snapshot):
                raise ValueError("native conversation has pending tool calls")
            if relay_id and relay_id in snapshot.get("relayInputIds", []):
                return None
            if human and input_id and (input_id in snapshot.get("handoffArchive", {}) or
                    any(item.get("messageId") == input_id for item in snapshot["messages"])):
                return None
            candidate = copy.deepcopy(snapshot)
            candidate["humanUserMessageIndices"] = human_indices(snapshot)
            if human and not hidden:
                candidate["humanUserMessageIndices"].append(len(candidate["messages"]))
            index = len(candidate["messages"])
            if human and input_id:
                record["messageId"] = input_id
            if human and attachments:
                record["handoffAttachments"] = handoff_attachments(attachments)
            if human and source_text is not None and source_text != message:
                record["handoffSourceText"] = source_text
            if human and references:
                record["handoffReferences"] = handoff_references(references)
            candidate["messages"].append({**record, **({"hidden": True} if hidden else {})})
            if relay_id:
                candidate["relayInputIds"] = sorted([*candidate.get("relayInputIds", []), relay_id])
            self._native_size_ok(candidate)
            self._save_native(session_id, candidate)
            return index

    def append_native_output_continuation(self, session_id: str, *, binding: str) -> None:
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            candidate = copy.deepcopy(snapshot)
            candidate["messages"].append({"role": "system", "content": CONTINUE_MESSAGE})
            self._save_native(session_id, self._validate_native_snapshot(candidate, binding=binding))

    @staticmethod
    def _is_native_runtime_state_message(message: dict[str, Any]) -> bool:
        if set(message) != {"role", "content"} or message.get("role") != "system":
            return False
        content = native_runtime_state_content(message)
        if not content:
            return False
        try:
            state = json.loads(content[len(NATIVE_RUNTIME_STATE_PREFIX):])
        except (TypeError, ValueError):
            return False
        required = {"exposureLayer", "projectContextActive", "allowedRuntimeActions", "availableToolNames"}
        optional = {"planMode", "modeConstraint", "projectPath", "loadedToolBlocks", "shellExecutor",
                    "compactionRecoveryInformation", "checkpointChoice", "executionMode", "questionTask",
                    "toolLoopReminder", "pendingApprovals"}
        if not isinstance(state, dict) or not required <= state.keys() or state.keys() - required - optional:
            return False
        if not isinstance(state["exposureLayer"], str) or state["exposureLayer"] not in {"planning", "execution"} or not isinstance(state["projectContextActive"], bool):
            return False
        for key in ("allowedRuntimeActions", "availableToolNames", "loadedToolBlocks", "compactionRecoveryInformation"):
            if key in state and (not isinstance(state[key], list) or any(not isinstance(item, str) for item in state[key])):
                return False
        return (all(isinstance(state[key], str) for key in ("modeConstraint", "projectPath") if key in state)
                and ("planMode" not in state or isinstance(state["planMode"], bool))
                and ("executionMode" not in state or state["executionMode"] in ("approval", "auto", "roslyn_full_auto"))
                and ("questionTask" not in state or isinstance(state["questionTask"], dict))
                and ("toolLoopReminder" not in state or state["toolLoopReminder"] == LOOP_REMINDER)
                and ("pendingApprovals" not in state or (
                    isinstance(state["pendingApprovals"], list) and len(state["pendingApprovals"]) <= 8
                    and all(isinstance(item, dict) and set(item) == {"approvalId", "tool", "status"}
                            and item["status"] == "awaiting_user_decision"
                            and all(isinstance(item[key], str) and item[key] for key in ("approvalId", "tool"))
                            for item in state["pendingApprovals"])
                ))
                and ("checkpointChoice" not in state or (
                    isinstance(state["checkpointChoice"], dict)
                    and state["checkpointChoice"].get("mode") in {"skip", "require"}
                    and set(state["checkpointChoice"]) == {"mode"}
                ))
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
            for previous in reversed(snapshot["messages"][snapshot["activeTurnStart"]:]):
                if _native_runtime_state_boundary(previous):
                    break
                if native_runtime_state_content(previous):
                    if previous == message:
                        return
                    break
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
        self.settle_native_calls(session_id, binding=binding, results={call_id: content})

    def settle_native_calls(self, session_id: str, *, binding: str, results: dict[str, str]) -> None:
        if not results:
            return
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            pending = self._native_pending(snapshot)
            all_calls = {call["id"] for item in snapshot["messages"] for call in item.get("tool_calls", []) or []}
            candidate = copy.deepcopy(snapshot)
            for call_id, content in results.items():
                call_id = self._native_required(call_id, "call id")
                if not isinstance(content, str):
                    raise ValueError("native tool content must be string")
                if call_id in all_calls and call_id not in pending:
                    raise ValueError("native call is already settled")
                if call_id not in pending:
                    raise ValueError("native call is unknown")
                pending.remove(call_id)
                candidate["messages"].append({"role": "tool", "tool_call_id": call_id, "content": content})
                if candidate.get("activeDispatchCallId") == call_id:
                    candidate["activeDispatchCallId"] = ""
            refresh_handoff_transactions(candidate)
            self._native_size_ok(candidate)
            self._save_native(session_id, candidate)

    def mark_native_dispatch(self, session_id: str, *, binding: str, call_id: str) -> None:
        """Persist which queued call is being dispatched right now.

        An empty ``call_id`` resets the marker. The marker survives crashes so
        recovery can tell a call that may have started executing from calls
        that provably never launched.
        """
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            candidate = copy.deepcopy(snapshot)
            candidate["activeDispatchCallId"] = str(call_id or "")
            self._save_native(session_id, candidate)

    def normalize_native_pending(self, session_id: str, *, binding: str) -> int:
        """Idempotently settle orphaned tool calls before a provider request.

        Runs at every native request assembly. Recovery of durable applied
        results comes first; the remaining orphans settle with an honest
        tri-state so the model never replays a write that may have run:

        - snapshots without the dispatch marker (legacy) are treated as
          unknown because nothing proves the call never launched;
        - the marked call (or nothing marked in a marked snapshot) settles as
          not_executed only when the marker proves it never launched;
        - a marked call settles as execution_unknown with ``isError`` and
          guidance to inspect current state before retrying.
        """
        session_id = self._native_required(session_id, "session id")
        binding = self._native_required(binding, "binding")
        with self._ports.shared_state_lock:
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                return 0
            pending = self._native_pending(snapshot)
            if not pending:
                if "activeDispatchCallId" in snapshot and snapshot["activeDispatchCallId"]:
                    candidate = copy.deepcopy(snapshot)
                    candidate["activeDispatchCallId"] = ""
                    self._save_native(session_id, candidate)
                return 0
            recovered = (
                self._ports.recover_native_results(session_id, snapshot, pending)
                if self._ports.recover_native_results is not None else {}
            )
            legacy = "activeDispatchCallId" not in snapshot
            dispatched = str(snapshot.get("activeDispatchCallId") or "")
            candidate = copy.deepcopy(snapshot)
            settled = 0
            for call_id in sorted(pending):
                if call_id in recovered:
                    content = recovered[call_id]
                elif legacy or call_id == dispatched:
                    content = json.dumps(
                        {
                            "status": "execution_unknown",
                            "reason": "runtime_interrupted",
                            "isError": True,
                            "guidance": (
                                "This call may have started executing before the interruption. "
                                "Inspect the current state before retrying."
                            ),
                            "observations": [],
                        },
                        ensure_ascii=False,
                    )
                else:
                    content = json.dumps(
                        {"status": "not_executed", "reason": "runtime_interrupted", "observations": []},
                        ensure_ascii=False,
                    )
                candidate["messages"].append({"role": "tool", "tool_call_id": call_id, "content": content})
                settled += 1
            candidate["activeDispatchCallId"] = ""
            refresh_handoff_transactions(candidate)
            self._native_size_ok(candidate)
            self._save_native(session_id, candidate)
            return settled

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

    def freeze_native_binding_tool_definition(self, session_id: str, *, binding: str, definition: dict | None) -> dict | None:
        """Retain the first Unity binding definition, independently of admission."""
        with self._ports.shared_state_lock:
            self._load_native(session_id, binding)
            snapshot = self._native_conversations.get(session_id)
            if snapshot is None or snapshot["binding"] != binding:
                raise ValueError("native conversation binding mismatch")
            if "bindingToolDefinition" not in snapshot:
                candidate = {**snapshot, "bindingToolDefinition": copy.deepcopy(definition)}
                candidate = self._validate_native_snapshot(candidate, binding=binding)
                self._save_native(session_id, candidate)
                snapshot = candidate
            return copy.deepcopy(snapshot["bindingToolDefinition"])

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
                if role not in ("user", "agent", "runtime"):
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
        reason: str = "user_stop",
    ) -> tuple[str, str, str] | None:
        session_id = str(session_id or "").strip()
        turn_id = str(turn_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        with self._ports.shared_state_lock:
            admitted = {**self._active_turns, **{key: value[0] for key, value in self._waiting_turns.items()}}
            if session_id and not (turn_id or client_turn_id):
                active = [
                    (key, active_turn_id)
                    for key, active_turn_id in admitted.items()
                    if key[0] == session_id
                ]
                if active:
                    for (_active_session_id, active_client_turn_id), active_turn_id in active:
                        self._stopped_core_dispatches.add((session_id, active_turn_id, active_client_turn_id))
                        self._cancelled_targets.setdefault(
                            (session_id, active_turn_id, active_client_turn_id), reason
                        )
                        waiting = self._waiting_turns.get((session_id, active_client_turn_id))
                        if waiting:
                            waiting[1].set()
                else:
                    # Preserve the supported "stop before the turn starts"
                    # behavior; the next turn consumes this session marker.
                    self._cancelled_targets.setdefault((session_id, "", ""), reason)
                return (session_id, "", "")

            # Desktop can stop a request by its session and client turn ID
            # before admission assigns a server turn ID. Reserve that exact
            # pair without touching any active turn under either ID alone.
            active_targets = [
                (active_session_id, active_turn_id, active_client_id)
                for (active_session_id, active_client_id), active_turn_id in admitted.items()
                if active_turn_id and active_client_id
            ]
            matching = [
                target for target in active_targets
                if (not session_id or target[0] == session_id)
                and (not turn_id or target[1] == turn_id)
                and (not client_turn_id or target[2] == client_turn_id)
            ]
            if not matching:
                matching_parents = [
                    (key, parent) for key, parent in self._subagent_parent_turns.items()
                    if (not session_id or parent[0] == session_id)
                    and (not turn_id or parent[1] == turn_id)
                    and (not client_turn_id or parent[2] == client_turn_id)
                ]
                if len(matching_parents) == 1:
                    key, parent = matching_parents[0]
                    active_turn = self._active_turns.get(key)
                    if active_turn is None:
                        # The queued continuation still needs to settle the tool receipt.
                        self._cancelled_targets.setdefault(parent, reason)
                        return parent
                    matching = [(key[0], active_turn, key[1])]
            colliding = [
                target for target in active_targets
                if (session_id and target[0] == session_id)
                or (turn_id and target[1] == turn_id)
                or (client_turn_id and target[2] == client_turn_id)
            ]
            if colliding and len(matching) != 1 and (turn_id or not (session_id and client_turn_id)):
                return None
            target = matching[0] if matching else (session_id, turn_id, client_turn_id)
            # A client ID can be reused in another session. An unscoped
            # legacy request may cancel owned work now, never a future turn.
            if matching or session_id or turn_id:
                self._cancelled_targets.setdefault(target, reason)
            if matching:
                self._stopped_core_dispatches.add(target)
                waiting = self._waiting_turns.get((target[0], target[2]))
                if waiting:
                    waiting[1].set()
            if target[0] and target[2]:
                self._steer_mailboxes.pop((target[0], target[2]), None)
            return target

    def admit_core_dispatch(
        self, *, session_id: str, turn_id: str, client_turn_id: str, write: bool, settlement_read: bool,
    ) -> bool:
        target = (session_id, turn_id, client_turn_id)
        with self._ports.shared_state_lock:
            if self._active_turns.get((session_id, client_turn_id)) != turn_id:
                return False
            if target in self._stopped_core_dispatches:
                # A transaction whose write was already sent may still verify
                # it. The turn cannot start another business write.
                return settlement_read
            if write:
                self._admitted_core_writes.add(target)
            return True

    def core_write_admitted(self, *, session_id: str, turn_id: str, client_turn_id: str) -> bool:
        with self._ports.shared_state_lock:
            return (session_id, turn_id, client_turn_id) in self._admitted_core_writes

    def dispatch_stopped(self, *, session_id: str, turn_id: str, client_turn_id: str) -> bool:
        with self._ports.shared_state_lock:
            return (session_id, turn_id, client_turn_id) in self._stopped_core_dispatches

    def queue_turn(
        self, *, session_id: str, turn_id: str, client_turn_id: str,
        subagent_parent_turn: tuple[str, str, str] | None = None,
    ) -> threading.Event | None:
        """Track an admitted request waiting for this session's current owner."""
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            if key in self._active_turns or key in self._waiting_turns or key in self._finalizing_turns:
                return None
            cancelled = threading.Event()
            if self.consume_cancel_request(session_id=session_id, turn_id=turn_id, client_turn_id=client_turn_id):
                cancelled.set()
            self._waiting_turns[key] = (turn_id, cancelled)
            if subagent_parent_turn is not None:
                self._subagent_parent_turns[key] = subagent_parent_turn
            return cancelled

    def discard_waiting_turn(self, *, session_id: str, turn_id: str, client_turn_id: str) -> None:
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            waiting = self._waiting_turns.get(key)
            if waiting and waiting[0] == turn_id:
                self._waiting_turns.pop(key)
                self._subagent_parent_turns.pop(key, None)

    def begin_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
        exclusive: bool = True,
        plan_mode: bool = False,
    ) -> bool:
        if not session_id or not client_turn_id:
            return True
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            if key in self._active_turns or any(owner[0] == session_id for owner in self._exclusive_turns):
                return False
            if exclusive and any(owner[0] == session_id for owner in (*self._active_turns, *self._finalizing_turns)):
                return False
            waiting = next((owner for owner in self._waiting_turns if owner[0] == session_id), None)
            if waiting is not None and waiting != key:
                return False
            self._waiting_turns.pop(key, None)
            self._active_turns[key] = turn_id
            if plan_mode:
                self._active_plan_turns.add(key)
            parent = self._subagent_parent_turns.get(key)
            if parent is not None:
                reason = self.consume_cancel_reason(session_id=parent[0], turn_id=parent[1], client_turn_id=parent[2])
                if reason is not None:
                    target = (session_id, turn_id, client_turn_id)
                    self._cancelled_targets.setdefault(target, reason)
                    self._stopped_core_dispatches.add(target)
            if exclusive:
                self._exclusive_turns.add(key)
            self._finalizing_turns.discard(key)
            self._steer_mailboxes.setdefault(key, [])
            return True

    def finish_turn(self, *, session_id: str, turn_id: str = "", client_turn_id: str,
                    before_release: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        if not session_id or not client_turn_id:
            return []
        with self._ports.shared_state_lock:
            key = (session_id, client_turn_id)
            active_turn_id = self._active_turns.get(key)
            if turn_id and active_turn_id is not None and active_turn_id != turn_id:
                return []
            if active_turn_id and before_release is not None:
                before_release()
            undrained = copy.deepcopy(self._steer_mailboxes.get(key, []))
            if active_turn_id:
                self._finalizing_turns.add(key)
            self._active_turns.pop(key, None)
            self._active_plan_turns.discard(key)
            self._subagent_parent_turns.pop(key, None)
            target = (session_id, active_turn_id or turn_id, client_turn_id)
            self._stopped_core_dispatches.discard(target)
            self._admitted_core_writes.discard(target)
            self._steer_mailboxes.pop(key, None)
            self._steer_seen_ids.pop(key, None)
            # A completed turn must not leave cancellation/steer state behind
            # that could affect a later turn reusing either identifier.
            self._cancelled_targets = {
                target: reason
                for target, reason in self._cancelled_targets.items()
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

    def session_busy(self, session_id: str | None = None) -> bool:
        with self._ports.shared_state_lock:
            if session_id is None:
                return bool(self._active_turns or self._finalizing_turns or self._waiting_turns)
            return any(key[0] == session_id for key in (*self._active_turns, *self._finalizing_turns, *self._waiting_turns))

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
            self._exclusive_turns.discard(key)
            entries = self._final_responses.setdefault(session_id, [])
            entries[:] = [entry for entry in entries if entry.get("clientTurnId") != client_turn_id]
            entries.append(item)
            if len(entries) > self.MAX_FINAL_RESPONSES:
                del entries[:-self.MAX_FINAL_RESPONSES]

    def clear_finalizing_turn(self, *, session_id: str, client_turn_id: str) -> None:
        """Close a finalization marker when recovery bookkeeping cannot be stored."""

        with self._ports.shared_state_lock:
            key = (str(session_id or "").strip(), str(client_turn_id or "").strip())
            self._finalizing_turns.discard(key)
            self._exclusive_turns.discard(key)

    def final_response(
        self, *, session_id: str, client_turn_id: str,
    ) -> dict[str, Any]:
        """Return one exact-turn recovery projection without exposing the transcript."""

        session_id = str(session_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        key = (session_id, client_turn_id)
        with self._ports.shared_state_lock:
            if key in self._active_turns or key in self._finalizing_turns or key in self._waiting_turns:
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
        attachments: list[dict[str, Any]] | None = None,
        followup_lane_id: str = "",
        reserve: Callable[[str], dict[str, Any]] | None = None,
        plan_mode: bool | None = None,
        authenticated_answer: str = "",
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()[:180]
        target_client_turn_id = str(target_client_turn_id or "").strip()[:240]
        input_id = str(input_id or "").strip()[:180]
        message = str(message or "").strip()[:4000]
        if not session_id or not target_client_turn_id or not input_id or not (message or attachments):
            return {"accepted": False, "mode": "followup", "reason": "invalid_request"}
        with self._ports.shared_state_lock:
            key = (session_id, target_client_turn_id)
            turn_id = self._active_turns.get(key)
            if not turn_id or self.cancel_requested(
                session_id=session_id, turn_id=turn_id, client_turn_id=target_client_turn_id,
            ):
                return {"accepted": False, "mode": "followup", "reason": "turn_not_active"}
            mailbox = self._steer_mailboxes.setdefault(key, [])
            seen_ids = self._steer_seen_ids.setdefault(key, set())
            if input_id in seen_ids:
                return {"accepted": True, "mode": "steer", "status": "accepted", "reason": "duplicate_input", "deduped": True, "queuedCount": len(mailbox)}
            if plan_mode is not None and plan_mode != (key in self._active_plan_turns):
                return {"accepted": False, "mode": "followup", "reason": "plan_mode_mismatch"}
            # Keep the hot same-turn mailbox bounded. Overflow is not dropped:
            # the API atomically falls back to the durable follow-up lane.
            if len(mailbox) >= self.MAX_STEER_MAILBOX:
                return {"accepted": False, "mode": "followup", "reason": "mailbox_full"}
            receipt = {}
            if reserve is not None:
                reserved = reserve(turn_id)
                if not reserved.get("accepted"):
                    return reserved
                receipt = {key: reserved[key] for key in ("queueId", "sequence") if key in reserved}
            mailbox_item: dict[str, Any] = {
                "inputId": input_id,
                "message": message,
            }
            if authenticated_answer:
                mailbox_item["authenticatedAnswer"] = authenticated_answer
            if attachments:
                mailbox_item["attachments"] = copy.deepcopy(attachments)
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
                **receipt,
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
        return self.consume_cancel_reason(
            session_id=session_id, turn_id=turn_id, client_turn_id=client_turn_id,
        ) is not None

    def consume_cancel_reason(
        self, *, session_id: str = "", turn_id: str = "", client_turn_id: str = "",
    ) -> str | None:
        session_id = str(session_id or "").strip()
        turn_id = str(turn_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        with self._ports.shared_state_lock:
            matched = [
                target
                for target in self._cancelled_targets
                if self._cancel_target_matches(
                    target,
                    session_id=session_id,
                    turn_id=turn_id,
                    client_turn_id=client_turn_id,
                )
            ]
            if not matched:
                return None
            reason = self._cancelled_targets[matched[0]]
            for target in matched:
                del self._cancelled_targets[target]
            return reason

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
