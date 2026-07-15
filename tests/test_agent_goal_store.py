import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_goal_store import AgentGoalStore, AgentGoalStoreError


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
        goal = store.create({"title": "recover", "chatId": "chat-a", "wakeAt": self.due_time()})
        _, delivery = store.wake(goal["goalId"])
        store.begin_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
        running = store.project_deliveries()[delivery["deliveryId"]]
        store._write_result(running, {"turnId": "already-finished"})

        reopened = self.make_store(runner_instance_id="process-b")
        self.assertEqual(reopened.project_deliveries()[delivery["deliveryId"]]["status"], "running")
        reconciled = reopened.reconcile_stale_running_deliveries()
        self.assertEqual([row["status"] for row in reconciled], ["completed"])
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
