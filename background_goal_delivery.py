"""Async orchestration for one durable VRCForge background goal delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from background_goal_runtime import (
    PHASE_TIMEOUT_SECONDS,
    ProviderPreflightCache,
    RuntimeLaneBudget,
    aggregate_bounded_usage,
    classify_provider_failure,
    classify_runtime_plan_outcome,
    classify_runtime_step_failure,
)


class BackgroundGoalDeliveryError(RuntimeError):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class BackgroundGoalDeliveryCoordinator:
    """Own capacity, timeout drainage, and durable outcome classification."""

    def __init__(
        self,
        *,
        gateway: Any,
        lane_budget: RuntimeLaneBudget,
        preflight: ProviderPreflightCache,
        on_state_change: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._gateway = gateway
        self._lane_budget = lane_budget
        self._preflight = preflight
        self._on_state_change = on_state_change
        self._drain_tasks: set[asyncio.Task[Any]] = set()

    @property
    def drain_task_count(self) -> int:
        return len(self._drain_tasks)

    async def execute(
        self,
        *,
        delivery_id: str,
        begin_params: dict[str, Any],
        runtime_params: dict[str, Any],
        agent_name: str,
        provider: str,
        base_url: str,
    ) -> dict[str, Any]:
        lease_token = f"goal:{delivery_id}"
        lease_result = self._lane_budget.acquire_result("background", lease_token)
        if lease_result == "duplicate":
            raise BackgroundGoalDeliveryError("Background goal delivery is already running.", 409)
        if lease_result != "acquired":
            state = await _atomic_to_thread(
                self._gateway.defer_agent_goal_delivery_capacity,
                delivery_id,
            )
            await self._emit_state(state)
            session_id = str(runtime_params.get("session_id") or runtime_params.get("sessionId") or "")
            client_turn_id = str(runtime_params.get("clientTurnId") or "")
            return {
                "ok": False,
                "status": "background_capacity",
                "backgroundGoalDeferred": True,
                "goalDeliveryId": delivery_id,
                "session_id": session_id,
                "sessionId": session_id,
                "turn_id": client_turn_id,
                "turnId": client_turn_id,
                "clientTurnId": client_turn_id,
                "observe": {},
                "plan": {
                    "summary": "",
                    "planner": "",
                    "shellNeeded": False,
                    "nextStep": "background_capacity",
                },
            }
        release_lease = True
        begin_attempted = True
        active_phase = "wake"
        worker: asyncio.Task[Any] | None = None
        try:
            preflight = await _atomic_to_thread(self._preflight.check, provider, base_url)
            if preflight.required and not preflight.reachable:
                state = await _atomic_to_thread(
                    self._gateway.skip_unreachable_agent_goal_provider,
                    delivery_id,
                    provider=preflight.provider,
                    base_url=preflight.base_url,
                )
                await self._emit_state(state)
                session_id = str(
                    runtime_params.get("session_id") or runtime_params.get("sessionId") or ""
                )
                client_turn_id = str(runtime_params.get("clientTurnId") or "")
                delivery = state.get("delivery") if isinstance(state, dict) else {}
                return {
                    "ok": False,
                    "status": "provider_unreachable",
                    "backgroundGoalSkipped": True,
                    "goalDeliveryId": delivery_id,
                    "providerWarningKey": (
                        delivery.get("providerWarningKey") if isinstance(delivery, dict) else ""
                    ),
                    "session_id": session_id,
                    "sessionId": session_id,
                    "turn_id": client_turn_id,
                    "turnId": client_turn_id,
                    "clientTurnId": client_turn_id,
                    "observe": {},
                    "plan": {
                        "summary": "",
                        "planner": "",
                        "shellNeeded": False,
                        "nextStep": "provider_unreachable",
                    },
                }

            active_phase = "project_lock"
            phase_state = await _atomic_to_thread(
                self._gateway.record_agent_goal_delivery_phase,
                delivery_id,
                active_phase,
            )
            projected = phase_state.get("delivery") if isinstance(phase_state, dict) else None
            if isinstance(projected, dict) and str(projected.get("status") or "") == "draining":
                state = await _atomic_to_thread(
                    self._gateway.finish_agent_goal_delivery_drain,
                    delivery_id,
                    retryable=True,
                    failure_class="timeout",
                    error="Background goal project lock worker exited after its live watchdog deadline.",
                )
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background goal project lock exceeded its deadline.", 504)
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._gateway.begin_agent_goal_delivery,
                    delivery_id,
                    begin_params,
                )
            )
            try:
                delivery_start = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=PHASE_TIMEOUT_SECONDS.get(active_phase, 30),
                )
            except TimeoutError as exc:
                state = await _atomic_to_thread(
                    self._gateway.drain_agent_goal_delivery,
                    delivery_id,
                    phase=active_phase,
                    failure_label="watchdog_project_lock_timeout",
                    error="Background goal project lock phase exceeded its deadline.",
                )
                self._start_drain(
                    delivery_id,
                    lease_token,
                    worker,
                    retryable=True,
                    failure_class="timeout",
                    error="Timed-out project lock worker exited after its deadline.",
                )
                release_lease = False
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background goal project lock timed out.", 504) from exc
            worker = None
            cached_response = delivery_start.get("response")
            if delivery_start.get("cached") and isinstance(cached_response, dict):
                return {**cached_response, "goalDeliveryId": delivery_id}
            active_phase = "provider_call"
            phase_state = await _atomic_to_thread(
                self._gateway.record_agent_goal_delivery_phase,
                delivery_id,
                active_phase,
            )
            projected = phase_state.get("delivery") if isinstance(phase_state, dict) else None
            if isinstance(projected, dict) and str(projected.get("status") or "") == "draining":
                state = await _atomic_to_thread(
                    self._gateway.finish_agent_goal_delivery_drain,
                    delivery_id,
                    retryable=True,
                    failure_class="timeout",
                    error="Background goal provider worker exited after its live watchdog deadline.",
                )
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background goal provider call exceeded its deadline.", 504)
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._gateway.runtime_message,
                    {**runtime_params, "_backgroundGoalRun": True},
                    agent_name=agent_name,
                )
            )
            try:
                payload = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=PHASE_TIMEOUT_SECONDS.get(active_phase, 30),
                )
            except TimeoutError as exc:
                await _atomic_to_thread(
                    self._gateway.request_runtime_cancel,
                    {
                        "sessionId": runtime_params.get("session_id") or runtime_params.get("sessionId") or "",
                        "clientTurnId": runtime_params.get("clientTurnId") or "",
                        "reason": "background_goal_provider_timeout",
                    },
                )
                state = await _atomic_to_thread(
                    self._gateway.drain_agent_goal_delivery,
                    delivery_id,
                    phase=active_phase,
                    failure_label="watchdog_provider_call_timeout",
                    error="Background goal provider call exceeded its deadline.",
                )
                self._start_drain(
                    delivery_id,
                    lease_token,
                    worker,
                    retryable=True,
                    failure_class="timeout",
                    error="Timed-out background goal worker exited after cancellation.",
                )
                release_lease = False
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background goal provider call timed out.", 504) from exc

            if not isinstance(payload, dict):
                raise RuntimeError("Background goal runtime returned an invalid response.")
            payload = {**payload, "goalDeliveryId": delivery_id}
            persisted = dict(payload)
            persisted.pop("contextCompaction", None)
            persisted.pop("context_compaction", None)
            usage = aggregate_bounded_usage(payload.get("contextUsage") or {})
            approval_id = _find_approval_id(payload)
            question_id = _find_question_id(payload)
            next_step = str((payload.get("plan") or {}).get("nextStep") or "").strip().lower()
            plan_outcome, plan_label = classify_runtime_plan_outcome(payload.get("plan"))
            outcome, outcome_label = _runtime_failure_outcome(payload)

            def persist_outcome() -> dict[str, Any]:
                if approval_id:
                    blocked = self._gateway.block_agent_goal_delivery(
                        delivery_id,
                        kind="approval",
                        reference=approval_id,
                        response=persisted,
                        context_usage=usage,
                    )
                    reconcile = getattr(self._gateway, "reconcile_linked_agent_goal_approval", None)
                    reconciled = reconcile(approval_id) if callable(reconcile) else None
                    return reconciled or blocked
                if question_id:
                    return self._gateway.block_agent_goal_delivery(
                        delivery_id,
                        kind="question",
                        reference=question_id,
                        response=persisted,
                        context_usage=usage,
                    )
                if next_step == "loop_suppressed":
                    return self._gateway.fail_agent_goal_delivery(
                        delivery_id,
                        "Repeated background tool failure was suppressed.",
                        failure_class="loop_suppressed",
                        failure_label="loop_suppressed",
                        retryable=False,
                        context_usage=usage,
                    )
                if next_step == "cancelled":
                    return self._gateway.fail_agent_goal_delivery(
                        delivery_id,
                        "Background goal run was cancelled.",
                        failure_class="cancelled",
                        failure_label="cancelled",
                        retryable=False,
                        context_usage=usage,
                    )
                if outcome == "denied":
                    return self._gateway.deny_agent_goal_delivery(
                        delivery_id,
                        reason=outcome_label,
                    )
                if outcome == "failed":
                    return self._gateway.fail_agent_goal_delivery(
                        delivery_id,
                        "Background goal tool outcome was not successful.",
                        failure_class="tool_failed",
                        failure_label=outcome_label,
                        retryable=False,
                        context_usage=usage,
                    )
                if plan_outcome == "parked":
                    return self._gateway.park_agent_goal_delivery(
                        delivery_id,
                        reason=plan_label,
                        failure_class=plan_label,
                        context_usage=usage,
                    )
                return self._gateway.complete_agent_goal_delivery(
                    delivery_id,
                    persisted,
                    context_usage=usage,
                )

            active_phase = "deliver"
            await _atomic_to_thread(
                self._gateway.record_agent_goal_delivery_phase,
                delivery_id,
                active_phase,
            )
            worker = asyncio.create_task(asyncio.to_thread(persist_outcome))
            try:
                state = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=PHASE_TIMEOUT_SECONDS.get(active_phase, 30),
                )
            except TimeoutError as exc:
                state = await _atomic_to_thread(
                    self._gateway.drain_agent_goal_delivery,
                    delivery_id,
                    phase=active_phase,
                    failure_label="watchdog_deliver_timeout",
                    error="Background goal delivery persistence exceeded its deadline.",
                )
                self._start_drain(
                    delivery_id,
                    lease_token,
                    worker,
                    retryable=True,
                    failure_class="timeout",
                    error="Timed-out delivery persistence worker exited after its deadline.",
                )
                release_lease = False
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background goal delivery timed out.", 504) from exc
            projected = state.get("delivery") if isinstance(state, dict) else None
            if isinstance(projected, dict) and str(projected.get("status") or "") == "draining":
                state = await _atomic_to_thread(
                    self._gateway.finish_agent_goal_delivery_drain,
                    delivery_id,
                    retryable=True,
                    failure_class="timeout",
                    error="Background goal worker exited after its live watchdog deadline.",
                )
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background goal delivery exceeded its deadline.", 504)
            await self._emit_state(state)
            return payload
        except asyncio.CancelledError:
            if release_lease and begin_attempted:
                if worker is not None and active_phase == "provider_call":
                    try:
                        await _atomic_to_thread(
                            self._gateway.request_runtime_cancel,
                            {
                                "sessionId": runtime_params.get("session_id") or runtime_params.get("sessionId") or "",
                                "clientTurnId": runtime_params.get("clientTurnId") or "",
                                "reason": "background_goal_request_cancelled",
                            },
                        )
                    except Exception:
                        pass
                release_lease = await self._cancel_delivery(
                    delivery_id=delivery_id,
                    lease_token=lease_token,
                    phase=active_phase,
                    failure_label="background_goal_request_cancelled",
                    error="Background goal request was cancelled before its worker exited.",
                    worker=worker,
                )
            raise
        except BackgroundGoalDeliveryError:
            raise
        except Exception as exc:
            decision = classify_provider_failure(exc, getattr(exc, "status_code", None))
            if begin_attempted:
                state = await _atomic_to_thread(
                    self._gateway.fail_agent_goal_delivery,
                    delivery_id,
                    f"Background goal provider failure: {decision.failure_class}.",
                    failure_class=decision.failure_class,
                    failure_label=f"provider_{decision.failure_class}",
                    retryable=decision.retryable,
                )
                await self._emit_state(state)
            status_code = int(getattr(exc, "status_code", 502) or 502)
            if not (400 <= status_code <= 599):
                status_code = 502
            raise BackgroundGoalDeliveryError(
                f"Background goal request failed ({decision.failure_class}).",
                status_code,
            ) from exc
        finally:
            if release_lease:
                self._lane_budget.release(lease_token)

    async def execute_approved_action(
        self,
        *,
        delivery_id: str,
        approval_id: str = "",
        approve_operation: Callable[[], dict[str, Any]],
        execute_operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Run one approved background action under the shared lane and apply watchdog.

        Python worker threads cannot be force-stopped safely. On timeout the
        delivery enters ``draining`` and keeps its lease until the worker exits,
        so a late result can never turn the run green.
        """

        lease_token = f"goal-apply:{delivery_id}"
        lease_result = self._lane_budget.acquire_result("background", lease_token)
        if lease_result == "duplicate":
            raise BackgroundGoalDeliveryError("Background approval delivery is already running.", 409)
        if lease_result != "acquired":
            raise BackgroundGoalDeliveryError("Background runtime capacity is currently full.", 429)
        release_lease = True
        approve_worker: asyncio.Task[Any] | None = None
        worker: asyncio.Task[Any] | None = None
        approved: dict[str, Any] = {}
        apply_started = False
        try:
            mark_approval_phase = getattr(self._gateway, "mark_agent_goal_approval_phase", None)
            if approval_id and callable(mark_approval_phase):
                phase_state = await _atomic_to_thread(mark_approval_phase, approval_id, "apply")
                apply_started = True
                if isinstance(phase_state, dict):
                    await self._emit_state(phase_state)
            approve_worker = asyncio.create_task(asyncio.to_thread(approve_operation))
            try:
                approved_value = await asyncio.wait_for(
                    asyncio.shield(approve_worker),
                    timeout=PHASE_TIMEOUT_SECONDS.get("apply", 120),
                )
            except TimeoutError as exc:
                state = await _atomic_to_thread(
                    self._gateway.drain_agent_goal_delivery,
                    delivery_id,
                    phase="apply",
                    failure_label="watchdog_approval_transition_timeout",
                    error="Background approval transition exceeded its deadline.",
                )
                self._start_drain(
                    delivery_id,
                    lease_token,
                    approve_worker,
                    retryable=False,
                    failure_class="apply_failed",
                    error="Timed-out approval transition worker exited after its deadline.",
                )
                release_lease = False
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Background approval transition timed out.", 504) from exc
            except Exception:
                restore_wait = getattr(self._gateway, "restore_agent_goal_approval_wait", None)
                try:
                    state = (
                        await _atomic_to_thread(restore_wait, approval_id)
                        if approval_id and callable(restore_wait)
                        else None
                    )
                except Exception:
                    state = await _atomic_to_thread(
                        self._gateway.fail_agent_goal_delivery,
                        delivery_id,
                        "Background approval transition could not be recovered.",
                        failure_class="approval_transition_failed",
                        failure_label="approval_transition_failed",
                        retryable=False,
                    )
                if isinstance(state, dict):
                    await self._emit_state(state)
                raise
            approved = approved_value if isinstance(approved_value, dict) else {}
            if not isinstance(approved, dict) or not approved.get("ok"):
                resolved_approval_id = str(
                    (approved.get("approval") or {}).get("id") or approval_id
                )
                restore_wait = getattr(self._gateway, "restore_agent_goal_approval_wait", None)
                if resolved_approval_id and callable(restore_wait):
                    state = await _atomic_to_thread(
                        restore_wait,
                        resolved_approval_id,
                    )
                    if isinstance(state, dict):
                        await self._emit_state(state)
                return approved, None
            if not apply_started:
                resolved_approval_id = str((approved.get("approval") or {}).get("id") or approval_id)
                if resolved_approval_id and callable(mark_approval_phase):
                    phase_state = await _atomic_to_thread(
                        mark_approval_phase,
                        resolved_approval_id,
                        "apply",
                    )
                    if isinstance(phase_state, dict):
                        await self._emit_state(phase_state)
                else:
                    await _atomic_to_thread(
                        self._gateway.record_agent_goal_delivery_phase,
                        delivery_id,
                        "apply",
                    )
                apply_started = True
            worker = asyncio.create_task(asyncio.to_thread(execute_operation, approved))
            try:
                execution = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=PHASE_TIMEOUT_SECONDS.get("apply", 120),
                )
                if isinstance(execution, dict) and execution.get("goalDeliveryResolutionPending"):
                    approval_id = str((approved.get("approval") or {}).get("id") or "")
                    if approval_id:
                        resolution = None
                        resolution_error: Exception | None = None
                        for _attempt in range(2):
                            try:
                                resolution = await _atomic_to_thread(
                                    self._gateway.reconcile_linked_agent_goal_approval,
                                    approval_id,
                                )
                                resolution_error = None
                                break
                            except Exception as exc:  # noqa: BLE001 - retry the bounded state projection.
                                resolution_error = exc
                        if resolution is None and resolution_error is not None:
                            resolution = await _atomic_to_thread(
                                self._gateway.fail_agent_goal_delivery,
                                delivery_id,
                                "Approved action finished, but its goal result could not be persisted.",
                                failure_class="deliver_failed",
                                failure_label="approval_result_persistence_failed",
                                retryable=False,
                            )
                            execution["goalDeliveryResolutionFailed"] = True
                        if resolution is not None:
                            execution["goalDelivery"] = resolution
                            execution.pop("goalDeliveryResolutionPending", None)
                            await self._emit_state(resolution)
                linked = execution.get("goalDelivery") if isinstance(execution, dict) else None
                linked_delivery = linked.get("delivery") if isinstance(linked, dict) else None
                if isinstance(linked_delivery, dict) and str(linked_delivery.get("status") or "") == "draining":
                    state = await _atomic_to_thread(
                        self._gateway.finish_agent_goal_delivery_drain,
                        delivery_id,
                        retryable=False,
                        failure_class="apply_failed",
                        error="Approved action worker exited after its live watchdog deadline.",
                    )
                    await self._emit_state(state)
                    raise BackgroundGoalDeliveryError("Approved background action exceeded its deadline.", 504)
                return approved, execution
            except TimeoutError as exc:
                state = await _atomic_to_thread(
                    self._gateway.drain_agent_goal_delivery,
                    delivery_id,
                    phase="apply",
                    failure_label="watchdog_apply_timeout",
                    error="Approved background action exceeded its deadline.",
                )
                self._start_drain(
                    delivery_id,
                    lease_token,
                    worker,
                    retryable=False,
                    failure_class="apply_failed",
                    error="Timed-out approved background action exited after its deadline.",
                )
                release_lease = False
                await self._emit_state(state)
                raise BackgroundGoalDeliveryError("Approved background action timed out.", 504) from exc
            except BackgroundGoalDeliveryError:
                raise
            except Exception:
                approval_id = str((approved.get("approval") or {}).get("id") or "")
                if approval_id:
                    state = await _atomic_to_thread(
                        self._gateway.resolve_agent_goal_approval,
                        approval_id,
                        {
                            "ok": False,
                            "status": "failed",
                            "error": "Approved action did not complete successfully.",
                        },
                    )
                    if isinstance(state, dict):
                        await self._emit_state(state)
                raise
        except asyncio.CancelledError:
            if release_lease:
                active_worker = worker or approve_worker
                if apply_started or active_worker is not None:
                    release_lease = await self._cancel_delivery(
                        delivery_id=delivery_id,
                        lease_token=lease_token,
                        phase="apply",
                        failure_label="background_goal_apply_cancelled",
                        error="Approved background action was cancelled before its worker exited.",
                        worker=active_worker,
                    )
            raise
        finally:
            if release_lease:
                self._lane_budget.release(lease_token)

    async def _cancel_delivery(
        self,
        *,
        delivery_id: str,
        lease_token: str,
        phase: str,
        failure_label: str,
        error: str,
        worker: asyncio.Task[Any] | None,
    ) -> bool:
        try:
            state = await _atomic_to_thread(
                self._gateway.drain_agent_goal_delivery,
                delivery_id,
                phase=phase,
                failure_label=failure_label,
                error=error,
            )
        except Exception:
            if worker is not None:
                self._start_drain(
                    delivery_id,
                    lease_token,
                    worker,
                    retryable=False,
                    failure_class="cancelled",
                    error=error,
                )
                return False
            return True
        delivery = state.get("delivery") if isinstance(state, dict) else None
        if not isinstance(delivery, dict) or str(delivery.get("status") or "") != "draining":
            await self._emit_state(state)
            return True
        if worker is not None:
            self._start_drain(
                delivery_id,
                lease_token,
                worker,
                retryable=False,
                failure_class="cancelled",
                error=error,
            )
            await self._emit_state(state)
            return False
        final_state = await _atomic_to_thread(
            self._gateway.finish_agent_goal_delivery_drain,
            delivery_id,
            retryable=False,
            failure_class="cancelled",
            error=error,
        )
        await self._emit_state(final_state)
        return True

    def _start_drain(
        self,
        delivery_id: str,
        lease_token: str,
        worker: asyncio.Task[Any],
        *,
        retryable: bool,
        failure_class: str,
        error: str,
    ) -> None:
        drain_task = asyncio.create_task(
            self._drain_worker(
                delivery_id,
                lease_token,
                worker,
                retryable=retryable,
                failure_class=failure_class,
                error=error,
            )
        )
        self._drain_tasks.add(drain_task)
        drain_task.add_done_callback(self._drain_tasks.discard)

    async def _drain_worker(
        self,
        delivery_id: str,
        lease_token: str,
        worker: asyncio.Task[Any],
        *,
        retryable: bool,
        failure_class: str,
        error: str,
    ) -> None:
        try:
            try:
                await worker
            except BaseException:
                pass
            state = await _atomic_to_thread(
                self._gateway.finish_agent_goal_delivery_drain,
                delivery_id,
                retryable=retryable,
                failure_class=failure_class,
                error=error,
            )
            await self._emit_state(state)
        finally:
            self._lane_budget.release(lease_token)

    async def _emit_state(self, state: dict[str, Any]) -> None:
        if self._on_state_change is not None:
            await self._on_state_change(state)


async def _atomic_to_thread(function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Finish one short state mutation before propagating task cancellation."""

    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await asyncio.shield(task)
        except BaseException:
            pass
        raise cancelled


def _find_approval_id(value: Any, *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, dict):
        direct = str(value.get("approvalId") or value.get("approval_id") or "").strip()
        if direct:
            return direct
        for key in ("shell", "skill", "write", "result", "entrypoint", "response", "plan"):
            nested = _find_approval_id(value.get(key), depth=depth + 1)
            if nested:
                return nested
        for item in list(value.get("steps") or [])[:16]:
            nested = _find_approval_id(item, depth=depth + 1)
            if nested:
                return nested
    return ""


def _find_question_id(value: Any, *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, dict):
        direct = str(value.get("questionId") or value.get("question_id") or "").strip()
        if direct:
            return direct
        question = value.get("question")
        if isinstance(question, dict):
            direct = str(question.get("id") or question.get("questionId") or "").strip()
            if direct:
                return direct
        for key in ("skill", "write", "result", "entrypoint", "response", "plan"):
            nested = _find_question_id(value.get(key), depth=depth + 1)
            if nested:
                return nested
        for item in list(value.get("steps") or [])[:16]:
            nested = _find_question_id(item, depth=depth + 1)
            if nested:
                return nested
    return ""


def _runtime_failure_outcome(value: Any, *, depth: int = 0) -> tuple[str, str]:
    """Return a fail-closed durable outcome for bounded runtime result fields."""

    if depth > 5 or not isinstance(value, dict):
        return "", ""
    status = str(value.get("status") or "").strip().lower().replace("-", "_")
    if status in {"denied", "rejected", "permission_denied"}:
        return "denied", f"runtime_{status}"
    if status in {"blocked", "pending_approval"}:
        failure_class = classify_runtime_step_failure(value)
        if failure_class == "permission_denied":
            return "denied", "runtime_permission_denied"
        combined = " ".join(
            str(value.get(key) or "").strip().lower()
            for key in ("failureClass", "failure_class", "reason", "error", "message")
        )
        if any(marker in combined for marker in ("permission", "forbidden", "approval denied")):
            return "denied", "runtime_permission_denied"
        if any(marker in combined for marker in ("unavailable", "not found", "missing", "dependency")):
            return "failed", "runtime_unavailable"
        return "failed", f"runtime_{status}"
    if status in {"unavailable", "error", "failed", "failure", "timeout", "timed_out"}:
        return "failed", f"runtime_{status}"
    if value.get("ok") is False:
        if classify_runtime_step_failure(value) == "permission_denied":
            return "denied", "runtime_permission_denied"
        return "failed", "runtime_ok_false"
    for key in ("shell", "skill", "write", "result", "entrypoint", "response"):
        outcome = _runtime_failure_outcome(value.get(key), depth=depth + 1)
        if outcome[0]:
            return outcome
    for item in list(value.get("steps") or [])[:16]:
        outcome = _runtime_failure_outcome(item, depth=depth + 1)
        if outcome[0]:
            return outcome
    if classify_runtime_step_failure(value) == "permission_denied":
        return "denied", "runtime_permission_denied"
    return "", ""


__all__ = ["BackgroundGoalDeliveryCoordinator", "BackgroundGoalDeliveryError"]
