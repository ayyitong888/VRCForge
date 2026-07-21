import asyncio
import threading
import unittest
from unittest.mock import patch

from background_goal_delivery import BackgroundGoalDeliveryCoordinator, BackgroundGoalDeliveryError
from background_goal_runtime import ProviderPreflightCache, RuntimeLaneBudget


class FakeGateway:
    def __init__(
        self,
        runtime_result=None,
        runtime_error=None,
        block_event=None,
        begin_block_event=None,
        complete_block_event=None,
    ) -> None:
        self.runtime_result = runtime_result or {"ok": True, "plan": {"nextStep": "done"}}
        self.runtime_error = runtime_error
        self.block_event = block_event
        self.begin_block_event = begin_block_event
        self.complete_block_event = complete_block_event
        self.calls: list[tuple] = []

    def begin_agent_goal_delivery(self, delivery_id, params):
        self.calls.append(("begin", delivery_id, params))
        if self.begin_block_event is not None:
            self.begin_block_event.wait(timeout=2)
        return {"cached": False, "response": None}

    def record_agent_goal_delivery_phase(self, delivery_id, phase):
        self.calls.append(("phase", delivery_id, phase))
        return {"delivery": {"deliveryId": delivery_id, "phase": phase}}

    def mark_agent_goal_approval_phase(self, approval_id, phase):
        self.calls.append(("approval_phase", approval_id, phase))
        return {"delivery": {"approvalId": approval_id, "phase": phase, "status": "applying"}}

    def runtime_message(self, params, *, agent_name):
        self.calls.append(("runtime", params, agent_name))
        if self.block_event is not None:
            self.block_event.wait(timeout=2)
        if self.runtime_error is not None:
            raise self.runtime_error
        return self.runtime_result

    def complete_agent_goal_delivery(self, delivery_id, response, *, context_usage=None):
        self.calls.append(("complete", delivery_id, response, context_usage))
        if self.complete_block_event is not None:
            self.complete_block_event.wait(timeout=2)
        return {"delivery": {"deliveryId": delivery_id, "status": "completed"}}

    def fail_agent_goal_delivery(self, delivery_id, error, **kwargs):
        self.calls.append(("fail", delivery_id, error, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "failed", **kwargs}}

    def skip_unreachable_agent_goal_provider(self, delivery_id, **kwargs):
        self.calls.append(("skip", delivery_id, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "skipped"}}

    def defer_agent_goal_delivery_capacity(self, delivery_id):
        self.calls.append(("capacity", delivery_id))
        return {"delivery": {"deliveryId": delivery_id, "status": "interrupted"}}

    def block_agent_goal_delivery(self, delivery_id, **kwargs):
        self.calls.append(("block", delivery_id, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "blocked", "blockedKind": kwargs["kind"]}}

    def deny_agent_goal_delivery(self, delivery_id, **kwargs):
        self.calls.append(("deny", delivery_id, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "denied"}}

    def park_agent_goal_delivery(self, delivery_id, **kwargs):
        self.calls.append(("park", delivery_id, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "parked", **kwargs}}

    def request_runtime_cancel(self, params):
        self.calls.append(("cancel", params))
        return {"ok": True}

    def drain_agent_goal_delivery(self, delivery_id, **kwargs):
        self.calls.append(("draining", delivery_id, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "draining"}}

    def finish_agent_goal_delivery_drain(self, delivery_id, **kwargs):
        self.calls.append(("drained", delivery_id, kwargs))
        return {"delivery": {"deliveryId": delivery_id, "status": "failed"}}

    def restore_agent_goal_approval_wait(self, approval_id):
        self.calls.append(("approval_wait_restored", approval_id))
        return {"delivery": {"approvalId": approval_id, "status": "blocked"}}


def execute(coordinator, **overrides):
    params = {
        "delivery_id": "delivery-1",
        "begin_params": {"clientTurnId": "turn-1"},
        "runtime_params": {"session_id": "session-1", "clientTurnId": "turn-1"},
        "agent_name": "test-agent",
        "provider": "remote",
        "base_url": "https://example.invalid",
    }
    params.update(overrides)
    return asyncio.run(coordinator.execute(**params))


class BackgroundGoalDeliveryCoordinatorTests(unittest.TestCase):
    def coordinator(self, gateway, *, probe=lambda _provider, _url: True):
        states = []

        async def state_change(state):
            states.append(state)

        lanes = RuntimeLaneBudget()
        coordinator = BackgroundGoalDeliveryCoordinator(
            gateway=gateway,
            lane_budget=lanes,
            preflight=ProviderPreflightCache(probe),
            on_state_change=state_change,
        )
        return coordinator, lanes, states

    def test_success_records_phases_usage_and_releases_lane(self):
        gateway = FakeGateway(
            runtime_result={
                "ok": True,
                "plan": {"nextStep": "done"},
                "contextUsage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
                "contextCompaction": {"summary": "must not persist"},
            }
        )
        coordinator, lanes, states = self.coordinator(gateway)

        payload = execute(coordinator)

        self.assertEqual(payload["goalDeliveryId"], "delivery-1")
        self.assertEqual([call[2] for call in gateway.calls if call[0] == "phase"], ["project_lock", "provider_call", "deliver"])
        completion = next(call for call in gateway.calls if call[0] == "complete")
        self.assertNotIn("contextCompaction", completion[2])
        self.assertEqual(completion[3]["totalTokens"], 12)
        self.assertEqual(completion[3]["costUnavailableReason"], "pricing_not_configured")
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertEqual(states[-1]["delivery"]["status"], "completed")

    def test_unreachable_local_provider_skips_without_starting(self):
        gateway = FakeGateway()
        coordinator, lanes, _states = self.coordinator(gateway, probe=lambda _provider, _url: False)

        payload = execute(coordinator, provider="local", base_url="http://127.0.0.1:11434")

        self.assertFalse(payload["ok"])
        self.assertTrue(payload["backgroundGoalSkipped"])
        self.assertEqual(payload["status"], "provider_unreachable")
        self.assertTrue(any(call[0] == "skip" for call in gateway.calls))
        self.assertFalse(any(call[0] == "begin" for call in gateway.calls))
        self.assertEqual(lanes.snapshot()["total"], 0)

    def test_full_background_lane_defers_without_consuming_the_delivery(self):
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)
        self.assertTrue(lanes.acquire("background", "occupied-1"))
        self.assertTrue(lanes.acquire("background", "occupied-2"))

        deferred = execute(coordinator)

        self.assertTrue(deferred["backgroundGoalDeferred"])
        self.assertEqual(deferred["status"], "background_capacity")
        self.assertFalse(any(call[0] == "begin" for call in gateway.calls))
        self.assertEqual(states[-1]["delivery"]["status"], "interrupted")
        lanes.release("occupied-1")
        lanes.release("occupied-2")

        completed = execute(coordinator)
        self.assertTrue(completed["ok"])
        self.assertTrue(any(call[0] == "begin" for call in gateway.calls))
        self.assertEqual(lanes.snapshot()["total"], 0)

    def test_duplicate_delivery_is_rejected_without_deferring_the_running_owner(self):
        entered_worker = threading.Event()
        release_worker = threading.Event()
        gateway = FakeGateway(block_event=release_worker)
        original_runtime = gateway.runtime_message

        def runtime(params, *, agent_name):
            entered_worker.set()
            return original_runtime(params, agent_name=agent_name)

        gateway.runtime_message = runtime
        coordinator, lanes, _states = self.coordinator(gateway)

        async def scenario():
            first = asyncio.create_task(
                coordinator.execute(
                    delivery_id="delivery-duplicate",
                    begin_params={"clientTurnId": "turn-duplicate"},
                    runtime_params={"session_id": "session-duplicate", "clientTurnId": "turn-duplicate"},
                    agent_name="test-agent",
                    provider="remote",
                    base_url="https://example.invalid",
                )
            )
            for _ in range(100):
                if entered_worker.is_set():
                    break
                await asyncio.sleep(0.01)
            with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                await coordinator.execute(
                    delivery_id="delivery-duplicate",
                    begin_params={"clientTurnId": "turn-duplicate"},
                    runtime_params={"session_id": "session-duplicate", "clientTurnId": "turn-duplicate"},
                    agent_name="test-agent",
                    provider="remote",
                    base_url="https://example.invalid",
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertFalse(any(call[0] == "capacity" for call in gateway.calls))
            release_worker.set()
            await first

        asyncio.run(scenario())
        self.assertEqual(sum(1 for call in gateway.calls if call[0] == "begin"), 1)
        self.assertEqual(sum(1 for call in gateway.calls if call[0] == "complete"), 1)
        self.assertEqual(lanes.snapshot()["total"], 0)

    def test_provider_network_error_is_retryable_and_not_green(self):
        gateway = FakeGateway(runtime_error=ConnectionError("offline"))
        coordinator, lanes, states = self.coordinator(gateway)

        with self.assertRaises(BackgroundGoalDeliveryError):
            execute(coordinator)

        failure = next(call for call in gateway.calls if call[0] == "fail")
        self.assertEqual(failure[3]["failure_class"], "network")
        self.assertTrue(failure[3]["retryable"])
        self.assertFalse(any(call[0] == "complete" for call in gateway.calls))
        self.assertEqual(states[-1]["delivery"]["status"], "failed")
        self.assertEqual(lanes.snapshot()["total"], 0)

    def test_incomplete_plan_terminals_are_parked_and_done_alone_completes(self):
        for next_step, expected_label in (
            ("context_compaction_required", "context_compaction_required"),
            ("await_user_instruction", "await_user_instruction"),
            ("paused", "paused"),
        ):
            with self.subTest(next_step=next_step):
                gateway = FakeGateway(runtime_result={"ok": True, "plan": {"nextStep": next_step}})
                coordinator, lanes, states = self.coordinator(gateway)
                execute(coordinator)
                parked = next(call for call in gateway.calls if call[0] == "park")
                self.assertEqual(parked[2]["reason"], expected_label)
                self.assertFalse(any(call[0] == "complete" for call in gateway.calls))
                self.assertEqual(states[-1]["delivery"]["status"], "parked")
                self.assertEqual(lanes.snapshot()["total"], 0)

        completed_gateway = FakeGateway(runtime_result={"ok": True, "plan": {"nextStep": "done"}})
        completed_coordinator, _lanes, _states = self.coordinator(completed_gateway)
        execute(completed_coordinator)
        self.assertTrue(any(call[0] == "complete" for call in completed_gateway.calls))
        self.assertFalse(any(call[0] == "park" for call in completed_gateway.calls))

    def test_approval_and_question_responses_block_instead_of_complete(self):
        for result, kind, reference in (
            ({"ok": True, "approvalId": "approval-1", "plan": {}}, "approval", "approval-1"),
            (
                {
                    "ok": True,
                    "plan": {},
                    "shell": {"status": "pending_approval", "approval_id": "approval-shell-1"},
                },
                "approval",
                "approval-shell-1",
            ),
            (
                {
                    "ok": True,
                    "plan": {},
                    "skill": {"result": {"question": {"questionId": "question-1"}}},
                },
                "question",
                "question-1",
            ),
        ):
            with self.subTest(kind=kind):
                gateway = FakeGateway(runtime_result=result)
                coordinator, lanes, _states = self.coordinator(gateway)
                execute(coordinator)
                blocked = next(call for call in gateway.calls if call[0] == "block")
                self.assertEqual(blocked[2]["kind"], kind)
                self.assertEqual(blocked[2]["reference"], reference)
                self.assertFalse(any(call[0] == "complete" for call in gateway.calls))
                self.assertEqual(lanes.snapshot()["total"], 0)

    def test_blocked_or_denied_tool_outcome_without_reference_is_never_completed(self):
        for result, expected_label, expected_call, expected_status in (
            ({"ok": True, "skill": {"status": "blocked"}, "plan": {}}, "runtime_blocked", "fail", "failed"),
            (
                {"ok": True, "write": {"result": {"status": "rejected"}}, "plan": {}},
                "runtime_rejected",
                "deny",
                "denied",
            ),
            (
                {
                    "ok": True,
                    "skill": {"status": "blocked", "failureClass": "permission_denied"},
                    "plan": {},
                },
                "runtime_permission_denied",
                "deny",
                "denied",
            ),
            (
                {
                    "ok": True,
                    "skill": {"status": "blocked", "error": "missing dependency"},
                    "plan": {},
                },
                "runtime_unavailable",
                "fail",
                "failed",
            ),
        ):
            with self.subTest(label=expected_label):
                gateway = FakeGateway(runtime_result=result)
                coordinator, lanes, states = self.coordinator(gateway)
                execute(coordinator)
                terminal = next(call for call in gateway.calls if call[0] == expected_call)
                if expected_call == "deny":
                    self.assertEqual(terminal[2]["reason"], expected_label)
                else:
                    self.assertEqual(terminal[3]["failure_label"], expected_label)
                self.assertFalse(any(call[0] == "complete" for call in gateway.calls))
                self.assertEqual(states[-1]["delivery"]["status"], expected_status)
                self.assertEqual(lanes.snapshot()["total"], 0)

    def test_unavailable_or_ok_false_tool_outcome_is_terminal_failure(self):
        for result, expected_label in (
            ({"ok": True, "skill": {"status": "unavailable"}, "plan": {}}, "runtime_unavailable"),
            ({"ok": True, "steps": [{"result": {"ok": False}}], "plan": {}}, "runtime_ok_false"),
        ):
            with self.subTest(label=expected_label):
                gateway = FakeGateway(runtime_result=result)
                coordinator, lanes, states = self.coordinator(gateway)
                execute(coordinator)
                failure = next(call for call in gateway.calls if call[0] == "fail")
                self.assertEqual(failure[3]["failure_label"], expected_label)
                self.assertFalse(failure[3]["retryable"])
                self.assertFalse(any(call[0] == "complete" for call in gateway.calls))
                self.assertEqual(states[-1]["delivery"]["status"], "failed")
                self.assertEqual(lanes.snapshot()["total"], 0)

    def test_timeout_keeps_lane_until_worker_has_drained(self):
        release_worker = threading.Event()
        gateway = FakeGateway(block_event=release_worker)
        coordinator, lanes, states = self.coordinator(gateway)

        async def scenario():
            with patch("background_goal_delivery.PHASE_TIMEOUT_SECONDS", {"provider_call": 0.01}):
                with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                    await coordinator.execute(
                        delivery_id="delivery-timeout",
                        begin_params={"clientTurnId": "turn-timeout"},
                        runtime_params={"session_id": "session-timeout", "clientTurnId": "turn-timeout"},
                        agent_name="test-agent",
                        provider="remote",
                        base_url="https://example.invalid",
                    )
                self.assertEqual(raised.exception.status_code, 504)
                self.assertEqual(lanes.snapshot()["background"], 1)
                release_worker.set()
                for _ in range(100):
                    if coordinator.drain_task_count == 0:
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(coordinator.drain_task_count, 0)
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertTrue(any(call[0] == "cancel" for call in gateway.calls))
        self.assertTrue(any(call[0] == "draining" for call in gateway.calls))
        self.assertTrue(any(call[0] == "drained" for call in gateway.calls))
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_project_lock_timeout_keeps_lane_until_begin_worker_exits(self):
        release_worker = threading.Event()
        gateway = FakeGateway(begin_block_event=release_worker)
        coordinator, lanes, _states = self.coordinator(gateway)

        async def scenario():
            with patch("background_goal_delivery.PHASE_TIMEOUT_SECONDS", {"project_lock": 0.01}):
                with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                    await coordinator.execute(
                        delivery_id="delivery-project-timeout",
                        begin_params={"clientTurnId": "turn-project-timeout"},
                        runtime_params={"session_id": "session-project-timeout"},
                        agent_name="test-agent",
                        provider="remote",
                        base_url="https://example.invalid",
                    )
                self.assertEqual(raised.exception.status_code, 504)
                self.assertEqual(lanes.snapshot()["background"], 1)
                release_worker.set()
                for _ in range(100):
                    if coordinator.drain_task_count == 0:
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertTrue(any(call[0] == "drained" for call in gateway.calls))

    def test_deliver_timeout_keeps_lane_until_persistence_worker_exits(self):
        release_worker = threading.Event()
        gateway = FakeGateway(complete_block_event=release_worker)
        coordinator, lanes, _states = self.coordinator(gateway)

        async def scenario():
            with patch("background_goal_delivery.PHASE_TIMEOUT_SECONDS", {"deliver": 0.01}):
                with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                    await coordinator.execute(
                        delivery_id="delivery-deliver-timeout",
                        begin_params={"clientTurnId": "turn-deliver-timeout"},
                        runtime_params={"session_id": "session-deliver-timeout"},
                        agent_name="test-agent",
                        provider="remote",
                        base_url="https://example.invalid",
                    )
                self.assertEqual(raised.exception.status_code, 504)
                self.assertEqual(lanes.snapshot()["background"], 1)
                release_worker.set()
                for _ in range(100):
                    if coordinator.drain_task_count == 0:
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertTrue(any(call[0] == "drained" for call in gateway.calls))

    def test_request_cancellation_keeps_lane_until_provider_worker_has_drained(self):
        release_worker = threading.Event()
        gateway = FakeGateway(block_event=release_worker)
        coordinator, lanes, states = self.coordinator(gateway)

        async def scenario():
            task = asyncio.create_task(
                coordinator.execute(
                    delivery_id="delivery-cancelled",
                    begin_params={"clientTurnId": "turn-cancelled"},
                    runtime_params={"session_id": "session-cancelled", "clientTurnId": "turn-cancelled"},
                    agent_name="test-agent",
                    provider="remote",
                    base_url="https://example.invalid",
                )
            )
            for _ in range(100):
                if any(call[0] == "runtime" for call in gateway.calls):
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(lanes.snapshot()["background"], 1)
            self.assertEqual(coordinator.drain_task_count, 1)
            release_worker.set()
            for _ in range(100):
                if coordinator.drain_task_count == 0:
                    break
                await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertTrue(any(call[0] == "cancel" for call in gateway.calls))
        drained = next(call for call in gateway.calls if call[0] == "drained")
        self.assertEqual(drained[2]["failure_class"], "cancelled")
        self.assertFalse(drained[2]["retryable"])
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_preflight_cancellation_closes_claimed_delivery(self):
        entered_probe = threading.Event()
        release_probe = threading.Event()

        def probe(_provider, _url):
            entered_probe.set()
            release_probe.wait(timeout=2)
            return True

        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway, probe=probe)

        async def scenario():
            task = asyncio.create_task(
                coordinator.execute(
                    delivery_id="delivery-preflight-cancelled",
                    begin_params={"clientTurnId": "turn-preflight-cancelled"},
                    runtime_params={"session_id": "session-preflight-cancelled"},
                    agent_name="test-agent",
                    provider="local",
                    base_url="http://127.0.0.1:11434",
                )
            )
            for _ in range(100):
                if entered_probe.is_set():
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            self.assertEqual(lanes.snapshot()["background"], 1)
            release_probe.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertTrue(any(call[0] == "draining" for call in gateway.calls))
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_approved_action_timeout_keeps_lane_until_worker_has_drained(self):
        release_worker = threading.Event()
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        def execute_approved(_approved):
            release_worker.wait(timeout=2)
            return {"ok": True, "status": "applied"}

        async def scenario():
            with patch("background_goal_delivery.PHASE_TIMEOUT_SECONDS", {"apply": 0.01}):
                with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                    await coordinator.execute_approved_action(
                        delivery_id="delivery-apply-timeout",
                        approve_operation=lambda: {"ok": True, "approval": {"id": "approval-1"}},
                        execute_operation=execute_approved,
                    )
                self.assertEqual(raised.exception.status_code, 504)
                self.assertEqual(lanes.snapshot()["background"], 1)
                release_worker.set()
                for _ in range(100):
                    if coordinator.drain_task_count == 0:
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(coordinator.drain_task_count, 0)
        self.assertEqual(lanes.snapshot()["total"], 0)
        drained = next(call for call in gateway.calls if call[0] == "drained")
        self.assertFalse(drained[2]["retryable"])
        self.assertEqual(drained[2]["failure_class"], "apply_failed")
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_approval_transition_timeout_keeps_lane_until_worker_has_drained(self):
        release_worker = threading.Event()
        entered_worker = threading.Event()
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        def approve():
            entered_worker.set()
            release_worker.wait(timeout=2)
            return {"ok": True, "approval": {"id": "approval-transition-timeout"}}

        async def scenario():
            with patch("background_goal_delivery.PHASE_TIMEOUT_SECONDS", {"apply": 0.01}):
                with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                    await coordinator.execute_approved_action(
                        delivery_id="delivery-approval-transition-timeout",
                        approval_id="approval-transition-timeout",
                        approve_operation=approve,
                        execute_operation=lambda _approved: {"ok": True, "status": "applied"},
                    )
                self.assertEqual(raised.exception.status_code, 504)
                self.assertTrue(entered_worker.is_set())
                self.assertEqual(lanes.snapshot()["background"], 1)
                self.assertEqual(coordinator.drain_task_count, 1)
                release_worker.set()
                for _ in range(100):
                    if coordinator.drain_task_count == 0:
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        drained = next(call for call in gateway.calls if call[0] == "drained")
        self.assertEqual(drained[2]["failure_class"], "apply_failed")
        self.assertFalse(drained[2]["retryable"])
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_approval_transition_exception_restores_wait_and_releases_lane(self):
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        async def scenario():
            with self.assertRaisesRegex(RuntimeError, "scope changed"):
                await coordinator.execute_approved_action(
                    delivery_id="delivery-approval-transition-error",
                    approval_id="approval-transition-error",
                    approve_operation=lambda: (_ for _ in ()).throw(RuntimeError("scope changed")),
                    execute_operation=lambda _approved: {"ok": True, "status": "applied"},
                )

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertIn(("approval_wait_restored", "approval-transition-error"), gateway.calls)
        self.assertEqual(states[-1]["delivery"]["status"], "blocked")

    def test_non_success_approval_transition_restores_wait_and_releases_lane(self):
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        async def scenario():
            return await coordinator.execute_approved_action(
                delivery_id="delivery-approval-not-ready",
                approval_id="approval-not-ready",
                approve_operation=lambda: {
                    "ok": False,
                    "approval": {"id": "approval-not-ready", "status": "pending"},
                },
                execute_operation=lambda _approved: {"ok": True, "status": "applied"},
            )

        approved, execution = asyncio.run(scenario())
        self.assertFalse(approved["ok"])
        self.assertIsNone(execution)
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertIn(("approval_wait_restored", "approval-not-ready"), gateway.calls)
        self.assertEqual(states[-1]["delivery"]["status"], "blocked")

    def test_approval_transition_cancellation_does_not_wait_for_worker_exit(self):
        release_worker = threading.Event()
        entered_worker = threading.Event()
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        def approve():
            entered_worker.set()
            release_worker.wait(timeout=2)
            return {"ok": True, "approval": {"id": "approval-transition-cancelled"}}

        async def scenario():
            task = asyncio.create_task(
                coordinator.execute_approved_action(
                    delivery_id="delivery-approval-transition-cancelled",
                    approval_id="approval-transition-cancelled",
                    approve_operation=approve,
                    execute_operation=lambda _approved: {"ok": True, "status": "applied"},
                )
            )
            for _ in range(100):
                if entered_worker.is_set():
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.5)
            self.assertEqual(lanes.snapshot()["background"], 1)
            self.assertEqual(coordinator.drain_task_count, 1)
            release_worker.set()
            for _ in range(100):
                if coordinator.drain_task_count == 0:
                    break
                await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        drained = next(call for call in gateway.calls if call[0] == "drained")
        self.assertEqual(drained[2]["failure_class"], "cancelled")
        self.assertFalse(drained[2]["retryable"])
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_apply_late_success_after_live_watchdog_finishes_as_failure(self):
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        async def scenario():
            with self.assertRaises(BackgroundGoalDeliveryError) as raised:
                await coordinator.execute_approved_action(
                    delivery_id="delivery-apply-live-watchdog",
                    approve_operation=lambda: {"ok": True, "approval": {"id": "approval-live"}},
                    execute_operation=lambda _approved: {
                        "ok": True,
                        "status": "applied",
                        "goalDelivery": {"delivery": {"status": "draining"}},
                    },
                )
            self.assertEqual(raised.exception.status_code, 504)

        asyncio.run(scenario())
        drained = next(call for call in gateway.calls if call[0] == "drained")
        self.assertFalse(drained[2]["retryable"])
        self.assertEqual(drained[2]["failure_class"], "apply_failed")
        self.assertEqual(lanes.snapshot()["total"], 0)
        self.assertEqual(states[-1]["delivery"]["status"], "failed")

    def test_approved_action_cancellation_keeps_lane_until_worker_has_drained(self):
        release_worker = threading.Event()
        entered_worker = threading.Event()
        gateway = FakeGateway()
        coordinator, lanes, states = self.coordinator(gateway)

        def execute_approved(_approved):
            entered_worker.set()
            release_worker.wait(timeout=2)
            return {"ok": True, "status": "applied"}

        async def scenario():
            task = asyncio.create_task(
                coordinator.execute_approved_action(
                    delivery_id="delivery-apply-cancelled",
                    approve_operation=lambda: {"ok": True, "approval": {"id": "approval-cancelled"}},
                    execute_operation=execute_approved,
                )
            )
            for _ in range(100):
                if entered_worker.is_set():
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(lanes.snapshot()["background"], 1)
            self.assertEqual(coordinator.drain_task_count, 1)
            release_worker.set()
            for _ in range(100):
                if coordinator.drain_task_count == 0:
                    break
                await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(lanes.snapshot()["total"], 0)
        drained = next(call for call in gateway.calls if call[0] == "drained")
        self.assertEqual(drained[2]["failure_class"], "cancelled")
        self.assertFalse(drained[2]["retryable"])
        self.assertEqual(states[-1]["delivery"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
