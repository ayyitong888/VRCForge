"""Bounded execution coordinator for Memory review provider calls.

The coordinator shares the runtime lane and provider policy primitives used by
other background work, but owns no durable Goal state.  Provider work and the
commit callback are deliberately separate: a timed-out or cancelled provider
worker may finish in the background, but its value can no longer reach the
commit boundary.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from background_goal_runtime import (
    PHASE_TIMEOUT_SECONDS,
    TOTAL_PROVIDER_ATTEMPTS,
    ProviderPreflightCache,
    RuntimeLaneBudget,
    classify_provider_failure,
    retry_backoff_seconds,
)


_LANES = frozenset({"background", "interactive"})
_PHASES = frozenset(
    {
        "lane",
        "preflight",
        "provider_call",
        "retry",
        "commit",
        "completed",
        "failed",
        "cancelled",
    }
)
_FAILURE_CLASSES = frozenset(
    {
        "",
        "auth",
        "cancelled",
        "capacity",
        "commit",
        "credit",
        "duplicate",
        "invalid_request",
        "network",
        "provider_unreachable",
        "rate_limit",
        "schema",
        "server_error",
        "timeout",
        "unknown",
    }
)

MemoryReviewCall = Callable[[], Any]
MemoryReviewCommit = Callable[[Any], Any]
MemoryReviewStateCallback = Callable[[Mapping[str, Any]], Any]
MemoryReviewSleep = Callable[[float], Awaitable[None]]
MemoryReviewContinueGuard = Callable[[], bool]


class MemoryReviewCommitDeferred(RuntimeError):
    """The short project-read commit lease was unavailable."""


class MemoryReviewIdleGate:
    """Generation gate for background work that may run only while idle.

    Activity signals advance an epoch even when no background generation is
    active. That closes the check-to-schedule race without holding a gateway
    lock while the scheduler evaluates its live blocker callback.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._epoch = 0
        self._next_generation = 0
        self._active_generation = 0
        self._on_revoke: Callable[[], Any] | None = None

    def try_acquire(
        self,
        blocker: Callable[[], str],
        on_revoke: Callable[[], Any],
    ) -> int | None:
        if not callable(blocker) or not callable(on_revoke):
            raise TypeError("idle gate callbacks must be callable")
        with self._lock:
            observed_epoch = self._epoch
        if blocker():
            return None
        with self._lock:
            if self._epoch != observed_epoch or self._active_generation:
                return None
            self._next_generation += 1
            self._active_generation = self._next_generation
            self._on_revoke = on_revoke
            return self._active_generation

    def signal_activity(self, _reason: str = "") -> bool:
        callback: Callable[[], Any] | None = None
        with self._lock:
            self._epoch += 1
            if self._active_generation:
                self._active_generation = 0
                callback = self._on_revoke
                self._on_revoke = None
        if callback is not None:
            try:
                callback()
            except Exception:
                # Interactive work must never fail because cancellation
                # notification for optional background work had a warning.
                pass
            return True
        return False

    def release(self, generation: int) -> bool:
        with self._lock:
            if not generation or self._active_generation != generation:
                return False
            self._active_generation = 0
            self._on_revoke = None
            return True

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return bool(generation and self._active_generation == generation)

    def run_if_current(self, generation: int, callback: Callable[[], Any]) -> bool:
        """Linearize the short durable commit against an activity signal."""

        if not callable(callback):
            raise TypeError("idle gate commit callback must be callable")
        with self._lock:
            if not generation or self._active_generation != generation:
                return False
            callback()
            return True

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "active": bool(self._active_generation),
                "epoch": self._epoch,
            }


@dataclass(frozen=True)
class MemoryReviewRunResult:
    """One bounded execution result.

    ``output`` is returned only to the direct caller.  State callbacks receive
    a separate fixed envelope that never includes provider input or output.
    """

    status: str
    attempts: int = 0
    failure_class: str = ""
    retryable: bool = False
    output: Any = None
    committed: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def as_dict(self, *, include_output: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "status": self.status,
            "attempts": self.attempts,
            "failureClass": self.failure_class,
            "retryable": self.retryable,
            "committed": self.committed,
        }
        if include_output:
            payload["output"] = self.output
        return payload


class MemoryReviewRuntimeCoordinator:
    """Coordinate one provider-only Memory review run.

    A shared ``RuntimeLaneBudget`` keeps this feature inside the application
    concurrency limits.  A coordinator also allows only one background run at
    a time, even when the shared budget has spare background capacity.
    """

    def __init__(
        self,
        *,
        lane_budget: RuntimeLaneBudget,
        preflight: ProviderPreflightCache,
        on_state: MemoryReviewStateCallback | None = None,
        sleep: MemoryReviewSleep = asyncio.sleep,
        provider_timeout_seconds: float | None = None,
    ) -> None:
        if not isinstance(lane_budget, RuntimeLaneBudget):
            raise TypeError("lane_budget must be a RuntimeLaneBudget")
        if not isinstance(preflight, ProviderPreflightCache):
            raise TypeError("preflight must be a ProviderPreflightCache")
        if on_state is not None and not callable(on_state):
            raise TypeError("on_state must be callable")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if provider_timeout_seconds is not None and (
            isinstance(provider_timeout_seconds, bool)
            or not isinstance(provider_timeout_seconds, (int, float))
            or provider_timeout_seconds <= 0
        ):
            raise ValueError("provider_timeout_seconds must be positive")

        self._lane_budget = lane_budget
        self._preflight = preflight
        self._on_state = on_state
        self._sleep = sleep
        self._provider_timeout_seconds = (
            float(provider_timeout_seconds) if provider_timeout_seconds is not None else None
        )
        self._state_lock = threading.RLock()
        self._next_generation = 0
        self._active_generations: dict[str, int] = {}
        self._background_token = ""
        self._draining_workers: set[asyncio.Task[Any]] = set()

    async def run(
        self,
        *,
        lane: str,
        token: str,
        provider: str,
        base_url: str | None,
        call: MemoryReviewCall,
        commit: MemoryReviewCommit | None = None,
        on_run_state: MemoryReviewStateCallback | None = None,
        continue_guard: MemoryReviewContinueGuard | None = None,
    ) -> MemoryReviewRunResult:
        """Run provider work under a lane lease and optional atomic commit.

        ``call`` may be synchronous or asynchronous.  It must only obtain and
        validate a provider result; durable mutation belongs in the synchronous
        ``commit`` callback.  This separation is what makes late provider
        completion harmless after a watchdog or cancellation ends the run.
        """

        normalized_lane = str(lane or "").strip().casefold()
        if normalized_lane not in _LANES:
            raise ValueError("lane must be 'background' or 'interactive'")
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise ValueError("token must not be empty")
        if not callable(call):
            raise TypeError("call must be callable")
        if commit is not None and not callable(commit):
            raise TypeError("commit must be callable")
        if on_run_state is not None and not callable(on_run_state):
            raise TypeError("on_run_state must be callable")
        if continue_guard is not None and not callable(continue_guard):
            raise TypeError("continue_guard must be callable")

        async def emit(
            phase: str,
            *,
            failure_class: str = "",
            attempt: int = 0,
        ) -> None:
            safe_phase = phase if phase in _PHASES else "failed"
            safe_failure = failure_class if failure_class in _FAILURE_CLASSES else "unknown"
            safe_attempt = max(0, min(TOTAL_PROVIDER_ATTEMPTS, int(attempt)))
            if on_run_state is not None and safe_phase != "completed":
                value = on_run_state(
                    {
                        "phase": safe_phase,
                        "failureClass": safe_failure,
                        "attempt": safe_attempt,
                    }
                )
                if inspect.isawaitable(value):
                    await value
            await self._emit_state(
                safe_phase,
                failure_class=safe_failure,
                attempt=safe_attempt,
            )

        lease_result = self._lane_budget.acquire_result(normalized_lane, normalized_token)
        if lease_result != "acquired":
            failure_class = "duplicate" if lease_result == "duplicate" else "capacity"
            await emit("lane", failure_class=failure_class)
            return MemoryReviewRunResult(
                status=lease_result,
                failure_class=failure_class,
            )

        generation: int | None = None
        deferred_release = False
        attempts = 0
        try:
            generation, activation_failure = self._activate(normalized_lane, normalized_token)
            if generation is None:
                await emit("lane", failure_class=activation_failure)
                return MemoryReviewRunResult(
                    status=activation_failure,
                    failure_class=activation_failure,
                )

            if continue_guard is not None and not continue_guard():
                await emit("cancelled", failure_class="cancelled")
                return MemoryReviewRunResult(
                    status="cancelled",
                    failure_class="cancelled",
                )

            await emit("preflight")
            preflight = await asyncio.to_thread(self._preflight.check, provider, base_url)
            if preflight.required and not preflight.reachable:
                await emit(
                    "failed",
                    failure_class="provider_unreachable",
                )
                return MemoryReviewRunResult(
                    status="provider_unreachable",
                    failure_class="provider_unreachable",
                )

            while attempts < TOTAL_PROVIDER_ATTEMPTS:
                if continue_guard is not None and not continue_guard():
                    await emit("cancelled", failure_class="cancelled", attempt=attempts)
                    return MemoryReviewRunResult(
                        status="cancelled",
                        attempts=attempts,
                        failure_class="cancelled",
                    )
                attempts += 1
                await emit("provider_call", attempt=attempts)
                worker = asyncio.create_task(self._invoke_provider(call))
                try:
                    output = await asyncio.wait_for(
                        asyncio.shield(worker),
                        timeout=self._provider_timeout(),
                    )
                except asyncio.CancelledError:
                    deferred_release = self._defer_release_until_worker_exit(
                        normalized_token,
                        generation,
                        worker,
                    )
                    raise
                except TimeoutError as exc:
                    decision = classify_provider_failure(exc)
                    deferred_release = self._defer_release_until_worker_exit(
                        normalized_token,
                        generation,
                        worker,
                    )
                    if deferred_release:
                        # A watchdog timeout leaves a real provider worker in
                        # flight.  End this durable attempt as retryable, but
                        # do not overlap another attempt or release capacity
                        # until that worker has actually exited.
                        await emit(
                            "failed",
                            failure_class=decision.failure_class,
                            attempt=attempts,
                        )
                        return MemoryReviewRunResult(
                            status="failed",
                            attempts=attempts,
                            failure_class=decision.failure_class,
                            retryable=decision.retryable,
                        )
                except Exception as exc:  # noqa: BLE001 - converted to a bounded class.
                    decision = classify_provider_failure(exc)
                else:
                    if not self._is_active(normalized_token, generation):
                        await emit(
                            "failed",
                            failure_class="cancelled",
                            attempt=attempts,
                        )
                        return MemoryReviewRunResult(
                            status="cancelled",
                            attempts=attempts,
                            failure_class="cancelled",
                        )
                    if continue_guard is not None and not continue_guard():
                        await emit("cancelled", failure_class="cancelled", attempt=attempts)
                        return MemoryReviewRunResult(
                            status="cancelled",
                            attempts=attempts,
                            failure_class="cancelled",
                        )
                    if commit is not None:
                        await emit("commit", attempt=attempts)
                        commit_worker = asyncio.create_task(self._invoke_commit(commit, output))
                        while True:
                            try:
                                await asyncio.shield(commit_worker)
                                break
                            except asyncio.CancelledError:
                                # The atomic durable transaction owns terminal
                                # status once it starts. Cancellation remains
                                # effective before this boundary, but cannot
                                # race a second terminal write against commit.
                                continue
                            except Exception as exc:  # noqa: BLE001 - do not expose persisted content.
                                failure_class = (
                                    "capacity"
                                    if isinstance(exc, MemoryReviewCommitDeferred)
                                    else "commit"
                                )
                                await emit(
                                    "failed",
                                    failure_class=failure_class,
                                    attempt=attempts,
                                )
                                return MemoryReviewRunResult(
                                    status="failed",
                                    attempts=attempts,
                                    failure_class=failure_class,
                                )
                    await emit("completed", attempt=attempts)
                    return MemoryReviewRunResult(
                        status="completed",
                        attempts=attempts,
                        output=output,
                        committed=commit is not None,
                    )

                if not decision.retryable or attempts >= TOTAL_PROVIDER_ATTEMPTS:
                    await emit(
                        "failed",
                        failure_class=decision.failure_class,
                        attempt=attempts,
                    )
                    return MemoryReviewRunResult(
                        status="failed",
                        attempts=attempts,
                        failure_class=decision.failure_class,
                        retryable=decision.retryable,
                    )

                await emit(
                    "retry",
                    failure_class=decision.failure_class,
                    attempt=attempts,
                )
                await self._sleep(float(retry_backoff_seconds(attempts)))

            raise AssertionError("provider attempt loop exceeded its bound")
        except asyncio.CancelledError:
            if generation is not None and not deferred_release:
                self._deactivate(normalized_token, generation)
            await emit("cancelled", failure_class="cancelled", attempt=attempts)
            raise
        finally:
            if generation is not None and not deferred_release:
                self._deactivate(normalized_token, generation)
            if not deferred_release:
                self._lane_budget.release(normalized_token)

    def snapshot(self) -> dict[str, int | bool]:
        """Return aggregate state without durable tokens or provider content."""

        with self._state_lock:
            return {
                "active": len(self._active_generations),
                "backgroundActive": bool(self._background_token),
                "drainingWorkers": len(self._draining_workers),
            }

    def _activate(self, lane: str, token: str) -> tuple[int | None, str]:
        with self._state_lock:
            if self._draining_workers:
                return None, "capacity"
            if token in self._active_generations:
                return None, "duplicate"
            if lane == "background" and self._background_token:
                return None, "duplicate"
            self._next_generation += 1
            generation = self._next_generation
            self._active_generations[token] = generation
            if lane == "background":
                self._background_token = token
            return generation, ""

    def _deactivate(self, token: str, generation: int) -> bool:
        with self._state_lock:
            if self._active_generations.get(token) != generation:
                return False
            self._active_generations.pop(token, None)
            if self._background_token == token:
                self._background_token = ""
            return True

    def _is_active(self, token: str, generation: int) -> bool:
        with self._state_lock:
            return self._active_generations.get(token) == generation

    def _provider_timeout(self) -> float:
        if self._provider_timeout_seconds is not None:
            return self._provider_timeout_seconds
        return float(PHASE_TIMEOUT_SECONDS.get("provider_call", 300))

    @staticmethod
    async def _invoke_provider(call: MemoryReviewCall) -> Any:
        if inspect.iscoroutinefunction(call):
            return await call()
        value = await asyncio.to_thread(call)
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    async def _invoke_commit(commit: MemoryReviewCommit, output: Any) -> Any:
        if inspect.iscoroutinefunction(commit):
            raise TypeError("commit callback must be synchronous")
        value = await asyncio.to_thread(commit, output)
        if inspect.isawaitable(value):
            close = getattr(value, "close", None)
            if callable(close):
                close()
            raise TypeError("commit callback must be synchronous")
        return value

    def _defer_release_until_worker_exit(
        self,
        token: str,
        generation: int,
        worker: asyncio.Task[Any],
    ) -> bool:
        """Keep the generation and lane leased while a late worker exists."""

        if worker.done():
            _consume_worker_result(worker)
            return False
        with self._state_lock:
            self._draining_workers.add(worker)

        def finish(task: asyncio.Task[Any]) -> None:
            _consume_worker_result(task)
            with self._state_lock:
                self._draining_workers.discard(task)
            if self._deactivate(token, generation):
                self._lane_budget.release(token)

        worker.add_done_callback(finish)
        return True

    async def _emit_state(
        self,
        phase: str,
        *,
        failure_class: str = "",
        attempt: int = 0,
    ) -> None:
        callback = self._on_state
        if callback is None:
            return
        safe_phase = phase if phase in _PHASES else "failed"
        safe_failure = failure_class if failure_class in _FAILURE_CLASSES else "unknown"
        signal = {
            "phase": safe_phase,
            "failureClass": safe_failure,
            "attempt": max(0, min(TOTAL_PROVIDER_ATTEMPTS, int(attempt))),
        }
        try:
            value = callback(signal)
            if inspect.isawaitable(value):
                await value
        except Exception:
            return


def _consume_worker_result(worker: asyncio.Task[Any]) -> None:
    try:
        worker.result()
    except (asyncio.CancelledError, Exception):
        return


__all__ = [
    "MemoryReviewCommitDeferred",
    "MemoryReviewIdleGate",
    "MemoryReviewRunResult",
    "MemoryReviewRuntimeCoordinator",
]
