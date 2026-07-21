from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from background_goal_runtime import (
    PHASE_TIMEOUT_SECONDS,
    PROVIDER_PREFLIGHT_CACHE_SECONDS,
    QUESTION_REMINDER_SECONDS as RUNTIME_QUESTION_REMINDER_SECONDS,
    TOTAL_PROVIDER_ATTEMPTS,
    deterministic_wake_stagger_seconds,
    retry_backoff_seconds,
)


GOAL_TERMINAL_STATUSES = {"completed", "cancelled"}
DELIVERY_TERMINAL_STATUSES = {"completed", "materialized", "denied", "blocked", "parked", "answered"}
WAKE_MIN_INTERVAL_MINUTES = 5
WAKE_MAX_INTERVAL_MINUTES = 10_080
DELIVERY_MAX_ATTEMPTS = TOTAL_PROVIDER_ATTEMPTS
QUESTION_REMINDER_SECONDS = RUNTIME_QUESTION_REMINDER_SECONDS
PROVIDER_REARM_SECONDS = PROVIDER_PREFLIGHT_CACHE_SECONDS
GOAL_DELIVERY_RESULT_SCHEMA = "vrcforge.agent_goal_delivery_result.v1"
GOAL_DELIVERY_RUN_SCHEMA = "vrcforge.agent_goal_run.v1"
DELIVERY_PHASES = {"wake", "project_lock", "provider_call", "apply", "deliver"}
DELIVERY_PHASE_TIMEOUT_SECONDS = dict(PHASE_TIMEOUT_SECONDS)
NON_RETRYABLE_FAILURE_CLASSES = {
    "auth_credit",
    "schema_privacy",
    "permission_denied",
    "approval_recovery_required",
}
_PROCESS_INSTANCE_ID = f"goal-runner-{os.getpid()}-{secrets.token_hex(8)}"


class AgentGoalStoreError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _summarize(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: max(0, limit - 3)]}..."


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _delivery_is_terminal(delivery: dict[str, Any]) -> bool:
    return bool(delivery.get("terminal")) or str(delivery.get("status") or "") in DELIVERY_TERMINAL_STATUSES


def _bounded_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return _summarize(value, 240)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _summarize(value, 2_000)
    if isinstance(value, int):
        return max(-10**15, min(value, 10**15))
    if isinstance(value, float):
        if not math.isfinite(value):
            return 0
        return max(-10**15, min(value, 10**15))
    if isinstance(value, dict):
        rows: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            rows[_summarize(key, 120)] = _bounded_json_value(item, depth=depth + 1)
        return rows
    if isinstance(value, (list, tuple)):
        return [_bounded_json_value(item, depth=depth + 1) for item in list(value)[:16]]
    return _summarize(value, 240)


def _bounded_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    bounded: dict[str, Any] = {}
    allowed_strings = {
        "schema",
        "source",
        "provider",
        "providerLabel",
        "model",
        "unavailableReason",
        "costUnavailableReason",
        "currency",
    }
    for key, raw in list(value.items())[:40]:
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(raw, bool):
            bounded[name] = raw
        elif isinstance(raw, int) and not isinstance(raw, bool):
            bounded[name] = max(0, min(raw, 10**15))
        elif isinstance(raw, float) and math.isfinite(raw):
            bounded[name] = max(0.0, min(raw, 10**15))
        elif name in allowed_strings:
            bounded[name] = _summarize(raw, 160)
    return bounded


def _merge_usage(base: Any, delta: Any) -> dict[str, Any]:
    current = _bounded_usage(base)
    incoming = _bounded_usage(delta)
    if not incoming:
        return current
    result = dict(current)
    for key, value in incoming.items():
        if isinstance(value, bool):
            result[key] = bool(result.get(key, True)) and value if key == "exact" else value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if key.lower().startswith("peak") or key.lower().startswith("max"):
                result[key] = max(float(result.get(key) or 0), value)
                if isinstance(value, int) and float(result[key]).is_integer():
                    result[key] = int(result[key])
            else:
                result[key] = (result.get(key) or 0) + value
        else:
            result[key] = value
    return result


def _normalize_failure_class(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace("/", "_")
    aliases = {
        "auth": "auth_credit",
        "credit": "auth_credit",
        "quota": "auth_credit",
        "billing": "auth_credit",
        "schema": "schema_privacy",
        "privacy": "schema_privacy",
        "schema_error": "schema_privacy",
        "privacy_error": "schema_privacy",
        "permission": "permission_denied",
        "denied": "permission_denied",
        "approval_denied": "permission_denied",
    }
    return aliases.get(text, text or "transient")


class AgentGoalStore:
    """Durable goal schedule and delivery projection.

    Goal schedule state and delivery execution state deliberately share one
    append-only log, while full runtime responses live in atomic sidecars. A
    wake only creates/claims a delivery. The schedule is advanced after the
    result sidecar and completion event are durable.
    """

    def __init__(
        self,
        *,
        log_path: Callable[[], Path],
        result_dir: Callable[[], Path],
        append_event: Callable[[Path, str, dict[str, Any]], dict[str, Any]],
        read_events: Callable[[Path], list[dict[str, Any]]],
        lock: threading.RLock,
        normalize_path: Callable[[str], str],
        runner_instance_id: str | None = None,
        run_dir: Callable[[], Path] | None = None,
    ) -> None:
        self._log_path = log_path
        self._result_dir = result_dir
        self._append_event_callback = append_event
        self._read_events_callback = read_events
        self._lock = lock
        self._normalize_path = normalize_path
        self._runner_instance_id = str(runner_instance_id or _PROCESS_INSTANCE_ID)
        self._run_dir = run_dir or (lambda: self._result_dir().parent / "agent-goal-runs")

    def _events(self) -> list[dict[str, Any]]:
        return self._read_events_callback(self._log_path())

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        row = self._append_event_callback(self._log_path(), "vrcforge.agent_goal.v2", event)
        delivery_id = str(event.get("deliveryId") or "").strip()
        if delivery_id and str(event.get("event") or "").startswith("goal_delivery_"):
            self._write_run_projection(delivery_id)
        return row

    def _run_path(self, delivery_id: str) -> Path:
        safe_id = "".join(character for character in str(delivery_id or "") if character.isalnum() or character in "-_")
        if not safe_id or safe_id != delivery_id:
            raise AgentGoalStoreError("Invalid goal delivery id.")
        return self._run_dir() / f"{safe_id}.json"

    def _write_run_projection(self, delivery_id: str) -> None:
        delivery = self.project_deliveries().get(delivery_id)
        if delivery is None:
            return
        allowed = (
            "goalId",
            "deliveryId",
            "chatId",
            "sessionId",
            "scheduledFor",
            "eligibleAt",
            "clientTurnId",
            "status",
            "state",
            "terminal",
            "attempt",
            "maxAttempts",
            "consumeRetry",
            "failureClass",
            "failureLabel",
            "retryable",
            "willRetry",
            "retryAt",
            "error",
            "phase",
            "phaseStartedAt",
            "phaseDeadlineAt",
            "deadlineAt",
            "runnerInstanceId",
            "provider",
            "providerLabel",
            "providerWarningKey",
            "model",
            "drainPending",
            "drainingAt",
            "blockedKind",
            "blockedReason",
            "approvalId",
            "approvalReference",
            "questionId",
            "questionReminderAt",
            "questionReminderSentAt",
            "questionAnsweredAt",
            "continuationScheduleAdvanced",
            "approvalPendingResolution",
            "noticeUnread",
            "noticeAcknowledgedAt",
            "recapRevision",
            "recapSeenAt",
            "recapSeenRevision",
            "toastRevision",
            "toastSentAt",
            "toastSentRevision",
            "usage",
            "resultAvailable",
            "scheduleAdvanced",
            "nextWakeAt",
            "skippedAt",
            "completedAt",
            "failedAt",
            "deniedAt",
            "blockedAt",
            "parkedAt",
            "createdAt",
            "updatedAt",
            "revision",
        )
        payload = {
            "schema": GOAL_DELIVERY_RUN_SCHEMA,
            **{key: _bounded_json_value(delivery.get(key)) for key in allowed if key in delivery},
        }
        path = self._run_path(delivery_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def read_run(self, delivery_id: str) -> dict[str, Any] | None:
        path = self._run_path(str(delivery_id or "").strip())
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != GOAL_DELIVERY_RUN_SCHEMA:
            return None
        if str(payload.get("deliveryId") or "") != str(delivery_id or ""):
            return None
        return payload

    def parse_wake_fields(self, params: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "wakeEveryMinutes" in params or "wake_every_minutes" in params:
            raw_interval = params.get("wakeEveryMinutes", params.get("wake_every_minutes"))
            if raw_interval in (None, "", 0, "0"):
                fields["wakeEveryMinutes"] = 0
            else:
                try:
                    interval = int(raw_interval)
                except (TypeError, ValueError) as exc:
                    raise AgentGoalStoreError("wakeEveryMinutes must be an integer number of minutes.") from exc
                if not (WAKE_MIN_INTERVAL_MINUTES <= interval <= WAKE_MAX_INTERVAL_MINUTES):
                    raise AgentGoalStoreError(
                        f"wakeEveryMinutes must be between {WAKE_MIN_INTERVAL_MINUTES} and {WAKE_MAX_INTERVAL_MINUTES}."
                    )
                fields["wakeEveryMinutes"] = interval
        if "wakeAt" in params or "wake_at" in params:
            raw_wake_at = params.get("wakeAt", params.get("wake_at"))
            if raw_wake_at in (None, ""):
                fields["wakeAt"] = ""
            else:
                parsed = _parse_timestamp(raw_wake_at)
                if parsed is None:
                    raise AgentGoalStoreError("wakeAt must be an ISO-8601 timestamp.")
                fields["wakeAt"] = _iso(parsed)
        elif fields.get("wakeEveryMinutes"):
            fields["wakeAt"] = _iso(now + timedelta(minutes=fields["wakeEveryMinutes"]))
        return fields

    def project_goals(self) -> dict[str, dict[str, Any]]:
        goals: dict[str, dict[str, Any]] = {}
        for event in self._events():
            goal_id = str(event.get("goalId") or "").strip()
            if not goal_id:
                continue
            event_name = str(event.get("event") or "")
            previous = goals.get(goal_id, {})
            if event_name.startswith("goal_delivery_"):
                if not previous:
                    continue
                projected = dict(previous)
                usage_delta = event.get("usageDelta")
                if not isinstance(usage_delta, dict) and event_name == "goal_delivery_completed":
                    usage_delta = event.get("usage")
                if isinstance(usage_delta, dict) and usage_delta:
                    projected["usageTotals"] = _merge_usage(projected.get("usageTotals"), usage_delta)
                advances_schedule = bool(event.get("scheduleAdvanced")) or (
                    event_name == "goal_delivery_completed" and "scheduleAdvanced" not in event
                )
                if advances_schedule:
                    terminal_at = str(
                        event.get("completedAt")
                        or event.get("failedAt")
                        or event.get("deniedAt")
                        or event.get("blockedAt")
                        or event.get("updatedAt")
                        or ""
                    )
                    projected.update(
                        {
                            "wakeAt": str(event.get("nextWakeAt") or ""),
                            "lastWokenAt": terminal_at,
                            "wakeCount": int(previous.get("wakeCount") or 0) + 1,
                            "revision": int(previous.get("revision") or 1) + 1,
                            "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
                        }
                    )
                elif isinstance(usage_delta, dict) and usage_delta:
                    projected["updatedAt"] = event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt")
                goals[goal_id] = projected
                continue
            merged = {
                **previous,
                **event,
                "id": goal_id,
                "goalId": goal_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
                "revision": int(event.get("revision") or (int(previous.get("revision") or 0) + 1)),
            }
            # Ownership is immutable after creation/binding. Legacy update and
            # wake events must not be allowed to silently retarget a goal.
            if previous and event_name not in {"goal_owner_bound"}:
                for key in ("chatId", "sessionId", "projectRoot"):
                    merged[key] = previous.get(key, "")
            goals[goal_id] = merged
        for goal in goals.values():
            scheduled = bool(str(goal.get("wakeAt") or "").strip() or int(goal.get("wakeEveryMinutes") or 0))
            if scheduled and not str(goal.get("chatId") or "").strip():
                goal["blockedReason"] = "owner_missing"
            elif goal.get("blockedReason") == "owner_missing":
                goal.pop("blockedReason", None)
            goal["eligibleAt"] = self._eligible_at(goal)
            goal.setdefault("usageTotals", {})
        return goals

    def project_deliveries(self) -> dict[str, dict[str, Any]]:
        deliveries: dict[str, dict[str, Any]] = {}
        for event in self._events():
            delivery_id = str(event.get("deliveryId") or "").strip()
            if not delivery_id or not str(event.get("event") or "").startswith("goal_delivery_"):
                continue
            previous = deliveries.get(delivery_id, {})
            merged = {
                **previous,
                **event,
                "id": delivery_id,
                "deliveryId": delivery_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
                "revision": int(event.get("revision") or (int(previous.get("revision") or 0) + 1)),
            }
            if previous:
                for key in (
                    "goalId",
                    "chatId",
                    "sessionId",
                    "projectRoot",
                    "scheduledFor",
                    "resumePrompt",
                    "clientTurnId",
                    "userItemId",
                    "agentItemId",
                ):
                    merged[key] = previous.get(key, merged.get(key, ""))
            deliveries[delivery_id] = merged
        for delivery in deliveries.values():
            delivery["attempt"] = max(1, int(delivery.get("attempt") or 1))
            delivery["maxAttempts"] = max(1, int(delivery.get("maxAttempts") or DELIVERY_MAX_ATTEMPTS))
            delivery["terminal"] = _delivery_is_terminal(delivery)
            delivery.setdefault("noticeUnread", False)
            delivery.setdefault("usage", {})
            continuation_prompt = str(delivery.get("continuationPrompt") or "").strip()
            if continuation_prompt:
                delivery["resumePrompt"] = continuation_prompt
        return deliveries

    def project_provider_warnings(self, *, include_acknowledged: bool = False) -> list[dict[str, Any]]:
        warnings: dict[str, dict[str, Any]] = {}
        for event in self._events():
            event_name = str(event.get("event") or "")
            warning_key = str(event.get("providerWarningKey") or "").strip()
            if not warning_key:
                continue
            if event_name == "goal_delivery_skipped" and str(event.get("failureClass") or "") == "provider_unreachable":
                previous = warnings.get(warning_key, {})
                count = int(previous.get("count") or 0) + 1
                timestamp = str(event.get("updatedAt") or event.get("createdAt") or event.get("skippedAt") or "")
                warnings[warning_key] = {
                    **previous,
                    "warningKey": warning_key,
                    "status": "provider_unreachable",
                    "provider": _summarize(event.get("provider"), 120),
                    "count": count,
                    "revision": count,
                    "firstSeenAt": previous.get("firstSeenAt") or timestamp,
                    "lastSeenAt": timestamp,
                    "acknowledgedRevision": int(previous.get("acknowledgedRevision") or 0),
                }
            elif event_name == "goal_provider_warning_acknowledged":
                previous = warnings.get(warning_key)
                if previous is None:
                    continue
                acknowledged_revision = max(
                    int(previous.get("acknowledgedRevision") or 0),
                    int(event.get("acknowledgedRevision") or 0),
                )
                previous["acknowledgedRevision"] = acknowledged_revision
                previous["acknowledgedAt"] = str(
                    event.get("acknowledgedAt") or event.get("updatedAt") or event.get("createdAt") or ""
                )
        rows = [
            warning
            for warning in warnings.values()
            if include_acknowledged
            or int(warning.get("revision") or 0) > int(warning.get("acknowledgedRevision") or 0)
        ]
        rows.sort(key=lambda item: str(item.get("lastSeenAt") or ""), reverse=True)
        return rows

    def _append_goal_schedule_projection(
        self,
        delivery: dict[str, Any],
        *,
        event: str,
        wake_at: str,
    ) -> None:
        goal_id = str(delivery.get("goalId") or "").strip()
        goal = self.project_goals().get(goal_id)
        if goal is None:
            raise AgentGoalStoreError("Goal for delivery was not found.", 404)
        self._append(
            {
                "event": event,
                "goalId": goal_id,
                "wakeAt": str(wake_at or ""),
                "revision": int(goal.get("revision") or 0) + 1,
            }
        )

    def _restore_goal_after_continuation(self, delivery: dict[str, Any]) -> None:
        if not bool(delivery.get("continuationScheduleAdvanced")):
            return
        self._append_goal_schedule_projection(
            delivery,
            event="goal_continuation_closed",
            wake_at=str(delivery.get("nextWakeAt") or ""),
        )

    def create(self, params: dict[str, Any]) -> dict[str, Any]:
        title = _summarize(params.get("title") or params.get("goal"), 240)
        if not title:
            raise AgentGoalStoreError("Goal title is required.")
        now = _utc_now()
        wake_fields = self.parse_wake_fields(params, now=now)
        scheduled = bool(str(wake_fields.get("wakeAt") or "").strip() or int(wake_fields.get("wakeEveryMinutes") or 0))
        chat_id = str(params.get("chatId") or params.get("chat_id") or "").strip()
        if scheduled and not chat_id:
            raise AgentGoalStoreError("Scheduled goals require a durable owner chatId.")
        goal_id = f"goal_{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        with self._lock:
            self._append(
                {
                    "event": "goal_created",
                    "status": "active",
                    "revision": 1,
                    **wake_fields,
                    "goalId": goal_id,
                    "title": title,
                    "summary": _summarize(params.get("summary"), 1000),
                    "projectRoot": str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip(),
                    "sessionId": str(params.get("sessionId") or params.get("session_id") or "").strip(),
                    "chatId": chat_id,
                    "approvalPolicy": "uses_vrcforge_approval_checkpoint_rollback",
                    "wakeCount": 0,
                }
            )
            return self.project_goals()[goal_id]

    def update(self, goal_id: str, params: dict[str, Any]) -> dict[str, Any]:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            raise AgentGoalStoreError("goalId is required.")
        status = str(params.get("status") or "").strip().lower()
        if status not in {"active", "paused", "completed", "cancelled"}:
            raise AgentGoalStoreError("Goal status must be active, paused, completed, or cancelled.")
        with self._lock:
            current = self.project_goals().get(goal_id)
            if current is None:
                raise AgentGoalStoreError(f"Goal was not found: {goal_id}", 404)
            current_status = str(current.get("status") or "")
            if current_status in GOAL_TERMINAL_STATUSES and status != current_status:
                raise AgentGoalStoreError("A completed or cancelled goal cannot be reactivated.", 409)
            requested_revision = params.get("expectedRevision", params.get("expected_revision"))
            if requested_revision is not None and int(requested_revision) != int(current.get("revision") or 0):
                raise AgentGoalStoreError("Goal revision changed; refresh before updating.", 409)
            wake_fields = self.parse_wake_fields(params, now=_utc_now())
            scheduled = bool(str(wake_fields.get("wakeAt", current.get("wakeAt")) or "").strip() or int(wake_fields.get("wakeEveryMinutes", current.get("wakeEveryMinutes")) or 0))
            if scheduled and not str(current.get("chatId") or "").strip():
                raise AgentGoalStoreError("Legacy scheduled goal has no owner chat; bind it explicitly before resuming.", 409)
            self._append(
                {
                    "event": "goal_updated",
                    "status": status,
                    "revision": int(current.get("revision") or 0) + 1,
                    **wake_fields,
                    "goalId": goal_id,
                    "summary": _summarize(params.get("summary") or params.get("note"), 1000),
                }
            )
            return self.project_goals()[goal_id]

    def bind_owner(self, goal_id: str, params: dict[str, Any]) -> dict[str, Any]:
        chat_id = str(params.get("chatId") or params.get("chat_id") or "").strip()
        if not chat_id:
            raise AgentGoalStoreError("chatId is required to bind a legacy goal.")
        with self._lock:
            current = self.project_goals().get(goal_id)
            if current is None:
                raise AgentGoalStoreError(f"Goal was not found: {goal_id}", 404)
            if str(current.get("chatId") or "").strip():
                if str(current.get("chatId")) == chat_id:
                    return current
                raise AgentGoalStoreError("Goal owner is immutable.", 409)
            self._append(
                {
                    "event": "goal_owner_bound",
                    "goalId": goal_id,
                    "revision": int(current.get("revision") or 0) + 1,
                    "chatId": chat_id,
                    "sessionId": str(params.get("sessionId") or params.get("session_id") or current.get("sessionId") or "").strip(),
                    "projectRoot": str(params.get("projectRoot") or params.get("project_root") or current.get("projectRoot") or "").strip(),
                }
            )
            return self.project_goals()[goal_id]

    def _scope_matches(self, row: dict[str, Any], project_root: str, session_id: str) -> bool:
        if project_root:
            existing = str(row.get("projectRoot") or "")
            if existing and self._normalize_path(existing) != self._normalize_path(project_root):
                return False
        if session_id and str(row.get("sessionId") or "") not in {"", session_id}:
            return False
        return True

    def list(self, *, limit: int, project_root: str = "", session_id: str = "") -> list[dict[str, Any]]:
        rows = [row for row in self.project_goals().values() if self._scope_matches(row, project_root, session_id)]
        rows.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        return rows[: max(1, min(limit, 200))]

    def _eligible_at(self, goal: dict[str, Any]) -> str:
        wake_at = _parse_timestamp(goal.get("wakeAt"))
        if wake_at is None:
            return ""
        try:
            stagger = int(
                deterministic_wake_stagger_seconds(
                    str(goal.get("goalId") or ""),
                    str(goal.get("wakeAt") or ""),
                )
            )
        except (TypeError, ValueError, OverflowError):
            stagger = 0
        # One-minute-overdue legacy wakes must remain immediately eligible.
        stagger = max(0, min(stagger, 45))
        return _iso(wake_at + timedelta(seconds=stagger))

    def _current_delivery_allows_wake(
        self,
        goal: dict[str, Any],
        *,
        now: datetime,
        deliveries: dict[str, dict[str, Any]],
    ) -> bool:
        scheduled_for = str(goal.get("wakeAt") or "").strip()
        goal_id = str(goal.get("goalId") or "").strip()
        if not goal_id or not scheduled_for:
            return False
        delivery = deliveries.get(self._delivery_id(goal_id, scheduled_for))
        if delivery is None:
            return True
        status = str(delivery.get("status") or "").strip().lower()
        if _delivery_is_terminal(delivery):
            return False
        if status in {"failed", "interrupted", "skipped"}:
            retry_at = _parse_timestamp(delivery.get("retryAt"))
            return retry_at is None or retry_at <= now
        # A claimed/running/draining/applying delivery already owns this exact
        # scheduled occurrence. Unknown states also fail closed so one stale
        # row cannot be dispatched twice.
        return False

    def is_due(
        self,
        goal: dict[str, Any],
        *,
        now: datetime,
        deliveries: dict[str, dict[str, Any]] | None = None,
    ) -> bool:
        if str(goal.get("status") or "") != "active" or str(goal.get("blockedReason") or ""):
            return False
        eligible_at = _parse_timestamp(goal.get("eligibleAt")) or _parse_timestamp(self._eligible_at(goal))
        if eligible_at is None or eligible_at > now:
            return False
        return self._current_delivery_allows_wake(
            goal,
            now=now,
            deliveries=deliveries if deliveries is not None else self.project_deliveries(),
        )

    def list_due(self, *, limit: int, project_root: str = "", session_id: str = "", now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or _utc_now()
        deliveries = self.project_deliveries()
        rows = [
            row
            for row in self.project_goals().values()
            if self._scope_matches(row, project_root, session_id)
            and self.is_due(row, now=now, deliveries=deliveries)
        ]
        rows.sort(
            key=lambda item: (
                str(item.get("eligibleAt") or self._eligible_at(item)),
                str(item.get("goalId") or ""),
            )
        )
        return rows[: max(1, min(limit, 200))]

    @staticmethod
    def _delivery_id(goal_id: str, scheduled_for: str) -> str:
        digest = hashlib.sha256(f"{goal_id}\0{scheduled_for}".encode("utf-8")).hexdigest()[:24]
        return f"goal_delivery_{digest}"

    def _assert_owner(self, goal: dict[str, Any], params: dict[str, Any]) -> None:
        for param_keys, field, label in (
            (("chatId", "chat_id"), "chatId", "chat"),
            (("sessionId", "session_id"), "sessionId", "session"),
            (("projectRoot", "project_root"), "projectRoot", "project"),
        ):
            requested = next((str(params.get(key) or "").strip() for key in param_keys if str(params.get(key) or "").strip()), "")
            existing = str(goal.get(field) or "").strip()
            if not requested:
                continue
            if field == "projectRoot":
                matches = bool(existing) and self._normalize_path(requested) == self._normalize_path(existing)
            else:
                matches = requested == existing
            if not matches:
                raise AgentGoalStoreError(f"Goal does not belong to this {label}.", 409)

    def wake(
        self,
        goal_id: str,
        params: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        params = params or {}
        with self._lock:
            goal = self.project_goals().get(str(goal_id or "").strip())
            if goal is None:
                raise AgentGoalStoreError(f"Goal was not found: {goal_id}", 404)
            self._assert_owner(goal, params)
            now = now or _utc_now()
            # Explicit wake keeps its detailed idempotency/retry response below.
            # Ignore the existing delivery only for this schedule-time check;
            # list_due still filters owned occurrences so one row cannot starve
            # later goals.
            if not self.is_due(goal, now=now, deliveries={}):
                raise AgentGoalStoreError("Goal is not due for wake.", 409)
            scheduled_for = str(goal.get("wakeAt") or "")
            delivery_id = self._delivery_id(str(goal.get("goalId") or ""), scheduled_for)
            existing = self.project_deliveries().get(delivery_id)
            if existing:
                existing = self._recover_result_if_needed(existing)
                if _delivery_is_terminal(existing):
                    return goal, existing
                if str(existing.get("status") or "") in {"failed", "interrupted", "skipped"}:
                    retry_at = _parse_timestamp(existing.get("retryAt"))
                    if retry_at and retry_at > now:
                        raise AgentGoalStoreError("Goal delivery is waiting to retry.", 409)
                    consume_retry = bool(
                        existing.get("consumeRetry", str(existing.get("status") or "") != "skipped")
                    )
                    attempt = int(existing.get("attempt") or 1) + (1 if consume_retry else 0)
                    max_attempts = int(existing.get("maxAttempts") or DELIVERY_MAX_ATTEMPTS)
                    if attempt > max_attempts:
                        return goal, existing
                    phase_deadline = now + timedelta(seconds=DELIVERY_PHASE_TIMEOUT_SECONDS["wake"])
                    self._append(
                        {
                            "event": "goal_delivery_claimed",
                            "goalId": goal["goalId"],
                            "deliveryId": delivery_id,
                            "status": "claimed",
                            "revision": int(existing.get("revision") or 0) + 1,
                            "claimedAt": _iso(now),
                            "retryAt": "",
                            "attempt": attempt,
                            "maxAttempts": max_attempts,
                            "terminal": False,
                            "state": "claimed",
                            "consumeRetry": False,
                            "retryable": False,
                            "willRetry": False,
                            "failureClass": "",
                            "failureLabel": "",
                            "error": "",
                            "phase": "wake",
                            "phaseStartedAt": _iso(now),
                            "phaseDeadlineAt": _iso(phase_deadline),
                            "deadlineAt": _iso(phase_deadline),
                            "runnerInstanceId": self._runner_instance_id,
                            "drainPending": False,
                        }
                    )
                    existing = self.project_deliveries()[delivery_id]
                return goal, existing
            title = str(goal.get("title") or "").strip()
            summary = str(goal.get("summary") or "").strip()
            prompt = f"Resume goal: {title}" + (f"\nContext: {summary}" if summary else "")
            base = {
                "goalId": goal["goalId"],
                "deliveryId": delivery_id,
                "scheduledFor": scheduled_for,
                "chatId": str(goal.get("chatId") or ""),
                "sessionId": str(goal.get("sessionId") or ""),
                "projectRoot": str(goal.get("projectRoot") or ""),
                "resumePrompt": prompt,
                "clientTurnId": f"goal-turn-{delivery_id}",
                "userItemId": f"goal-user-{delivery_id}",
                "agentItemId": f"goal-agent-{delivery_id}",
                "eligibleAt": str(goal.get("eligibleAt") or self._eligible_at(goal)),
                "attempt": 1,
                "maxAttempts": DELIVERY_MAX_ATTEMPTS,
                "terminal": False,
                "consumeRetry": False,
                "retryable": False,
                "noticeUnread": False,
                "runnerInstanceId": self._runner_instance_id,
            }
            phase_deadline = now + timedelta(seconds=DELIVERY_PHASE_TIMEOUT_SECONDS["wake"])
            phase_fields = {
                "phase": "wake",
                "phaseStartedAt": _iso(now),
                "phaseDeadlineAt": _iso(phase_deadline),
                "deadlineAt": _iso(phase_deadline),
            }
            self._append(
                {
                    "event": "goal_delivery_pending",
                    "status": "pending",
                    "state": "pending",
                    "revision": 1,
                    **phase_fields,
                    **base,
                }
            )
            self._append(
                {
                    "event": "goal_delivery_claimed",
                    "status": "claimed",
                    "state": "claimed",
                    "revision": 2,
                    "claimedAt": _iso(now),
                    **phase_fields,
                    **base,
                }
            )
            return goal, self.project_deliveries()[delivery_id]

    def begin_delivery(self, delivery_id: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            delivery = self._recover_result_if_needed(delivery)
            if _delivery_is_terminal(delivery):
                status = str(delivery.get("status") or "")
                response = self._read_result_for_delivery(delivery)
                if status == "completed" and response is None:
                    self._mark_result_corrupt_locked(delivery)
                    raise AgentGoalStoreError(
                        "Goal delivery completed but its durable result is unavailable; it will not be rerun.",
                        409,
                    )
                if status == "completed":
                    return {"delivery": delivery, "response": response, "cached": True}
                raise AgentGoalStoreError("Goal delivery is terminal and will not be rerun.", 409)
            requested_turn = str(params.get("clientTurnId") or params.get("client_turn_id") or "").strip()
            if requested_turn and requested_turn != str(delivery.get("clientTurnId") or ""):
                raise AgentGoalStoreError("Goal delivery clientTurnId is immutable.", 409)
            if str(delivery.get("status") or "") == "running":
                raise AgentGoalStoreError("Goal delivery is already running.", 409)
            if str(delivery.get("status") or "") == "draining":
                raise AgentGoalStoreError("Goal delivery is draining a timed-out worker.", 409)
            if str(delivery.get("status") or "") not in {"pending", "claimed", "failed", "interrupted"}:
                raise AgentGoalStoreError("Goal delivery cannot be started from its current state.", 409)
            event = {
                "event": "goal_delivery_running",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery_id,
                "status": "running",
                "state": "running",
                "terminal": False,
                "revision": int(delivery.get("revision") or 0) + 1,
                "startedAt": _iso(_utc_now()),
                "clientTurnId": delivery.get("clientTurnId"),
                "provider": _summarize(params.get("provider"), 120),
                "providerLabel": _summarize(params.get("providerLabel"), 160),
                "model": _summarize(params.get("model"), 160),
                "runnerInstanceId": self._runner_instance_id,
                "drainPending": False,
            }
            self._append(event)
            return {"delivery": self.project_deliveries()[delivery_id], "response": None, "cached": False}

    def _usage_fields(self, delivery: dict[str, Any], context_usage: Any) -> dict[str, Any]:
        delta = _bounded_usage(context_usage)
        if not delta:
            return {}
        return {"usage": _merge_usage(delivery.get("usage"), delta), "usageDelta": delta}

    def mark_delivery_phase(
        self,
        delivery_id: str,
        phase: str,
        *,
        now: datetime | None = None,
        deadline: datetime | str | None = None,
    ) -> dict[str, Any]:
        normalized_phase = str(phase or "").strip().lower().replace("-", "_")
        if normalized_phase not in DELIVERY_PHASES:
            raise AgentGoalStoreError("Goal delivery phase is invalid.")
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if _delivery_is_terminal(delivery) or str(delivery.get("status") or "") == "draining":
                return delivery
            started_at = now or _utc_now()
            parsed_deadline = deadline if isinstance(deadline, datetime) else _parse_timestamp(deadline)
            if parsed_deadline is None:
                parsed_deadline = started_at + timedelta(seconds=DELIVERY_PHASE_TIMEOUT_SECONDS[normalized_phase])
            self._append(
                {
                    "event": "goal_delivery_phase_recorded",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "phase": normalized_phase,
                    "phaseStartedAt": _iso(started_at),
                    "phaseDeadlineAt": _iso(parsed_deadline),
                    "deadlineAt": _iso(parsed_deadline),
                }
            )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def record_phase(
        self,
        delivery_id: str,
        phase: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.mark_delivery_phase(delivery_id, phase, now=now)

    def mark_by_approval_phase(
        self,
        approval_id: str,
        phase: str,
        deadline: datetime | str | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_phase = str(phase or "").strip().lower().replace("-", "_")
        if normalized_phase not in DELIVERY_PHASES:
            raise AgentGoalStoreError("Goal delivery phase is invalid.")
        with self._lock:
            delivery = self._find_delivery_by_approval_locked(approval_id)
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery approval.", 404)
            if str(delivery.get("status") or "") in {"completed", "materialized", "denied", "failed", "parked"}:
                return delivery
            started_at = now or _utc_now()
            parsed_deadline = deadline if isinstance(deadline, datetime) else _parse_timestamp(deadline)
            if parsed_deadline is None:
                parsed_deadline = started_at + timedelta(seconds=DELIVERY_PHASE_TIMEOUT_SECONDS[normalized_phase])
            self._append(
                {
                    "event": "goal_delivery_approval_phase_recorded",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "status": "applying" if normalized_phase == "apply" else str(delivery.get("status") or "blocked"),
                    "state": "applying" if normalized_phase == "apply" else str(delivery.get("state") or "blocked"),
                    "terminal": False if normalized_phase == "apply" else bool(delivery.get("terminal")),
                    "approvalPendingResolution": normalized_phase == "apply",
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "phase": normalized_phase,
                    "phaseStartedAt": _iso(started_at),
                    "phaseDeadlineAt": _iso(parsed_deadline),
                    "deadlineAt": _iso(parsed_deadline),
                }
            )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def restore_approval_wait(self, approval_id: str) -> dict[str, Any]:
        """Return a failed approval transition to its durable waiting state."""

        with self._lock:
            delivery = self._find_delivery_by_approval_locked(approval_id)
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery approval.", 404)
            if str(delivery.get("status") or "") != "applying":
                return delivery
            self._append(
                {
                    "event": "goal_delivery_approval_wait_restored",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "status": "blocked",
                    "state": "blocked",
                    "terminal": True,
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "phase": "",
                    "phaseStartedAt": "",
                    "phaseDeadlineAt": "",
                    "deadlineAt": "",
                    "approvalPendingResolution": False,
                    "drainPending": False,
                }
            )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def _result_path(self, delivery_id: str) -> Path:
        safe_id = "".join(character for character in delivery_id if character.isalnum() or character in "-_")
        if not safe_id or safe_id != delivery_id:
            raise AgentGoalStoreError("Invalid goal delivery id.")
        return self._result_dir() / f"{safe_id}.json"

    def _write_result(
        self,
        delivery: dict[str, Any],
        response: dict[str, Any],
        *,
        completed_at: datetime | None = None,
    ) -> None:
        path = self._result_path(str(delivery.get("deliveryId") or ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        durable_completed_at = completed_at or _utc_now()
        payload = {
            "schema": GOAL_DELIVERY_RESULT_SCHEMA,
            "deliveryId": delivery.get("deliveryId"),
            "goalId": delivery.get("goalId"),
            "clientTurnId": delivery.get("clientTurnId"),
            "completedAt": _iso(durable_completed_at),
            "response": response,
            "responseDigest": hashlib.sha256(
                json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_result_record_for_delivery(
        self,
        delivery: dict[str, Any],
    ) -> tuple[dict[str, Any], datetime] | None:
        delivery_id = str(delivery.get("deliveryId") or "")
        path = self._result_path(delivery_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != GOAL_DELIVERY_RESULT_SCHEMA:
            return None
        for key in ("deliveryId", "goalId", "clientTurnId"):
            actual = payload.get(key)
            expected = delivery.get(key)
            if not isinstance(actual, str) or actual != str(expected or ""):
                return None
        response = payload.get("response")
        if not isinstance(response, dict):
            return None
        completed_at = _parse_timestamp(payload.get("completedAt"))
        if completed_at is None:
            return None
        expected_digest = str(payload.get("responseDigest") or "")
        actual_digest = hashlib.sha256(
            json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if not expected_digest or not secrets.compare_digest(expected_digest, actual_digest):
            return None
        return response, completed_at

    def _read_result_for_delivery(self, delivery: dict[str, Any]) -> dict[str, Any] | None:
        record = self._read_result_record_for_delivery(delivery)
        return record[0] if record is not None else None

    def _mark_result_corrupt_locked(self, delivery: dict[str, Any]) -> dict[str, Any]:
        if str(delivery.get("status") or "") != "completed":
            return delivery
        next_revision = int(delivery.get("revision") or 0) + 1
        self._append(
            {
                "event": "goal_delivery_result_corrupt",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "failed",
                "state": "failed",
                "terminal": True,
                "revision": next_revision,
                "failedAt": _iso(_utc_now()),
                "failureClass": "result_corrupt",
                "failureLabel": "result_corrupt",
                "error": "The durable background result is missing or invalid.",
                "retryable": False,
                "willRetry": False,
                "retryAt": "",
                "resultAvailable": False,
                "noticeUnread": True,
                "recapRevision": next_revision,
                "toastRevision": next_revision,
                "scheduleAdvanced": False,
            }
        )
        return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def read_result(self, delivery_id: str) -> dict[str, Any] | None:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                return None
            return self._read_result_for_delivery(delivery)

    def _next_wake_at(self, goal: dict[str, Any], completed_at: datetime) -> str:
        try:
            interval = int(goal.get("wakeEveryMinutes") or 0)
        except (TypeError, ValueError):
            interval = 0
        return _iso(completed_at + timedelta(minutes=interval)) if interval > 0 else ""

    def _schedule_fields(self, delivery: dict[str, Any], terminal_at: datetime) -> dict[str, Any]:
        goal = self.project_goals().get(str(delivery.get("goalId") or ""), {})
        return {
            "scheduleAdvanced": True,
            "nextWakeAt": self._next_wake_at(goal, terminal_at),
        }

    def _recover_result_if_needed(self, delivery: dict[str, Any]) -> dict[str, Any]:
        if _delivery_is_terminal(delivery) or str(delivery.get("status") or "") in {"draining", "applying"}:
            return delivery
        result_record = self._read_result_record_for_delivery(delivery)
        if result_record is None:
            return delivery
        response, completed_at = result_record
        usage_fields = self._usage_fields(delivery, response.get("contextUsage"))
        next_revision = int(delivery.get("revision") or 0) + 1
        self._append(
            {
                "event": "goal_delivery_completed",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "completed",
                "revision": next_revision,
                "completedAt": _iso(completed_at),
                "terminal": True,
                "state": "completed",
                "retryAt": "",
                "phaseDeadlineAt": "",
                "deadlineAt": "",
                "noticeUnread": False,
                "recapRevision": next_revision,
                "resultAvailable": True,
                "recoveredFromSidecar": True,
                **self._schedule_fields(delivery, completed_at),
                **usage_fields,
            }
        )
        return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def reconcile_stale_running_deliveries(self) -> list[dict[str, Any]]:
        """Reconcile deliveries abandoned by an earlier main-server process.

        This is deliberately explicit: helper, CLI, and stdio processes may
        construct a store over the main server's audit directory without
        owning delivery execution. Only the FastAPI startup path calls it.
        """

        with self._lock:
            return self._reconcile_stale_running_deliveries_locked()

    def _reconcile_stale_running_deliveries_locked(self) -> list[dict[str, Any]]:
        """Make deliveries abandoned by an earlier process retryable.

        A second store in the same process may share this log while the first
        request is still executing, so its matching runner token must retain
        the normal "already running" conflict. A new process receives a new
        token. For that case, recover a durable result sidecar first; only a
        sidecar-free running delivery is marked interrupted for a later retry.
        """

        reconciled: list[dict[str, Any]] = []
        for delivery in list(self.project_deliveries().values()):
            status = str(delivery.get("status") or "")
            if status not in {"pending", "claimed", "running", "draining", "applying"}:
                continue
            runner_instance_id = str(delivery.get("runnerInstanceId") or "")
            if runner_instance_id == self._runner_instance_id:
                continue
            if status in {"pending", "claimed"}:
                now = _utc_now()
                self._append(
                    {
                        "event": "goal_delivery_interrupted",
                        "goalId": delivery.get("goalId"),
                        "deliveryId": delivery.get("deliveryId"),
                        "status": "interrupted",
                        "state": "interrupted",
                        "terminal": False,
                        "revision": int(delivery.get("revision") or 0) + 1,
                        "interruptedAt": _iso(now),
                        "retryAt": _iso(now),
                        "consumeRetry": False,
                        "retryable": True,
                        "reason": "process_restart_wake_interrupted",
                        "phaseDeadlineAt": "",
                        "deadlineAt": "",
                        "runnerInstanceId": "",
                    }
                )
                reconciled.append(
                    self.project_deliveries()[str(delivery.get("deliveryId") or "")]
                )
                continue
            if status == "draining":
                reconciled.append(
                    self._finish_delivery_drain_locked(
                        delivery,
                        retryable=bool(delivery.get("retryable", True)),
                        failure_class=str(delivery.get("failureClass") or "timeout"),
                        error=str(delivery.get("error") or "worker ended during process restart"),
                        now=_utc_now(),
                    )
                )
                continue
            if status == "applying":
                draining = self._mark_delivery_draining_locked(
                    delivery,
                    "apply",
                    "process_restart_apply_interrupted",
                    "Approved action worker ended during process restart.",
                    now=_utc_now(),
                )
                reconciled.append(
                    self._finish_delivery_drain_locked(
                        draining,
                        retryable=False,
                        failure_class="apply_failed",
                        error="Approved action worker ended during process restart.",
                        now=_utc_now(),
                    )
                )
                continue
            recovered = self._recover_result_if_needed(delivery)
            if str(recovered.get("status") or "") != "running":
                reconciled.append(recovered)
                continue
            self._append(
                {
                    "event": "goal_delivery_interrupted",
                    "goalId": recovered.get("goalId"),
                    "deliveryId": recovered.get("deliveryId"),
                    "status": "interrupted",
                    "state": "interrupted",
                    "terminal": False,
                    "revision": int(recovered.get("revision") or 0) + 1,
                    "interruptedAt": _iso(_utc_now()),
                    "retryAt": "",
                    "consumeRetry": True,
                    "retryable": True,
                    "reason": "process_restart",
                }
            )
            reconciled.append(self.project_deliveries()[str(recovered.get("deliveryId") or "")])
        return reconciled

    def _complete_delivery_locked(
        self,
        delivery: dict[str, Any],
        response: dict[str, Any],
        *,
        completed_at: datetime,
        schedule_already_advanced: bool = False,
        context_usage: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise AgentGoalStoreError("Goal delivery response must be an object.")
        response_payload = dict(response)
        self._write_result(delivery, response_payload, completed_at=completed_at)
        usage_value = context_usage if isinstance(context_usage, dict) else response_payload.get("contextUsage")
        next_revision = int(delivery.get("revision") or 0) + 1
        event = {
            "event": "goal_delivery_completed",
            "goalId": delivery.get("goalId"),
            "deliveryId": delivery.get("deliveryId"),
            "status": "completed",
            "state": "completed",
            "terminal": True,
            "revision": next_revision,
            "completedAt": _iso(completed_at),
            "retryAt": "",
            "phaseDeadlineAt": "",
            "deadlineAt": "",
            "drainPending": False,
            "noticeUnread": False,
            "recapRevision": next_revision,
            "resultAvailable": True,
            "scheduleAdvanced": False if schedule_already_advanced else True,
            **self._usage_fields(delivery, usage_value),
        }
        if not schedule_already_advanced:
            event.update(self._schedule_fields(delivery, completed_at))
        self._append(event)
        completed = self.project_deliveries()[str(delivery.get("deliveryId") or "")]
        self._restore_goal_after_continuation(completed)
        return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def complete_delivery(
        self,
        delivery_id: str,
        response: dict[str, Any],
        *,
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            delivery = self._recover_result_if_needed(delivery)
            if _delivery_is_terminal(delivery) or str(delivery.get("status") or "") in {"draining", "applying"}:
                return delivery
            if str(delivery.get("status") or "") != "running":
                raise AgentGoalStoreError("Goal delivery is not running.", 409)
            return self._complete_delivery_locked(
                delivery,
                response,
                completed_at=now or _utc_now(),
                schedule_already_advanced=bool(
                    delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
                ),
                context_usage=context_usage,
            )

    def _failure_details(
        self,
        error: Any,
        *,
        classification: dict[str, Any] | None,
        failure_class: str,
        failure_label: str,
        retryable: bool | None,
    ) -> tuple[str, str, str, bool]:
        details = dict(classification or {})
        if isinstance(error, dict):
            details = {**error, **details}
        error_text = _summarize(
            details.get("message") or details.get("error") or ("" if isinstance(error, dict) else error),
            1_000,
        )
        normalized_class = _normalize_failure_class(
            failure_class
            or details.get("failureClass")
            or details.get("failure_class")
            or details.get("kind")
            or details.get("class")
        )
        normalized_label = _summarize(
            failure_label or details.get("failureLabel") or details.get("failure_label") or normalized_class,
            160,
        )
        if retryable is None:
            raw_retryable = details.get("retryable")
            retryable_value = bool(raw_retryable) if raw_retryable is not None else normalized_class not in NON_RETRYABLE_FAILURE_CLASSES
        else:
            retryable_value = bool(retryable)
        if normalized_class in NON_RETRYABLE_FAILURE_CLASSES:
            retryable_value = False
        return error_text, normalized_class, normalized_label, retryable_value

    def _fail_delivery_locked(
        self,
        delivery: dict[str, Any],
        error: Any,
        *,
        classification: dict[str, Any] | None = None,
        failure_class: str = "",
        failure_label: str = "",
        retryable: bool | None = None,
        now: datetime,
        context_usage: Any = None,
        schedule_already_advanced: bool = False,
        allow_terminal_transition: bool = False,
    ) -> dict[str, Any]:
        if _delivery_is_terminal(delivery) and not allow_terminal_transition:
            return delivery
        if str(delivery.get("status") or "") == "failed" and not bool(delivery.get("terminal")):
            return delivery
        error_text, normalized_class, normalized_label, retryable_value = self._failure_details(
            error,
            classification=classification,
            failure_class=failure_class,
            failure_label=failure_label,
            retryable=retryable,
        )
        attempt = max(1, int(delivery.get("attempt") or 1))
        max_attempts = max(1, int(delivery.get("maxAttempts") or DELIVERY_MAX_ATTEMPTS))
        will_retry = retryable_value and attempt < max_attempts
        next_revision = int(delivery.get("revision") or 0) + 1
        event: dict[str, Any] = {
            "event": "goal_delivery_failed",
            "goalId": delivery.get("goalId"),
            "deliveryId": delivery.get("deliveryId"),
            "status": "failed",
            "state": "failed",
            "terminal": not will_retry,
            "revision": next_revision,
            "attempt": attempt,
            "maxAttempts": max_attempts,
            "failedAt": _iso(now),
            "error": error_text,
            "failureClass": normalized_class,
            "failureLabel": normalized_label,
            "retryable": retryable_value,
            "willRetry": will_retry,
            "consumeRetry": will_retry,
            "retryAt": _iso(now + timedelta(seconds=retry_backoff_seconds(attempt))) if will_retry else "",
            "phaseDeadlineAt": "",
            "deadlineAt": "",
            "drainPending": False,
            "noticeUnread": not will_retry,
            "recapRevision": next_revision if not will_retry else int(delivery.get("recapRevision") or 0),
            "toastRevision": next_revision if not will_retry else int(delivery.get("toastRevision") or 0),
            "scheduleAdvanced": False,
            **self._usage_fields(delivery, context_usage),
        }
        if not will_retry and not schedule_already_advanced:
            event.update(self._schedule_fields(delivery, now))
        self._append(event)
        failed = self.project_deliveries()[str(delivery.get("deliveryId") or "")]
        if not will_retry:
            self._restore_goal_after_continuation(failed)
        return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def fail_delivery(
        self,
        delivery_id: str,
        error: Any,
        *,
        retry_seconds: int | None = None,
        classification: dict[str, Any] | None = None,
        failure_class: str = "",
        failure_label: str = "",
        retryable: bool | None = None,
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        # ``retry_seconds`` remains accepted for old callers; retry policy is
        # now centralized in retry_backoff_seconds(attempt).
        _ = retry_seconds
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            return self._fail_delivery_locked(
                delivery,
                error,
                classification=classification,
                failure_class=failure_class,
                failure_label=failure_label,
                retryable=retryable,
                now=now or _utc_now(),
                context_usage=context_usage,
                schedule_already_advanced=bool(
                    delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
                ),
            )

    def skip_provider_unreachable(
        self,
        delivery_id: str,
        *,
        now: datetime | None = None,
        rearm_seconds: int = PROVIDER_REARM_SECONDS,
        provider: str = "",
        base_url: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if _delivery_is_terminal(delivery):
                return delivery
            if str(delivery.get("status") or "") == "skipped" and _parse_timestamp(delivery.get("retryAt")):
                return delivery
            skipped_at = now or _utc_now()
            retry_at = skipped_at + timedelta(seconds=max(1, int(rearm_seconds or PROVIDER_REARM_SECONDS)))
            provider_name = _summarize(provider, 120)
            provider_warning_key = "provider_warning_" + hashlib.sha256(
                f"{provider_name}\0{str(base_url or '').strip()}".encode("utf-8")
            ).hexdigest()[:24]
            self._append(
                {
                    "event": "goal_delivery_skipped",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "status": "skipped",
                    "state": "skipped",
                    "terminal": False,
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "skippedAt": _iso(skipped_at),
                    "failureClass": "provider_unreachable",
                    "failureLabel": "provider_unreachable",
                    "retryable": True,
                    "willRetry": True,
                    "consumeRetry": False,
                    "retryAt": _iso(retry_at),
                    "eligibleAt": _iso(retry_at),
                    "provider": provider_name,
                    "providerWarningKey": provider_warning_key,
                    "phaseDeadlineAt": "",
                    "deadlineAt": "",
                    "noticeUnread": False,
                }
            )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def skip_delivery(
        self,
        delivery_id: str,
        reason: str = "provider_unreachable",
        *,
        now: datetime | None = None,
        rearm_seconds: int = PROVIDER_REARM_SECONDS,
    ) -> dict[str, Any]:
        if str(reason or "").strip().lower() != "provider_unreachable":
            raise AgentGoalStoreError("Unsupported goal delivery skip reason.")
        return self.skip_provider_unreachable(delivery_id, now=now, rearm_seconds=rearm_seconds)

    def defer_delivery_capacity(
        self,
        delivery_id: str,
        *,
        now: datetime | None = None,
        rearm_seconds: int = 5,
        failure_class: str = "capacity",
        failure_label: str = "background_lane_full",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if _delivery_is_terminal(delivery):
                return delivery
            if expected_revision is not None and int(delivery.get("revision") or 0) != int(expected_revision):
                return delivery
            if str(delivery.get("status") or "") not in {"pending", "claimed"}:
                return delivery
            deferred_at = now or _utc_now()
            retry_at = deferred_at + timedelta(seconds=max(1, int(rearm_seconds or 5)))
            self._append(
                {
                    "event": "goal_delivery_capacity_deferred",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "status": "interrupted",
                    "state": "interrupted",
                    "terminal": False,
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "interruptedAt": _iso(deferred_at),
                    "failureClass": _normalize_failure_class(failure_class),
                    "failureLabel": _summarize(failure_label, 160),
                    "retryable": True,
                    "willRetry": True,
                    "consumeRetry": False,
                    "retryAt": _iso(retry_at),
                    "phaseDeadlineAt": "",
                    "deadlineAt": "",
                    "noticeUnread": False,
                }
            )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def _mark_delivery_draining_locked(
        self,
        delivery: dict[str, Any],
        phase: str,
        failure_label: str,
        error: Any,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if _delivery_is_terminal(delivery):
            return delivery
        if str(delivery.get("status") or "") == "draining":
            return delivery
        normalized_phase = str(phase or delivery.get("phase") or "").strip().lower().replace("-", "_")
        if normalized_phase not in DELIVERY_PHASES:
            raise AgentGoalStoreError("Goal delivery phase is invalid.")
        self._append(
            {
                "event": "goal_delivery_draining",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "draining",
                "state": "draining",
                "terminal": False,
                "revision": int(delivery.get("revision") or 0) + 1,
                "drainingAt": _iso(now),
                "drainPending": True,
                "phase": normalized_phase,
                "failureClass": "timeout",
                "failureLabel": _summarize(failure_label, 160),
                "error": _summarize(error, 1_000),
                "retryable": True,
                "retryAt": "",
            }
        )
        return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def mark_delivery_draining(
        self,
        delivery_id: str,
        phase: str,
        failure_label: str,
        error: Any,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            return self._mark_delivery_draining_locked(
                delivery,
                phase,
                failure_label,
                error,
                now=now or _utc_now(),
            )

    def _finish_delivery_drain_locked(
        self,
        delivery: dict[str, Any],
        *,
        retryable: bool,
        failure_class: str,
        error: Any,
        now: datetime,
    ) -> dict[str, Any]:
        if str(delivery.get("status") or "") != "draining":
            if _delivery_is_terminal(delivery) or str(delivery.get("status") or "") == "failed":
                return delivery
            raise AgentGoalStoreError("Goal delivery is not draining.", 409)
        return self._fail_delivery_locked(
            delivery,
            error,
            failure_class=failure_class,
            failure_label=str(delivery.get("failureLabel") or failure_class),
            retryable=retryable,
            now=now,
            schedule_already_advanced=bool(
                delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
            ),
        )

    def finish_delivery_drain(
        self,
        delivery_id: str,
        retryable: bool,
        failure_class: str,
        error: Any,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            return self._finish_delivery_drain_locked(
                delivery,
                retryable=retryable,
                failure_class=failure_class,
                error=error,
                now=now or _utc_now(),
            )

    def reconcile_phase_watchdogs(self, now: datetime | None = None) -> list[dict[str, Any]]:
        checked_at = now or _utc_now()
        with self._lock:
            reconciled: list[dict[str, Any]] = []
            for delivery in list(self.project_deliveries().values()):
                if _delivery_is_terminal(delivery) or str(delivery.get("status") or "") == "draining":
                    continue
                phase = str(delivery.get("phase") or "")
                deadline = _parse_timestamp(delivery.get("deadlineAt") or delivery.get("phaseDeadlineAt"))
                if phase not in DELIVERY_PHASES or deadline is None or deadline > checked_at:
                    continue
                label = f"watchdog_{phase}_timeout"
                if phase == "wake" and str(delivery.get("status") or "") in {"pending", "claimed"}:
                    reconciled.append(
                        self.defer_delivery_capacity(
                            str(delivery.get("deliveryId") or ""),
                            now=checked_at,
                            rearm_seconds=5,
                            failure_class="timeout",
                            failure_label=label,
                            expected_revision=int(delivery.get("revision") or 0),
                        )
                    )
                    continue
                reconciled.append(
                    self._mark_delivery_draining_locked(
                        delivery,
                        phase,
                        label,
                        f"Goal delivery {phase} phase exceeded its deadline.",
                        now=checked_at,
                    )
                )
            return reconciled

    def reconcile_stale_watchdogs(self, now: datetime | None = None) -> list[dict[str, Any]]:
        return self.reconcile_phase_watchdogs(now=now)

    def block_delivery(
        self,
        delivery_id: str,
        *,
        kind: str,
        reference: str,
        reason: str = "",
        response: dict[str, Any] | None = None,
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in {"approval", "question"}:
            raise AgentGoalStoreError("Goal delivery block kind must be approval or question.")
        normalized_reference = _summarize(reference, 240)
        if not normalized_reference:
            raise AgentGoalStoreError("Goal delivery block reference is required.")
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if str(delivery.get("status") or "") == "blocked" and str(delivery.get("blockedKind") or "") == normalized_kind:
                return delivery
            if _delivery_is_terminal(delivery) or str(delivery.get("status") or "") == "draining":
                return delivery
            blocked_at = now or _utc_now()
            bounded_response = _bounded_json_value(response or {})
            next_revision = int(delivery.get("revision") or 0) + 1
            event: dict[str, Any] = {
                "event": "goal_delivery_blocked",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "blocked",
                "state": "blocked",
                "terminal": True,
                "revision": next_revision,
                "blockedAt": _iso(blocked_at),
                "blockedKind": normalized_kind,
                "blockedReason": _summarize(reason or f"waiting_for_{normalized_kind}", 500),
                "blockedResponse": bounded_response if isinstance(bounded_response, dict) else {},
                "retryable": False,
                "retryAt": "",
                "phaseDeadlineAt": "",
                "deadlineAt": "",
                "noticeUnread": True,
                "recapRevision": next_revision,
                "toastRevision": next_revision,
                # A blocked occurrence remains the single owner of this goal's
                # current schedule. Recurring goals do not advance until the
                # decision is resolved, so they cannot accumulate duplicate
                # approvals or questions in the background.
                "scheduleAdvanced": False,
                **self._usage_fields(delivery, context_usage or (response or {}).get("contextUsage")),
            }
            if normalized_kind == "approval":
                event["approvalId"] = normalized_reference
                event["approvalReference"] = normalized_reference
            else:
                event["questionId"] = normalized_reference
                event["questionReminderAt"] = _iso(blocked_at + timedelta(seconds=QUESTION_REMINDER_SECONDS))
                event["questionReminderSentAt"] = ""
            self._append(event)
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def block_delivery_for_approval(
        self,
        delivery_id: str,
        approval_id: str,
        *,
        reason: str = "",
        response: dict[str, Any] | None = None,
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.block_delivery(
            delivery_id,
            kind="approval",
            reference=approval_id,
            reason=reason,
            response=response,
            context_usage=context_usage,
            now=now,
        )

    def block_delivery_for_question(
        self,
        delivery_id: str,
        question_id: str,
        *,
        reason: str = "",
        response: dict[str, Any] | None = None,
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.block_delivery(
            delivery_id,
            kind="question",
            reference=question_id,
            reason=reason,
            response=response,
            context_usage=context_usage,
            now=now,
        )

    def _find_delivery_by_approval_locked(self, approval_id: str) -> dict[str, Any] | None:
        reference = str(approval_id or "").strip()
        if not reference:
            return None
        matches = [
            delivery
            for delivery in self.project_deliveries().values()
            if reference in {str(delivery.get("approvalId") or ""), str(delivery.get("approvalReference") or "")}
        ]
        matches.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        return matches[0] if matches else None

    def delivery_for_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            delivery = self._find_delivery_by_approval_locked(approval_id)
            return dict(delivery) if delivery is not None else None

    def reconcile_missing_approvals(
        self,
        known_approval_ids: set[str],
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fail closed when a pending decision cannot survive process recovery."""

        checked_at = now or _utc_now()
        known = {str(item or "").strip() for item in known_approval_ids if str(item or "").strip()}
        with self._lock:
            reconciled: list[dict[str, Any]] = []
            for delivery in list(self.project_deliveries().values()):
                if str(delivery.get("status") or "") != "blocked":
                    continue
                if str(delivery.get("blockedKind") or "") != "approval":
                    continue
                approval_id = str(
                    delivery.get("approvalId") or delivery.get("approvalReference") or ""
                ).strip()
                if not approval_id or approval_id in known:
                    continue
                reconciled.append(
                    self._fail_delivery_locked(
                        delivery,
                        "The pending approval could not be recovered after the runtime restarted.",
                        failure_class="approval_recovery_required",
                        failure_label="approval_recovery_required",
                        retryable=False,
                        now=checked_at,
                        schedule_already_advanced=bool(
                            delivery.get("scheduleAdvanced")
                            or delivery.get("continuationScheduleAdvanced")
                        ),
                        allow_terminal_transition=True,
                    )
                )
            return reconciled

    def deny_delivery(
        self,
        delivery_id: str,
        *,
        reason: str = "",
        approval_reference: str = "",
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            status = str(delivery.get("status") or "")
            if status == "denied":
                return delivery
            if status in {"completed", "materialized", "failed", "parked", "draining"}:
                return delivery
            denied_at = now or _utc_now()
            reference = _summarize(
                approval_reference or delivery.get("approvalReference") or delivery.get("approvalId"),
                240,
            )
            schedule_already_advanced = bool(
                delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
            )
            next_revision = int(delivery.get("revision") or 0) + 1
            event: dict[str, Any] = {
                "event": "goal_delivery_denied",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "denied",
                "state": "denied",
                "terminal": True,
                "revision": next_revision,
                "deniedAt": _iso(denied_at),
                "blockedReason": _summarize(reason or "approval_denied", 500),
                "approvalId": reference,
                "approvalReference": reference,
                "failureClass": "permission_denied",
                "failureLabel": "permission_denied",
                "retryable": False,
                "retryAt": "",
                "phaseDeadlineAt": "",
                "deadlineAt": "",
                "noticeUnread": True,
                "recapRevision": next_revision,
                "toastRevision": next_revision,
                "scheduleAdvanced": False,
                **self._usage_fields(delivery, context_usage),
            }
            if not schedule_already_advanced:
                event.update(self._schedule_fields(delivery, denied_at))
            self._append(event)
            denied = self.project_deliveries()[str(delivery.get("deliveryId") or "")]
            self._restore_goal_after_continuation(denied)
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def deny_by_approval(
        self,
        approval_id: str,
        *,
        reason: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self._find_delivery_by_approval_locked(approval_id)
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery approval.", 404)
            return self.deny_delivery(
                str(delivery.get("deliveryId") or ""),
                reason=reason,
                approval_reference=approval_id,
                now=now,
            )

    def park_delivery(
        self,
        delivery_id: str,
        *,
        reason: str,
        failure_class: str,
        context_usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if str(delivery.get("status") or "") == "parked" or _delivery_is_terminal(delivery):
                return delivery
            parked_at = now or _utc_now()
            next_revision = int(delivery.get("revision") or 0) + 1
            event = {
                "event": "goal_delivery_parked",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "parked",
                "state": "parked",
                "terminal": True,
                "revision": next_revision,
                "parkedAt": _iso(parked_at),
                "blockedReason": _summarize(reason, 500),
                "failureClass": _normalize_failure_class(failure_class),
                "failureLabel": _summarize(reason or failure_class, 160),
                "retryable": False,
                "retryAt": "",
                "phaseDeadlineAt": "",
                "deadlineAt": "",
                "noticeUnread": True,
                "recapRevision": next_revision,
                "toastRevision": next_revision,
                "scheduleAdvanced": False,
                **self._usage_fields(delivery, context_usage),
            }
            if not bool(delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")):
                event.update(self._schedule_fields(delivery, parked_at))
            self._append(event)
            parked = self.project_deliveries()[str(delivery.get("deliveryId") or "")]
            self._restore_goal_after_continuation(parked)
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def resolve_delivery_approval(
        self,
        approval_id: str,
        execution: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self._find_delivery_by_approval_locked(approval_id)
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery approval.", 404)
            status = str(delivery.get("status") or "")
            if status in {"completed", "materialized", "denied", "failed", "draining", "parked"}:
                return delivery
            if status not in {"blocked", "applying"}:
                raise AgentGoalStoreError("Goal delivery is not waiting for this approval.", 409)
            execution_payload = execution if isinstance(execution, dict) else {}
            execution_status = str(execution_payload.get("status") or "").strip().lower()
            ok = bool(execution_payload.get("ok")) or execution_status in {"ok", "completed", "applied", "approved", "success"}
            resolved_at = now or _utc_now()
            if not ok:
                return self._fail_delivery_locked(
                    delivery,
                    execution_payload.get("error") or execution_payload.get("message") or "Approved action failed to apply.",
                    failure_class="apply_failed",
                    failure_label="apply_failed",
                    retryable=False,
                    now=resolved_at,
                    context_usage=execution_payload.get("contextUsage"),
                    schedule_already_advanced=bool(
                        delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
                    ),
                    allow_terminal_transition=status == "blocked",
                )
            response = dict(delivery.get("blockedResponse") or {})
            execution_response = execution_payload.get("response")
            if isinstance(execution_response, dict):
                response.update(execution_response)
            response["approvalExecution"] = {
                key: _bounded_json_value(execution_payload.get(key))
                for key in ("ok", "status", "summary", "approvalId", "checkpointId")
                if key in execution_payload
            }
            return self._complete_delivery_locked(
                delivery,
                response,
                completed_at=resolved_at,
                schedule_already_advanced=bool(
                    delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
                ),
                context_usage=(
                    execution_payload.get("contextUsage")
                    if isinstance(execution_payload.get("contextUsage"), dict)
                    else {}
                ),
            )

    def resolve_delivery_question(
        self,
        question_id: str,
        *,
        continuation_prompt: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        reference = str(question_id or "").strip()
        if not reference:
            raise AgentGoalStoreError("Question id is required.")
        with self._lock:
            matches = [
                delivery
                for delivery in self.project_deliveries().values()
                if str(delivery.get("questionId") or "") == reference
            ]
            matches.sort(
                key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
                reverse=True,
            )
            if not matches:
                raise AgentGoalStoreError("Unknown goal delivery question.", 404)
            delivery = matches[0]
            if str(delivery.get("questionAnsweredAt") or ""):
                return delivery
            status = str(delivery.get("status") or "")
            if status not in {"blocked", "parked"} or str(delivery.get("blockedKind") or "") != "question":
                raise AgentGoalStoreError("Goal delivery is not waiting for this question.", 409)
            prompt = _summarize(continuation_prompt, 4_000)
            if not prompt:
                raise AgentGoalStoreError("Question continuation prompt is required.")
            answered_at = now or _utc_now()
            schedule_was_advanced = bool(
                delivery.get("scheduleAdvanced") or delivery.get("continuationScheduleAdvanced")
            )
            next_revision = int(delivery.get("revision") or 0) + 1
            self._append(
                {
                    "event": "goal_delivery_question_answered",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "status": "interrupted",
                    "state": "interrupted",
                    "terminal": False,
                    "revision": next_revision,
                    "questionAnsweredAt": _iso(answered_at),
                    "continuationPrompt": prompt,
                    "continuationScheduleAdvanced": schedule_was_advanced,
                    "consumeRetry": False,
                    "retryable": True,
                    "willRetry": True,
                    "retryAt": "",
                    "noticeUnread": False,
                    "phaseDeadlineAt": "",
                    "deadlineAt": "",
                }
            )
            answered = self.project_deliveries()[str(delivery.get("deliveryId") or "")]
            if schedule_was_advanced:
                self._append_goal_schedule_projection(
                    answered,
                    event="goal_question_continuation_scheduled",
                    wake_at=str(answered.get("scheduledFor") or ""),
                )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def record_question_reprompt_once(
        self,
        delivery_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if str(delivery.get("blockedKind") or "") != "question":
                raise AgentGoalStoreError("Goal delivery is not blocked on a question.", 409)
            if str(delivery.get("questionReminderSentAt") or ""):
                return delivery
            reminded_at = now or _utc_now()
            reminder_at = _parse_timestamp(delivery.get("questionReminderAt"))
            if reminder_at is None or reminder_at > reminded_at:
                return delivery
            next_revision = int(delivery.get("revision") or 0) + 1
            self._append(
                {
                    "event": "goal_delivery_question_reminded",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery.get("deliveryId"),
                    "status": "parked",
                    "state": "parked",
                    "terminal": True,
                    "revision": next_revision,
                    "questionReminderSentAt": _iso(reminded_at),
                    "parkedAt": _iso(reminded_at),
                    "noticeUnread": True,
                    "recapRevision": next_revision,
                    "toastRevision": next_revision,
                }
            )
            return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def emit_due_question_reminders(self, now: datetime | None = None) -> list[dict[str, Any]]:
        checked_at = now or _utc_now()
        with self._lock:
            rows: list[dict[str, Any]] = []
            for delivery in list(self.project_deliveries().values()):
                if str(delivery.get("status") or "") != "blocked":
                    continue
                if str(delivery.get("blockedKind") or "") != "question":
                    continue
                if str(delivery.get("questionReminderSentAt") or ""):
                    continue
                reminder_at = _parse_timestamp(delivery.get("questionReminderAt"))
                if reminder_at is None or reminder_at > checked_at:
                    continue
                rows.append(self.record_question_reprompt_once(str(delivery.get("deliveryId") or ""), now=checked_at))
            return rows

    def list_unread_deliveries(self, *, chat_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                delivery
                for delivery in self.project_deliveries().values()
                if bool(delivery.get("noticeUnread"))
                and (not chat_id or str(delivery.get("chatId") or "") == str(chat_id))
            ]
            rows.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
            return rows[: max(1, min(int(limit or 200), 200))]

    def list_catchup(self, *, chat_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for delivery in self.project_deliveries().values():
                if not (
                    _delivery_is_terminal(delivery)
                    or str(delivery.get("status") or "") in {"blocked", "parked"}
                ):
                    continue
                if chat_id and str(delivery.get("chatId") or "") != str(chat_id):
                    continue
                revision = int(delivery.get("revision") or 0)
                recap_revision = int(delivery.get("recapRevision") or revision)
                if delivery.get("recapSeenRevision") is not None:
                    seen_revision = int(delivery.get("recapSeenRevision") or 0)
                else:
                    seen_revision = recap_revision if str(delivery.get("recapSeenAt") or "") else 0
                if recap_revision <= seen_revision:
                    continue
                rows.append(delivery)
            rows.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
            return rows[: max(1, min(int(limit or 50), 200))]

    def background_state(self, chat_id: str = "") -> dict[str, Any]:
        with self._lock:
            all_deliveries = list(self.project_deliveries().values())
            unread_by_chat: dict[str, int] = {}
            for delivery in all_deliveries:
                if not bool(delivery.get("noticeUnread")):
                    continue
                owner = str(delivery.get("chatId") or "")
                unread_by_chat[owner] = unread_by_chat.get(owner, 0) + 1
            recent = self.list_catchup(chat_id=chat_id, limit=50)
            provider_warnings = self.project_provider_warnings()
            unread = [
                row
                for row in all_deliveries
                if bool(row.get("noticeUnread"))
                and (not chat_id or str(row.get("chatId") or "") == str(chat_id))
            ]
            return {
                "schema": "vrcforge.agent_goal_background_state.v1",
                "chatId": str(chat_id or ""),
                "recent": recent,
                "deliveries": recent,
                "unread": unread,
                "unreadByChat": unread_by_chat,
                "totalUnread": sum(unread_by_chat.values()) + len(provider_warnings),
                "providerWarnings": provider_warnings,
                "providerWarningCount": len(provider_warnings),
            }

    def acknowledge_background_notifications(
        self,
        chat_id: str,
        delivery_ids: list[Any] | tuple[Any, ...] | None = None,
        *,
        kind: str = "recap",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        owner_chat = str(chat_id or "").strip()
        if not owner_chat:
            raise AgentGoalStoreError("chatId is required to acknowledge background notifications.")
        normalized_kind = str(kind or "recap").strip().lower()
        if normalized_kind not in {"recap", "toast", "provider"}:
            raise AgentGoalStoreError("Background acknowledgement kind must be recap, toast, or provider.")
        with self._lock:
            deliveries = self.project_deliveries()
            requested: dict[str, int | None] = {}
            for item in delivery_ids or []:
                if isinstance(item, dict):
                    delivery_id = str(item.get("deliveryId") or item.get("delivery_id") or "").strip()
                    raw_revision = item.get("expectedRevision", item.get("expected_revision"))
                    expected_revision = int(raw_revision) if raw_revision is not None else None
                else:
                    delivery_id = str(item or "").strip()
                    expected_revision = None
                if delivery_id:
                    requested[delivery_id] = expected_revision
            acknowledged_at = now or _utc_now()
            if normalized_kind == "provider":
                warnings = {
                    str(item.get("warningKey") or ""): item
                    for item in self.project_provider_warnings(include_acknowledged=True)
                }
                for warning_key, expected_revision in requested.items():
                    warning = warnings.get(warning_key)
                    if warning is None:
                        continue
                    current_revision = int(warning.get("revision") or 0)
                    if expected_revision is not None and expected_revision != current_revision:
                        continue
                    if current_revision <= int(warning.get("acknowledgedRevision") or 0):
                        continue
                    self._append(
                        {
                            "event": "goal_provider_warning_acknowledged",
                            "providerWarningKey": warning_key,
                            "acknowledgedRevision": current_revision,
                            "acknowledgedAt": _iso(acknowledged_at),
                        }
                    )
                return self.background_state(owner_chat)
            for delivery in deliveries.values():
                if str(delivery.get("chatId") or "") != owner_chat:
                    continue
                delivery_id = str(delivery.get("deliveryId") or "")
                if requested and delivery_id not in requested:
                    continue
                expected_revision = requested.get(delivery_id) if requested else None
                current_revision = int(delivery.get("revision") or 0)
                if expected_revision is not None and expected_revision != current_revision:
                    continue
                if normalized_kind == "toast":
                    toast_revision = int(delivery.get("toastRevision") or 0)
                    if toast_revision <= int(delivery.get("toastSentRevision") or 0):
                        continue
                    self._append(
                        {
                            "event": "goal_delivery_toast_acknowledged",
                            "goalId": delivery.get("goalId"),
                            "deliveryId": delivery_id,
                            "revision": current_revision + 1,
                            "toastRevision": toast_revision,
                            "toastSentAt": _iso(acknowledged_at),
                            "toastSentRevision": toast_revision,
                        }
                    )
                    continue
                recap_revision = int(delivery.get("recapRevision") or current_revision)
                if (
                    not bool(delivery.get("noticeUnread"))
                    and int(delivery.get("recapSeenRevision") or 0) >= recap_revision
                ):
                    continue
                self._append(
                    {
                        "event": "goal_delivery_notification_acknowledged",
                        "goalId": delivery.get("goalId"),
                        "deliveryId": delivery_id,
                        "revision": current_revision + 1,
                        "noticeUnread": False,
                        "noticeAcknowledgedAt": _iso(acknowledged_at),
                        "recapRevision": recap_revision,
                        "recapSeenAt": _iso(acknowledged_at),
                        "recapSeenRevision": recap_revision,
                    }
                )
            return self.background_state(owner_chat)

    def acknowledge_notification(
        self,
        delivery_id: str,
        *,
        chat_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.acknowledge_background_notifications(chat_id, [delivery_id], kind="recap", now=now)

    def list_recoverable(self, *, limit: int = 20, chat_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for delivery in self.project_deliveries().values():
                delivery = self._recover_result_if_needed(delivery)
                if str(delivery.get("status") or "") != "completed":
                    continue
                if chat_id and str(delivery.get("chatId") or "") != chat_id:
                    continue
                response = self._read_result_for_delivery(delivery)
                if response is None:
                    self._mark_result_corrupt_locked(delivery)
                    continue
                rows.append({**delivery, "response": response})
            rows.sort(key=lambda item: str(item.get("completedAt") or item.get("updatedAt") or ""))
            return rows[: max(1, min(limit, 200))]

    def mark_materialized(self, delivery_id: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if str(delivery.get("status") or "") == "materialized":
                return delivery
            if str(delivery.get("status") or "") != "completed":
                raise AgentGoalStoreError("Goal delivery result is not ready to materialize.", 409)
            requested_chat = str(params.get("chatId") or params.get("chat_id") or "").strip()
            if requested_chat != str(delivery.get("chatId") or ""):
                raise AgentGoalStoreError("Goal delivery must be materialized in its owner chat.", 409)
            expected_revision = params.get("expectedRevision", params.get("expected_revision"))
            if expected_revision is not None and int(expected_revision) != int(delivery.get("revision") or 0):
                raise AgentGoalStoreError("Goal delivery revision changed; refresh before acknowledging.", 409)
            self._append(
                {
                    "event": "goal_delivery_materialized",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery_id,
                    "status": "materialized",
                    "state": "materialized",
                    "terminal": True,
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "materializedAt": _iso(_utc_now()),
                }
            )
            return self.project_deliveries()[delivery_id]
