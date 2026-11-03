from __future__ import annotations

import copy
import threading
import time
import unittest

from cross_session_conversation_service import (
    CrossSessionConversationError,
    CrossSessionConversationService,
)


class Harness:
    def __init__(self) -> None:
        self.chats = {
            "source": {
                "id": "source",
                "sessionId": "source-session",
                "title": "Source",
                "projectPath": "C:/Unity/Project",
                "updatedAt": "2026-11-02T00:00:00Z",
                "revision": 1,
                "items": [{"id": "user-source", "type": "user", "clientTurnId": "source-turn", "text": "Delegate this."}],
            },
            "offscreen": {
                "id": "offscreen",
                "sessionId": "offscreen-session",
                "title": "Offscreen target",
                "projectPath": "c:\\unity\\project",
                "updatedAt": "2026-11-03T00:00:00Z",
                "revision": 4,
                "items": [{"id": "old", "type": "agent", "response": {"plan": {"reply": "Earlier reply."}}}],
            },
            "foreign": {
                "id": "foreign",
                "sessionId": "foreign-session",
                "title": "Other project",
                "projectPath": "C:/Unity/Other",
                "revision": 1,
                "items": [],
            },
        }
        self.queue: list[dict] = []
        self.busy: set[str] = set()
        self.steers: list[dict] = []
        self.accept_steer = True
        self.dispatched: list[dict] = []
        self.chat_changed = 0
        self.next_id = 0
        self.lock = threading.RLock()
        self.load_calls: list[tuple[str, str]] = []

    @staticmethod
    def normalize_scope(value: str) -> str:
        return str(value or "").replace("\\", "/").rstrip("/").casefold()

    def load(self, chat_id: str, scope: str):
        self.load_calls.append((chat_id, scope))
        with self.lock:
            chat = self.chats.get(chat_id)
            return copy.deepcopy(chat) if chat else None

    def list(self, _scope: str):
        with self.lock:
            return copy.deepcopy(list(self.chats.values()))

    def create(self, record, _scope: str):
        with self.lock:
            if record["id"] in self.chats:
                raise CrossSessionConversationError("duplicate chat")
            self.chats[record["id"]] = copy.deepcopy(dict(record))

    def save(self, chat_id: str, _scope: str, updated, expected_revision: int):
        with self.lock:
            current = self.chats[chat_id]
            if current["revision"] != expected_revision:
                raise RuntimeError("revision changed")
            self.chats[chat_id] = copy.deepcopy(dict(updated))
            return copy.deepcopy(self.chats[chat_id])

    def enqueue(self, params):
        with self.lock:
            entry = {
                "queueId": f"queue-{len(self.queue) + 1}",
                "sequence": len(self.queue) + 1,
                "sessionId": params["sessionId"],
                "runtimeSessionId": params["runtimeSessionId"],
                "clientTurnId": params["clientTurnId"],
                "message": params["message"],
                "deliveryMode": params["deliveryMode"],
                "projectRoot": params["projectRoot"],
                "status": "pending",
            }
            self.queue.append(entry)
            return {"accepted": True, "queueId": entry["queueId"], "sequence": entry["sequence"]}

    def list_queue(self):
        with self.lock:
            return copy.deepcopy(self.queue)

    def claim(self, *, session_id, owner_id, limit=1, queue_id=""):
        _ = owner_id, limit
        with self.lock:
            item = next((row for row in self.queue
                         if row["sessionId"] == session_id
                         and (not queue_id or row["queueId"] == queue_id)
                         and row["status"] in {"pending", "paused"}), None)
            if item is None:
                return []
            item["status"] = "claimed"
            item["claimToken"] = f"claim-{item['queueId']}"
            item["leaseUntil"] = time.time() + 300
            return [copy.deepcopy(item)]

    def release(self, queue_id, session_id, claim_token):
        with self.lock:
            item = next((row for row in self.queue
                         if row["queueId"] == queue_id and row["sessionId"] == session_id
                         and row.get("claimToken") == claim_token and row["status"] == "claimed"), None)
            if item is None:
                return False
            item["status"] = "paused"
            item.pop("claimToken", None)
            item.pop("leaseUntil", None)
            return True

    def dispatch(self, params):
        self.assert_forward_only = params.get("_humanUserMessageSource") is None
        self.assert_forward_only = self.assert_forward_only and params.get("planMode") is False
        self.assert_forward_only = self.assert_forward_only and params.get("session_id") == "offscreen-session"
        queue_id = params["followupQueueId"]
        with self.lock:
            entry = next(row for row in self.queue if row["queueId"] == queue_id)
            if entry["status"] != "pending":
                raise CrossSessionConversationError("not pending")
            entry["status"] = "claimed"
            self.dispatched.append(copy.deepcopy(params))
        result = {
            "ok": True,
            "sessionId": params["session_id"],
            "clientTurnId": params["clientTurnId"],
            "turnId": "runtime-turn-1",
            "plan": {"reply": "Completed in the offscreen target."},
        }
        with self.lock:
            entry["status"] = "acked"
        return result

    def active_client_turn(self, chat_id, _session_id, _scope):
        return "active-turn" if chat_id in self.busy else ""

    def submit_steer(self, params):
        self.steers.append(copy.deepcopy(dict(params)))
        return {"accepted": self.accept_steer and params["sessionId"] == "offscreen-session"}

    def service(self, *, max_turns: int = 25):
        return CrossSessionConversationService(
            load_chat=self.load,
            list_chats=self.list,
            create_chat=self.create,
            save_chat=self.save,
            enqueue_followup=self.enqueue,
            list_followups=self.list_queue,
            claim_followup=self.claim,
            release_followup=self.release,
            target_busy=lambda chat_id, _session_id, _scope: chat_id in self.busy,
            active_client_turn=self.active_client_turn,
            submit_steer=self.submit_steer,
            dispatch_runtime=self.dispatch,
            publish_runtime=lambda _result: None,
            notify_chat_changed=lambda: setattr(self, "chat_changed", self.chat_changed + 1),
            normalize_scope=self.normalize_scope,
            make_id=self.make_id,
            now=lambda: "2026-11-03T00:00:00Z",
        )

    def make_id(self):
        self.next_id += 1
        return f"{self.next_id:032x}"


def trusted_context(*, chat_id: str = "source", session_id: str = "source-session", turn_id: str = "source-turn", budget: int = 25, action_id: str = "tool-action-1"):
    return {
        "_trustedConversationContext": {
            "chatId": chat_id,
            "sessionId": session_id,
            "clientTurnId": turn_id,
            "actionId": action_id,
            "projectRoot": "C:/Unity/Project",
            "maxAgenticTurns": budget,
        }
    }


class CrossSessionConversationServiceTests(unittest.TestCase):
    def test_list_and_read_are_project_scoped_bounded_and_public_only(self):
        harness = Harness()
        harness.chats["offscreen"]["items"] = [
            {"id": "user", "type": "user", "text": "Visible user message", "attachments": [{"name": "secret.png"}]},
            {"id": "answer", "type": "question_answer", "question": "Pick a choice", "text": "Choice B"},
            {"id": "agent", "type": "agent", "response": {"plan": {"reply": "Visible assistant reply"}}},
            {"id": "tool", "type": "tool_result", "text": "must not be exposed"},
        ]
        service = harness.service()

        listed = service.list_conversations({**trusted_context(), "limit": 99})
        self.assertEqual([item["chatId"] for item in listed["items"]], ["offscreen", "source"])
        read = service.read_conversation({**trusted_context(), "chatId": "offscreen", "limit": 99})
        self.assertEqual(read["total"], 3)
        self.assertEqual(read["messages"][1]["text"], "Question: Pick a choice\nAnswer: Choice B")
        self.assertNotIn("secret.png", str(read))
        self.assertNotIn("must not be exposed", str(read))

    def test_read_can_inspect_the_trusted_current_conversation(self):
        harness = Harness()
        result = harness.service().read_conversation({**trusted_context(), "chatId": "source"})
        self.assertEqual(result["chatId"], "source")
        self.assertEqual(harness.load_calls, [("source", "C:/Unity/Project")])
        self.assertEqual(result["messages"], [{
            "role": "user",
            "text": "Delegate this.",
            "truncated": False,
            "createdAt": "",
        }])

    def test_read_only_loads_target_and_uses_trusted_host_scope(self):
        harness = Harness()
        result = harness.service().read_conversation({**trusted_context(), "chatId": "offscreen"})
        self.assertEqual(result["chatId"], "offscreen")
        self.assertEqual(harness.load_calls, [("offscreen", "C:/Unity/Project")])

    def test_create_uses_fresh_session_and_does_not_copy_history(self):
        harness = Harness()
        result = harness.service().create_conversation({**trusted_context(), "title": "New work"})
        created = harness.chats[result["chatId"]]
        self.assertNotEqual(created["sessionId"], "source-session")
        self.assertEqual(created["projectPath"], "C:/Unity/Project")
        self.assertEqual(created["items"], [])

    def test_send_message_is_queue_only_for_idle_target(self):
        harness = Harness()
        service = harness.service()
        response = service.send_message({**trusted_context(budget=8), "chatId": "offscreen", "message": "Read this when you next work."})
        self.assertEqual(response["status"], "queued")
        self.assertEqual(harness.dispatched, [])
        self.assertEqual(harness.queue, [])
        forwarded = harness.chats["offscreen"]["items"][-1]
        self.assertTrue(forwarded["forwardedByAgent"])
        self.assertIn("not a new user instruction", forwarded["text"])
        self.assertEqual(forwarded["deliveryMode"], "queue_only")

    def test_send_message_delivers_to_active_turn_without_starting_a_new_one(self):
        harness = Harness()
        harness.busy.add("offscreen")
        response = harness.service().send_message({**trusted_context(), "chatId": "offscreen", "message": "Use this in your current work."})
        self.assertEqual(response["status"], "delivered_to_active_turn")
        self.assertFalse(response["completed"])
        self.assertEqual(harness.steers[0]["targetClientTurnId"], "active-turn")
        self.assertEqual(harness.dispatched, [])
        self.assertEqual(harness.queue, [])

    def test_followup_task_starts_idle_offscreen_target_and_persists_terminal_result(self):
        harness = Harness()
        service = harness.service()
        service.send_message({**trusted_context(), "chatId": "offscreen", "message": "Keep this note for the next turn."})
        response = service.followup_task({**trusted_context(budget=8, action_id="tool-action-2"), "chatId": "offscreen", "message": "Inspect this project now."})
        self.assertEqual(response["status"], "queued")
        self.assertEqual(harness.dispatched, [])
        self.assertEqual(service.process_pending(), 1)
        self.assertEqual(len(harness.dispatched), 1)
        params = harness.dispatched[0]
        self.assertTrue(harness.assert_forward_only)
        self.assertEqual(params["maxAgenticTurns"], 8)
        self.assertEqual(params["projectRoot"], "C:/Unity/Project")
        self.assertEqual(params["history"][0], {"role": "assistant", "text": "Earlier reply."})
        self.assertIn("Keep this note", params["history"][1]["text"])
        self.assertIn("Inspect this project now.", params["message"])
        persisted = harness.chats["offscreen"]["items"]
        self.assertTrue(persisted[-2]["forwardedByAgent"])
        self.assertEqual(persisted[-2]["queuedFrom"], False)
        self.assertEqual(persisted[-1]["id"], f"runtime-reply-{response['clientTurnId']}")

    def test_followup_task_is_delivered_to_busy_active_turn(self):
        harness = Harness()
        harness.busy.add("offscreen")
        service = harness.service()
        result = service.followup_task({**trusted_context(), "chatId": "offscreen", "message": "Continue with this task."})
        self.assertEqual(result["status"], "delivered_to_active_turn")
        self.assertFalse(result["completed"])
        self.assertEqual(harness.queue, [])
        self.assertEqual(harness.dispatched, [])
        self.assertEqual(harness.steers[0]["targetClientTurnId"], "active-turn")

    def test_followup_task_queues_when_active_turn_ends_before_delivery(self):
        harness = Harness()
        harness.busy.add("offscreen")
        harness.accept_steer = False
        service = harness.service()
        service.followup_task({**trusted_context(), "chatId": "offscreen", "message": "Run after the active turn."})
        self.assertEqual(harness.queue[0]["status"], "pending")
        self.assertEqual(service.process_pending(), 0)

        harness.busy.clear()
        service.notify_runtime_available()
        self.assertEqual(service.process_pending(), 1)
        self.assertEqual(len(harness.dispatched), 1)

    def _assert_dispatch_failure_pauses_queue_and_marks_chat_retryable(self, status_after_dispatch):
        harness = Harness()
        service = harness.service()
        params = {**trusted_context(), "chatId": "offscreen", "message": "Retry this task."}
        service.followup_task(params)
        attempts = []

        def rejected(_params):
            attempts.append(1)
            harness.queue[0]["status"] = status_after_dispatch
            raise RuntimeError("temporary runtime admission rejection")

        service._dispatch_runtime = rejected
        self.assertEqual(service.process_pending(), 0)
        self.assertEqual(attempts, [1])
        self.assertEqual(harness.queue[0]["status"], "paused")
        forwarded = harness.chats["offscreen"]["items"][-1]
        self.assertEqual(forwarded["queueStatus"], "paused")
        self.assertTrue(forwarded["queuedFrom"])
        self.assertEqual(forwarded["queueEnvelope"]["queueId"], harness.queue[0]["queueId"])
        retry = service.followup_task(params)
        self.assertEqual(retry["status"], "paused", retry)

    def test_pending_dispatch_failure_pauses_queue_and_marks_chat_retryable(self):
        self._assert_dispatch_failure_pauses_queue_and_marks_chat_retryable("pending")

    def test_already_paused_admission_failure_marks_chat_retryable(self):
        self._assert_dispatch_failure_pauses_queue_and_marks_chat_retryable("paused")

    def test_failed_followup_queue_admission_is_retried_without_duplicate_item(self):
        harness = Harness()
        service = harness.service()
        params = {**trusted_context(), "chatId": "offscreen", "message": "Queue this once."}
        original_enqueue = service._enqueue_followup

        def fail_admission(_params):
            raise RuntimeError("temporary durable queue failure")

        service._enqueue_followup = fail_admission
        with self.assertRaisesRegex(RuntimeError, "temporary durable queue failure"):
            service.followup_task(params)
        saved_items = copy.deepcopy(harness.chats["offscreen"]["items"])
        self.assertEqual(len(saved_items), 2)
        self.assertEqual(harness.queue, [])

        service._enqueue_followup = original_enqueue
        retry = service.followup_task(params)
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["status"], "queued", retry)
        self.assertTrue(retry["deduped"])
        self.assertEqual(len(harness.queue), 1)
        self.assertEqual(harness.chats["offscreen"]["items"], saved_items)

    def test_duplicate_tool_action_is_idempotent(self):
        harness = Harness()
        service = harness.service()
        first = service.followup_task({**trusted_context(), "chatId": "offscreen", "message": "One message."})
        second = service.followup_task({**trusted_context(), "chatId": "offscreen", "message": "One message."})
        self.assertEqual(second["clientTurnId"], first["clientTurnId"])
        self.assertTrue(second["deduped"])
        self.assertEqual(len(harness.queue), 1)

    def test_target_outside_trusted_project_is_rejected(self):
        service = Harness().service()
        with self.assertRaisesRegex(CrossSessionConversationError, "trusted project scope"):
            service.read_conversation({**trusted_context(), "chatId": "foreign"})


if __name__ == "__main__":
    unittest.main()
