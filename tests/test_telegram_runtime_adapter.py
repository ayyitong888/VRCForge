import asyncio
import threading

from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from telegram_runtime_adapter import TelegramRuntimeAdapter
from channel_runtime_adapter import ChannelAdapterError
from channel_runtime_recovery import ChannelRuntimeRecovery
from channel_dashboard_integration import DashboardChannelRuntime
from dashboard_api_models import AgentRuntimeMessageRequest


class Store:
    def __init__(self):
        self.chats = []
        self.revisions = [{"storeId": "app", "digest": "d"}]
        self.fail = False

    def load(self):
        return {"chats": self.chats, "sourceRevisions": self.revisions}

    def save(self, chats, revisions):
        if self.fail:
            raise RuntimeError("conflict")
        self.chats = chats
        self.revisions = revisions
        return {"ok": True}


def test_explicit_steer_does_not_start_an_idle_session():
    async def case():
        runtime, store = Runtime(), Store()
        adapter = TelegramRuntimeAdapter(runtime, store, lambda event: None)
        chat = await adapter.create_session("/project", "idle")
        result = await adapter.submit(chat["id"], "steer-1", "change direction", "/project", steer_only=True)
        assert result["status"] == "notaccepted"
        assert result["reason"] == "no_active_turn"
        assert store.chats[0]["items"] == []
        await adapter.shutdown()
    asyncio.run(case())


class Runtime:
    def __init__(self):
        self.calls = []

    async def start_turn(self, session_id, client_turn_id, message, context=None):
        self.calls.append(("start", session_id, client_turn_id, message))
        return {"status": "waiting_approval", "approvalId": "a1"}

    async def steer(self, session_id, target, client, message, context=None):
        self.calls.append(("steer", session_id, target, client, message))
        return {"accepted": True}

    async def cancel(self, session_id, target, context=None):
        self.calls.append(("cancel", session_id, target))


def test_create_list_select_and_turn_persists_messages():
    async def scenario():
        store, runtime, events = Store(), Runtime(), []
        adapter = TelegramRuntimeAdapter(runtime, store, events.append)
        session = await adapter.create_session("D:/Project", "Demo")
        assert (await adapter.list_sessions("D:/Project"))[0]["id"] == session["id"]
        await adapter.get_session(session["id"], "D:/Project")
        await adapter.start_turn(session["sessionId"], "apply", "turn1", "D:/Project")
        for _ in range(8):
            await asyncio.sleep(0)
        assert [item["type"] for item in store.chats[0]["items"]] == ["user", "agent"]
        assert store.chats[0]["items"][1]["response"]["status"] == "waiting_approval"
        await adapter.shutdown()

    asyncio.run(scenario())


def test_project_scope_and_store_failure_are_explicit():
    async def scenario():
        store, runtime = Store(), Runtime()
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        session = await adapter.create_session("D:/Project")
        try:
            await adapter.get_session(session["id"], "D:/Other")
        except ChannelAdapterError as exc:
            assert exc.code == "session_scope_mismatch"
        else:
            raise AssertionError("scope mismatch accepted")
        store.fail = True
        try:
            await adapter.start_turn(session["sessionId"], "x", "turn1", "D:/Project")
        except ChannelAdapterError as exc:
            assert exc.code == "store_conflict"
        else:
            raise AssertionError("store failure accepted")
        await adapter.shutdown()

    asyncio.run(scenario())


def test_steer_and_cancel_route_to_active_session():
    async def scenario():
        store, runtime = Store(), Runtime()
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        session = await adapter.create_session("D:/Project")
        await adapter.start_turn(session["sessionId"], "x", "turn1", "D:/Project")
        assert (await adapter.steer(session["id"], "change", "turn2", "D:/Project"))["status"] == "steer_submitted"
        assert (await adapter.stop(session["id"], "D:/Project"))["status"] == "requested"
        await asyncio.sleep(0)
        assert any(call[0] == "steer" for call in runtime.calls)
        assert any(call[0] == "cancel" for call in runtime.calls)
        await adapter.shutdown()

    asyncio.run(scenario())


def test_start_event_does_not_discard_context_needed_for_later_control():
    async def scenario():
        done = asyncio.Event()
        seen = []

        class LongRuntime(Runtime):
            async def start_turn(self, session_id, client_turn_id, message, context):
                await done.wait()
                return {"reply": "ok"}

            async def steer(self, session_id, target, client, message, context):
                seen.append(context)
                return {"accepted": True}

        adapter = TelegramRuntimeAdapter(LongRuntime(), Store(), lambda event: None)
        chat = await adapter.create_session("D:/Project")
        await adapter.submit(chat["id"], "turn1", "inspect", "D:/Project")
        for _ in range(6):
            await asyncio.sleep(0)
        await adapter.submit(chat["id"], "turn2", "change", "D:/Project")
        for _ in range(6):
            await asyncio.sleep(0)
        assert seen[0]["chatId"] == chat["id"]
        assert seen[0]["projectRoot"] == "D:/Project"
        done.set()
        await adapter.shutdown()

    asyncio.run(scenario())


def test_recovery_guard_runs_only_for_submit_and_blocks_old_active_but_allows_steer():
    async def scenario():
        store, runtime = Store(), Runtime()
        calls = []

        class Recovery:
            async def check(self, session_id, client_turn_id, project_root):
                calls.append((session_id, client_turn_id, project_root))
                return type("Decision", (), {"allowed": False, "reason": "existing_run_active", "status": "active"})()

        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        session = await adapter.create_session("D:/Project")
        await adapter.start_turn(session["sessionId"], "initial", "initial-turn", "D:/Project")
        adapter._recovery = Recovery()
        assert (await adapter.get_session(session["id"], "D:/Project"))["id"] == session["id"]
        steered = await adapter.submit(session["id"], "new-turn", "new work", "D:/Project")
        assert steered["status"] == "steer_submitted"
        assert [item["id"] for item in store.chats[0]["items"]] == ["initial-turn", "new-turn"]
        # Steering an already active session has its own coordinator path and
        # must not be blocked by the new-turn recovery guard.
        assert (await adapter.steer(session["id"], "change", "steer-turn", "D:/Project"))["status"] == "steer_submitted"
        assert len(calls) == 0
        await adapter.shutdown()

    asyncio.run(scenario())


def test_project_continuation_preserves_payload_and_never_calls_runtime():
    async def scenario():
        store, runtime, events = Store(), Runtime(), []
        adapter = TelegramRuntimeAdapter(runtime, store, events.append)
        session = await adapter.create_session("D:/Project", "Approval")
        payload = {
            "sessionId": session["sessionId"],
            "turnId": "approval-turn-1",
            "clientTurnId": "telegram-turn-1",
            "plan": {"reply": "approved", "summary": "exact plan"},
            "result": {"tool": "capture", "value": [1, 2, 3]},
        }

        result = await adapter.project_continuation(payload, "D:/Project", "telegram-route")
        assert result["status"] == "projected"
        assert runtime.calls == []
        item = store.chats[0]["items"][0]
        assert item["id"] == "approval-turn-1"
        assert item["type"] == "agent"
        assert item["response"] == payload
        returned = [event for event in events if event.get("event") == "turn_returned"]
        assert returned[-1]["metadata"]["route"] == "telegram-route"
        assert returned[-1]["result"] == payload
        await adapter.shutdown()

    asyncio.run(scenario())


def test_runtime_history_matches_desktop_contract_and_restores_user_agent_roles():
    async def scenario():
        store, runtime = Store(), Runtime()
        session_state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
        seen = {}

        class HistoryRuntime(Runtime):
            async def start_turn(self, session_id, client_turn_id, message, context):
                seen.update(context)
                restored = session_state.restore_session(session_id, context["history"], "2026-01-01T00:00:00Z")
                seen["restored"] = restored
                seen["session"] = session_state._sessions[session_id]
                return {"turnId": "history-turn", "sessionId": session_id, "clientTurnId": client_turn_id, "plan": {"reply": "ok"}}

        store.chats = [{
            "id": "chat-1", "sessionId": "session-1", "projectPath": "D:/Project", "title": "x",
            "items": [
                {"id": "u", "type": "user", "text": "inspect", "attachments": [{"name": "a.txt", "type": "text/plain", "size": 3, "payloadKind": "text"}]},
                {"id": "a", "type": "agent", "response": {"turnId": "old", "plan": {"reply": "done"}}, "compactionRecovery": [{"id": "r1"}]},
                {"id": "c", "type": "compact", "detail": "Older context"},
            ],
        }]
        adapter = TelegramRuntimeAdapter(HistoryRuntime(), store, lambda _event: None)
        result = await adapter.submit("chat-1", "new", "next", "D:/Project")
        assert result["status"] == "started"
        for _ in range(8):
            await asyncio.sleep(0)
        assert [entry["role"] for entry in seen["history"]] == ["user", "agent", "agent"]
        assert seen["history"][0]["text"].startswith("inspect\n\nAttachments:")
        assert seen["history"][1] == {"role": "agent", "text": "done"}
        assert seen["history"][2] == {"role": "agent", "text": "Older context"}
        assert seen["compactionRecovery"] == [{"id": "r1"}]
        assert seen["restored"] == 3
        assert [(row["role"], row["message"]) for row in seen["session"]["turns"]] == [
            ("user", seen["history"][0]["text"]), ("agent", "done"), ("agent", "Older context")
        ]
        await adapter.shutdown()

    asyncio.run(scenario())


def test_runtime_event_uses_turn_identity_and_does_not_overwrite_existing_item():
    async def scenario():
        store, runtime = Store(), Runtime()
        store.chats = [{"id": "chat-1", "sessionId": "session-1", "projectPath": "D:/Project", "items": []}]
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        await adapter._handle_runtime_event({
            "event": "turn_returned", "sessionId": "session-1", "clientTurnId": "client-1",
            "result": {"turnId": "turn-1", "clientTurnId": "client-1", "plan": {"reply": "first"}},
        })
        assert store.chats[0]["items"][0]["id"] == "turn-1"
        await adapter._handle_runtime_event({
            "event": "turn_returned", "sessionId": "session-1", "clientTurnId": "client-1",
            "result": {"turnId": "turn-1", "clientTurnId": "client-1", "plan": {"reply": "different"}},
        })
        assert len(store.chats[0]["items"]) == 1
        assert store.chats[0]["items"][0]["response"]["plan"]["reply"] == "first"
        store.chats[0]["items"] = [{"id": "turn-2", "type": "user", "text": "preserve"}]
        await adapter._handle_runtime_event({
            "event": "turn_returned", "sessionId": "session-1", "clientTurnId": "client-2",
            "result": {"turnId": "turn-2", "plan": {"reply": "must not replace user"}},
        })
        assert store.chats[0]["items"] == [{"id": "turn-2", "type": "user", "text": "preserve"}]
        await adapter.shutdown()

    asyncio.run(scenario())


def test_project_continuation_rejects_cross_session_and_cross_project_scope():
    async def scenario():
        store, runtime = Store(), Runtime()
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        first = await adapter.create_session("D:/Project-A")
        second = await adapter.create_session("D:/Project-B")
        payload = {
            "sessionId": first["sessionId"],
            "turnId": "scope-turn",
            "clientTurnId": "scope-client",
            "plan": {"reply": "x"},
        }
        try:
            await adapter.project_continuation(payload, "D:/Project-B", "route")
        except ChannelAdapterError as exc:
            assert exc.code == "session_scope_mismatch"
        else:
            raise AssertionError("cross-project continuation accepted")
        payload["sessionId"] = "missing-session"
        try:
            await adapter.project_continuation(payload, "D:/Project-B", "route")
        except ChannelAdapterError as exc:
            assert exc.code == "session_not_found"
        else:
            raise AssertionError("unknown continuation session accepted")
        assert all(not chat["items"] for chat in store.chats)
        assert second["projectPath"] == "D:/Project-B"
        await adapter.shutdown()

    asyncio.run(scenario())


def test_project_continuation_duplicate_reoffers_same_notification_without_saving():
    async def scenario():
        class CountingStore(Store):
            def __init__(self):
                super().__init__()
                self.save_calls = 0

            def save(self, chats, revisions):
                self.save_calls += 1
                return super().save(chats, revisions)

        store, runtime, events = CountingStore(), Runtime(), []
        adapter = TelegramRuntimeAdapter(runtime, store, events.append)
        session = await adapter.create_session("D:/Project")
        payload = {"sessionId": session["sessionId"], "turnId": "same-turn", "clientTurnId": "same-client", "plan": {"reply": "same"}}
        first = await adapter.project_continuation(payload, "D:/Project", "route")
        saves_after_first = store.save_calls
        events_after_first = len(events)
        duplicate = await adapter.project_continuation(payload, "D:/Project", "route")
        assert first["status"] == "projected"
        assert duplicate["status"] == "duplicate"
        assert store.save_calls == saves_after_first
        assert len(events) == events_after_first + 1
        assert events[-1] == events[-2]
        assert len(store.chats[0]["items"]) == 1
        await adapter.shutdown()

    asyncio.run(scenario())


def test_project_continuation_retries_only_store_conflict_and_unknown_write_fails_once():
    async def scenario():
        class ConflictStore(Store):
            def __init__(self):
                super().__init__()
                self.conflict_once = False
                self.save_calls = 0

            def save(self, chats, revisions):
                self.save_calls += 1
                if self.conflict_once:
                    self.conflict_once = False
                    raise ChannelAdapterError("store_conflict")
                return super().save(chats, revisions)

        conflict_store, runtime = ConflictStore(), Runtime()
        adapter = TelegramRuntimeAdapter(runtime, conflict_store, lambda _event: None)
        session = await adapter.create_session("D:/Project")
        conflict_store.conflict_once = True
        result = await adapter.project_continuation(
            {"sessionId": session["sessionId"], "turnId": "retry-turn", "clientTurnId": "retry-client", "plan": {"reply": "ok"}},
            "D:/Project",
            "route",
        )
        assert result["status"] == "projected"
        assert conflict_store.save_calls == 3  # create + conflict + successful retry
        await adapter.shutdown()

        class UnknownStore(Store):
            def __init__(self):
                super().__init__()
                self.save_calls = 0

            def save(self, chats, revisions):
                self.save_calls += 1
                if self.save_calls > 1:
                    raise RuntimeError("disk unavailable")
                return super().save(chats, revisions)

        unknown_store = UnknownStore()
        unknown_adapter = TelegramRuntimeAdapter(Runtime(), unknown_store, lambda _event: None)
        unknown_session = await unknown_adapter.create_session("D:/Project")
        try:
            await unknown_adapter.project_continuation(
                {"sessionId": unknown_session["sessionId"], "turnId": "unknown-turn", "clientTurnId": "unknown-client"},
                "D:/Project",
                "route",
            )
        except ChannelAdapterError as exc:
            assert exc.code == "store_write_failed"
        else:
            raise AssertionError("unknown write failure accepted")
        assert unknown_store.save_calls == 2
        await unknown_adapter.shutdown()

    asyncio.run(scenario())


def test_external_recovery_run_can_be_steered_and_stopped_without_starting_runtime():
    async def scenario():
        store, runtime = Store(), Runtime()
        session_runs = []
        recovery = ChannelRuntimeRecovery(lambda **_kwargs: {"runs": session_runs})
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None, recovery=recovery)
        session = await adapter.create_session("D:/Project")
        session_runs.append({
            "sessionId": session["sessionId"], "projectRoot": "D:/Project",
            "clientTurnId": "desktop-turn", "status": "running",
        })
        steered = await adapter.steer(session["id"], "adjust", "telegram-steer", "D:/Project")
        assert steered["status"] == "steer_submitted" and steered["external"] is True
        stopped = await adapter.stop(session["id"], "D:/Project")
        assert stopped["status"] == "requested" and stopped["external"] is True
        await asyncio.sleep(0)
        assert [call[0] for call in runtime.calls] == ["steer", "cancel"]
        assert all(call[1] == session["sessionId"] for call in runtime.calls)
        await adapter.shutdown()

    asyncio.run(scenario())


def test_submit_entry_steers_unique_external_run_instead_of_starting_new_runtime():
    async def scenario():
        store, runtime = Store(), Runtime()
        runs = []
        recovery = ChannelRuntimeRecovery(lambda **_kwargs: {"runs": runs})
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None, recovery=recovery)
        session = await adapter.create_session("D:/Project")
        runs.append({"sessionId": session["sessionId"], "projectRoot": "D:/Project", "clientTurnId": "desktop-turn", "status": "running"})
        result = await adapter.submit(session["id"], "telegram-turn", "continue", "D:/Project")
        assert result["status"] == "steer_submitted" and result["external"] is True
        await asyncio.sleep(0)
        assert [call[0] for call in runtime.calls] == ["steer"]
        assert runtime.calls[0][2] == "desktop-turn" and runtime.calls[0][1] == session["sessionId"]
        assert runtime.calls[0][3] == "telegram-turn" and runtime.calls[0][4] == "continue"
        assert (session["sessionId"], "desktop-turn") not in adapter._runtime_bridge.contexts
        assert (session["sessionId"], "telegram-turn") not in adapter._runtime_bridge.contexts
        await adapter.shutdown()

    asyncio.run(scenario())


def test_external_recovery_unknown_or_waiting_state_is_not_steerable_or_restartable():
    async def scenario():
        store, runtime = Store(), Runtime()
        runs = []
        recovery = ChannelRuntimeRecovery(lambda **_kwargs: {"runs": runs})
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None, recovery=recovery)
        session = await adapter.create_session("D:/Project")
        runs.append({
            "sessionId": session["sessionId"], "projectRoot": "D:/Project",
            "clientTurnId": "approval-turn", "status": "waiting_for_approval",
        })
        steer = await adapter.steer(session["id"], "change", "telegram-steer", "D:/Project")
        stop = await adapter.stop(session["id"], "D:/Project")
        assert steer["status"] == "notaccepted"
        assert stop["status"] == "notaccepted"
        assert runtime.calls == []
        runs[:] = [{"sessionId": session["sessionId"], "projectRoot": "D:/Project", "clientTurnId": "x", "status": "future_state"}]
        unknown = await adapter.steer(session["id"], "change", "x", "D:/Project")
        assert unknown["status"] == "notaccepted" and unknown["recoveryStatus"] == "unknown"
        assert runtime.calls == []
        await adapter.shutdown()

    asyncio.run(scenario())


def test_general_chat_supports_full_scope_and_bind_preserves_native_identity_history():
    async def scenario():
        store, runtime = Store(), Runtime()
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        chat = await adapter.create_session("", "General")
        assert chat["projectType"] == "general"
        assert (await adapter.list_sessions(""))[0]["projectType"] == "general"
        await adapter.start_turn(chat["sessionId"], "hello", "general-turn", "")
        for _ in range(8):
            await asyncio.sleep(0)
        bound = await adapter.bind_project(chat["id"], "D:/Project")
        assert bound["projectType"] == "unity"
        assert bound["sessionId"] == chat["sessionId"]
        assert store.chats[0]["items"][0]["id"] == "general-turn"
        try:
            await adapter.bind_project(chat["id"], "D:/Other")
        except ChannelAdapterError as exc:
            assert exc.code == "session_scope_mismatch"
        else:
            raise AssertionError("project-bound chat was rebound")
        await adapter.shutdown()
    asyncio.run(scenario())


def test_bind_project_refuses_owned_active_turn_and_dashboard_context_keeps_saved_model():
    async def scenario():
        store = Store()
        runtime = Runtime()
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None)
        chat = await adapter.create_session("")
        store.chats[0]["provider"] = "saved-provider"
        store.chats[0]["model"] = "saved-model"
        result = await adapter.submit(chat["id"], "active-turn", "work", "")
        assert result["status"] == "started"
        try:
            await adapter.bind_project(chat["id"], "D:/Project")
        except ChannelAdapterError as exc:
            assert exc.code == "active_run"
        else:
            raise AssertionError("active general chat was rebound")
        await adapter.stop(chat["id"], "")
        await adapter.shutdown()
    asyncio.run(scenario())


def test_runtime_completion_event_is_emitted_after_transcript_save():
    async def scenario():
        store, runtime, events = Store(), Runtime(), []
        adapter = TelegramRuntimeAdapter(runtime, store, events.append)
        chat = await adapter.create_session("", "General")
        await adapter.submit(chat["id"], "turn-1", "hello", "")
        for _ in range(8):
            await asyncio.sleep(0)
        returned = next(event for event in events if event.get("event") == "turn_returned")
        assert store.chats[0]["items"][-1]["type"] == "agent"
        assert returned["result"] == store.chats[0]["items"][-1]["response"]
        assert not adapter._coordinator.has_active_session(chat["sessionId"])
        await adapter.shutdown()
    asyncio.run(scenario())


def test_dashboard_general_payload_omits_empty_context_limit_for_pydantic_schema():
    async def scenario():
        seen = []

        async def message(params):
            seen.append(params)
            return {}

        runtime = DashboardChannelRuntime(
            message=message, steer=lambda _params: {}, cancel=lambda _params: {},
            current_config=lambda: type("Config", (), {"provider": "global-p", "model": "global-m"})(),
            agent_name="telegram-agent", stop_reason="stop",
        )
        await runtime.start_turn("s", "c", "hello", {
            "chatId": "chat", "projectRoot": "", "projectType": "general", "history": [],
        })
        assert "contextLimit" not in seen[0]
        AgentRuntimeMessageRequest(**seen[0])
    asyncio.run(scenario())


def test_bind_project_accepts_only_real_project_selection_checkpoint():
    async def scenario():
        store, runtime = Store(), Runtime()
        runs = []
        recovery = ChannelRuntimeRecovery(lambda **_kwargs: {"runs": list(runs)})
        adapter = TelegramRuntimeAdapter(runtime, store, lambda _event: None, recovery=recovery)
        chat = await adapter.create_session("")
        runs.append({
            "sessionId": chat["sessionId"], "projectRoot": "", "clientTurnId": "turn-before-selection",
            "status": "blocked", "nextStep": "needs_user_action",
            "skillTool": "vrcforge_request_project_selection", "skillStatus": "needs_user_action",
            "approvalIds": [],
            "steps": [{"kind": "skill", "tool": "vrcforge_request_project_selection",
                       "status": "needs_user_action", "outcome": {"status": "needs_user_action"},
                       "result": {"ok": True, "status": "needs_user_action", "projectSelectionRequested": True}}],
        })
        bound = await adapter.bind_project(chat["id"], "D:/Project")
        assert bound["projectType"] == "unity"
        await adapter.shutdown()
    asyncio.run(scenario())
