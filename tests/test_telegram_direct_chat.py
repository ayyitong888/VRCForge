"""Composition tests use real channel/session/store code; network and provider are local fakes."""
import asyncio
import copy
import pytest

from test_telegram_service_integration import setup_service, until, OWNER


def test_first_message_general_restart_and_new_chat_keep_history(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        try:
            transport.push(1, "你好，先讨论一下搭配")
            await until(lambda: len(dashboard.chats) == 1 and len(dashboard.chats[0]["items"]) == 2, "general turn not saved")
            original = copy.deepcopy(dashboard.chats[0])
            params = dashboard.calls[0][1]
            assert params["projectType"] == "general" and not params["projectRoot"]
            assert not any("请选择工程" in text for _, text in transport.sent)
            await service.stop()
            await service.start()
            transport.push(2, "接着刚才说的")
            await until(lambda: len(dashboard.calls) >= 2, "restarted binding missing")
            assert dashboard.calls[-1][1]["session_id"] == original["sessionId"]
            assert dashboard.calls[-1][1]["history"]
            await until(lambda: len(dashboard.chats[0]["items"]) == 4, "second turn not saved")
            transport.push(3, "新对话")
            await until(lambda: len(dashboard.chats) == 2, "new general chat missing")
            assert dashboard.chats[1]["sessionId"] != original["sessionId"]
            assert dashboard.chats[0]["items"][:2] == original["items"]
            assert not dashboard.chats[1]["projectPath"]
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_project_request_only_when_runtime_asks_continues_same_chat_once(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        async def message(params):
            dashboard.calls.append(("message", copy.deepcopy(params)))
            if not params.get("projectRoot"):
                result = await asyncio.to_thread(service.request_project_selection, {
                    "sessionId": params["session_id"], "chatId": params["chatId"],
                    "clientTurnId": params["clientTurnId"], "projectRoot": "", "requestText": params["message"],
                })
                assert result["status"] == "needs_user_action"
                return {"ok": True, "plan": {"reply": result["summary"]}}
            return {"ok": True, "plan": {"reply": "PROJECT_CONTINUED"}}
        service._runtime._runtime_bridge.runtime._message = message
        try:
            transport.push(1, "把外套改成深蓝色，拉链保持银色")
            await until(lambda: any("请选择工程" in row["text"] for row in transport.messages), "runtime project chooser not shown")
            original = copy.deepcopy(dashboard.chats[0])
            chooser = next(row for row in transport.messages if "请选择工程" in row["text"])
            button = chooser["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            transport.click(2, button)
            await until(lambda: any("PROJECT_CONTINUED" in row["text"] for row in transport.messages), "selected project not resumed")
            assert len(dashboard.chats) == 1
            assert dashboard.chats[0]["id"] == original["id"]
            assert dashboard.chats[0]["sessionId"] == original["sessionId"]
            params = dashboard.calls[-1][1]
            assert params["projectRoot"] == project and params["projectType"] == "unity"
            assert "拉链保持银色" in params["message"] and params["history"]
            assert params["clientTurnId"] != dashboard.calls[0][1]["clientTurnId"]
            transport.click(3, button)
            await until(lambda: any("失效" in row["text"] for row in transport.messages), "duplicate not rejected")
            assert len(dashboard.calls) == 2
            assert service._store.project_request(str(OWNER)) is None
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_project_request_scope_and_pending_stop(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        try:
            transport.push(1, "hello")
            await until(lambda: dashboard.chats and len(dashboard.chats[0]["items"]) == 2, "general session missing")
            chat = dashboard.chats[0]
            scope = {"sessionId": chat["sessionId"], "chatId": chat["id"], "clientTurnId": "telegram-test",
                     "projectRoot": "", "requestText": "inspect"}
            for bad in ({"sessionId": "foreign"}, {"chatId": "foreign"}, {"projectRoot": project}):
                result = await asyncio.to_thread(service.request_project_selection, {**scope, **bad})
                assert result["status"] == "failed"
            assert service._store.project_request(str(OWNER)) is None
            await asyncio.to_thread(service.request_project_selection, scope)
            service._offer_project_selection({"sessionId": chat["sessionId"], "clientTurnId": "telegram-test"})
            transport.push(2, "/stop")
            await until(lambda: service._store.project_request(str(OWNER)) is None, "stop did not cancel pending request")
            assert not service._store.get_binding(str(OWNER))["project_root"]
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_choose_existing_desktop_chat_without_dumping_history(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        dashboard.chats = [{"id": f"desktop-{i}", "sessionId": f"sess-desktop-{i}", "title": f"衣装{i}",
                            "projectPath": project, "items": [{"id": "old", "type": "user", "text": "PRIVATE_HISTORY"}],
                            "model": "saved-model", "provider": "saved-provider"} for i in range(8)]
        try:
            response = await service._dispatcher.dispatch({"update_id": 1, "message": {
                "from": {"id": OWNER}, "chat": {"id": OWNER, "type": "private"}, "text": "/sessions 衣装"}})
            buttons = response["messages"][0]["reply_markup"]["inline_keyboard"]
            assert len([row for row in buttons if ":s@" in row[0]["callback_data"]]) == 5
            assert "PRIVATE_HISTORY" not in str(response)
            transport.click(2, buttons[0][0]["callback_data"])
            await until(lambda: service._store.get_binding(str(OWNER)) is not None, "selection missing")
            chosen = service._store.get_binding(str(OWNER))["session_id"]
            transport.push(3, "继续调整")
            await until(lambda: bool(dashboard.calls), "desktop session not continued")
            assert dashboard.calls[0][1]["chatId"] == chosen
            assert dashboard.calls[0][1]["model"] == "saved-model"
            assert dashboard.calls[0][1]["history"][0]["text"] == "PRIVATE_HISTORY"
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_stop_during_project_binding_keeps_saved_scope_without_resuming(tmp_path):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        saved, release = asyncio.Event(), asyncio.Event()
        try:
            transport.push(1, "hello")
            await until(lambda: dashboard.chats and len(dashboard.chats[0]["items"]) == 2, "general session missing")
            chat = dashboard.chats[0]
            original_bind = service._runtime.bind_project
            async def paused_bind(chat_id, selected):
                result = await original_bind(chat_id, selected)
                saved.set()
                await release.wait()
                return result
            service._runtime.bind_project = paused_bind
            prompt = service._dispatcher.onboarding.choose_project(str(OWNER), 2,
                chat_id=chat["id"], text="inspect my avatar")
            transport.click(2, prompt["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
            await asyncio.wait_for(saved.wait(), 2)
            transport.push(3, "/stop")
            await until(lambda: not service._dispatcher.onboarding.has_pending(str(OWNER)), "stop was blocked by selection")
            release.set()
            await until(lambda: service._store.get_binding(str(OWNER))["project_root"] == project, "saved chat and route diverged")
            assert dashboard.chats[0]["projectPath"] == project
            assert len([call for call in dashboard.calls if call[0] == "message"]) == 1
            assert service._store.project_request(str(OWNER)) is None
        finally:
            release.set()
            await service.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("pause_at", ["get_session", "save"])
def test_stop_before_resumed_turn_starts_prevents_execution(tmp_path, pause_at):
    async def scenario():
        service, transport, dashboard, project = await setup_service(tmp_path)
        paused, release = asyncio.Event(), asyncio.Event()
        try:
            transport.push(1, "hello")
            await until(lambda: dashboard.chats and len(dashboard.chats[0]["items"]) == 2, "general session missing")
            chat = dashboard.chats[0]
            if pause_at == "get_session":
                original = service._runtime.get_session
                async def delayed(chat_id, scope):
                    result = await original(chat_id, scope)
                    if scope:
                        paused.set()
                        await release.wait()
                    return result
                service._runtime.get_session = delayed
            else:
                original = service._runtime._store.save
                async def delayed(chats, revisions):
                    result = await original(chats, revisions)
                    if len(chats[0]["items"]) == 3:
                        paused.set()
                        await release.wait()
                    return result
                service._runtime._store.save = delayed
            prompt = service._dispatcher.onboarding.choose_project(str(OWNER), 2,
                chat_id=chat["id"], text="inspect my avatar")
            transport.click(2, prompt["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
            await asyncio.wait_for(paused.wait(), 2)
            transport.push(3, "/stop")
            await until(lambda: service._dispatcher._stop_revision == 1, "stop not admitted while submit pending")
            release.set()
            await until(lambda: 2 not in service._host._active_ids, "selection dispatch not completed")
            assert len([call for call in dashboard.calls if call[0] == "message"]) == 1
        finally:
            release.set()
            await service.stop()
    asyncio.run(scenario())
