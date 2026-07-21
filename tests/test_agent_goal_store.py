import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_goal_store import (
    GOAL_DELIVERY_RESULT_SCHEMA,
    GOAL_DELIVERY_RUN_SCHEMA,
    AgentGoalStore,
    AgentGoalStoreError,
)
from background_goal_runtime import retry_backoff_seconds


class AgentGoalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lock = threading.RLock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_store(self, *, runner_instance_id: str | None = None) -> AgentGoalStore:
        def append(path: Path, schema: str, event: dict):
            row = {
                "schema": schema,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                **event,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            return row

        def read(path: Path):
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

        return AgentGoalStore(
            log_path=lambda: self.root / "agent-goals.jsonl",
            result_dir=lambda: self.root / "agent-goal-results",
            append_event=append,
            read_events=read,
            lock=self.lock,
            normalize_path=lambda value: str(Path(value)).lower(),
            runner_instance_id=runner_instance_id,
        )

    def due_time(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    def start_delivery(
        self,
        store: AgentGoalStore,
        *,
        chat_id: str = "chat-a",
        recurring_minutes: int = 0,
    ) -> tuple[dict, dict]:
        params = {"title": f"goal-{chat_id}", "chatId": chat_id, "wakeAt": self.due_time()}
        if recurring_minutes:
            params["wakeEveryMinutes"] = recurring_minutes
        goal = store.create(params)
        _, delivery = store.wake(goal["goalId"])
        started = store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        return goal, started["delivery"]

    def test_scheduled_goal_requires_owner_chat(self) -> None:
        store = self.make_store()
        with self.assertRaisesRegex(AgentGoalStoreError, "owner chatId"):
            store.create({"title": "orphan", "wakeAt": self.due_time()})

    def test_wake_does_not_consume_schedule_and_completion_is_restart_safe(self) -> None:
        store = self.make_store()
        goal = store.create({"title": "durable", "chatId": "chat-a", "wakeAt": self.due_time()})
        original_wake = goal["wakeAt"]

        woken_goal, delivery = store.wake(goal["goalId"], {"chatId": "chat-a"})
        self.assertEqual(woken_goal["wakeAt"], original_wake)
        self.assertEqual(delivery["status"], "claimed")
        started = store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        self.assertFalse(started["cached"])
        response = {"turnId": "turn-result", "text": "finished", "sessionId": "session-a"}
        completed = store.complete_delivery(delivery["deliveryId"], response)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeAt"], "")

        reopened = self.make_store()
        recoverable = reopened.list_recoverable(chat_id="chat-a")
        self.assertEqual(len(recoverable), 1)
        self.assertEqual(recoverable[0]["response"], response)
        acknowledged = reopened.mark_materialized(
            delivery["deliveryId"],
            {"chatId": "chat-a", "expectedRevision": recoverable[0]["revision"]},
        )
        self.assertEqual(acknowledged["status"], "materialized")
        self.assertEqual(reopened.list_recoverable(chat_id="chat-a"), [])

    def test_materialized_completion_recap_is_seen_once_across_restart(self) -> None:
        store = self.make_store()
        _goal, delivery = self.start_delivery(store)
        completed = store.complete_delivery(delivery["deliveryId"], {"turnId": "done"})
        materialized = store.mark_materialized(
            delivery["deliveryId"],
            {"chatId": "chat-a", "expectedRevision": completed["revision"]},
        )
        recap = store.list_catchup(chat_id="chat-a")
        self.assertEqual([(row["status"], row["deliveryId"]) for row in recap], [("materialized", delivery["deliveryId"])])
        store.acknowledge_background_notifications(
            "chat-a",
            [{"deliveryId": delivery["deliveryId"], "expectedRevision": materialized["revision"]}],
        )
        self.assertEqual(store.list_catchup(chat_id="chat-a"), [])
        self.assertEqual(self.make_store().list_catchup(chat_id="chat-a"), [])

    def test_completed_before_materialization_remains_recoverable_and_visible_after_restart(self) -> None:
        store = self.make_store()
        _goal, delivery = self.start_delivery(store)
        store.complete_delivery(delivery["deliveryId"], {"turnId": "durable-before-chat-save"})

        reopened = self.make_store()
        self.assertEqual(
            [(row["status"], row["deliveryId"]) for row in reopened.list_catchup(chat_id="chat-a")],
            [("completed", delivery["deliveryId"])],
        )
        self.assertEqual(
            [row["deliveryId"] for row in reopened.list_recoverable(chat_id="chat-a")],
            [delivery["deliveryId"]],
        )
        reopened_again = self.make_store()
        self.assertEqual(
            [row["deliveryId"] for row in reopened_again.list_catchup(chat_id="chat-a")],
            [delivery["deliveryId"]],
        )

    def test_new_actionable_revision_survives_seen_and_stale_ack(self) -> None:
        store = self.make_store()
        _goal, delivery = self.start_delivery(store)
        blocked_at = datetime.now(timezone.utc)
        blocked = store.block_delivery_for_approval(
            delivery["deliveryId"],
            "approval-revision",
            now=blocked_at,
        )
        store.acknowledge_background_notifications(
            "chat-a",
            [{"deliveryId": delivery["deliveryId"], "expectedRevision": blocked["revision"]}],
        )
        denied = store.deny_by_approval("approval-revision", reason="user rejected")
        store.acknowledge_background_notifications(
            "chat-a",
            [{"deliveryId": delivery["deliveryId"], "expectedRevision": blocked["revision"]}],
        )
        recap = store.list_catchup(chat_id="chat-a")
        self.assertEqual(len(recap), 1)
        self.assertEqual(recap[0]["status"], "denied")
        self.assertEqual(recap[0]["recapRevision"], denied["recapRevision"])

    def test_seen_question_block_reappears_once_when_parked(self) -> None:
        store = self.make_store()
        _goal, delivery = self.start_delivery(store)
        blocked_at = datetime.now(timezone.utc)
        blocked = store.block_delivery_for_question(
            delivery["deliveryId"],
            "question-revision",
            now=blocked_at,
        )
        store.acknowledge_background_notifications(
            "chat-a",
            [{"deliveryId": delivery["deliveryId"], "expectedRevision": blocked["revision"]}],
        )
        parked = store.record_question_reprompt_once(
            delivery["deliveryId"],
            now=blocked_at + timedelta(seconds=1_801),
        )
        recap = store.list_catchup(chat_id="chat-a")
        self.assertEqual([(row["status"], row["recapRevision"]) for row in recap], [("parked", parked["recapRevision"])])
        state = store.background_state("chat-a")
        self.assertEqual(state["totalUnread"], 1)
        self.assertEqual(len(state["unread"]), 1)

    def test_provider_warning_is_aggregated_and_revision_acknowledged(self) -> None:
        store = self.make_store()
        deliveries = [self.start_delivery(store, chat_id=f"chat-{index}")[1] for index in range(2)]
        for delivery in deliveries:
            store.skip_provider_unreachable(
                delivery["deliveryId"],
                provider="local-runtime",
                base_url="http://127.0.0.1:11434",
            )
        warnings = store.project_provider_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["count"], 2)
        self.assertNotIn("baseUrl", warnings[0])
        store.acknowledge_background_notifications(
            "chat-0",
            [{"deliveryId": warnings[0]["warningKey"], "expectedRevision": warnings[0]["revision"]}],
            kind="provider",
        )
        self.assertEqual(store.project_provider_warnings(), [])
        self.assertEqual(self.make_store().project_provider_warnings(), [])

    def test_capacity_deferral_rearms_without_consuming_retry(self) -> None:
        store = self.make_store()
        goal = store.create({"title": "capacity", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, delivery = store.wake(goal["goalId"])
        deferred_at = datetime.now(timezone.utc)
        deferred = store.defer_delivery_capacity(
            delivery["deliveryId"],
            now=deferred_at,
            rearm_seconds=5,
        )
        self.assertEqual(deferred["status"], "interrupted")
        self.assertFalse(deferred["consumeRetry"])
        self.assertEqual(deferred["attempt"], 1)
        _, reclaimed = store.wake(goal["goalId"], now=deferred_at + timedelta(seconds=5))
        self.assertEqual(reclaimed["attempt"], 1)

    def test_capacity_deferral_never_rewrites_a_running_delivery(self) -> None:
        store = self.make_store()
        _goal, delivery = self.start_delivery(store)
        before = store.project_deliveries()[delivery["deliveryId"]]
        deferred = store.defer_delivery_capacity(delivery["deliveryId"])
        self.assertEqual(deferred["status"], "running")
        self.assertEqual(deferred["revision"], before["revision"])

    def test_stale_owner_and_terminal_reactivation_are_rejected(self) -> None:
        store = self.make_store()
        goal = store.create(
            {
                "title": "isolated",
                "chatId": "chat-a",
                "projectRoot": str(self.root / "project-a"),
                "wakeAt": self.due_time(),
            }
        )
        with self.assertRaisesRegex(AgentGoalStoreError, "chat"):
            store.wake(goal["goalId"], {"chatId": "chat-b"})
        cancelled = store.update(goal["goalId"], {"status": "cancelled"})
        with self.assertRaisesRegex(AgentGoalStoreError, "cannot be reactivated"):
            store.update(goal["goalId"], {"status": "active", "expectedRevision": cancelled["revision"]})

    def test_existing_sidecar_is_recovered_before_retry(self) -> None:
        store = self.make_store(runner_instance_id="process-a")
        goal = store.create(
            {
                "title": "recover",
                "chatId": "chat-a",
                "wakeAt": self.due_time(),
                "wakeEveryMinutes": 10,
            }
        )
        _, delivery = store.wake(goal["goalId"])
        store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        running = store.project_deliveries()[delivery["deliveryId"]]
        durable_completed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        store._write_result(
            running,
            {"turnId": "already-finished"},
            completed_at=durable_completed_at,
        )

        reopened = self.make_store(runner_instance_id="process-b")
        self.assertEqual(reopened.project_deliveries()[delivery["deliveryId"]]["status"], "running")
        reconciled = reopened.reconcile_stale_running_deliveries()
        self.assertEqual([row["status"] for row in reconciled], ["completed"])
        self.assertEqual(reconciled[0]["completedAt"], durable_completed_at.isoformat())
        self.assertEqual(
            reconciled[0]["nextWakeAt"],
            (durable_completed_at + timedelta(minutes=10)).isoformat(),
        )
        replay = reopened.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        self.assertTrue(replay["cached"])
        self.assertEqual(replay["response"]["turnId"], "already-finished")
        reopened_again = self.make_store(runner_instance_id="process-c")
        completed_events = [
            event
            for event in reopened_again._events()
            if event.get("event") == "goal_delivery_completed" and event.get("deliveryId") == delivery["deliveryId"]
        ]
        self.assertEqual(len(completed_events), 1)

    def test_completed_delivery_with_missing_truncated_or_tampered_result_never_reruns(self) -> None:
        for mode in ("missing", "truncated", "tampered"):
            with self.subTest(mode=mode):
                store = self.make_store()
                goal = store.create(
                    {"title": f"corrupt-{mode}", "chatId": f"chat-{mode}", "wakeAt": self.due_time()}
                )
                _, delivery = store.wake(goal["goalId"])
                store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
                store.complete_delivery(delivery["deliveryId"], {"turnId": f"turn-{mode}", "text": "safe"})
                result_path = store._result_path(delivery["deliveryId"])
                if mode == "missing":
                    result_path.unlink()
                elif mode == "truncated":
                    result_path.write_text('{"schema":', encoding="utf-8")
                else:
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                    payload["response"]["text"] = "changed"
                    result_path.write_text(json.dumps(payload), encoding="utf-8")

                reopened = self.make_store()
                self.assertEqual(reopened.list_recoverable(chat_id=f"chat-{mode}"), [])
                projected = reopened.project_deliveries()[delivery["deliveryId"]]
                self.assertEqual(projected["status"], "failed")
                self.assertEqual(projected["failureClass"], "result_corrupt")
                self.assertFalse(projected["retryable"])
                with self.assertRaisesRegex(AgentGoalStoreError, "terminal"):
                    reopened.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
                self.assertEqual(reopened.project_goals()[goal["goalId"]]["wakeCount"], 1)

    def test_stale_running_delivery_is_rearmed_once_after_process_restart(self) -> None:
        store = self.make_store(runner_instance_id="process-a")
        goal = store.create({"title": "restart", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, delivery = store.wake(goal["goalId"])
        store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})

        reopened = self.make_store(runner_instance_id="process-b")
        self.assertEqual(reopened.project_deliveries()[delivery["deliveryId"]]["status"], "running")
        self.assertFalse(any(event.get("event") == "goal_delivery_interrupted" for event in reopened._events()))
        reconciled = reopened.reconcile_stale_running_deliveries()
        self.assertEqual([row["status"] for row in reconciled], ["interrupted"])
        interrupted = reopened.project_deliveries()[delivery["deliveryId"]]
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["reason"], "process_restart")

        reopened_again = self.make_store(runner_instance_id="process-b")
        self.assertEqual(reopened_again.reconcile_stale_running_deliveries(), [])
        interrupted_events = [
            event
            for event in reopened_again._events()
            if event.get("event") == "goal_delivery_interrupted" and event.get("deliveryId") == delivery["deliveryId"]
        ]
        self.assertEqual(len(interrupted_events), 1)
        self.assertEqual(reopened_again.list_due(limit=10)[0]["goalId"], goal["goalId"])

        _, claimed = reopened_again.wake(goal["goalId"])
        self.assertEqual(claimed["status"], "claimed")
        started = reopened_again.begin_delivery(
            delivery["deliveryId"],
            {"clientTurnId": delivery["clientTurnId"]},
        )
        self.assertFalse(started["cached"])
        reopened_again.complete_delivery(delivery["deliveryId"], {"turnId": "retry-done"})
        self.assertEqual(reopened_again.project_goals()[goal["goalId"]]["wakeAt"], "")

    def test_stale_claim_is_rearmed_without_consuming_retry_or_waiting_for_deadline(self) -> None:
        store = self.make_store(runner_instance_id="process-a")
        goal = store.create({"title": "restart claim", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, delivery = store.wake(goal["goalId"])
        self.assertEqual(delivery["status"], "claimed")
        self.assertEqual(delivery["runnerInstanceId"], "process-a")

        reopened = self.make_store(runner_instance_id="process-b")
        reconciled = reopened.reconcile_stale_running_deliveries()
        self.assertEqual(len(reconciled), 1)
        interrupted = reconciled[0]
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["reason"], "process_restart_wake_interrupted")
        self.assertFalse(interrupted["consumeRetry"])
        self.assertEqual(interrupted["attempt"], 1)

        self.assertEqual(reopened.reconcile_stale_running_deliveries(), [])
        retry_at = datetime.fromisoformat(interrupted["retryAt"])
        _, reclaimed = reopened.wake(goal["goalId"], now=retry_at)
        self.assertEqual(reclaimed["status"], "claimed")
        self.assertEqual(reclaimed["attempt"], 1)
        self.assertEqual(reclaimed["runnerInstanceId"], "process-b")

    def test_same_process_reopen_preserves_running_conflict(self) -> None:
        store = self.make_store(runner_instance_id="process-a")
        goal = store.create({"title": "still-running", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, delivery = store.wake(goal["goalId"])
        store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})

        reopened = self.make_store(runner_instance_id="process-a")
        self.assertEqual(reopened.reconcile_stale_running_deliveries(), [])
        self.assertEqual(reopened.project_deliveries()[delivery["deliveryId"]]["status"], "running")
        with self.assertRaisesRegex(AgentGoalStoreError, "already running"):
            reopened.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        self.assertFalse(
            any(
                event.get("event") == "goal_delivery_interrupted" and event.get("deliveryId") == delivery["deliveryId"]
                for event in reopened._events()
            )
        )

    def test_recovery_rejects_foreign_or_corrupt_result_sidecars(self) -> None:
        corruptions = {
            "schema": lambda payload: payload.update({"schema": "vrcforge.agent_goal_delivery_result.future"}),
            "delivery": lambda payload: payload.update({"deliveryId": "goal_delivery_foreign"}),
            "goal": lambda payload: payload.update({"goalId": "goal_foreign"}),
            "turn": lambda payload: payload.update({"clientTurnId": "goal-turn-foreign"}),
            "response": lambda payload: payload.update({"response": ["not", "an", "object"]}),
            "completed_at": lambda payload: payload.update({"completedAt": "not-a-timestamp"}),
        }
        for index, (name, corrupt) in enumerate(corruptions.items()):
            with self.subTest(name=name):
                store = self.make_store(runner_instance_id=f"writer-{index}")
                goal = store.create(
                    {"title": f"strict-{name}", "chatId": f"chat-{name}", "wakeAt": self.due_time()}
                )
                _, delivery = store.wake(goal["goalId"])
                store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
                running = store.project_deliveries()[delivery["deliveryId"]]
                store._write_result(running, {"turnId": f"wrong-{name}"})
                sidecar_path = store._result_path(delivery["deliveryId"])
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                corrupt(sidecar)
                sidecar_path.write_text(json.dumps(sidecar) + "\n", encoding="utf-8")

                reopened = self.make_store(runner_instance_id=f"reader-{index}")
                reconciled = reopened.reconcile_stale_running_deliveries()
                current = reopened.project_deliveries()[delivery["deliveryId"]]
                self.assertEqual(current["status"], "interrupted")
                self.assertIsNone(reopened.read_result(delivery["deliveryId"]))
                self.assertEqual(
                    [row["deliveryId"] for row in reconciled if row["deliveryId"] == delivery["deliveryId"]],
                    [delivery["deliveryId"]],
                )
                self.assertFalse(
                    any(
                        event.get("event") == "goal_delivery_completed"
                        and event.get("deliveryId") == delivery["deliveryId"]
                        for event in reopened._events()
                    )
                )

    def test_transient_result_sidecar_read_error_leaves_running_delivery_untouched(self) -> None:
        store = self.make_store(runner_instance_id="process-a")
        goal = store.create({"title": "locked", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, delivery = store.wake(goal["goalId"])
        store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        running = store.project_deliveries()[delivery["deliveryId"]]
        store._write_result(running, {"turnId": "already-finished"})

        reopened = self.make_store(runner_instance_id="process-b")
        sidecar_path = reopened._result_path(delivery["deliveryId"])
        original_read_text = Path.read_text

        def read_text_with_transient_lock(path: Path, *args, **kwargs):
            if path == sidecar_path:
                raise PermissionError("result sidecar is temporarily locked")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", read_text_with_transient_lock):
            with self.assertRaisesRegex(PermissionError, "temporarily locked"):
                reopened.reconcile_stale_running_deliveries()

        current = reopened.project_deliveries()[delivery["deliveryId"]]
        self.assertEqual(current["status"], "running")
        self.assertFalse(
            any(
                event.get("event") == "goal_delivery_interrupted" and event.get("deliveryId") == delivery["deliveryId"]
                for event in reopened._events()
            )
        )

    def test_recurring_completion_coalesces_missed_intervals(self) -> None:
        store = self.make_store()
        goal = store.create(
            {"title": "recurring", "chatId": "chat-a", "wakeAt": self.due_time(), "wakeEveryMinutes": 5}
        )
        _, delivery = store.wake(goal["goalId"])
        store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        completed_at = datetime.now(timezone.utc)
        store.complete_delivery(delivery["deliveryId"], {"turnId": "done"})
        projected = store.project_goals()[goal["goalId"]]
        next_wake = datetime.fromisoformat(projected["wakeAt"])
        self.assertGreater(next_wake, completed_at + timedelta(minutes=4))
        self.assertEqual(projected["wakeCount"], 1)

    def test_due_projection_is_timezone_aware_and_keeps_one_minute_overdue_compatible(self) -> None:
        store = self.make_store()
        goal = store.create({"title": "staggered", "chatId": "chat-a", "wakeAt": self.due_time()})
        eligible_at = datetime.fromisoformat(store.project_goals()[goal["goalId"]]["eligibleAt"])
        self.assertIsNotNone(eligible_at.tzinfo)
        self.assertLessEqual(eligible_at, datetime.now(timezone.utc))
        self.assertEqual(store.list_due(limit=10)[0]["goalId"], goal["goalId"])

    def test_same_tick_goals_dispatch_in_eligible_time_order(self) -> None:
        store = self.make_store()
        scheduled = self.due_time()
        goals = [
            store.create({"title": f"ordered-{index}", "chatId": f"chat-{index}", "wakeAt": scheduled})
            for index in range(4)
        ]
        expected = sorted(goals, key=lambda item: (item["eligibleAt"], item["goalId"]))
        due = store.list_due(
            limit=10,
            now=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        self.assertEqual([item["goalId"] for item in due], [item["goalId"] for item in expected])

    def test_retryable_failure_uses_initial_plus_two_attempts_then_advances_schedule(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        self.assertEqual(delivery["attempt"], 1)
        self.assertEqual(delivery["maxAttempts"], 3)

        first_failed_at = datetime.now(timezone.utc)
        first = store.fail_delivery(
            delivery["deliveryId"],
            {"message": "network reset", "failureClass": "network", "retryable": True},
            now=first_failed_at,
        )
        self.assertEqual(first["status"], "failed")
        self.assertFalse(first["terminal"])
        self.assertEqual(first["failureClass"], "network")
        self.assertEqual(
            datetime.fromisoformat(first["retryAt"]),
            first_failed_at + timedelta(seconds=retry_backoff_seconds(1)),
        )
        with self.assertRaisesRegex(AgentGoalStoreError, "waiting to retry"):
            store.wake(goal["goalId"], now=first_failed_at)

        first_retry_at = datetime.fromisoformat(first["retryAt"])
        _, second_claim = store.wake(goal["goalId"], now=first_retry_at)
        self.assertEqual(second_claim["attempt"], 2)
        store.begin_delivery(second_claim["deliveryId"], {"clientTurnId": second_claim["clientTurnId"]})
        second = store.fail_delivery(
            delivery["deliveryId"],
            "temporary timeout",
            failure_class="timeout",
            retryable=True,
            now=first_retry_at,
        )
        self.assertFalse(second["terminal"])
        self.assertEqual(
            datetime.fromisoformat(second["retryAt"]),
            first_retry_at + timedelta(seconds=retry_backoff_seconds(2)),
        )

        second_retry_at = datetime.fromisoformat(second["retryAt"])
        _, third_claim = store.wake(goal["goalId"], now=second_retry_at)
        self.assertEqual(third_claim["attempt"], 3)
        store.begin_delivery(third_claim["deliveryId"], {"clientTurnId": third_claim["clientTurnId"]})
        final = store.fail_delivery(
            delivery["deliveryId"],
            "still unavailable",
            failure_class="network",
            retryable=True,
            now=second_retry_at,
        )
        self.assertTrue(final["terminal"])
        self.assertTrue(final["retryable"])
        self.assertEqual(final["retryAt"], "")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeAt"], "")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 1)
        self.assertEqual(store.complete_delivery(delivery["deliveryId"], {"turnId": "late"})["status"], "failed")

    def test_permanent_failure_classes_never_retry(self) -> None:
        for index, failure_class in enumerate(("auth_credit", "schema_privacy", "permission_denied")):
            with self.subTest(failure_class=failure_class):
                store = self.make_store()
                goal, delivery = self.start_delivery(store, chat_id=f"chat-permanent-{index}")
                failed = store.fail_delivery(
                    delivery["deliveryId"],
                    "permanent",
                    failure_class=failure_class,
                    retryable=True,
                )
                self.assertTrue(failed["terminal"])
                self.assertFalse(failed["retryable"])
                self.assertEqual(failed["attempt"], 1)
                self.assertEqual(failed["retryAt"], "")
                self.assertEqual(store.project_goals()[goal["goalId"]]["wakeAt"], "")

    def test_provider_unreachable_rearms_without_consuming_attempt(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        skipped_at = datetime.now(timezone.utc)
        skipped = store.skip_provider_unreachable(delivery["deliveryId"], now=skipped_at)
        self.assertEqual(skipped["status"], "skipped")
        self.assertFalse(skipped["consumeRetry"])
        self.assertEqual(skipped["attempt"], 1)
        self.assertEqual(datetime.fromisoformat(skipped["retryAt"]), skipped_at + timedelta(seconds=300))

        with self.assertRaisesRegex(AgentGoalStoreError, "waiting to retry"):
            store.wake(goal["goalId"], now=skipped_at + timedelta(seconds=299))
        _, reclaimed = store.wake(goal["goalId"], now=skipped_at + timedelta(seconds=300))
        self.assertEqual(reclaimed["status"], "claimed")
        self.assertEqual(reclaimed["attempt"], 1)

    def test_due_listing_skips_owned_or_backing_off_occurrences_without_starving_later_goals(self) -> None:
        store = self.make_store()
        first = store.create({"title": "first", "chatId": "chat-a", "wakeAt": self.due_time()})
        second = store.create({"title": "second", "chatId": "chat-b", "wakeAt": self.due_time()})
        _, first_delivery = store.wake(first["goalId"])
        skipped_at = datetime.now(timezone.utc)
        store.skip_provider_unreachable(first_delivery["deliveryId"], now=skipped_at)

        due_while_backing_off = store.list_due(limit=10, now=skipped_at + timedelta(seconds=1))
        self.assertEqual([row["goalId"] for row in due_while_backing_off], [second["goalId"]])

        _, second_delivery = store.wake(second["goalId"])
        store.begin_delivery(second_delivery["deliveryId"], {"clientTurnId": second_delivery["clientTurnId"]})
        third = store.create({"title": "third", "chatId": "chat-c", "wakeAt": self.due_time()})
        due_while_running = store.list_due(limit=10, now=skipped_at + timedelta(seconds=1))
        self.assertEqual([row["goalId"] for row in due_while_running], [third["goalId"]])

    def test_watchdog_drains_before_retry_and_late_completion_is_ignored(self) -> None:
        store = self.make_store(runner_instance_id="process-a")
        goal, delivery = self.start_delivery(store)
        phase_started = datetime.now(timezone.utc)
        phased = store.mark_delivery_phase(delivery["deliveryId"], "provider_call", now=phase_started)
        deadline = datetime.fromisoformat(phased["deadlineAt"])
        reconciled = store.reconcile_phase_watchdogs(now=deadline)
        self.assertEqual(len(reconciled), 1)
        draining = reconciled[0]
        self.assertEqual(draining["status"], "draining")
        self.assertTrue(draining["drainPending"])
        self.assertEqual(draining["failureLabel"], "watchdog_provider_call_timeout")

        late = store.complete_delivery(delivery["deliveryId"], {"turnId": "late-worker"})
        self.assertEqual(late["status"], "draining")
        _, still_draining = store.wake(goal["goalId"], now=deadline + timedelta(minutes=1))
        self.assertEqual(still_draining["status"], "draining")
        self.assertEqual(still_draining["attempt"], 1)
        with self.assertRaisesRegex(AgentGoalStoreError, "draining"):
            store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})

        drained = store.finish_delivery_drain(
            delivery["deliveryId"],
            retryable=True,
            failure_class="timeout",
            error="worker exited",
            now=deadline + timedelta(seconds=1),
        )
        self.assertEqual(drained["status"], "failed")
        self.assertFalse(drained["terminal"])
        retry_at = datetime.fromisoformat(drained["retryAt"])
        _, retry_claim = store.wake(goal["goalId"], now=retry_at)
        self.assertEqual(retry_claim["attempt"], 2)

    def test_wake_watchdog_releases_unclaimed_handoff_without_draining_or_retry_cost(self) -> None:
        store = self.make_store()
        goal = store.create({"title": "handoff", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, claimed = store.wake(goal["goalId"])
        deadline = datetime.fromisoformat(claimed["deadlineAt"])

        reconciled = store.reconcile_phase_watchdogs(now=deadline)
        self.assertEqual(len(reconciled), 1)
        deferred = reconciled[0]
        self.assertEqual(deferred["status"], "interrupted")
        self.assertEqual(deferred["failureLabel"], "watchdog_wake_timeout")
        self.assertFalse(deferred["consumeRetry"])
        self.assertFalse(deferred.get("drainPending", False))
        retry_at = datetime.fromisoformat(deferred["retryAt"])
        _, reclaimed = store.wake(goal["goalId"], now=retry_at)
        self.assertEqual(reclaimed["status"], "claimed")
        self.assertEqual(reclaimed["attempt"], 1)

    def test_handoff_deferral_uses_revision_cas(self) -> None:
        store = self.make_store()
        goal = store.create({"title": "handoff cas", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, claimed = store.wake(goal["goalId"])
        unchanged = store.defer_delivery_capacity(
            claimed["deliveryId"],
            expected_revision=int(claimed["revision"]) - 1,
        )
        self.assertEqual(unchanged["status"], "claimed")
        self.assertEqual(unchanged["revision"], claimed["revision"])

    def test_approval_block_deny_is_terminal_not_green_and_restart_safe(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        blocked = store.block_delivery_for_approval(
            delivery["deliveryId"],
            "approval-1",
            response={"turnId": "pending", "text": "waiting"},
            context_usage={"exact": True, "inputTokens": 4, "outputTokens": 1, "totalTokens": 5},
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertTrue(blocked["terminal"])
        self.assertEqual(blocked["approvalReference"], "approval-1")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 0)
        with self.assertRaisesRegex(AgentGoalStoreError, "not ready"):
            store.mark_materialized(delivery["deliveryId"], {"chatId": "chat-a"})

        reopened = self.make_store()
        denied = reopened.deny_by_approval("approval-1", reason="user rejected")
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(denied["failureClass"], "permission_denied")
        self.assertEqual(denied["approvalReference"], "approval-1")
        self.assertTrue(denied["noticeUnread"])
        self.assertEqual(reopened.project_goals()[goal["goalId"]]["wakeCount"], 1)
        self.assertEqual(reopened.complete_delivery(delivery["deliveryId"], {"turnId": "late"})["status"], "denied")

        run_sidecar = reopened.read_run(delivery["deliveryId"])
        self.assertEqual(run_sidecar["schema"], GOAL_DELIVERY_RUN_SCHEMA)
        self.assertNotIn("blockedResponse", run_sidecar)
        self.assertFalse((self.root / "agent-goal-results" / f"{delivery['deliveryId']}.json").exists())

    def test_missing_restart_approval_fails_closed_and_advances_schedule(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store, recurring_minutes=5)
        store.block_delivery_for_approval(delivery["deliveryId"], "approval-lost")

        reopened = self.make_store()
        reconciled = reopened.reconcile_missing_approvals(set())

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["status"], "failed")
        self.assertEqual(reconciled[0]["failureLabel"], "approval_recovery_required")
        self.assertTrue(reconciled[0]["noticeUnread"])
        self.assertEqual(reopened.project_goals()[goal["goalId"]]["wakeCount"], 1)
        self.assertEqual(reopened.reconcile_missing_approvals(set()), [])

    def test_approval_resolution_completes_from_original_response_without_double_schedule(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        store.block_delivery_for_approval(
            delivery["deliveryId"],
            "approval-ok",
            response={
                "turnId": "pending",
                "text": "original",
                "contextUsage": {"exact": True, "inputTokens": 7, "outputTokens": 2, "totalTokens": 9},
            },
            context_usage={"exact": True, "inputTokens": 7, "outputTokens": 2, "totalTokens": 9},
        )
        applying = store.mark_by_approval_phase(
            "approval-ok",
            "apply",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.assertEqual(applying["status"], "applying")
        completed = store.resolve_delivery_approval(
            "approval-ok",
            {"ok": True, "status": "applied", "response": {"writeStatus": "applied"}},
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 1)
        result = store.read_result(delivery["deliveryId"])
        self.assertEqual(result["turnId"], "pending")
        self.assertEqual(result["text"], "original")
        self.assertEqual(result["writeStatus"], "applied")
        self.assertEqual(completed["usage"]["totalTokens"], 9)
        self.assertEqual(store.project_goals()[goal["goalId"]]["usageTotals"]["totalTokens"], 9)

    def test_failed_approval_transition_restores_the_pending_wait_state(self) -> None:
        store = self.make_store()
        _goal, delivery = self.start_delivery(store)
        blocked = store.block_delivery_for_approval(
            delivery["deliveryId"],
            "approval-retry",
            response={"turnId": "pending"},
        )
        applying = store.mark_by_approval_phase("approval-retry", "apply")
        restored = store.restore_approval_wait("approval-retry")

        self.assertEqual(applying["status"], "applying")
        self.assertEqual(restored["status"], "blocked")
        self.assertTrue(restored["terminal"])
        self.assertFalse(restored["approvalPendingResolution"])
        self.assertEqual(restored["approvalId"], blocked["approvalId"])
        self.assertEqual(restored["recapRevision"], blocked["recapRevision"])

    def test_approval_execution_failure_is_terminal_apply_failed(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        store.block_delivery_for_approval(delivery["deliveryId"], "approval-fail", response={"turnId": "pending"})
        failed = store.resolve_delivery_approval("approval-fail", {"ok": False, "error": "apply crashed"})
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["terminal"])
        self.assertEqual(failed["failureClass"], "apply_failed")
        self.assertEqual(failed["retryAt"], "")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 1)

    def test_incomplete_plan_can_be_parked_without_false_completion(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        parked = store.park_delivery(
            delivery["deliveryId"],
            reason="await_user_instruction",
            failure_class="await_user_instruction",
            context_usage={"inputTokens": 3, "outputTokens": 1, "totalTokens": 4},
        )
        self.assertEqual(parked["status"], "parked")
        self.assertTrue(parked["terminal"])
        self.assertTrue(parked["noticeUnread"])
        self.assertEqual(parked["failureLabel"], "await_user_instruction")
        self.assertEqual(parked["usage"]["totalTokens"], 4)
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 1)
        self.assertEqual(store.complete_delivery(delivery["deliveryId"], {"turnId": "late"})["status"], "parked")

    def test_question_reminder_emits_once_then_parks_across_restart(self) -> None:
        store = self.make_store()
        _, delivery = self.start_delivery(store)
        blocked_at = datetime.now(timezone.utc)
        blocked = store.block_delivery_for_question(
            delivery["deliveryId"],
            "question-1",
            response={"turnId": "question"},
            now=blocked_at,
        )
        reminder_at = datetime.fromisoformat(blocked["questionReminderAt"])
        store.acknowledge_background_notifications("chat-a")
        self.assertEqual(store.emit_due_question_reminders(now=reminder_at - timedelta(seconds=1)), [])
        reminded = store.emit_due_question_reminders(now=reminder_at)
        self.assertEqual(len(reminded), 1)
        self.assertEqual(reminded[0]["status"], "parked")
        self.assertTrue(reminded[0]["noticeUnread"])
        self.assertEqual(store.emit_due_question_reminders(now=reminder_at + timedelta(hours=1)), [])

        reopened = self.make_store()
        self.assertEqual(reopened.emit_due_question_reminders(now=reminder_at + timedelta(days=1)), [])
        events = [
            event
            for event in reopened._events()
            if event.get("event") == "goal_delivery_question_reminded"
            and event.get("deliveryId") == delivery["deliveryId"]
        ]
        self.assertEqual(len(events), 1)

    def test_question_answer_rearms_exactly_once_and_suppresses_later_reminders(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store)
        blocked_at = datetime.now(timezone.utc)
        blocked = store.block_delivery_for_question(
            delivery["deliveryId"],
            "question-answer",
            response={"turnId": "waiting"},
            now=blocked_at,
        )
        answered = store.resolve_delivery_question(
            "question-answer",
            continuation_prompt="Continue after the selected answer.",
            now=blocked_at + timedelta(seconds=5),
        )
        self.assertEqual(answered["status"], "interrupted")
        self.assertFalse(answered["terminal"])
        self.assertFalse(answered["noticeUnread"])
        self.assertFalse(answered["scheduleAdvanced"])
        self.assertEqual(answered["resumePrompt"], "Continue after the selected answer.")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 0)

        duplicate = store.resolve_delivery_question(
            "question-answer",
            continuation_prompt="must not replace the first answer",
            now=blocked_at + timedelta(seconds=10),
        )
        self.assertEqual(duplicate["revision"], answered["revision"])
        _, reclaimed = store.wake(goal["goalId"], now=blocked_at + timedelta(seconds=10))
        self.assertEqual(reclaimed["deliveryId"], delivery["deliveryId"])
        self.assertEqual(reclaimed["attempt"], 1)
        self.assertEqual(reclaimed["resumePrompt"], "Continue after the selected answer.")
        store.begin_delivery(reclaimed["deliveryId"], {"clientTurnId": reclaimed["clientTurnId"]})
        completed = store.complete_delivery(reclaimed["deliveryId"], {"turnId": "continued"})
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 1)
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeAt"], "")
        reminder_at = datetime.fromisoformat(blocked["questionReminderAt"])
        self.assertEqual(store.emit_due_question_reminders(now=reminder_at + timedelta(days=1)), [])

        reopened = self.make_store()
        restored = reopened.resolve_delivery_question("question-answer")
        self.assertEqual(restored["status"], "completed")
        answered_events = [
            event
            for event in reopened._events()
            if event.get("event") == "goal_delivery_question_answered"
            and event.get("deliveryId") == delivery["deliveryId"]
        ]
        self.assertEqual(len(answered_events), 1)

    def test_recurring_blocked_goal_remains_single_flight_until_decision(self) -> None:
        store = self.make_store()
        goal, delivery = self.start_delivery(store, recurring_minutes=5)
        blocked_at = datetime.now(timezone.utc)
        store.block_delivery_for_approval(
            delivery["deliveryId"],
            "approval-single-flight",
            now=blocked_at,
        )
        self.assertEqual(store.project_goals()[goal["goalId"]]["wakeCount"], 0)
        self.assertEqual(store.list_due(limit=10, now=blocked_at + timedelta(minutes=30)), [])

        denied = store.deny_by_approval(
            "approval-single-flight",
            reason="user rejected",
            now=blocked_at + timedelta(minutes=31),
        )
        self.assertEqual(denied["status"], "denied")
        projected = store.project_goals()[goal["goalId"]]
        self.assertEqual(projected["wakeCount"], 1)
        self.assertGreater(datetime.fromisoformat(projected["wakeAt"]), blocked_at + timedelta(minutes=31))

    def test_usage_run_sidecar_and_background_unread_ack_are_durable_and_scoped(self) -> None:
        store = self.make_store()
        goal_a, delivery_a = self.start_delivery(store, chat_id="chat-a")
        completed = store.complete_delivery(
            delivery_a["deliveryId"],
            {
                "turnId": "done",
                "contextUsage": {
                    "schema": "vrcforge.context_usage.v1",
                    "exact": True,
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "totalTokens": 15,
                    "requestCount": 1,
                },
            },
        )
        self.assertEqual(completed["usage"]["totalTokens"], 15)
        self.assertEqual(store.project_goals()[goal_a["goalId"]]["usageTotals"]["inputTokens"], 10)
        self.assertFalse(completed["noticeUnread"])
        self.assertEqual(store.read_run(delivery_a["deliveryId"])["schema"], GOAL_DELIVERY_RUN_SCHEMA)
        result_sidecar = json.loads(store._result_path(delivery_a["deliveryId"]).read_text(encoding="utf-8"))
        self.assertEqual(result_sidecar["schema"], GOAL_DELIVERY_RESULT_SCHEMA)

        _, delivery_b = self.start_delivery(store, chat_id="chat-b")
        failed_b = store.fail_delivery(
            delivery_b["deliveryId"],
            "bad key",
            failure_class="auth_credit",
        )
        _, delivery_c = self.start_delivery(store, chat_id="chat-a")
        blocked_c = store.block_delivery_for_question(delivery_c["deliveryId"], "question-c")
        self.assertTrue(failed_b["noticeUnread"])
        self.assertTrue(blocked_c["noticeUnread"])

        state = store.background_state("chat-a")
        self.assertEqual({row["chatId"] for row in state["recent"]}, {"chat-a"})
        self.assertEqual(state["unreadByChat"], {"chat-a": 1, "chat-b": 1})
        self.assertEqual(state["totalUnread"], 2)
        acknowledged = store.acknowledge_background_notifications("chat-a", [delivery_c["deliveryId"]])
        self.assertEqual(acknowledged["totalUnread"], 1)
        self.assertEqual(acknowledged["unreadByChat"], {"chat-b": 1})

        reopened = self.make_store()
        self.assertEqual(reopened.background_state("chat-a")["totalUnread"], 1)
        self.assertFalse(reopened.project_deliveries()[delivery_c["deliveryId"]]["noticeUnread"])

    def test_success_failure_block_and_deny_terminal_events_are_idempotent(self) -> None:
        store = self.make_store()

        _, success_delivery = self.start_delivery(store, chat_id="chat-success")
        store.complete_delivery(success_delivery["deliveryId"], {"turnId": "done"})
        store.complete_delivery(success_delivery["deliveryId"], {"turnId": "late"})

        _, failed_delivery = self.start_delivery(store, chat_id="chat-failed")
        store.fail_delivery(failed_delivery["deliveryId"], "invalid", failure_class="schema_privacy")
        store.fail_delivery(failed_delivery["deliveryId"], "duplicate", failure_class="schema_privacy")

        _, blocked_delivery = self.start_delivery(store, chat_id="chat-blocked")
        store.block_delivery_for_approval(blocked_delivery["deliveryId"], "approval-idempotent")
        store.block_delivery_for_approval(blocked_delivery["deliveryId"], "approval-idempotent")
        store.deny_by_approval("approval-idempotent")
        store.deny_by_approval("approval-idempotent")

        events = store._events()
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event.get("event") == "goal_delivery_completed"
                    and event.get("deliveryId") == success_delivery["deliveryId"]
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event.get("event") == "goal_delivery_failed"
                    and event.get("deliveryId") == failed_delivery["deliveryId"]
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event.get("event") == "goal_delivery_blocked"
                    and event.get("deliveryId") == blocked_delivery["deliveryId"]
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event.get("event") == "goal_delivery_denied"
                    and event.get("deliveryId") == blocked_delivery["deliveryId"]
                ]
            ),
            1,
        )

    def test_legacy_unowned_schedule_fails_closed_until_explicit_bind(self) -> None:
        legacy = {
            "schema": "vrcforge.agent_goal.v1",
            "event": "goal_created",
            "goalId": "goal_legacy",
            "title": "Legacy",
            "status": "active",
            "wakeAt": self.due_time(),
            "wakeEveryMinutes": 0,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        (self.root / "agent-goals.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        store = self.make_store()

        projected = store.project_goals()["goal_legacy"]
        self.assertEqual(projected["blockedReason"], "owner_missing")
        self.assertEqual(store.list_due(limit=10), [])
        with self.assertRaisesRegex(AgentGoalStoreError, "not due"):
            store.wake("goal_legacy")

        bound = store.bind_owner("goal_legacy", {"chatId": "chat-legacy"})
        self.assertNotIn("blockedReason", bound)
        self.assertEqual(store.list_due(limit=10)[0]["goalId"], "goal_legacy")


if __name__ == "__main__":
    unittest.main()
