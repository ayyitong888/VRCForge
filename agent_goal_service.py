"""App-lifetime owner for durable Agent goals and their delivery state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent_goal_store import AgentGoalStore, AgentGoalStoreError


class AgentGoalServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class GoalStateLockPort(Protocol):
    """The existing re-entrant lock shared with approval state."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...

    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GoalStorePorts:
    """Persistence capabilities used by the one app-owned Goal store."""

    log_path: Callable[[], Path]
    result_dir: Callable[[], Path]
    run_dir: Callable[[], Path]
    append_event: Callable[[Path, str, dict[str, Any]], dict[str, Any]]
    read_events: Callable[[Path], list[dict[str, Any]]]
    shared_state_lock: GoalStateLockPort
    normalize_path: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class GoalApprovalStatePorts:
    """Read the authoritative approval registry under the shared state lock."""

    get: Callable[[str], Mapping[str, Any] | None]
    items: Callable[[], Sequence[tuple[str, Mapping[str, Any]]]]
    ids: Callable[[], set[str]]


@dataclass(frozen=True, slots=True)
class GoalEventPorts:
    """Existing privacy and bounded-summary policy for public Goal events."""

    redact: Callable[[Any], Any]
    redact_persistence: Callable[[Any], Any]
    summarize: Callable[[str, int], str]


class AgentGoalService:
    """Own schedules, deliveries, approval/question resolution and recovery."""

    def __init__(
        self,
        store_ports: GoalStorePorts,
        approval_state: GoalApprovalStatePorts,
        events: GoalEventPorts,
        *,
        runner_instance_id: str | None = None,
    ) -> None:
        self._store_ports = store_ports
        self._approval_state = approval_state
        self._events = events
        self._store = AgentGoalStore(
            log_path=store_ports.log_path,
            result_dir=store_ports.result_dir,
            run_dir=store_ports.run_dir,
            append_event=store_ports.append_event,
            read_events=store_ports.read_events,
            lock=store_ports.shared_state_lock,  # type: ignore[arg-type]
            normalize_path=store_ports.normalize_path,
            runner_instance_id=runner_instance_id,
        )

    @property
    def log_path(self) -> Path:
        return Path(self._store_ports.log_path())

    @property
    def result_dir(self) -> Path:
        return Path(self._store_ports.result_dir())

    @property
    def run_dir(self) -> Path:
        return Path(self._store_ports.run_dir())

    def project_goals(self) -> dict[str, dict[str, Any]]:
        return self._store.project_goals()

    def project_deliveries(self) -> dict[str, dict[str, Any]]:
        return self._store.project_deliveries()

    def create_agent_goal(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        goal = self._store_call(self._store.create, params or {})
        return {"ok": True, "schema": "vrcforge.agent_goal.v2", "goal": self._redact(goal)}

    def get_current_agent_goal(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = params or {}
        goal = self._store.current(
            chat_id=str(raw.get("chatId") or raw.get("chat_id") or "").strip(),
            session_id=str(raw.get("sessionId") or raw.get("session_id") or "").strip(),
            project_root=str(raw.get("projectRoot") or raw.get("project_root") or "").strip(),
        )
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal.current.v1",
            "goal": self._redact(goal) if goal is not None else None,
            "summary": str(goal.get("title") or goal.get("summary") or "No unfinished goal.") if goal else "No unfinished goal.",
        }

    def create_agent_goal_from_agent(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = dict(params or {})
        raw["title"] = raw.get("objective") or raw.get("title") or raw.get("goal")
        goal = self._store_call(self._store.create_if_no_unfinished, raw)
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal.v2",
            "goal": self._redact(goal),
            "summary": f"Goal created: {goal.get('title') or goal.get('goalId')}",
        }

    def update_agent_goal_from_agent(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = dict(params or {})
        current = self._store.current(
            chat_id=str(raw.get("chatId") or raw.get("chat_id") or "").strip(),
            session_id=str(raw.get("sessionId") or raw.get("session_id") or "").strip(),
            project_root=str(raw.get("projectRoot") or raw.get("project_root") or "").strip(),
        )
        if current is None:
            raise AgentGoalServiceError("No unfinished goal exists for this conversation.", 404)
        goal = self._store_call(self._store.update_from_agent, str(current.get("goalId") or ""), raw)
        attempts = int(goal.get("agentBlockedAttempts") or 0)
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal.v2",
            "goal": self._redact(goal),
            "blockedAttempts": attempts,
            "blockedThreshold": 3,
            "summary": (
                f"Goal completed: {goal.get('title') or goal.get('goalId')}"
                if str(goal.get("status") or "") == "completed"
                else f"Blocked evidence recorded ({attempts}/3)."
            ),
        }

    def update_agent_goal(self, goal_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        goal = self._store_call(self._store.update, goal_id, params or {})
        return {"ok": True, "schema": "vrcforge.agent_goal.v2", "goal": self._redact(goal)}

    def bind_agent_goal_owner(self, goal_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        goal = self._store_call(self._store.bind_owner, goal_id, params or {})
        return {"ok": True, "schema": "vrcforge.agent_goal.v2", "goal": self._redact(goal)}

    def list_agent_goals(
        self,
        *,
        limit: int = 50,
        project_root: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        goals = self._store.list(limit=limit, project_root=project_root, session_id=session_id)
        return {
            "ok": True,
            "schema": "vrcforge.agent_goals.v2",
            "goals": [self._redact(goal) for goal in goals],
            "count": len(goals),
        }

    def goal_is_due(self, goal: dict[str, Any], *, now: datetime) -> bool:
        return self._store.is_due(goal, now=now)

    def list_due_agent_goals(
        self,
        *,
        limit: int = 20,
        project_root: str = "",
        session_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        goals = self._store.list_due(
            limit=limit,
            project_root=project_root,
            session_id=session_id,
            now=now,
        )
        return {
            "ok": True,
            "schema": "vrcforge.agent_goals_due.v2",
            "now": now.isoformat(),
            "goals": [self._redact(goal) for goal in goals],
            "count": len(goals),
        }

    def reconcile_stale_agent_goal_deliveries(self) -> dict[str, Any]:
        deliveries = self._store.reconcile_stale_running_deliveries()
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal_deliveries_reconciled.v1",
            "deliveries": [self._redact(delivery) for delivery in deliveries],
            "count": len(deliveries),
        }

    def wake_agent_goal(self, goal_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        goal, delivery = self._store_call(self._store.wake, goal_id, params or {})
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal_delivery.v1",
            "goal": self._redact(goal),
            "delivery": self._redact(delivery),
            "resumePrompt": str(delivery.get("resumePrompt") or ""),
        }

    def begin_agent_goal_delivery(
        self,
        delivery_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._store_call(self._store.begin_delivery, delivery_id, params or {})
        return {"ok": True, "schema": "vrcforge.agent_goal_delivery.v1", **payload}

    def record_agent_goal_delivery_phase(self, delivery_id: str, phase: str) -> dict[str, Any]:
        delivery = self._store_call(self._store.mark_delivery_phase, delivery_id, phase)
        return self._delivery_payload(delivery)

    def complete_agent_goal_delivery(
        self,
        delivery_id: str,
        response: dict[str, Any],
        *,
        context_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.complete_delivery,
            delivery_id,
            self._persistence_dict(response),
            context_usage=context_usage,
        )
        return self._delivery_payload(delivery)

    def fail_agent_goal_delivery(
        self,
        delivery_id: str,
        error: Any,
        *,
        failure_class: str = "",
        failure_label: str = "",
        retryable: bool | None = None,
        context_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.fail_delivery,
            delivery_id,
            error,
            failure_class=failure_class,
            failure_label=failure_label,
            retryable=retryable,
            context_usage=context_usage,
        )
        return self._delivery_payload(delivery)

    def skip_unreachable_agent_goal_provider(
        self,
        delivery_id: str,
        *,
        provider: str = "",
        base_url: str = "",
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.skip_provider_unreachable,
            delivery_id,
            provider=provider,
            base_url=base_url,
        )
        return self._delivery_payload(delivery)

    def defer_agent_goal_delivery_capacity(self, delivery_id: str) -> dict[str, Any]:
        return self._delivery_payload(
            self._store_call(self._store.defer_delivery_capacity, delivery_id)
        )

    def defer_agent_goal_delivery_wake_timeout(self, delivery_id: str) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.defer_delivery_capacity,
            delivery_id,
            rearm_seconds=5,
            failure_class="timeout",
            failure_label="watchdog_wake_timeout",
        )
        return self._delivery_payload(delivery)

    def defer_agent_goal_delivery_handoff(
        self,
        delivery_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.defer_delivery_capacity,
            delivery_id,
            rearm_seconds=5,
            failure_class="handoff",
            failure_label="client_handoff_deferred",
            expected_revision=expected_revision,
        )
        return self._delivery_payload(delivery)

    def park_agent_goal_delivery(
        self,
        delivery_id: str,
        *,
        reason: str,
        failure_class: str,
        context_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.park_delivery,
            delivery_id,
            reason=reason,
            failure_class=failure_class,
            context_usage=context_usage,
        )
        return self._delivery_payload(delivery)

    def drain_agent_goal_delivery(
        self,
        delivery_id: str,
        *,
        phase: str,
        failure_label: str,
        error: str,
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.mark_delivery_draining,
            delivery_id,
            phase,
            failure_label,
            error,
        )
        return self._delivery_payload(delivery)

    def finish_agent_goal_delivery_drain(
        self,
        delivery_id: str,
        *,
        retryable: bool,
        failure_class: str,
        error: str,
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.finish_delivery_drain,
            delivery_id,
            retryable,
            failure_class,
            error,
        )
        return self._delivery_payload(delivery)

    def block_agent_goal_delivery(
        self,
        delivery_id: str,
        *,
        kind: str,
        reference: str,
        response: dict[str, Any],
        context_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if kind == "approval":
            operation = self._store.block_delivery_for_approval
        elif kind == "question":
            operation = self._store.block_delivery_for_question
        else:
            raise AgentGoalServiceError("Goal delivery block kind is invalid.")
        delivery = self._store_call(
            operation,
            delivery_id,
            reference,
            response=self._persistence_dict(response),
            context_usage=context_usage,
        )
        return self._delivery_payload(delivery)

    def mark_agent_goal_approval_phase(self, approval_id: str, phase: str) -> dict[str, Any] | None:
        try:
            delivery = self._store.mark_by_approval_phase(approval_id, phase)
        except AgentGoalStoreError as exc:
            if exc.status_code == 404:
                return None
            self._raise_store_error(exc)
        return self._delivery_payload(delivery)

    def restore_agent_goal_approval_wait(self, approval_id: str) -> dict[str, Any] | None:
        with _locked(self._store_ports.shared_state_lock):
            approval = self._approval_state.get(str(approval_id or "").strip())
            if not approval or not str(approval.get("goalDeliveryId") or "").strip():
                return None
            status = str(approval.get("status") or "").strip().lower()
            if status in {"rejected", "expired", "revision_requested", "applied", "failed"}:
                return self.reconcile_linked_agent_goal_approval(approval_id)
            try:
                delivery = self._store.restore_approval_wait(approval_id)
            except AgentGoalStoreError as exc:
                if exc.status_code == 404:
                    return None
                self._raise_store_error(exc)
            return self._delivery_payload(delivery)

    def agent_goal_delivery_for_approval(self, approval_id: str) -> dict[str, Any] | None:
        delivery = self._store.delivery_for_approval(approval_id)
        return self._delivery_payload(delivery) if delivery is not None else None

    def resolve_agent_goal_approval(
        self,
        approval_id: str,
        execution: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            delivery = self._store.resolve_delivery_approval(
                approval_id,
                self._persistence_dict(execution),
            )
        except AgentGoalStoreError as exc:
            if exc.status_code == 404:
                return None
            self._raise_store_error(exc)
        return self._delivery_payload(delivery)

    def deny_agent_goal_delivery(
        self,
        delivery_id: str,
        *,
        reason: str = "",
        approval_reference: str = "",
    ) -> dict[str, Any]:
        delivery = self._store_call(
            self._store.deny_delivery,
            delivery_id,
            reason=reason,
            approval_reference=approval_reference,
        )
        return self._delivery_payload(delivery)

    def deny_agent_goal_approval(self, approval_id: str, *, reason: str = "") -> dict[str, Any] | None:
        try:
            delivery = self._store.deny_by_approval(approval_id, reason=reason)
        except AgentGoalStoreError as exc:
            if exc.status_code == 404:
                return None
            self._raise_store_error(exc)
        return self._delivery_payload(delivery)

    def reconcile_linked_agent_goal_approval(self, approval_id: str) -> dict[str, Any] | None:
        with _locked(self._store_ports.shared_state_lock):
            approval = self._approval_state.get(str(approval_id or "").strip())
            if not approval or not str(approval.get("goalDeliveryId") or "").strip():
                return None
            status = str(approval.get("status") or "").strip().lower()
            resolved_approval_id = str(approval.get("id") or approval_id)
            if status in {"rejected", "expired", "revision_requested"}:
                return self.deny_agent_goal_approval(
                    resolved_approval_id,
                    reason="approval_denied" if status == "rejected" else "approval_recovery_required",
                )
            if status not in {"applied", "failed"}:
                return self.agent_goal_delivery_for_approval(resolved_approval_id)
            execution: dict[str, Any] = {
                "ok": status == "applied",
                "status": status,
                "approvalId": resolved_approval_id,
            }
            if status == "applied":
                completion_outcome = _mapping(approval.get("completionOutcome"))
                completion_status = str(completion_outcome.get("status") or "").strip().lower()
                if completion_status == "needs_user_action":
                    execution["ok"] = False
                    execution["status"] = "needs_user_action"
                    execution["error"] = self._events.summarize(
                        str(
                            completion_outcome.get("summary")
                            or "Approved action was committed but required verification did not pass."
                        ),
                        500,
                    )
                execution["summary"] = self._events.summarize(
                    str(approval.get("resultSummary") or ""),
                    500,
                )
                checkpoint = approval.get("checkpoint")
                checkpoint_id = str(checkpoint.get("id") or "") if isinstance(checkpoint, Mapping) else ""
                if checkpoint_id:
                    execution["checkpointId"] = checkpoint_id
            else:
                execution["error"] = "Approved action did not complete successfully."
            return self.resolve_agent_goal_approval(resolved_approval_id, execution)

    def attach_linked_goal_resolution(
        self,
        payload: dict[str, Any],
        approval: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not str(approval.get("goalDeliveryId") or "").strip():
            return payload
        try:
            resolved = self.reconcile_linked_agent_goal_approval(str(approval.get("id") or ""))
        except Exception:  # noqa: BLE001 - a later reconciliation remains fail closed.
            payload["goalDeliveryResolutionPending"] = True
            return payload
        if resolved is not None:
            payload["goalDelivery"] = resolved
        return payload

    def resolve_agent_goal_question(
        self,
        question_id: str,
        *,
        continuation_prompt: str = "",
    ) -> dict[str, Any] | None:
        safe_prompt = str(self._events.redact_persistence(continuation_prompt) or "")
        try:
            delivery = self._store.resolve_delivery_question(
                question_id,
                continuation_prompt=safe_prompt,
            )
        except AgentGoalStoreError as exc:
            if exc.status_code == 404:
                return None
            self._raise_store_error(exc)
        return self._delivery_payload(delivery)

    def reconcile_agent_goal_watchdogs(self, *, finalize_orphans: bool = False) -> dict[str, Any]:
        draining = self._store.reconcile_phase_watchdogs()
        deliveries: list[dict[str, Any]] = list(draining)
        if finalize_orphans:
            deliveries = [
                self._store.finish_delivery_drain(
                    str(delivery.get("deliveryId") or ""),
                    str(delivery.get("phase") or "") != "apply",
                    "timeout",
                    "Abandoned "
                    f"{str(delivery.get('phase') or '') or 'runtime'} phase was closed during startup recovery.",
                )
                for delivery in draining
            ]
        approval_deliveries: list[dict[str, Any]] = []
        for approval_id, approval in list(self._approval_state.items()):
            if str(approval.get("status") or "").strip().lower() not in {
                "rejected",
                "expired",
                "revision_requested",
                "applied",
                "failed",
            }:
                continue
            previous = self.agent_goal_delivery_for_approval(approval_id)
            previous_delivery = _mapping(previous).get("delivery")
            previous_delivery = dict(previous_delivery) if isinstance(previous_delivery, Mapping) else {}
            resolved = self.reconcile_linked_agent_goal_approval(approval_id)
            resolved_delivery = _mapping(resolved).get("delivery")
            resolved_delivery = dict(resolved_delivery) if isinstance(resolved_delivery, Mapping) else {}
            if resolved_delivery and (
                not previous_delivery
                or int(resolved_delivery.get("revision") or 0)
                != int(previous_delivery.get("revision") or 0)
            ):
                approval_deliveries.append(resolved_delivery)
        missing = self._store.reconcile_missing_approvals(self._approval_state.ids())
        reminders = self._store.emit_due_question_reminders()
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal_watchdogs.v1",
            "deliveries": [self._redact(item) for item in [*deliveries, *approval_deliveries, *missing]],
            "reminders": [self._redact(item) for item in reminders],
        }

    def tick_agent_goal_question_reminders(self) -> dict[str, Any]:
        reminders = self._store.emit_due_question_reminders()
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal_question_reminders.v1",
            "reminders": [self._redact(delivery) for delivery in reminders],
            "count": len(reminders),
        }

    def agent_goal_background_state(self, *, chat_id: str = "") -> dict[str, Any]:
        return {"ok": True, **self._redact_dict(self._store.background_state(chat_id))}

    def acknowledge_agent_goal_background_state(
        self,
        *,
        chat_id: str,
        delivery_ids: list[Any] | None = None,
        kind: str = "recap",
    ) -> dict[str, Any]:
        state = self._store_call(
            self._store.acknowledge_background_notifications,
            chat_id,
            delivery_ids,
            kind=kind,
        )
        return {"ok": True, **self._redact_dict(state)}

    def list_recoverable_agent_goal_deliveries(
        self,
        *,
        limit: int = 20,
        chat_id: str = "",
    ) -> dict[str, Any]:
        deliveries = self._store.list_recoverable(limit=limit, chat_id=chat_id)
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal_deliveries.v1",
            "deliveries": [self._redact(delivery) for delivery in deliveries],
            "count": len(deliveries),
        }

    def materialize_agent_goal_delivery(
        self,
        delivery_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        delivery = self._store_call(self._store.mark_materialized, delivery_id, params or {})
        return self._delivery_payload(delivery)

    def raw_delivery_for_approval(self, approval_id: str) -> dict[str, Any] | None:
        return self._store.delivery_for_approval(approval_id)

    def reconcile_missing_approvals(self, approval_ids: set[str]) -> list[dict[str, Any]]:
        return self._store.reconcile_missing_approvals(approval_ids)

    def _delivery_payload(self, delivery: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "vrcforge.agent_goal_delivery.v1",
            "delivery": self._redact(delivery),
        }

    def _persistence_dict(self, value: Any) -> dict[str, Any]:
        redacted = self._events.redact_persistence(value)
        return dict(redacted) if isinstance(redacted, Mapping) else {}

    def _redact(self, value: Any) -> Any:
        return self._events.redact(value)

    def _redact_dict(self, value: Any) -> dict[str, Any]:
        redacted = self._events.redact(value)
        return dict(redacted) if isinstance(redacted, Mapping) else {}

    @staticmethod
    def _raise_store_error(exc: AgentGoalStoreError) -> None:
        raise AgentGoalServiceError(str(exc), status_code=exc.status_code) from exc

    def _store_call(self, operation: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except AgentGoalStoreError as exc:
            self._raise_store_error(exc)


class _LockContext:
    def __init__(self, lock: GoalStateLockPort) -> None:
        self._lock = lock

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self._lock.release()


def _locked(lock: GoalStateLockPort) -> _LockContext:
    return _LockContext(lock)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "AgentGoalService",
    "AgentGoalServiceError",
    "GoalApprovalStatePorts",
    "GoalEventPorts",
    "GoalStateLockPort",
    "GoalStorePorts",
]
