from __future__ import annotations

import json
import secrets
import copy
from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from background_goal_runtime import classify_runtime_plan_outcome, classify_runtime_step_failure
from runtime_planner_service import public_runtime_payload
from agent_runtime_event_projection import project_runtime_turn_event


_COMPLETED_RUN_STATUSES = frozenset({"completed", "failed", "denied", "cancelled"})


def _terminal_continuation(event: dict[str, Any]) -> dict[str, Any] | None:
    continuation = event.get("continuationEvent")
    if not isinstance(continuation, dict):
        return None
    projected = dict(continuation)
    if event.get("startedAt"):
        projected["startedAt"] = event["startedAt"]
    if event.get("event") == "runtime_turn_completed":
        # A runtime invocation can end at a question/approval boundary while
        # the user's task is still waiting. Only terminal runs own this time.
        projected.pop("completedAt", None)
        if event.get("status") in _COMPLETED_RUN_STATUSES and event.get("createdAt"):
            projected["completedAt"] = event["createdAt"]
    return projected



def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _public_event(value: dict[str, Any]) -> dict[str, Any]:
    projected = dict(value)
    projected.pop("runtimeOwnerNonce", None)
    projected.pop("usageReconciliation", None)
    projected.pop("continuationTaskSeed", None)
    projected.pop("continuationTerminalEvent", None)
    if projected.get("shellContinuationState"):
        projected.pop("taskSeed", None)
        projected.pop("result", None)
        projected.pop("stdout", None)
        projected.pop("stderr", None)
    return projected


def _restore_private_native_snapshot(
    projected: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    """Keep only the validated native continuation snapshot in private storage."""

    if source.get("shellContinuationState") not in {
        "pending",
        "dispatching",
        "delivered",
        "interrupted",
    }:
        return projected
    seed = source.get("continuationTaskSeed")
    snapshot = seed.get("_nativeConversation") if isinstance(seed, dict) else None
    if not isinstance(snapshot, dict):
        return projected
    stored_seed = projected.get("continuationTaskSeed")
    if not isinstance(stored_seed, dict):
        stored_seed = {}
        projected["continuationTaskSeed"] = stored_seed
    stored_seed["_nativeConversation"] = copy.deepcopy(snapshot)
    return projected


@dataclass(frozen=True)
class AgentRuntimeRunLedgerPorts:
    """Fixed capabilities for the app-owned runtime-run JSONL ledger.

    Filesystem authority is limited to the dynamic ``log_path`` supplied by
    the containing AgentGateway. The owner has no Provider, tool execution,
    approval, checkpoint, session, or network capability. Its lifetime and
    shared lock are owned by that Gateway.
    """

    log_path: Callable[[], Path]
    shared_state_lock: AbstractContextManager[Any]
    now: Callable[[], str]
    normalize_path: Callable[[str], str]
    normalize_visual_accent: Callable[[Any], str]
    summarize_text: Callable[[str, int], str]
    redact: Callable[[Any], Any]
    ensure_append_boundary: Callable[[Path], None]
    flush_and_fsync: Callable[[Any], None]
    error_factory: Callable[[str, int], Exception]


class AgentRuntimeRunLedger:
    """Persist and project runtime-run events without owning runtime execution."""

    __slots__ = ("_ports", "_instance_nonce")

    def __init__(self, ports: AgentRuntimeRunLedgerPorts) -> None:
        self._ports = ports
        # Process-local ownership marker. It is deliberately never exposed in
        # public projections; a fresh gateway instance cannot claim an old run.
        self._instance_nonce = secrets.token_hex(16)

    @property
    def log_path(self) -> Path:
        return self._ports.log_path()

    @property
    def shared_state_lock(self) -> AbstractContextManager[Any]:
        return self._ports.shared_state_lock

    def normalize_visual_accent(self, value: Any) -> str:
        return self._ports.normalize_visual_accent(value)

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        source_entry = {
                "schema": "vrcforge.runtime_run.v1",
                "id": f"runevt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": self._ports.now(),
                "updatedAt": self._ports.now(),
                **entry,
            }
        if entry.get("event") == "runtime_turn_started":
            source_entry["runtimeOwnerNonce"] = self._instance_nonce
            source_entry["startedAt"] = source_entry["createdAt"]
        if entry.get("event") == "runtime_turn_completed":
            started_at = self._start_times().get((str(entry.get("sessionId") or ""), str(entry.get("turnId") or "")))
            if started_at:
                source_entry["startedAt"] = started_at
            source_entry.pop("completedAt", None)
            if entry.get("status") in _COMPLETED_RUN_STATUSES:
                source_entry["completedAt"] = source_entry["createdAt"]
        continuation = _terminal_continuation(source_entry)
        if continuation is not None:
            source_entry["continuationEvent"] = continuation
        safe_entry = _restore_private_native_snapshot(
            self._ports.redact(source_entry), source_entry
        )
        path = self.log_path
        with self._ports.shared_state_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._ports.ensure_append_boundary(path)
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")
                self._ports.flush_and_fsync(log_file)
        return safe_entry

    @staticmethod
    def _turn_key(event: dict[str, Any]) -> str:
        session = str(event.get("sessionId") or "").strip()
        client = str(event.get("clientTurnId") or "").strip()
        turn = str(event.get("turnId") or "").strip()
        return f"{session}\0{client or turn}" if session and (client or turn) else ""

    def append_timeline_event(
        self,
        *,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
        event: dict[str, Any],
        provider: str = "",
        provider_label: str = "",
        model: str = "",
        project_root: str = "",
    ) -> None:
        """Persist one safe visible event before notifying the live projector."""

        self.append({
            "event": "runtime_turn_timeline_event",
            "status": "running",
            "sessionId": str(session_id or "")[:180],
            "turnId": str(turn_id or "")[:180],
            "clientTurnId": str(client_turn_id or "")[:240],
            "provider": str(provider or "")[:120],
            "providerLabel": str(provider_label or "")[:160],
            "model": str(model or "")[:160],
            "projectRoot": str(project_root or "")[:1000],
            "timelineEvent": copy.deepcopy(event),
        })

    def recover_interrupted_runs(self) -> int:
        """Materialize old, non-terminal runs as durable foreground continuations."""
        with self.shared_state_lock:
            starts: dict[str, dict[str, Any]] = {}
            timelines: dict[str, list[dict[str, Any]]] = {}
            terminals: set[str] = set()
            interrupted: set[str] = set()
            try:
                source = self.log_path.open("r", encoding="utf-8")
            except FileNotFoundError:
                return 0
            def owned_events():
                # Existing ledgers can be large. Older records have no live
                # owner marker; do not parse their full provider/tool payloads.
                with source:
                    for line in source:
                        if not starts and '"runtimeOwnerNonce"' not in line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, dict):
                            yield event
            for event in owned_events():
                key = self._turn_key(event)
                if not key:
                    continue
                name = str(event.get("event") or "")
                if name == "runtime_turn_started" and event.get("runtimeOwnerNonce"):
                    starts[key] = event
                elif name == "runtime_turn_timeline_event":
                    item = event.get("timelineEvent")
                    if isinstance(item, dict):
                        timelines.setdefault(key, []).append(item)
                elif name == "runtime_turn_completed":
                    terminals.add(key)
                elif name == "runtime_turn_interrupted":
                    interrupted.add(key)
            created = 0
            for key, started in starts.items():
                if key in terminals or key in interrupted or started.get("runtimeOwnerNonce") == self._instance_nonce:
                    continue
                # Each event is already bounded by the live projector. Keep
                # the full visible order rather than dropping early messages.
                timeline = timelines.get(key, [])
                continuation = {
                    "schema": "vrcforge.runtime_turn_event.v1",
                    "continuationSource": "foreground_interrupted",
                    "sessionId": str(started.get("sessionId") or ""),
                    "turnId": str(started.get("turnId") or ""),
                    "clientTurnId": str(started.get("clientTurnId") or ""),
                    "provider": str(started.get("provider") or ""),
                    "providerLabel": str(started.get("providerLabel") or ""),
                    "model": str(started.get("model") or ""),
                    "projectRoot": str(started.get("projectRoot") or ""),
                    "plan": {"nextStep": "needs_user_action", "reason": "runtime_interrupted",
                             "taskCompletion": {"status": "interrupted"}},
                }
                if timeline:
                    continuation["timeline"] = timeline
                self.append({
                    "event": "runtime_turn_interrupted",
                    "status": "interrupted",
                    "sessionId": started.get("sessionId") or "",
                    "turnId": started.get("turnId") or "",
                    "clientTurnId": started.get("clientTurnId") or "",
                    "provider": started.get("provider") or "",
                    "providerLabel": started.get("providerLabel") or "",
                    "model": started.get("model") or "",
                    "projectRoot": started.get("projectRoot") or "",
                    "continuationEvent": continuation,
                })
                created += 1
            return created

    def read_events(
        self, *, limit: int = 400, contains: str | None = None,
        start_times: dict[tuple[str, str], str] | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        path = self.log_path
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as source:
                lines = deque(maxlen=None if int(limit) <= 0 else max(1, min(int(limit), 2000)))
                for line in source:
                    # Reuse this scan rather than rereading the whole ledger
                    # on each UI poll when starts precede the recent window.
                    if start_times is not None and '"runtime_turn_started"' in line:
                        try:
                            started = json.loads(line)
                        except json.JSONDecodeError:
                            started = None
                        if (isinstance(started, dict) and started.get("event") == "runtime_turn_started"
                                and started.get("sessionId") and started.get("turnId") and started.get("createdAt")):
                            key = (str(started["sessionId"]), str(started["turnId"]))
                            start_times[key] = str(started["createdAt"])
                    if contains is not None and contains not in line:
                        continue
                    if predicate is not None:
                        try:
                            candidate = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(candidate, dict) or not predicate(candidate):
                            continue
                    lines.append(line)
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _start_times(self) -> dict[tuple[str, str], str]:
        # Progress can push a start beyond the bounded recent-event window.
        # Read only start records and bind by the exact invocation identity.
        return {
            (str(row.get("sessionId") or ""), str(row.get("turnId") or "")): str(row["createdAt"])
            for row in self.read_events(limit=0, contains='"runtime_turn_started"')
            if row.get("event") == "runtime_turn_started" and row.get("createdAt")
            and row.get("sessionId") and row.get("turnId")
        }

    def _exact_run_events(self, session_id: str, client_turn_id: str):
        """Stream the owned ledger with exact identity filtering before paging.

        The request owns this read handle. Do not recover runs here: a read
        of a currently running turn must never change its lifecycle.
        """
        try:
            with self.log_path.open("r", encoding="utf-8") as source:
                for line in source:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # A concurrent append can leave an incomplete tail.
                    if (isinstance(event, dict) and (not session_id or event.get("sessionId") == session_id)
                            and (not client_turn_id or event.get("clientTurnId") == client_turn_id)):
                        yield event
        except FileNotFoundError:
            return
        except OSError as exc:
            raise self._ports.error_factory("Runtime timeline is temporarily unavailable.", 503) from exc

    def read_timeline(
        self, *, session_id: str, client_turn_id: str,
        after_sequence: int = 0, limit: int = 200,
    ) -> dict[str, Any]:
        """Read bounded public events from the next inclusive sequence cursor."""
        session_id, client_turn_id = session_id.strip(), client_turn_id.strip()
        if not session_id or not client_turn_id:
            raise self._ports.error_factory("sessionId and clientTurnId are required.", 400)
        cursor, page_size = max(0, int(after_sequence)), max(1, min(int(limit), 200))
        run: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        has_more = False
        last_sequence = cursor - 1
        run_fields = ("sessionId", "turnId", "clientTurnId", "status", "provider", "providerLabel",
                      "model", "projectRoot", "createdAt", "updatedAt", "startedAt", "completedAt")
        for row in self._exact_run_events(session_id, client_turn_id):
            name = row.get("event")
            previous = run or {}
            # Requests made after completion cannot reopen an execution.
            if not (previous.get("status") in _COMPLETED_RUN_STATUSES
                    and name not in {"runtime_turn_started", "runtime_turn_completed"}):
                run = {**previous, **{key: row[key] for key in run_fields if key in row}}
                if name == "runtime_turn_started":
                    run["startedAt"] = row.get("startedAt") or row.get("createdAt") or ""
                    run.pop("completedAt", None)
            if name != "runtime_turn_timeline_event":
                continue
            item = row.get("timelineEvent")
            if not isinstance(item, dict) or type(item.get("sequence")) is not int:
                continue
            sequence = item["sequence"]
            if sequence < cursor or sequence <= last_sequence:
                continue
            if len(events) >= page_size:
                has_more = True
                continue
            # Reuse the public continuation projector's timeline allowlist;
            # its temporary envelope is never exposed as run/completion state.
            projected = project_runtime_turn_event({
                "continuationSource": "foreground_completed", "sessionId": session_id,
                "turnId": str(row.get("turnId") or "timeline"),
                "timeline": [_public_event(item)],
            })
            public_items = _ensure_list(_ensure_dict(projected).get("timeline"))
            if public_items:
                events.append(self._ports.redact(public_items[0]))
                last_sequence = sequence
        return {
            "ok": True, "run": self._ports.redact(_public_event(run)) if run else None,
            "events": events, "nextSequence": last_sequence + 1, "hasMore": has_more,
        }

    def read_terminal_continuation(
        self, *, session_id: str, client_turn_id: str, turn_id: str,
    ) -> dict[str, Any] | None:
        """Return only this invocation's already-persisted public terminal."""
        if not session_id or not client_turn_id or not turn_id:
            return None
        terminal = None
        for row in self._exact_run_events(session_id, client_turn_id):
            if row.get("turnId") != turn_id or row.get("event") != "runtime_turn_completed":
                continue
            continuation = _terminal_continuation(row)
            if continuation is not None:
                terminal = project_runtime_turn_event(self._ports.redact(_public_event(continuation)))
        return terminal

    def record_queue_event(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        if not client_turn_id:
            raise self._ports.error_factory("clientTurnId is required.", 400)
        event = {
            "event": "runtime_turn_queued",
            "status": "queued",
            "sessionId": str(params.get("session_id") or params.get("sessionId") or "").strip(),
            "clientTurnId": client_turn_id,
            "messageSummary": self._ports.summarize_text(str(params.get("message") or ""), 240),
            "attachmentCount": len(_ensure_list(params.get("attachments"))),
            "provider": params.get("provider") or "",
            "providerLabel": params.get("providerLabel") or params.get("provider_label") or "",
            "model": params.get("model") or "",
            "projectRoot": params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "",
        }
        self.append(event)
        return {"ok": True, "status": "queued", "event": event}

    def list_runs(
        self,
        *,
        limit: int = 50,
        session_id: str = "",
        project_root: str = "",
        client_turn_id: str = "",
    ) -> dict[str, Any]:
        start_times: dict[tuple[str, str], str] = {}
        session_id = session_id.strip()
        project_root = project_root.strip()
        client_turn_id = client_turn_id.strip()
        normalized_project_root = self._ports.normalize_path(project_root) if project_root else ""

        def project_matches(value: str) -> bool:
            if not normalized_project_root:
                return True
            candidate = str(value or "").strip()
            if not candidate:
                return True
            return self._ports.normalize_path(candidate) == normalized_project_root

        def event_approval_ids(event: dict[str, Any]) -> set[str]:
            ids = {str(event.get("approvalId") or "").strip()}
            ids.update(str(item).strip() for item in _ensure_list(event.get("approvalIds")))
            return {item for item in ids if item}

        related_approval_ids: set[str] = set()
        if session_id:
            for event in self._exact_run_events(session_id, ""):
                related_approval_ids.update(event_approval_ids(event))

        def identity_matches(event: dict[str, Any]) -> bool:
            related = bool(related_approval_ids.intersection(event_approval_ids(event)))
            return ((not session_id or event.get("sessionId") == session_id or related)
                    and (not client_turn_id or event.get("clientTurnId") == client_turn_id)
                    and project_matches(str(event.get("projectRoot") or "")))

        # A busy unrelated session must not evict this chat before filtering.
        events = self.read_events(
            limit=max(limit * 8, 100), start_times=start_times,
            predicate=identity_matches if session_id or client_turn_id or project_root else None,
        )

        runs_by_key: dict[str, dict[str, Any]] = {}
        event_count_by_key: dict[str, int] = {}
        filtered_events: list[dict[str, Any]] = []
        for event in events:
            related_by_approval = bool(related_approval_ids.intersection(event_approval_ids(event)))
            if session_id and str(event.get("sessionId") or "") != session_id and not related_by_approval:
                continue
            if client_turn_id and str(event.get("clientTurnId") or "") != client_turn_id:
                continue
            if not project_matches(str(event.get("projectRoot") or "")):
                continue
            filtered_events.append(event)
            key = (
                str(event.get("clientTurnId") or "").strip()
                or str(event.get("turnId") or "").strip()
                or f"event:{event.get('id') or len(filtered_events)}"
            )
            event_count_by_key[key] = event_count_by_key.get(key, 0) + 1
            previous = runs_by_key.get(key, {})
            merged = {**previous, **event}
            if event.get("event") == "runtime_turn_cancel_requested":
                # A cancellation request can arrive after execution has finished.
                # Keep that request in events without reopening the completed run.
                if previous.get("event") == "runtime_turn_completed":
                    merged = dict(previous)
                else:
                    for identity_key in ("sessionId", "turnId"):
                        if not event.get(identity_key) and previous.get(identity_key):
                            merged[identity_key] = previous[identity_key]
            merged["eventCount"] = event_count_by_key[key]
            merged["lastEvent"] = event.get("event") or ""
            runs_by_key[key] = merged
        runs = sorted(
            runs_by_key.values(),
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or item.get("timestamp") or ""),
            reverse=True,
        )[: max(1, min(limit, 200))]
        for run in runs:
            started_at = start_times.get((str(run.get("sessionId") or ""), str(run.get("turnId") or "")))
            if started_at:
                run["startedAt"] = started_at
        return {
            "ok": True,
            "schema": "vrcforge.runtime_runs.v1",
            "runs": [self._ports.redact(_public_event(item)) for item in runs],
            "events": [
                self._ports.redact(_public_event(item))
                for item in filtered_events[-max(1, min(limit, 200)) :]
            ],
            "count": len(runs),
        }

    def list_runtime_continuations(self, *, limit: int = 64) -> list[dict[str, Any]]:
        """Return bounded durable chat projections for reconnect replay."""

        self.recover_interrupted_runs()
        # Count durable continuations, not the intervening live progress rows.
        start_times: dict[tuple[str, str], str] = {}
        events = self.read_events(limit=max(limit * 8, 100), contains='"continuationEvent"', start_times=start_times)
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for event in events:
            continuation = _terminal_continuation(event)
            if not isinstance(continuation, dict):
                continue
            session_id = str(continuation.get("sessionId") or "").strip()
            turn_id = str(continuation.get("turnId") or "").strip()
            client_turn_id = str(continuation.get("clientTurnId") or "").strip()
            if not session_id or not turn_id:
                continue
            started_at = start_times.get((session_id, turn_id))
            if started_at:
                continuation["startedAt"] = started_at
            key = f"{session_id}:{client_turn_id or turn_id}"
            if key not in by_key:
                order.append(key)
            elif (continuation.get("continuationSource") == "foreground_interrupted"
                  and by_key[key].get("continuationSource") != "foreground_interrupted"):
                continue
            # Older receipt rows can replace a terminal projection without
            # carrying its timestamp. Retain that same turn's terminal fact;
            # issuing a receipt or reconnecting must never move the clock.
            previous = by_key.get(key, {})
            if (event.get("event") == "runtime_turn_journey_receipt_issued"
                    and not continuation.get("completedAt")
                    and previous.get("turnId") == turn_id
                    and previous.get("completedAt")):
                continuation["completedAt"] = previous["completedAt"]
            by_key[key] = self._ports.redact(public_runtime_payload(continuation))
        selected = order[-max(1, min(int(limit), 200)) :]
        return [by_key[key] for key in selected]

    def shell_continuation_states(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return the latest private dispatch state for each Shell session."""

        latest: dict[str, dict[str, Any]] = {}
        for event in self.read_events(limit=0):
            state = str(event.get("shellContinuationState") or "").strip()
            session_id = str(event.get("shellSessionId") or "").strip()
            if state in {"pending", "dispatching", "delivered", "interrupted"} and session_id:
                latest[session_id] = event
        values = list(latest.values())
        if int(limit) <= 0:
            return values
        return values[-max(1, min(int(limit), 200)) :]

    def stage_shell_continuation(
        self,
        *,
        shell_session_id: str,
        task_seed: dict[str, Any],
        terminal_event: dict[str, Any],
    ) -> bool:
        """Durably stage one stable Shell terminal identity before dispatch."""

        session_id = str(shell_session_id or "").strip()[:100]
        if not session_id or not isinstance(task_seed, dict) or not isinstance(terminal_event, dict):
            return False
        terminal_session_id = str(terminal_event.get("shellSessionId") or "").strip()
        if terminal_session_id and terminal_session_id != session_id:
            return False
        with self._ports.shared_state_lock:
            if self._latest_shell_continuation_locked(session_id) is not None:
                return False
            self.append(
                {
                    "event": "runtime_shell_continuation_pending",
                    "status": "pending",
                    "shellContinuationState": "pending",
                    "shellSessionId": session_id,
                    "sessionId": str(terminal_event.get("runtimeSessionId") or "")[:180],
                    "turnId": str(terminal_event.get("turnId") or "")[:180],
                    "clientTurnId": str(terminal_event.get("clientTurnId") or "")[:240],
                    "continuationTaskSeed": dict(task_seed),
                    "continuationTerminalEvent": dict(terminal_event),
                }
            )
        return True

    def claim_shell_continuation(self, shell_session_id: str) -> dict[str, Any] | None:
        """CAS one pending continuation to fsynced dispatching state."""

        session_id = str(shell_session_id or "").strip()[:100]
        if not session_id:
            return None
        with self._ports.shared_state_lock:
            current = self._latest_shell_continuation_locked(session_id)
            if current is None or current.get("shellContinuationState") != "pending":
                return None
            task_seed = current.get("continuationTaskSeed")
            terminal_event = current.get("continuationTerminalEvent")
            if not isinstance(task_seed, dict) or not isinstance(terminal_event, dict):
                self._append_shell_continuation_state_locked(
                    current,
                    "interrupted",
                    reason="durable_continuation_payload_missing",
                )
                return None
            self._append_shell_continuation_state_locked(current, "dispatching")
            return {
                "shellSessionId": session_id,
                "taskSeed": dict(task_seed),
                "terminalEvent": dict(terminal_event),
            }

    def deliver_shell_continuation(self, shell_session_id: str) -> bool:
        """Mark a claimed continuation delivered after its callback returns."""

        return self._finish_shell_continuation(shell_session_id, "delivered")

    def interrupt_shell_continuation(self, shell_session_id: str, *, reason: str) -> bool:
        """Fail closed after a dispatch exception or abandoned process owner."""

        return self._finish_shell_continuation(shell_session_id, "interrupted", reason=reason)

    def _finish_shell_continuation(
        self,
        shell_session_id: str,
        state: str,
        *,
        reason: str = "",
    ) -> bool:
        session_id = str(shell_session_id or "").strip()[:100]
        if not session_id or state not in {"delivered", "interrupted"}:
            return False
        with self._ports.shared_state_lock:
            current = self._latest_shell_continuation_locked(session_id)
            if current is None or current.get("shellContinuationState") != "dispatching":
                return False
            self._append_shell_continuation_state_locked(current, state, reason=reason)
        return True

    def _latest_shell_continuation_locked(self, shell_session_id: str) -> dict[str, Any] | None:
        for event in reversed(self.read_events(limit=0)):
            if (
                str(event.get("shellSessionId") or "").strip() == shell_session_id
                and str(event.get("shellContinuationState") or "").strip()
                in {"pending", "dispatching", "delivered", "interrupted"}
            ):
                return event
        return None

    def _append_shell_continuation_state_locked(
        self,
        current: dict[str, Any],
        state: str,
        *,
        reason: str = "",
    ) -> None:
        self.append(
            {
                "event": f"runtime_shell_continuation_{state}",
                "status": state,
                "shellContinuationState": state,
                "shellSessionId": str(current.get("shellSessionId") or "")[:100],
                "sessionId": str(current.get("sessionId") or "")[:180],
                "turnId": str(current.get("turnId") or "")[:180],
                "clientTurnId": str(current.get("clientTurnId") or "")[:240],
                **(
                    {"continuationTaskSeed": dict(current["continuationTaskSeed"])}
                    if isinstance(current.get("continuationTaskSeed"), dict)
                    else {}
                ),
                **(
                    {"continuationTerminalEvent": dict(current["continuationTerminalEvent"])}
                    if isinstance(current.get("continuationTerminalEvent"), dict)
                    else {}
                ),
                **(
                    {"continuationInterruptedReason": self._ports.summarize_text(reason, 240)}
                    if reason
                    else {}
                ),
            }
        )

    def build_run_from_turn(
        self,
        *,
        event: str,
        status: str,
        agent_name: str,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
        message: str,
        attachments: list[dict[str, Any]],
        params: dict[str, Any],
        top_plan: dict[str, Any],
        steps: list[dict[str, Any]],
        shell_payload: dict[str, Any] | None,
        skill_payload: dict[str, Any] | None,
        write_payload: dict[str, Any] | None,
        approval_id: str,
        continuation_event: dict[str, Any] | None = None,
        context_usage: dict[str, Any] | None = None,
        usage_reconciliation: dict[str, Any] | None = None,
        context_compaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        approval_ids = []
        if approval_id:
            approval_ids.append(approval_id)
        for payload in (shell_payload, skill_payload, write_payload):
            record = _ensure_dict(payload)
            extracted = str(record.get("approval_id") or record.get("approvalId") or "").strip()
            if extracted and extracted not in approval_ids:
                approval_ids.append(extracted)
            nested = _ensure_dict(record.get("approval"))
            nested_id = str(nested.get("id") or "").strip()
            if nested_id and nested_id not in approval_ids:
                approval_ids.append(nested_id)
        record = {
            "event": event,
            "status": status,
            "agent": agent_name,
            "sessionId": session_id,
            "turnId": turn_id,
            "clientTurnId": client_turn_id,
            "goalDeliveryId": str(params.get("goalDeliveryId") or params.get("goal_delivery_id") or ""),
            "messageSummary": self._ports.summarize_text(message, 240),
            "attachmentCount": len(attachments),
            "provider": params.get("provider") or "",
            "providerLabel": params.get("providerLabel") or params.get("provider_label") or "",
            "model": params.get("model") or "",
            "projectRoot": params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "",
            "computerUseRequested": bool(params.get("_computerUseRequested")),
            "computerUseVisualTheme": str(params.get("_computerUseVisualTheme") or "light"),
            "computerUseVisualAccent": self.normalize_visual_accent(params.get("_computerUseVisualAccent")),
            "planSummary": self._ports.summarize_text(str(top_plan.get("summary") or top_plan.get("reply") or ""), 240),
            "planner": top_plan.get("planner") or "",
            "nextStep": top_plan.get("nextStep") or "",
            "stepCount": len(steps),
            "steps": steps,
            "approvalIds": approval_ids,
            "shellStatus": shell_payload.get("status") if shell_payload else "none",
            "skillStatus": skill_payload.get("status") if skill_payload else "none",
            "skillTool": skill_payload.get("tool") if skill_payload else "",
            "writeStatus": write_payload.get("status") if write_payload else "none",
            "writeTool": write_payload.get("tool") if write_payload else "",
        }
        if context_usage:
            record["contextUsage"] = context_usage
        if usage_reconciliation:
            record["usageReconciliation"] = usage_reconciliation
        if context_compaction:
            record["contextCompaction"] = context_compaction
        if continuation_event:
            record["continuationEvent"] = continuation_event
        return record

    @staticmethod
    def turn_run_status(
        *,
        top_plan: dict[str, Any],
        shell_payload: dict[str, Any] | None,
        skill_payload: dict[str, Any] | None,
        write_payload: dict[str, Any] | None,
        approval_id: str,
    ) -> str:
        plan_outcome, _plan_label = classify_runtime_plan_outcome(top_plan)
        if plan_outcome == "cancelled":
            return "cancelled"
        # A live ask-user boundary is the current turn terminal even when an
        # earlier diagnostic/write step in the same turn failed.  Keep this
        # narrow: only the explicit question_pending plan reason and a real
        # questionId from either user-question tool qualifies; generic needs_user_action
        # receipts (scope denial, failed verification, etc.) remain terminal
        # failures/blocked outcomes below.
        question_boundary = (
            str(top_plan.get("nextStep") or "").strip() == "needs_user_action"
            and str(_ensure_dict(top_plan.get("completionGate")).get("reason") or "").strip()
            in {"question_pending", "visual_checkpoint_pending"}
        )
        question_payload = _ensure_dict(skill_payload)
        question_id = ""
        if question_boundary and question_payload.get("tool") in {"vrcforge_ask_user", "vrcforge_call_user"}:
            for candidate in (
                _ensure_dict(question_payload.get("result")).get("question"),
                question_payload.get("outcome"),
            ):
                if isinstance(candidate, dict):
                    question_id = str(candidate.get("questionId") or "").strip()
                    if question_id:
                        break
        if (
            question_id
            and question_payload.get("ok") is True
            and str(question_payload.get("status") or "").strip() == "needs_user_action"
        ):
            return "blocked"
        payloads = [
            _ensure_dict(payload)
            for payload in (shell_payload, skill_payload, write_payload)
            if isinstance(payload, dict)
        ]
        statuses = {
            str(payload.get("status") or "").strip().lower().replace("-", "_")
            for payload in payloads
        }
        failure_classes = {classify_runtime_step_failure(payload) for payload in payloads}
        if "permission_denied" in failure_classes:
            return "denied"
        if statuses & {"denied", "rejected", "permission_denied"}:
            return "denied"
        if statuses & {"failed", "failure", "error", "unavailable", "timeout", "timed_out"}:
            return "failed"
        if any(payload.get("ok") is False for payload in payloads):
            return "failed"
        blocked_statuses = {
            "blocked",
            "pending",
            "pending_approval",
            "approval_required",
            "needs_input",
            "waiting_for_approval",
            "waiting_for_answer",
            "needs_user_action",
        }
        if statuses & blocked_statuses:
            return "blocked"
        if approval_id and not statuses & {"applied", "executed", "completed", "success"}:
            return "blocked"
        if plan_outcome == "failed":
            return "failed"
        if plan_outcome == "parked":
            return "blocked"
        return "completed"
