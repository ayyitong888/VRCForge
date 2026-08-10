from __future__ import annotations

import json
import secrets
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from background_goal_runtime import classify_runtime_plan_outcome, classify_runtime_step_failure


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
    projected.pop("continuationTaskSeed", None)
    projected.pop("continuationTerminalEvent", None)
    if projected.get("shellContinuationState"):
        projected.pop("taskSeed", None)
        projected.pop("result", None)
        projected.pop("stdout", None)
        projected.pop("stderr", None)
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

    __slots__ = ("_ports",)

    def __init__(self, ports: AgentRuntimeRunLedgerPorts) -> None:
        self._ports = ports

    @property
    def log_path(self) -> Path:
        return self._ports.log_path()

    @property
    def shared_state_lock(self) -> AbstractContextManager[Any]:
        return self._ports.shared_state_lock

    def normalize_visual_accent(self, value: Any) -> str:
        return self._ports.normalize_visual_accent(value)

    def append(self, entry: dict[str, Any]) -> None:
        safe_entry = self._ports.redact(
            {
                "schema": "vrcforge.runtime_run.v1",
                "id": f"runevt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": self._ports.now(),
                "updatedAt": self._ports.now(),
                **entry,
            }
        )
        path = self.log_path
        with self._ports.shared_state_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._ports.ensure_append_boundary(path)
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")
                self._ports.flush_and_fsync(log_file)

    def read_events(self, *, limit: int = 400) -> list[dict[str, Any]]:
        path = self.log_path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        selected_lines = lines if int(limit) <= 0 else lines[-max(1, min(int(limit), 2000)) :]
        for line in selected_lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

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
        events = self.read_events(limit=max(limit * 8, 100))
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
            for event in events:
                if str(event.get("sessionId") or "") == session_id:
                    related_approval_ids.update(event_approval_ids(event))

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
            merged["eventCount"] = event_count_by_key[key]
            merged["lastEvent"] = event.get("event") or ""
            runs_by_key[key] = merged
        runs = sorted(
            runs_by_key.values(),
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or item.get("timestamp") or ""),
            reverse=True,
        )[: max(1, min(limit, 200))]
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

        events = self.read_events(limit=max(limit * 8, 100))
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for event in events:
            continuation = event.get("continuationEvent")
            if not isinstance(continuation, dict):
                continue
            session_id = str(continuation.get("sessionId") or "").strip()
            turn_id = str(continuation.get("turnId") or "").strip()
            client_turn_id = str(continuation.get("clientTurnId") or "").strip()
            if not session_id or not turn_id:
                continue
            key = f"{session_id}:{client_turn_id or turn_id}"
            if key not in by_key:
                order.append(key)
            by_key[key] = self._ports.redact(continuation)
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
