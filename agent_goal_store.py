from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


GOAL_TERMINAL_STATUSES = {"completed", "cancelled"}
DELIVERY_TERMINAL_STATUSES = {"completed", "materialized"}
WAKE_MIN_INTERVAL_MINUTES = 5
WAKE_MAX_INTERVAL_MINUTES = 10_080


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
    ) -> None:
        self._log_path = log_path
        self._result_dir = result_dir
        self._append_event_callback = append_event
        self._read_events_callback = read_events
        self._lock = lock
        self._normalize_path = normalize_path

    def _events(self) -> list[dict[str, Any]]:
        return self._read_events_callback(self._log_path())

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        return self._append_event_callback(self._log_path(), "vrcforge.agent_goal.v2", event)

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
            if event_name.startswith("goal_delivery_") and event_name != "goal_delivery_completed":
                continue
            previous = goals.get(goal_id, {})
            if event_name == "goal_delivery_completed":
                if not previous:
                    continue
                goals[goal_id] = {
                    **previous,
                    "wakeAt": str(event.get("nextWakeAt") or ""),
                    "lastWokenAt": str(event.get("completedAt") or event.get("updatedAt") or ""),
                    "wakeCount": int(previous.get("wakeCount") or 0) + 1,
                    "revision": int(previous.get("revision") or 1) + 1,
                    "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
                }
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
                for key in ("goalId", "chatId", "sessionId", "projectRoot", "scheduledFor", "resumePrompt", "userItemId", "agentItemId"):
                    merged[key] = previous.get(key, merged.get(key, ""))
            deliveries[delivery_id] = merged
        return deliveries

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

    def is_due(self, goal: dict[str, Any], *, now: datetime) -> bool:
        if str(goal.get("status") or "") != "active" or str(goal.get("blockedReason") or ""):
            return False
        wake_at = _parse_timestamp(goal.get("wakeAt"))
        return wake_at is not None and wake_at <= now

    def list_due(self, *, limit: int, project_root: str = "", session_id: str = "", now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or _utc_now()
        rows = [row for row in self.project_goals().values() if self._scope_matches(row, project_root, session_id) and self.is_due(row, now=now)]
        rows.sort(key=lambda item: str(item.get("wakeAt") or ""))
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

    def wake(self, goal_id: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        params = params or {}
        with self._lock:
            goal = self.project_goals().get(str(goal_id or "").strip())
            if goal is None:
                raise AgentGoalStoreError(f"Goal was not found: {goal_id}", 404)
            self._assert_owner(goal, params)
            now = _utc_now()
            if not self.is_due(goal, now=now):
                raise AgentGoalStoreError("Goal is not due for wake.", 409)
            scheduled_for = str(goal.get("wakeAt") or "")
            delivery_id = self._delivery_id(str(goal.get("goalId") or ""), scheduled_for)
            existing = self.project_deliveries().get(delivery_id)
            if existing:
                existing = self._recover_result_if_needed(existing)
                if str(existing.get("status") or "") in {"failed", "interrupted"}:
                    retry_at = _parse_timestamp(existing.get("retryAt"))
                    if retry_at and retry_at > now:
                        raise AgentGoalStoreError("Goal delivery is waiting to retry.", 409)
                    self._append(
                        {
                            "event": "goal_delivery_claimed",
                            "goalId": goal["goalId"],
                            "deliveryId": delivery_id,
                            "status": "claimed",
                            "revision": int(existing.get("revision") or 0) + 1,
                            "claimedAt": _iso(now),
                            "retryAt": "",
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
            }
            self._append({"event": "goal_delivery_pending", "status": "pending", "revision": 1, **base})
            self._append(
                {
                    "event": "goal_delivery_claimed",
                    "status": "claimed",
                    "revision": 2,
                    "claimedAt": _iso(now),
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
            if str(delivery.get("status") or "") in DELIVERY_TERMINAL_STATUSES:
                return {"delivery": delivery, "response": self.read_result(delivery_id), "cached": True}
            requested_turn = str(params.get("clientTurnId") or params.get("client_turn_id") or "").strip()
            if requested_turn and requested_turn != str(delivery.get("clientTurnId") or ""):
                raise AgentGoalStoreError("Goal delivery clientTurnId is immutable.", 409)
            if str(delivery.get("status") or "") == "running":
                raise AgentGoalStoreError("Goal delivery is already running.", 409)
            if str(delivery.get("status") or "") not in {"pending", "claimed", "failed", "interrupted"}:
                raise AgentGoalStoreError("Goal delivery cannot be started from its current state.", 409)
            event = {
                "event": "goal_delivery_running",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery_id,
                "status": "running",
                "revision": int(delivery.get("revision") or 0) + 1,
                "startedAt": _iso(_utc_now()),
                "clientTurnId": delivery.get("clientTurnId"),
                "provider": _summarize(params.get("provider"), 120),
                "providerLabel": _summarize(params.get("providerLabel"), 160),
                "model": _summarize(params.get("model"), 160),
            }
            self._append(event)
            return {"delivery": self.project_deliveries()[delivery_id], "response": None, "cached": False}

    def _result_path(self, delivery_id: str) -> Path:
        safe_id = "".join(character for character in delivery_id if character.isalnum() or character in "-_")
        if not safe_id or safe_id != delivery_id:
            raise AgentGoalStoreError("Invalid goal delivery id.")
        return self._result_dir() / f"{safe_id}.json"

    def _write_result(self, delivery: dict[str, Any], response: dict[str, Any]) -> None:
        path = self._result_path(str(delivery.get("deliveryId") or ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "vrcforge.agent_goal_delivery_result.v1",
            "deliveryId": delivery.get("deliveryId"),
            "goalId": delivery.get("goalId"),
            "clientTurnId": delivery.get("clientTurnId"),
            "completedAt": _iso(_utc_now()),
            "response": response,
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

    def read_result(self, delivery_id: str) -> dict[str, Any] | None:
        path = self._result_path(delivery_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        response = payload.get("response") if isinstance(payload, dict) else None
        return response if isinstance(response, dict) else None

    def _next_wake_at(self, goal: dict[str, Any], completed_at: datetime) -> str:
        try:
            interval = int(goal.get("wakeEveryMinutes") or 0)
        except (TypeError, ValueError):
            interval = 0
        return _iso(completed_at + timedelta(minutes=interval)) if interval > 0 else ""

    def _recover_result_if_needed(self, delivery: dict[str, Any]) -> dict[str, Any]:
        if str(delivery.get("status") or "") in DELIVERY_TERMINAL_STATUSES:
            return delivery
        response = self.read_result(str(delivery.get("deliveryId") or ""))
        if response is None:
            return delivery
        goal = self.project_goals().get(str(delivery.get("goalId") or ""), {})
        completed_at = _utc_now()
        self._append(
            {
                "event": "goal_delivery_completed",
                "goalId": delivery.get("goalId"),
                "deliveryId": delivery.get("deliveryId"),
                "status": "completed",
                "revision": int(delivery.get("revision") or 0) + 1,
                "completedAt": _iso(completed_at),
                "nextWakeAt": self._next_wake_at(goal, completed_at),
                "resultAvailable": True,
                "recoveredFromSidecar": True,
            }
        )
        return self.project_deliveries()[str(delivery.get("deliveryId") or "")]

    def complete_delivery(self, delivery_id: str, response: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            delivery = self._recover_result_if_needed(delivery)
            if str(delivery.get("status") or "") in DELIVERY_TERMINAL_STATUSES:
                return delivery
            if str(delivery.get("status") or "") != "running":
                raise AgentGoalStoreError("Goal delivery is not running.", 409)
            self._write_result(delivery, response)
            completed_at = _utc_now()
            goal = self.project_goals().get(str(delivery.get("goalId") or ""), {})
            self._append(
                {
                    "event": "goal_delivery_completed",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery_id,
                    "status": "completed",
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "completedAt": _iso(completed_at),
                    "nextWakeAt": self._next_wake_at(goal, completed_at),
                    "resultAvailable": True,
                }
            )
            return self.project_deliveries()[delivery_id]

    def fail_delivery(self, delivery_id: str, error: str, *, retry_seconds: int = 60) -> dict[str, Any]:
        with self._lock:
            delivery = self.project_deliveries().get(str(delivery_id or "").strip())
            if delivery is None:
                raise AgentGoalStoreError("Unknown goal delivery.", 404)
            if str(delivery.get("status") or "") in DELIVERY_TERMINAL_STATUSES:
                return delivery
            self._append(
                {
                    "event": "goal_delivery_failed",
                    "goalId": delivery.get("goalId"),
                    "deliveryId": delivery_id,
                    "status": "failed",
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "error": _summarize(error, 1000),
                    "retryAt": _iso(_utc_now() + timedelta(seconds=max(1, retry_seconds))),
                }
            )
            return self.project_deliveries()[delivery_id]

    def list_recoverable(self, *, limit: int = 20, chat_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for delivery in self.project_deliveries().values():
                delivery = self._recover_result_if_needed(delivery)
                if str(delivery.get("status") or "") != "completed":
                    continue
                if chat_id and str(delivery.get("chatId") or "") != chat_id:
                    continue
                rows.append({**delivery, "response": self.read_result(str(delivery.get("deliveryId") or ""))})
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
                    "revision": int(delivery.get("revision") or 0) + 1,
                    "materializedAt": _iso(_utc_now()),
                }
            )
            return self.project_deliveries()[delivery_id]
