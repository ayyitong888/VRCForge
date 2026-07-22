import asyncio
import json
import threading

import pytest

from background_goal_runtime import ProviderPreflightCache, RuntimeLaneBudget
from memory_review_runtime import MemoryReviewIdleGate, MemoryReviewRuntimeCoordinator


async def _no_wait(_seconds: float) -> None:
    return None


def _coordinator(
    *,
    lane_budget: RuntimeLaneBudget | None = None,
    probe=lambda _provider, _url: True,
    on_state=None,
    sleep=_no_wait,
    timeout: float = 0.2,
) -> MemoryReviewRuntimeCoordinator:
    return MemoryReviewRuntimeCoordinator(
        lane_budget=lane_budget or RuntimeLaneBudget(),
        preflight=ProviderPreflightCache(probe),
        on_state=on_state,
        sleep=sleep,
        provider_timeout_seconds=timeout,
    )


def test_shared_lane_capacity_and_duplicate_do_not_call_provider():
    async def scenario():
        budget = RuntimeLaneBudget(total_limit=1, background_limit=1)
        assert budget.acquire("interactive", "existing-runtime") is True
        calls = 0

        def call():
            nonlocal calls
            calls += 1
            return {"unused": True}

        coordinator = _coordinator(lane_budget=budget)
        capacity = await coordinator.run(
            lane="background",
            token="memory-capacity",
            provider="remote",
            base_url=None,
            call=call,
        )
        assert capacity.status == "capacity"
        assert capacity.attempts == 0
        assert calls == 0

        budget.release("existing-runtime")
        assert budget.acquire("background", "memory-duplicate") is True
        duplicate = await coordinator.run(
            lane="background",
            token="memory-duplicate",
            provider="remote",
            base_url=None,
            call=call,
        )
        assert duplicate.status == "duplicate"
        assert duplicate.attempts == 0
        assert calls == 0

    asyncio.run(scenario())


def test_continue_guard_rechecks_after_provider_and_before_commit() -> None:
    async def scenario() -> None:
        allowed = True
        commits = 0

        def call() -> dict[str, bool]:
            nonlocal allowed
            allowed = False
            return {"validated": True}

        def commit(_result: object) -> None:
            nonlocal commits
            commits += 1

        coordinator = _coordinator()
        result = await coordinator.run(
            lane="background",
            token="guard-after-provider",
            provider="remote",
            base_url=None,
            call=call,
            commit=commit,
            continue_guard=lambda: allowed,
        )
        assert result.status == "cancelled"
        assert result.attempts == 1
        assert commits == 0

    asyncio.run(scenario())


def test_idle_gate_epoch_rejects_activity_between_blocker_and_acquire() -> None:
    gate = MemoryReviewIdleGate()
    blocker_entered = threading.Event()
    activity_done = threading.Event()

    def signal() -> None:
        assert blocker_entered.wait(timeout=2)
        gate.signal_activity("interactive")
        activity_done.set()

    worker = threading.Thread(target=signal)
    worker.start()

    def blocker() -> str:
        blocker_entered.set()
        assert activity_done.wait(timeout=2)
        return ""

    generation = gate.try_acquire(blocker, lambda: None)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert generation is None
    assert gate.snapshot()["active"] is False


def test_unreachable_loopback_preflight_consumes_no_attempt():
    async def scenario():
        calls = 0
        probes = 0

        def probe(_provider, _url):
            nonlocal probes
            probes += 1
            return False

        def call():
            nonlocal calls
            calls += 1
            return "unused"

        coordinator = _coordinator(probe=probe)
        result = await coordinator.run(
            lane="interactive",
            token="preflight",
            provider="local",
            base_url="http://127.0.0.1:11434",
            call=call,
        )
        assert result.status == "provider_unreachable"
        assert result.attempts == 0
        assert result.retryable is False
        assert calls == 0
        assert probes == 1
        assert coordinator.snapshot()["active"] == 0

    asyncio.run(scenario())


def test_retryable_provider_failures_use_shared_attempt_and_backoff_policy():
    async def scenario():
        calls = 0
        delays = []
        commits = []

        async def sleep(seconds):
            delays.append(seconds)

        def call():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("network unavailable")
            if calls == 2:
                error = RuntimeError("service unavailable")
                error.status_code = 503
                raise error
            return {"candidateCount": 1}

        coordinator = _coordinator(sleep=sleep)
        result = await coordinator.run(
            lane="background",
            token="retry-success",
            provider="remote",
            base_url=None,
            call=call,
            commit=commits.append,
        )
        assert result.ok is True
        assert result.attempts == 3
        assert result.committed is True
        assert result.output == {"candidateCount": 1}
        assert commits == [{"candidateCount": 1}]
        assert delays == [60.0, 120.0]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "failure_class"),
    [
        (RuntimeError("401 unauthorized"), "auth"),
        (RuntimeError("402 payment required"), "credit"),
        (RuntimeError("response validation schema failed"), "schema"),
        (RuntimeError("400 invalid request"), "invalid_request"),
    ],
)
def test_non_retryable_provider_failures_stop_after_one_attempt(error, failure_class):
    async def scenario():
        calls = 0

        def call():
            nonlocal calls
            calls += 1
            raise error

        result = await _coordinator().run(
            lane="interactive",
            token=f"one-{failure_class}",
            provider="remote",
            base_url=None,
            call=call,
        )
        assert result.status == "failed"
        assert result.failure_class == failure_class
        assert result.retryable is False
        assert result.attempts == 1
        assert calls == 1

    asyncio.run(scenario())


def test_timed_out_late_provider_results_never_cross_commit_boundary():
    async def scenario():
        budget = RuntimeLaneBudget(total_limit=1, background_limit=1)
        release = threading.Event()
        started = threading.Event()
        calls = 0
        committed = []

        def call():
            nonlocal calls
            calls += 1
            started.set()
            release.wait(timeout=2)
            return {"lateSecret": "must-not-commit"}

        coordinator = _coordinator(lane_budget=budget, timeout=0.01)
        result = await coordinator.run(
            lane="background",
            token="late-result",
            provider="remote",
            base_url=None,
            call=call,
            commit=committed.append,
        )
        assert started.is_set()
        assert result.status == "failed"
        assert result.failure_class == "timeout"
        assert result.retryable is True
        assert result.attempts == 1
        assert calls == 1
        assert committed == []
        assert coordinator.snapshot()["active"] == 1
        assert coordinator.snapshot()["backgroundActive"] is True
        assert budget.snapshot()["total"] == 1

        release.set()
        for _ in range(50):
            if (
                coordinator.snapshot()["drainingWorkers"] == 0
                and budget.snapshot()["total"] == 0
            ):
                break
            await asyncio.sleep(0.01)
        assert coordinator.snapshot()["drainingWorkers"] == 0
        assert coordinator.snapshot()["active"] == 0
        assert coordinator.snapshot()["backgroundActive"] is False
        assert budget.snapshot()["total"] == 0
        assert committed == []

    asyncio.run(scenario())


def test_cancellation_defers_lane_release_until_late_worker_exits():
    async def scenario():
        budget = RuntimeLaneBudget(total_limit=1, background_limit=1)
        release = threading.Event()
        started = threading.Event()
        committed = []

        def call():
            started.set()
            release.wait(timeout=2)
            return "late"

        coordinator = _coordinator(lane_budget=budget, timeout=1)
        task = asyncio.create_task(
            coordinator.run(
                lane="background",
                token="cancelled-run",
                provider="remote",
                base_url=None,
                call=call,
                commit=committed.append,
            )
        )
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.snapshot()["total"] == 1
        assert coordinator.snapshot()["active"] == 1
        assert coordinator.snapshot()["backgroundActive"] is True

        release.set()
        for _ in range(50):
            if budget.snapshot()["total"] == 0:
                break
            await asyncio.sleep(0.01)
        assert budget.snapshot()["total"] == 0
        assert coordinator.snapshot()["active"] == 0
        assert coordinator.snapshot()["backgroundActive"] is False
        assert committed == []

    asyncio.run(scenario())


def test_draining_cancelled_worker_blocks_new_paid_calls_even_with_spare_capacity():
    async def scenario():
        budget = RuntimeLaneBudget(total_limit=5, background_limit=2)
        release = threading.Event()
        started = threading.Event()
        calls = {"first": 0, "second": 0, "third": 0}

        def blocked_call():
            calls["first"] += 1
            started.set()
            release.wait(timeout=2)
            return "late"

        coordinator = _coordinator(lane_budget=budget, timeout=1)
        first = asyncio.create_task(
            coordinator.run(
                lane="interactive",
                token="draining-first",
                provider="remote",
                base_url=None,
                call=blocked_call,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert coordinator.snapshot()["drainingWorkers"] == 1
        assert budget.snapshot()["total"] == 1

        def forbidden_second():
            calls["second"] += 1
            return "must-not-run"

        second = await coordinator.run(
            lane="interactive",
            token="draining-second",
            provider="remote",
            base_url=None,
            call=forbidden_second,
        )
        assert second.status == "capacity"
        assert second.attempts == 0
        assert calls["second"] == 0

        release.set()
        for _ in range(50):
            if coordinator.snapshot()["drainingWorkers"] == 0:
                break
            await asyncio.sleep(0.01)
        assert coordinator.snapshot()["drainingWorkers"] == 0

        def final_call():
            calls["third"] += 1
            return "fresh"

        third = await coordinator.run(
            lane="interactive",
            token="draining-third",
            provider="remote",
            base_url=None,
            call=final_call,
        )
        assert third.ok is True
        assert third.output == "fresh"
        assert calls == {"first": 1, "second": 0, "third": 1}

    asyncio.run(scenario())


def test_background_runs_are_singleflight_even_with_spare_lane_capacity():
    async def scenario():
        budget = RuntimeLaneBudget(total_limit=5, background_limit=2)
        release = threading.Event()
        started = threading.Event()
        calls = 0

        def blocking_call():
            nonlocal calls
            calls += 1
            started.set()
            release.wait(timeout=2)
            return "first"

        coordinator = _coordinator(lane_budget=budget, timeout=1)
        first = asyncio.create_task(
            coordinator.run(
                lane="background",
                token="background-one",
                provider="remote",
                base_url=None,
                call=blocking_call,
            )
        )
        await asyncio.to_thread(started.wait, 1)
        second = await coordinator.run(
            lane="background",
            token="background-two",
            provider="remote",
            base_url=None,
            call=lambda: "must-not-run",
        )
        assert second.status == "duplicate"
        assert second.attempts == 0
        assert calls == 1
        assert budget.snapshot()["background"] == 1

        release.set()
        assert (await first).ok is True
        assert budget.snapshot()["total"] == 0

    asyncio.run(scenario())


def test_state_signals_use_only_bounded_privacy_envelope():
    async def scenario():
        signals = []
        marker = "sensitive-provider-prompt-and-response"

        def call():
            raise RuntimeError(f"schema invalid: {marker}")

        coordinator = _coordinator(on_state=signals.append)
        result = await coordinator.run(
            lane="interactive",
            token="private-run-token",
            provider="remote",
            base_url="https://credential.example.invalid/private",
            call=call,
        )
        assert result.failure_class == "schema"
        assert signals
        assert all(set(signal) == {"phase", "failureClass", "attempt"} for signal in signals)
        serialized = json.dumps(signals, sort_keys=True)
        assert marker not in serialized
        assert "private-run-token" not in serialized
        assert "credential.example.invalid" not in serialized
        assert all(0 <= signal["attempt"] <= 3 for signal in signals)

    asyncio.run(scenario())


def test_run_specific_state_is_persisted_before_public_refresh_signal():
    async def scenario():
        order = []
        durable_signals = []

        async def persist(signal):
            durable_signals.append(dict(signal))
            order.append(("durable", signal["phase"]))

        async def broadcast(signal):
            order.append(("broadcast", signal["phase"]))

        coordinator = _coordinator(on_state=broadcast)
        result = await coordinator.run(
            lane="interactive",
            token="durable-state",
            provider="remote",
            base_url=None,
            call=lambda: {"candidateCount": 1},
            commit=lambda _output: None,
            on_run_state=persist,
        )
        assert result.ok is True
        assert [signal["phase"] for signal in durable_signals] == [
            "preflight",
            "provider_call",
            "commit",
        ]
        assert order[-1] == ("broadcast", "completed")
        for phase in ("preflight", "provider_call", "commit"):
            assert order.index(("durable", phase)) < order.index(("broadcast", phase))

    asyncio.run(scenario())


def test_durable_commit_does_not_block_the_event_loop():
    async def scenario():
        commit_started = threading.Event()
        release_commit = threading.Event()

        def commit(_output):
            commit_started.set()
            release_commit.wait(timeout=2)

        coordinator = _coordinator(timeout=1)
        task = asyncio.create_task(
            coordinator.run(
                lane="interactive",
                token="nonblocking-commit",
                provider="remote",
                base_url=None,
                call=lambda: {"candidateCount": 1},
                commit=commit,
            )
        )
        assert await asyncio.to_thread(commit_started.wait, 1)
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        assert task.done() is False
        release_commit.set()
        assert (await task).ok is True

    asyncio.run(scenario())


def test_cancellation_during_commit_yields_terminal_ownership_to_disk_transaction():
    async def scenario():
        budget = RuntimeLaneBudget(total_limit=1, background_limit=1)
        commit_started = threading.Event()
        release_commit = threading.Event()
        committed = []

        def commit(output):
            commit_started.set()
            release_commit.wait(timeout=2)
            committed.append(output)

        coordinator = _coordinator(lane_budget=budget, timeout=1)
        task = asyncio.create_task(
            coordinator.run(
                lane="background",
                token="cancelled-commit",
                provider="remote",
                base_url=None,
                call=lambda: "validated",
                commit=commit,
            )
        )
        assert await asyncio.to_thread(commit_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert budget.snapshot()["total"] == 1
        assert coordinator.snapshot()["active"] == 1

        release_commit.set()
        result = await task
        assert result.ok is True
        assert result.committed is True
        assert committed == ["validated"]
        assert budget.snapshot()["total"] == 0
        assert coordinator.snapshot()["active"] == 0

    asyncio.run(scenario())
