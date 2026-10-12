"""Offline composition proof: real Telegram layers through dashboard ports.

Only network I/O, platform encryption, and native runtime/chat persistence are
local fakes. No service, dispatcher, adapter, coordinator or queue is mocked.
"""
import asyncio
import copy
import json
import sqlite3
import threading
from types import SimpleNamespace
import pytest

from telegram_bot_service import TelegramBotService
from telegram_bot_settings import TelegramSettings
from telegram_dashboard_integration import make_telegram_runtime_factory


OWNER = 12345


class LocalTransport:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.messages = []
        self.closed = False
        self.bot_id = 98765

    async def get_me(self):
        return {"id": self.bot_id, "is_bot": True}

    async def get_updates(self, **_kwargs):
        return [await self.incoming.get()]

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        self.messages.append({"chat_id": chat_id, "text": text, **copy.deepcopy(kwargs)})
        return {"message_id": len(self.sent)}

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.sent.append((chat_id, text))
        self.messages.append({"chat_id": chat_id, "message_id": message_id, "text": text, **copy.deepcopy(kwargs)})
        return {"message_id": message_id}

    async def answer_callback_query(self, callback_id, **kwargs):
        return True

    def click(self, number, data):
        self.incoming.put_nowait({"update_id": number, "callback_query": {
            "id": f"callback-{number}", "from": {"id": OWNER, "is_bot": False},
            "message": {"message_id": number, "chat": {"id": OWNER, "type": "private"}},
            "data": data,
        }})

    async def close(self):
        self.closed = True

    def push(self, number, text):
        self.incoming.put_nowait({"update_id": number, "message": {
            "from": {"id": OWNER, "is_bot": False},
            "chat": {"id": OWNER, "type": "private"}, "text": text,
        }})


class LocalDashboard:
    """CAS fake intentionally rejects stale revisions and copies snapshots."""
    def __init__(self):
        self.chats = []
        self.revision = 0
        self.lock = threading.Lock()
        self.calls = []
        self.release = asyncio.Event()
        self.hold = False
        self.response = {"ok": True, "plan": {"reply": "INTEGRATION_FINAL_RESPONSE"}}

    def load(self):
        with self.lock:
            return {"chats": copy.deepcopy(self.chats), "sourceRevisions": [
                {"storeId": "app", "digest": str(self.revision)}]}

    def save(self, chats, revisions):
        with self.lock:
            assert revisions == [{"storeId": "app", "digest": str(self.revision)}], "CAS conflict"
            self.chats = copy.deepcopy(chats)
            self.revision += 1
            return {"ok": True}

    async def message(self, params):
        self.calls.append(("message", copy.deepcopy(params)))
        if self.hold:
            await self.release.wait()
        return copy.deepcopy(self.response)

    def steer(self, params):
        self.calls.append(("steer", copy.deepcopy(params)))
        return {"accepted": True}

    def cancel(self, params):
        self.calls.append(("cancel", copy.deepcopy(params)))
        self.release.set()
        return {"status": "requested"}


async def until(predicate, description):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(wait(), timeout=4)
    except asyncio.TimeoutError:
        raise AssertionError(description) from None


async def setup_service(tmp_path, monitor_snapshot=None):
    project = tmp_path / "Project"
    project.mkdir()
    transport, dashboard = LocalTransport(), LocalDashboard()
    settings = TelegramSettings(tmp_path / "telegram.protected", protect=lambda data: b"test:" + data,
                                unprotect=lambda data: data.removeprefix(b"test:"))
    service = TelegramBotService(settings=settings, database_path=tmp_path / "telegram.sqlite3",
        owns_backend=lambda: True, transport_factory=lambda _token: transport,
        monitor_snapshot=monitor_snapshot,
        runtime_factory=make_telegram_runtime_factory(load_chats=dashboard.load, save_chats=dashboard.save,
            message=dashboard.message, steer=dashboard.steer, cancel=dashboard.cancel,
            current_config=lambda: SimpleNamespace(provider="local-provider", model="local-model")))
    await service.configure(token="123456:" + "x" * 24, owner_id=OWNER, projects=[str(project)])
    assert service.status()["configured"] is True
    assert "token" not in service.status()
    await service.start()
    return service, transport, dashboard, str(project.resolve())


def test_service_injects_status_snapshot_into_real_dispatcher(tmp_path):
    async def scenario():
        calls = []
        async def snapshot(project, session):
            calls.append((project, session))
            return {"taskStatus": "running", "unityStatus": "connected", "progressSummary": "checking avatar"}
        service, transport, dashboard, project = await setup_service(tmp_path, snapshot)
        try:
            await choose_and_create(transport, dashboard, project)
            transport.push(4, "/status")
            await until(lambda: any("Unity/Core：已连接" in text and "checking avatar" in text
                                   for _, text in transport.sent), "live status not delivered through service")
            assert (project, dashboard.chats[0]["id"]) in calls
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_runtime_status_notifies_scoped_phases_with_identity_and_bounded_projection(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        try:
            await choose_and_create(transport, dashboard, project)
            session = dashboard.chats[0]["sessionId"]
            events = []
            original_emit = service._host.emit

            def capture(event):
                events.append(copy.deepcopy(dict(event)))
                return original_emit(event)

            service._host.emit = capture
            base = {"sessionId": session, "turnId": "turn-status-1", "clientTurnId": "client-status-1"}
            service.observe_runtime_status({**base, "phase": "preparing", "timeline": [{"secret": "must-not-forward"}]})
            await asyncio.sleep(0)
            service.observe_runtime_status({**base, "phase": "preparing", "timeline": [{"secret": "duplicate"}]})
            service.observe_runtime_status({**base, "phase": "waiting_for_approval", "timeline": [{"secret": "approval"}]})
            service.observe_runtime_status({**base, "sessionId": "foreign-session", "phase": "running_tool"})
            await asyncio.sleep(0.05)
            status_events = [event for event in events if event.get("event") == "telegram_task_status_changed"]
            assert len(status_events) == 1
            assert [event["result"]["status"] for event in status_events] == ["preparing"]
            for event in status_events:
                assert event["sessionId"] == session
                assert event["turnId"] == "turn-status-1"
                assert event["clientTurnId"] == "client-status-1"
                assert event["phase"] == "preparing"
                assert "timeline" not in event
            assert not any(event.get("sessionId") == "foreign-session" for event in status_events)
        finally:
            await service.stop()

    asyncio.run(scenario())


async def choose_and_create(transport, dashboard, project):
    transport.push(1, "/projects")
    await until(lambda: any(project in text for _, text in transport.sent), "project allowlist reply missing")
    transport.push(2, "/project 1")
    await until(lambda: any("已选择工程" in text for _, text in transport.sent), "project selection reply missing")
    transport.push(3, "/new Integration")
    await until(lambda: any("已新建会话" in text for _, text in transport.sent), "new-session reply missing")
    assert len(dashboard.chats) == 1


def test_service_projects_new_message_reaches_native_and_durable_outbox(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        host = service._host
        try:
            await choose_and_create(transport, dashboard, project)
            transport.push(4, "inspect this avatar")
            await until(lambda: any("INTEGRATION_FINAL_RESPONSE" in text for _, text in transport.sent),
                        "final native response did not reach Telegram sender")
            await until(lambda: any(item["type"] == "agent" for item in dashboard.chats[0]["items"]),
                        "final response missing from dashboard transcript")
            kind, params = dashboard.calls[0]
            assert kind == "message"
            assert params["chatId"] == dashboard.chats[0]["id"]
            assert params["session_id"] == dashboard.chats[0]["sessionId"]
            assert params["projectRoot"] == project
            assert params["provider"] == "local-provider"
            assert params["model"] == "local-model"
            assert all(chat_id == OWNER for chat_id, _ in transport.sent)
            with sqlite3.connect(service.database_path) as connection:
                rows = connection.execute("SELECT payload, status FROM telegram_outbox").fetchall()
            assert any("INTEGRATION_FINAL_RESPONSE" in json.loads(payload)["text"] and status == "sent"
                       for payload, status in rows)
        finally:
            await service.stop()
        assert transport.closed
        assert not host.status()["running"]
        assert not host._tasks
        assert service.status()["enabled"] is False
    asyncio.run(scenario())


def test_service_busy_message_steers_and_stop_reaches_native_before_turn_finishes(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        dashboard.hold = True
        try:
            await choose_and_create(transport, dashboard, project)
            transport.push(4, "long running task")
            await until(lambda: any(kind == "message" for kind, _ in dashboard.calls), "native turn not started")
            transport.push(5, "change the plan while busy")
            await until(lambda: any(kind == "steer" for kind, _ in dashboard.calls), "busy message did not reach native steer")
            assert not dashboard.release.is_set()
            transport.push(6, "/stop")
            await until(lambda: any(kind == "cancel" for kind, _ in dashboard.calls), "stop did not reach native cancel")
            start = next(params for kind, params in dashboard.calls if kind == "message")
            steer = next(params for kind, params in dashboard.calls if kind == "steer")
            cancel = next(params for kind, params in dashboard.calls if kind == "cancel")
            assert steer["targetClientTurnId"] == start["clientTurnId"]
            assert steer["sessionId"] == start["session_id"]
            assert steer["message"] == "change the plan while busy"
            assert cancel["clientTurnId"] == start["clientTurnId"]
            assert cancel["session_id"] == start["session_id"]
            assert len([1 for kind, _ in dashboard.calls if kind == "message"]) == 1
        finally:
            await service.stop()
        assert transport.closed
        assert service._runtime is None
    asyncio.run(scenario())


def test_observed_continuation_only_projects_watched_session_once_without_execution(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        dashboard.response = {"ok": True, "status": "waiting_approval",
                              "plan": {"reply": "APPROVAL_REQUIRED"}}
        host = service._host
        try:
            await choose_and_create(transport, dashboard, project)
            transport.push(4, "change avatar after approval")
            await until(lambda: any("APPROVAL_REQUIRED" in text for _, text in transport.sent),
                        "initial approval reply not sent")
            await until(lambda: not service._runtime._event_tasks, "initial transcript not settled")
            request = next(params for kind, params in dashboard.calls if kind == "message")
            calls_before = copy.deepcopy(dashboard.calls)
            payload = {"ok": True, "sessionId": request["session_id"],
                       "turnId": "approval-continuation-1", "clientTurnId": request["clientTurnId"],
                       "plan": {"reply": "CONTINUATION_RESULT"}}
            # Exercise the worker-thread entry, not the internal scheduling helper.
            await asyncio.to_thread(service.observe_continuation, {**payload, "sessionId": "foreign-session",
                                    "plan": {"reply": "FOREIGN_MUST_NOT_SEND"}})
            await asyncio.to_thread(service.observe_continuation, payload)
            await until(lambda: any("CONTINUATION_RESULT" in text for _, text in transport.sent),
                        "watched continuation reply not delivered")
            await until(lambda: not service._projections, "continuation projection task leaked")
            await asyncio.to_thread(service.observe_continuation, payload)
            await asyncio.sleep(0.05)
            await until(lambda: not service._projections, "duplicate projection task leaked")
            assert sum("CONTINUATION_RESULT" in text for _, text in transport.sent) == 1
            assert not any("FOREIGN_MUST_NOT_SEND" in text for _, text in transport.sent)
            assert sum(item["id"] == "approval-continuation-1"
                       for item in dashboard.chats[0]["items"]) == 1
            assert dashboard.calls == calls_before
        finally:
            await service.stop()
        assert not service._projections and not host._tasks
        assert service._loop is None
        service.observe_continuation(payload)
        await asyncio.sleep(0)
        assert not service._projections
    asyncio.run(scenario())


def test_desktop_result_notifies_watched_session_without_writing_chat(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        try:
            await choose_and_create(transport, dashboard, project)
            chat = dashboard.chats[0]
            before = copy.deepcopy(dashboard.chats)
            calls = copy.deepcopy(dashboard.calls)
            payload = {"ok": True, "sessionId": chat["sessionId"], "turnId": "desktop-1",
                "clientTurnId": "desktop-user-1", "plan": {"reply": "DESKTOP_SESSION_RESULT"}}
            service.notify_foreground_result(payload)
            service.notify_foreground_result(payload)
            service.notify_foreground_result({**payload, "sessionId": "foreign", "plan": {"reply": "FOREIGN"}})
            await until(lambda: any(text == "DESKTOP_SESSION_RESULT" for _, text in transport.sent), "desktop result not forwarded")
            assert sum(text == "DESKTOP_SESSION_RESULT" for _, text in transport.sent) == 1
            assert not any(text == "FOREIGN" for _, text in transport.sent)
            assert dashboard.chats == before and dashboard.calls == calls
        finally:
            await service.stop()
    asyncio.run(scenario())


class CompletedLedger:
    def __init__(self, payloads):
        self.payloads = payloads
        self.reads = []

    async def completed_continuations(self, session, project):
        self.reads.append((session, project))
        return copy.deepcopy(self.payloads.get(session, []))


def test_natural_request_starts_directly_then_new_and_switch_keep_history(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        def choices(since):
            return [button for message in transport.messages[since:]
                    for row in message.get("reply_markup", {}).get("inline_keyboard", []) for button in row]
        async def choose(number, label, since):
            await until(lambda: any(label in b["text"] for b in choices(since)), f"missing button {label}")
            data = next(b["callback_data"] for b in choices(since) if label in b["text"])
            after = len(transport.messages)
            transport.click(number, data)
            return after, data
        try:
            transport.push(1, "看看我的模型有哪些材质")
            await until(lambda: any("INTEGRATION_FINAL_RESPONSE" in text for _, text in transport.sent), "no resumed result")
            await until(lambda: any(item["type"] == "agent" for item in dashboard.chats[0]["items"]), "history missing")
            request = next(params for kind, params in dashboard.calls if kind == "message")
            assert request["message"] == "看看我的模型有哪些材质"
            assert request["clientTurnId"] == f"telegram-{OWNER}-1"
            assert not request["projectRoot"] and request["projectType"] == "general"
            assert any(m.get("reply_markup", {}).get("remove_keyboard") is True for m in transport.messages)
            first = copy.deepcopy(dashboard.chats[0])
            transport.click(4, "setup:old:n@0")
            await until(lambda: any("失效" in text for _, text in transport.sent), "stale callback did not reject")
            assert len(dashboard.chats) == 1
            assert sum(kind == "message" for kind, _ in dashboard.calls) == 1
            after = len(transport.messages)
            transport.push(5, "新对话")
            await until(lambda: len(dashboard.chats) == 2, "new keyboard action did not create session")
            await until(lambda: any("已新建会话" in m["text"] for m in transport.messages[after:]), "missing switch confirmation")
            assert dashboard.chats[0] == first
            after = len(transport.messages)
            transport.push(6, "切换对话")
            after, _ = await choose(8, "看看我的模型有哪些材质", after)
            await until(lambda: service._store.get_binding(str(OWNER))["session_id"] == first["id"], "did not bind original chat")
            transport.push(9, "继续检查")
            await until(lambda: sum(kind == "message" for kind, _ in dashboard.calls) == 2, "bound text did not reach runtime")
            latest = [params for kind, params in dashboard.calls if kind == "message"][-1]
            assert latest["session_id"] == first["sessionId"]
            assert len(dashboard.chats) == 2
            assert not any("你：" in m["text"] for m in transport.messages)  # No unsolicited history dump.
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_switch_while_running_keeps_old_result_label_and_new_binding(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        dashboard.hold = True
        try:
            await choose_and_create(transport, dashboard, project)
            first = dashboard.chats[0]
            transport.push(4, "long task")
            await until(lambda: bool(dashboard.calls), "old task did not start")
            transport.push(5, "新对话")
            await until(lambda: len(dashboard.chats) == 2, "new conversation was blocked by old task")
            second = dashboard.chats[1]
            await until(lambda: service._store.get_binding(str(OWNER))["session_id"] == second["id"], "new binding missing")
            assert not dashboard.release.is_set()
            dashboard.release.set()
            await until(lambda: any("来自其他对话：Integration" in text and "INTEGRATION_FINAL_RESPONSE" in text
                                   for _, text in transport.sent), "old result has no source label")
            assert service._store.get_binding(str(OWNER))["session_id"] == second["id"]
            assert not second["items"]
            assert dashboard.calls[0][1]["session_id"] == first["sessionId"]
        finally:
            await service.stop()
    asyncio.run(scenario())


async def finish_initial_task(service, transport, dashboard, update_id):
    transport.push(update_id, "inspect")
    await until(lambda: any(kind == "message" and params["clientTurnId"] == f"telegram-{OWNER}-{update_id}"
                           for kind, params in dashboard.calls), "initial runtime not called")
    await until(lambda: any(item.get("type") == "agent" for item in dashboard.chats[-1]["items"]),
                "initial transcript not settled")
    await until(lambda: not service._runtime._event_tasks and not service._runtime._coordinator.active_sessions,
                "initial turn not settled")


def test_restart_recovers_both_watched_sessions_from_ledger_without_reexecution(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        try:
            await choose_and_create(transport, dashboard, project)
            await finish_initial_task(service, transport, dashboard, 4)
            transport.push(5, "/new Second")
            await until(lambda: len(dashboard.chats) == 2, "second chat not created")
            await finish_initial_task(service, transport, dashboard, 6)
            calls = copy.deepcopy(dashboard.calls)
            sessions = {chat["sessionId"] for chat in dashboard.chats}
            assert {row["session_id"] for row in service._store.watches()} == sessions
            await service.stop()
            ledger = CompletedLedger({chat["sessionId"]: [{"ok": True, "sessionId": chat["sessionId"],
                "turnId": f"offline-{index}", "clientTurnId": f"telegram-{OWNER}-{4 + index * 2}",
                "plan": {"reply": f"OFFLINE_RESULT_{index}"}}] for index, chat in enumerate(dashboard.chats)})
            service.recovery = ledger
            replay_transport = LocalTransport()
            service.transport_factory = lambda token: replay_transport
            await service.start()
            await until(lambda: len([text for _, text in replay_transport.sent if "OFFLINE_RESULT_" in text]) == 2,
                        "both offline results not recovered")
            assert any("来自其他对话：Integration" in text and "OFFLINE_RESULT_0" in text
                       for _, text in replay_transport.sent)
            assert set(ledger.reads) == {(session, project) for session in sessions}
            assert dashboard.calls == calls
            await service.stop()
            again = LocalTransport()
            service.transport_factory = lambda token: again
            await service.start()
            await until(lambda: not service._projections, "repeat recovery did not settle")
            await asyncio.sleep(0.3)
            assert not any("OFFLINE_RESULT_" in text for _, text in again.sent)
            assert dashboard.calls == calls
        finally:
            await service.stop()
        assert not service._projections
    asyncio.run(scenario())


@pytest.mark.parametrize("revoked", ["project", "owner", "bot"])
def test_restart_does_not_recover_revoked_project_owner_or_other_bot(tmp_path, revoked):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        try:
            await choose_and_create(transport, dashboard, project)
            await finish_initial_task(service, transport, dashboard, 4)
            session = dashboard.chats[0]["sessionId"]
            await service.stop()
            calls = copy.deepcopy(dashboard.calls)
            ledger = CompletedLedger({session: [{"ok": True, "sessionId": session, "turnId": "revoked",
                "clientTurnId": f"telegram-{OWNER}-4", "plan": {"reply": "MUST_NOT_RECOVER"}}]})
            service.recovery = ledger
            if revoked == "project":
                other = tmp_path / "OtherProject"
                other.mkdir()
                await service.configure(token="", owner_id=OWNER, projects=[str(other)])
            if revoked == "owner":
                await service.configure(token="", owner_id=OWNER + 1, projects=[project])
            fresh = LocalTransport()
            if revoked == "bot":
                fresh.bot_id += 1
            service.transport_factory = lambda token: fresh
            await service.start()
            await until(lambda: not service._projections, "scoped recovery did not settle")
            await asyncio.sleep(0.3)
            assert ledger.reads == []
            assert not service._watched
            assert not any("MUST_NOT_RECOVER" in text for _, text in fresh.sent)
            assert dashboard.calls == calls
        finally:
            await service.stop()
    asyncio.run(scenario())
