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
        for line in lines[-max(1, min(limit, 2000)) :]:
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
            "runs": [self._ports.redact(item) for item in runs],
            "events": [self._ports.redact(item) for item in filtered_events[-max(1, min(limit, 200)) :]],
            "count": len(runs),
        }

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
