import asyncio

from telegram_bot_commands import TelegramCommandDispatcher
from telegram_bot_store import TelegramBotStore
from telegram_bot_approvals import TelegramApprovalController
from telegram_bot_questions import TelegramBotQuestions


class Runtime:
    def __init__(self):
        self.calls = []
        self.sessions = {
            "s1": {"id": "s1", "sessionId": "runtime-s1", "title": "Existing", "projectPath": "/allowed"},
        }

    async def list_sessions(self, project):
        return [dict(row) for row in self.sessions.values() if row.get("projectPath", "") == project]

    async def create_session(self, project, title, request_id=None):
        row = {"id": "s2", "sessionId": "runtime-s2", "title": title, "projectPath": project}
        self.sessions[row["id"]] = row
        return dict(row)

    async def get_session(self, session_id, project):
        row = self.sessions.get(session_id)
        if row is None or row.get("projectPath", "") != project:
            return None
        return dict(row)


    async def submit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"status": "started"}

    async def stop(self, session_id, project):
        self.calls.append(session_id)
        return {"status": "requested"}


def update(text, *, sender=42, chat=42, kind="private", update_id=1):
    return {"update_id": update_id, "message": {"from": {"id": sender},
            "chat": {"id": chat, "type": kind}, "text": text}}


def test_status_uses_scoped_monitor_and_bounds_failure(tmp_path):
    async def run():
        calls = []
        mode = "success"
        async def snapshot(project, session):
            calls.append((project, session))
            if mode == "timeout":
                await asyncio.sleep(60)
            if mode == "failure":
                raise RuntimeError("private-token-must-not-leak")
            return {"taskStatus": "waiting_approval", "unityStatus": "disconnected",
                    "progressSummary": "检查当前模型"}
        with TelegramBotStore(tmp_path / "status.db", "7") as store:
            store.set_binding("42", "/allowed", "s1")
            commands = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store,
                runtime=Runtime(), monitor_snapshot=snapshot, status_timeout=0.01)
            reply = await commands.dispatch(update("/status"))
            assert "等待审批" in reply["text"] and "Unity/Core：未连接" in reply["text"]
            assert "检查当前模型" in reply["text"]
            assert calls == [("/allowed", "s1")]
            mode = "timeout"
            assert "读取超时" in (await asyncio.wait_for(commands.dispatch(update("/status")), 0.2))["text"]
            mode = "failure"
            reply = await commands.dispatch(update("/status"))
            assert "暂不可用" in reply["text"] and "private-token" not in reply["text"]
    asyncio.run(run())


def test_owner_and_project_scope_before_execution(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "bot.db", bot_identity="7") as store:
            coordinator = Runtime()
            commands = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",),
                store=store, runtime=coordinator)
            for message in (update("change", sender=43), update("change", kind="group"),
                            update("change", chat=43), update("change", sender="42")):
                assert await commands.dispatch(message) is None
            assert "任务已接收" in (await commands.dispatch(update("change")))["text"]
            await commands.dispatch(update("/project /arbitrary"))
            assert store.get_binding("42")["project_root"] == ""
            await commands.dispatch(update("/project 1"))
            assert store.get_binding("42")["project_root"] == "/allowed"
            await commands.dispatch(update("/use foreign"))
            assert store.get_binding("42")["session_id"] == ""
            assert len(coordinator.calls) == 1 and coordinator.calls[0][0][3] == ""
    asyncio.run(run())


def test_approval_buttons_dispatch_only_current_owner_decision(tmp_path):
    async def run():
        class Port:
            def __init__(self):
                self.calls = []

            async def list_pending(self, project, session):
                return [{"approvalId": "a1", "status": "pending", "projectRoot": project,
                         "sessionId": session, "targetTool": "SetProperty", "arguments": {"value": "red"}}]

            async def decide(self, approval_id, project, approve):
                self.calls.append((approval_id, project, approve))
                return {"ok": True}

        port = Port()
        with TelegramBotStore(tmp_path / "approval.db", bot_identity="7") as store:
            store.set_binding("42", str(tmp_path), "s1")
            coordinator = Runtime()
            coordinator.sessions["s1"] = {"id": "s1", "sessionId": "runtime-s1", "title": "Existing", "projectPath": str(tmp_path)}
            commands = TelegramCommandDispatcher(owner_id=42, projects=(str(tmp_path),), store=store,
                runtime=coordinator, approvals=TelegramApprovalController(port))
            response = await commands.dispatch(update("/approvals"))
            button = response["messages"][0]["reply_markup"]["inline_keyboard"][0][0]
            callback = {"update_id": 2, "callback_query": {"id": "callback-1", "from": {"id": 43},
                "message": {"message_id": 91, "text": response["messages"][0]["text"],
                    "chat": {"id": 42, "type": "private"}}, "data": button["callback_data"]}}
            assert await commands.dispatch(callback) is None
            assert port.calls == []
            callback["callback_query"]["from"]["id"] = 42
            result = await commands.dispatch(callback)
            card = result["messages"][0]
            assert "已批准" in card["text"] and "SetProperty" in card["text"]
            assert card["edit_message_id"] == 91
            assert card["reply_markup"] == {"inline_keyboard": []}
            await commands.dispatch(callback)
            assert port.calls == [("a1", str(tmp_path), True)]
    asyncio.run(run())


def test_session_commands_and_stable_runtime_identity(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "bot.db", bot_identity="7") as store:
            coordinator = Runtime()
            commands = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",),
                store=store, runtime=coordinator)
            await commands.dispatch(update("/project 1"))
            sessions = await commands.dispatch(update("/sessions"))
            assert any("Existing" in button["text"]
                       for row in sessions["messages"][0]["reply_markup"]["inline_keyboard"]
                       for button in row)
            await commands.dispatch(update("/new hello"))
            assert store.get_binding("42")["session_id"] == "s2"
            assert store.watches() == [{"route": "42", "project_root": "/allowed", "session_id": "runtime-s2"}]
            await commands.dispatch(update("/use s1"))
            assert {row["session_id"] for row in store.watches()} == {"runtime-s1", "runtime-s2"}
            await commands.dispatch(update("change red", update_id=81))
            args, kwargs = coordinator.calls[0]
            assert args == ("s1", "telegram-42-81", "change red", "/allowed")
            assert kwargs["metadata"].route == "42"
            assert kwargs["metadata"].project_root == "/allowed"
            await commands.dispatch(update("/unknown"))
            assert len(coordinator.calls) == 1
            result = await commands.dispatch(update("/stop"))
            assert "已请求" in result["text"] and "已停止" not in result["text"]
    asyncio.run(run())


def test_history_is_scoped_recent_bounded_text_only(tmp_path):
    async def run():
        class HistoryRuntime(Runtime):
            async def history(self, chat, project):
                self.calls.append((chat, project))
                return [{"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index:02d}:" + "x" * 1200,
                         "secret": "SECRET_FIELD"} for index in range(12)] + [
                    {"role": "tool", "content": "RAW_TOOL_MUST_NOT_SHOW"},
                    {"role": "assistant", "content": {"secret": "SECRET_OBJECT"}}]
        with TelegramBotStore(tmp_path / "history.db", "7") as store:
            runtime = HistoryRuntime()
            commands = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store, runtime=runtime)
            assert "选择" in (await commands.dispatch(update("/history")))["text"]
            await commands.dispatch(update("/project 1"))
            await commands.dispatch(update("/use s1"))
            assert await commands.dispatch(update("/history", sender=43)) is None
            reply = await commands.dispatch(update("/history"))
            assert runtime.calls == [("s1", "/allowed")]
            assert len(reply["messages"]) == 11
            content = "\n".join(row["text"] for row in reply["messages"])
            assert "message-00:" not in content and "message-01:" not in content
            assert "message-02:" in content and "message-11:" in content
            assert "SECRET" not in content and "RAW_TOOL" not in content
            assert all(len(row["text"].partition("：")[2]) <= 1000 for row in reply["messages"][:-1])
            assert "未删除" in reply["messages"][-1]["text"]
    asyncio.run(run())


def test_failed_session_watch_does_not_change_binding(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "watch-full.db", "7") as store:
            commands = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store, runtime=Runtime())
            await commands.dispatch(update("/project 1"))
            for index in range(100):
                assert store.watch_session("42", "/allowed", f"occupied-{index}")
            assert "尚未切换" in (await commands.dispatch(update("/use s1")))["text"]
            assert "尚未切换" in (await commands.dispatch(update("/new new")))["text"]
            assert store.get_binding("42")["session_id"] == ""
            assert "找不到" in (await commands.dispatch(update("/use missing")))["text"]
    asyncio.run(run())


def test_question_commands_derive_scope_from_route_and_preserve_queued_status(tmp_path):
    async def run():
        class Port:
            def __init__(self):
                self.list_calls = []
                self.answer_calls = []

            async def list_pending(self, *, project, session):
                self.list_calls.append((project, session))
                return {"questions": [{
                    "questionId": "q1", "header": "选择方案", "question": "继续吗？",
                    "options": [{"id": "yes", "label": "继续"}],
                    "status": "pending", "projectRoot": project, "sessionId": session,
                    "private": "must-not-be-forwarded",
                }]}

            async def answer(self, question_id, params):
                self.answer_calls.append((question_id, params))
                return {"ok": True, "question": {
                    "questionId": question_id, "header": "选择方案", "question": "继续吗？",
                    "status": "answered", "projectRoot": params["projectRoot"],
                    "sessionId": params["sessionId"], "runtimeContinuationStatus": "queued",
                }}

        port = Port()
        facade = TelegramBotQuestions(port.list_pending, port.answer)
        with TelegramBotStore(tmp_path / "questions.db", bot_identity="7") as store:
            runtime = Runtime()
            commands = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",),
                store=store, runtime=runtime, questions=facade)
            await commands.dispatch(update("/project 1", update_id=1))
            await commands.dispatch(update("/use s1", update_id=2))

            listed = await commands.dispatch(update("/questions", update_id=3))
            assert listed["messages"][0]["text"].startswith("选择方案")
            assert "q1" in listed["messages"][0]["text"]
            assert "must-not-be-forwarded" not in listed["messages"][0]["text"]
            assert port.list_calls == [("/allowed", "runtime-s1")]

            foreign = await commands.dispatch(update("/answer foreign yes", update_id=4))
            assert "尚未确认" in foreign["text"]
            assert port.answer_calls == []

            queued = await commands.dispatch(update("/answer q1 yes", update_id=5))
            assert "等待任务继续" in queued["text"]
            assert port.answer_calls == [("q1", {"answer": "yes", "projectRoot": "/allowed", "sessionId": "runtime-s1"})]
    asyncio.run(run())
