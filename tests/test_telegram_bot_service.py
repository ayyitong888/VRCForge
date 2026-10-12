import asyncio

import pytest

import telegram_bot_service as module
from telegram_bot_service import TelegramBotService
from telegram_bot_settings import TelegramSettings
from telegram_bot_store import TelegramBotStore
from telegram_bot_approvals import TelegramApprovalController


def settings(tmp_path):
    value = TelegramSettings(tmp_path / "config", protect=lambda data: data, unprotect=lambda data: data)
    value.save(token="123:" + "a" * 24, owner_id=42, projects=[str(tmp_path)])
    return value


def test_menu_does_not_block_dispatch_or_stop_and_connection_test_is_read_only(tmp_path):
    async def scenario():
        entered, cancelled, calls = asyncio.Event(), asyncio.Event(), []
        class Transport:
            async def get_me(self):
                calls.append("getMe")
                return {"id": 77, "is_bot": True}
            async def get_updates(self, **kwargs):
                await asyncio.Event().wait()
            async def set_my_commands(self, commands, *, owner_id):
                calls.append("setMyCommands")
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            async def close(self):
                calls.append("close")
        class Runtime:
            async def shutdown(self):
                pass
        service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "menu.db",
            runtime_factory=lambda emit: Runtime(), owns_backend=lambda: True,
            transport_factory=lambda token: Transport())
        assert await service.test_connection() == {"ok": True}
        assert calls == ["getMe", "close"]
        await asyncio.wait_for(service.start(), 0.5)
        await asyncio.wait_for(entered.wait(), 0.5)
        menu = service._menu
        def update(owner):
            return {"message": {"from": {"id": owner}, "chat": {"id": owner, "type": "private"}, "text": "/help"}}
        assert await service._host.dispatch(update(43)) is None
        assert not menu._seen_owner
        result = await asyncio.wait_for(service._host.dispatch(update(42)), 0.5)
        assert result["text"] and menu._seen_owner
        assert service.status()["errorCode"] is None
        menu.error_code = "menu_registration_failed"
        assert service.status()["menuErrorCode"] == "menu_registration_failed"
        assert service.status()["errorCode"] is None
        await asyncio.wait_for(service.stop(), 0.5)
        assert cancelled.is_set() and menu._task is None and service._menu is None
    asyncio.run(scenario())


def test_start_failure_after_host_started_closes_every_owner_despite_cleanup_error(tmp_path, monkeypatch):
    async def scenario():
        closed = []
        captured = {}
        class Transport:
            async def get_me(self):
                return {"id": 77, "is_bot": True}
            async def get_updates(self, **kwargs):
                await asyncio.sleep(60)
            async def close(self):
                closed.append("transport")
        class Runtime:
            async def shutdown(self):
                closed.append("runtime")
                raise RuntimeError("runtime cleanup failed")
        class BrokenMonitor:
            def __init__(self, *args):
                pass
            async def start(self):
                captured["host"] = service._host
                captured["store"] = service._store
                assert service._host._tasks
                raise RuntimeError("original start failure")
            async def close(self):
                closed.append("monitor")
                raise RuntimeError("monitor cleanup failed")
        monkeypatch.setattr(module, "TelegramBotMonitor", BrokenMonitor)
        service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "bot.db",
            runtime_factory=lambda emit: Runtime(), owns_backend=lambda: True,
            transport_factory=lambda token: Transport(), monitor_snapshot=lambda *args: None)
        with pytest.raises(RuntimeError, match="original start failure"):
            await service.start()
        assert "monitor" in closed and "runtime" in closed and "transport" in closed
        assert captured["store"]._closed
        assert captured["host"]._closed
        assert not captured["host"]._tasks
        assert service._host is None and service._monitor is None and service._runtime is None
        assert service.status()["errorCode"] == "connection_failed"
    asyncio.run(scenario())


def test_monitor_keeps_new_selected_chat_in_same_project(tmp_path):
    service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "bot.db",
        runtime_factory=lambda emit: None, owns_backend=lambda: True)
    with TelegramBotStore(service.database_path, "77") as store:
        service._store = store
        project = str(tmp_path.resolve())
        store.watch_session("42", project, "runtime-old")
        store.set_binding("42", project, "chat-new")
        assert {row["session_id"] for row in service._monitor_bindings()} == {"runtime-old", "chat-new"}
        store.set_binding("42", project, "runtime-old")
        assert len(service._monitor_bindings()) == 1
    service._store = None


def test_test_connection_uses_saved_proxy_and_closes_temporary_transport(tmp_path):
    async def scenario():
        seen = {}
        class Transport:
            def __init__(self, token, **kwargs):
                seen.update(kwargs)
                self.closed = False
            async def get_me(self):
                return {"id": 77, "is_bot": True}
            async def close(self):
                self.closed = True
                seen["closed"] = True
        value = TelegramSettings(tmp_path / "config", protect=lambda data: data, unprotect=lambda data: data)
        value.save(token="123:" + "a" * 24, owner_id=42, projects=[str(tmp_path)],
                   proxy_mode="custom", proxy_url="http://127.0.0.1:8080")
        service = TelegramBotService(settings=value, database_path=tmp_path / "bot.db",
            runtime_factory=lambda emit: None, owns_backend=lambda: True, transport_factory=Transport)
        assert await service.test_connection() == {"ok": True}
        assert seen == {"proxy_mode": "custom", "proxy_url": "http://127.0.0.1:8080", "closed": True}
    asyncio.run(scenario())


def test_observer_bounds_and_cancels_owned_projection_tasks_on_stop(tmp_path):
    async def scenario():
        entered, cancelled = [], []
        class Transport:
            async def get_me(self):
                return {"id": 77, "is_bot": True}
            async def get_updates(self, **kwargs):
                await asyncio.sleep(60)
            async def close(self):
                pass
        class Runtime:
            async def project_continuation(self, payload, project, route):
                entered.append(payload["turnId"])
                try:
                    await asyncio.sleep(60)
                finally:
                    cancelled.append(payload["turnId"])
            async def shutdown(self):
                pass
        service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "observe.db",
            runtime_factory=lambda emit: Runtime(), owns_backend=lambda: True,
            transport_factory=lambda token: Transport())
        await service.start()
        service._store.watch_session("42", str(tmp_path), "watched")
        host = service._host
        try:
            for index in range(9):
                service.observe_continuation({"sessionId": "watched", "turnId": str(index)})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert len(entered) == 8 and len(service._projections) == 8
            assert service.status()["errorCode"] == "continuation_projection_busy"
        finally:
            await asyncio.wait_for(service.stop(), 0.8)
        assert set(cancelled) == set(entered)
        assert not service._projections and not host._tasks
        service.observe_continuation({"sessionId": "watched", "turnId": "after-stop"})
        await asyncio.sleep(0)
        assert len(entered) == 8
    asyncio.run(scenario())


def test_waiting_approval_queues_scoped_card_without_approvals_command(tmp_path):
    async def scenario():
        class Port:
            async def list_pending(self, project_root, session_id):
                return [{"approvalId": "a1", "status": "pending", "targetTool": "write",
                         "reason": "change Avatar/Body", "projectRoot": project_root,
                         "sessionId": session_id, "arguments": {"objectPath": "Avatar/Body"}}]
            async def decide(self, approval_id, project_root, approve):
                return {"ok": True}

        service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "approval.db",
            runtime_factory=lambda emit: None, owns_backend=lambda: True, approval_port=Port())
        service._store = TelegramBotStore(service.database_path, "77")
        class Host:
            def emit(self, event):
                pass
            async def close(self):
                pass
        service._host = Host()
        service._approval_controller = TelegramApprovalController(service.approval_port)
        binding = {"route": "42", "project_root": str(tmp_path), "session_id": "session-1"}
        service._watched["session-1"] = binding
        event = {"event": "telegram_task_status_changed", "sessionId": "session-1",
                 "phase": "waiting_approval", "result": {"status": "waiting_approval"},
                 "metadata": {"route": "42"}}
        for _ in range(300):
            service._emit(event)
        service._emit({"event": "turn_returned", "sessionId": "session-1",
                       "result": {"status": "needs_user_action"}, "metadata": {"route": "42"}})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        item = service._store.claim_outbox()[0]
        assert item.payload["chat_id"] == 42
        assert "需要审批" in item.payload["text"]
        assert item.payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("vrca:")
        service._store.close()
    asyncio.run(scenario())


def test_no_pending_approval_does_not_queue_a_bogus_card(tmp_path):
    async def scenario():
        class Port:
            async def list_pending(self, project_root, session_id):
                return [{"approvalId": "other", "status": "pending", "targetTool": "write",
                         "reason": "other session", "projectRoot": project_root,
                         "sessionId": "different-session", "arguments": {"objectPath": "Other"}}]
            async def decide(self, approval_id, project_root, approve):
                return {"ok": True}

        service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "empty.db",
            runtime_factory=lambda emit: None, owns_backend=lambda: True, approval_port=Port())
        service._store = TelegramBotStore(service.database_path, "77")
        class Host:
            def emit(self, event):
                pass
        service._host = Host()
        service._approval_controller = TelegramApprovalController(service.approval_port)
        binding = {"route": "42", "project_root": str(tmp_path), "session_id": "session-1"}
        service._watched["session-1"] = binding
        service._emit({"event": "turn_returned", "sessionId": "session-1",
                       "result": {"status": "waiting_approval"}, "metadata": {"route": "42"}})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert service._store.claim_outbox() == []
        service._store.close()
    asyncio.run(scenario())


def test_approval_notification_task_is_cancelled_on_stop(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        class Port:
            async def list_pending(self, project_root, session_id):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
                return []
            async def decide(self, approval_id, project_root, approve):
                return {"ok": True}

        service = TelegramBotService(settings=settings(tmp_path), database_path=tmp_path / "cancel.db",
            runtime_factory=lambda emit: None, owns_backend=lambda: True, approval_port=Port())
        service._store = TelegramBotStore(service.database_path, "77")
        class Host:
            def emit(self, event):
                pass
            async def close(self):
                pass
        service._host = Host()
        service._approval_controller = TelegramApprovalController(service.approval_port)
        binding = {"route": "42", "project_root": str(tmp_path), "session_id": "session-1"}
        service._watched["session-1"] = binding
        service._emit({"event": "telegram_task_status_changed", "sessionId": "session-1",
                       "result": {"status": "waiting_approval"}, "metadata": {"route": "42"}})
        await asyncio.wait_for(entered.wait(), 0.5)
        await asyncio.wait_for(service.stop(), 0.5)
        assert cancelled.is_set()
        assert not service._approval_tasks
    asyncio.run(scenario())
