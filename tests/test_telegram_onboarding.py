import asyncio
import pytest

from telegram_bot_commands import TelegramCommandDispatcher
from telegram_bot_onboarding import TelegramOnboarding


class Store:
    def __init__(self):
        self.binding = None
        self.watches = []
        self.project_requests = {}

    def get_binding(self, route):
        return self.binding

    def set_binding(self, route, project, chat):
        self.binding = {"route": route, "project_root": project, "session_id": chat}
        return None

    def watch_session(self, route, project, session):
        self.watches.append((route, project, session))
        return True

    def project_request(self, route):
        return self.project_requests.get(route)

    def save_project_request(self, route, payload):
        self.project_requests[route] = dict(payload)

    def clear_project_request(self, route):
        self.project_requests.pop(route, None)

    def bind_selected_project(self, route, chat_id, runtime_session, project):
        if not self.binding or self.binding.get("session_id") != chat_id:
            return False
        self.binding["project_root"] = project
        return True


class Runtime:
    def __init__(self):
        self.submits = []
        self.sessions = {}

    async def list_sessions(self, project):
        return [dict(row) for row in self.sessions.values() if row.get("projectPath", "") == project]

    async def create_session(self, project, title, request_id=None):
        row = {"id": "chat-new", "sessionId": "sess-new", "title": title, "projectPath": project}
        self.sessions[row["id"]] = row
        return dict(row)

    async def get_session(self, chat_id, project):
        row = self.sessions.get(chat_id)
        if row is None:
            row = {"id": chat_id, "sessionId": "sess-1", "title": "Existing", "projectPath": project}
            self.sessions[chat_id] = row
        if row.get("projectPath", "") != project:
            from channel_runtime_adapter import ChannelAdapterError
            raise ChannelAdapterError("session_scope_mismatch")
        return dict(row)

    async def submit(self, *args, **kwargs):
        self.submits.append((args, kwargs))
        return {"status": "started"}

    async def stop(self, *args, **kwargs):
        return {"status": "requested"}


def update(text, update_id=7):
    return {"update_id": update_id, "message": {"from": {"id": 42},
        "chat": {"id": 42, "type": "private"}, "text": text}}


def callback(data):
    return {"callback_query": {"id": "q", "from": {"id": 42}, "data": data,
        "message": {"chat": {"id": 42, "type": "private"}}}}


def test_onboarding_preserves_original_text_and_consumes_once():
    async def scenario():
        store, runtime = Store(), Runtime()
        dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/project",), store=store, runtime=runtime)
        first = await dispatcher.dispatch(update("打开 Unity 并检查场景"))
        assert "任务已接收，等待执行结果。" in first["text"]
        assert runtime.submits and runtime.submits[0][0][2] == "打开 Unity 并检查场景"
        assert runtime.submits[0][1]["metadata"].project_root == ""
        legacy = TelegramOnboarding(projects=("/project",), store=store, runtime=runtime)
        prompt = legacy.begin("42", "legacy request", 8)
        project_button = prompt["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        session_prompt = await legacy.consume("42", project_button)
        session_button = session_prompt["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        await legacy.consume("42", session_button)
        assert "失效" in (await legacy.consume("42", session_button))["text"]
    asyncio.run(scenario())


def test_pending_text_is_not_overwritten_and_stop_cancels_without_submit():
    async def scenario():
        store, runtime = Store(), Runtime()
        dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/project",), store=store, runtime=runtime)
        first = await dispatcher.dispatch(update("原始需求"))
        blocked = await dispatcher.dispatch(update("新的需求", 8))
        assert "任务已接收" in first["text"] and "任务已接收" in blocked["text"]
        cancelled = await dispatcher.dispatch(update("/stop", 9))
        assert "停止" in cancelled["text"]
        assert len(runtime.submits) == 2
    asyncio.run(scenario())


def test_start_is_onboarding_and_callback_does_not_require_approvals():
    async def scenario():
        store, runtime = Store(), Runtime()
        dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/project",), store=store, runtime=runtime)
        response = await dispatcher.dispatch(update("/start"))
        assert response["reply_markup"] == {"remove_keyboard": True}
    asyncio.run(scenario())


def test_expired_nonce_is_rejected():
    now = [0.0]
    controller = TelegramOnboarding(projects=("/project",), store=Store(), runtime=Runtime(), clock=lambda: now[0])
    prompt = controller.begin("42", "text", 1)
    data = prompt["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    now[0] = 601.0
    result = asyncio.run(controller.consume("42", data))
    assert "失效" in result["text"]


def buttons(reply):
    return [button for row in reply["messages"][0]["reply_markup"]["inline_keyboard"] for button in row]


def button(reply, label):
    return next(item["callback_data"] for item in buttons(reply) if item["text"] == label)


def test_only_three_entry_choices_then_five_per_page_and_snapshot_selection():
    async def scenario():
        store, runtime = Store(), Runtime()
        rows = [{"id": f"chat-{i}", "title": f"title-{i}", "updatedAt": f"2026-10-{i + 1:02}",
                 "items": [{"text": "PRIVATE_HISTORY"}]} for i in range(40)]
        calls = []
        async def listing(project):
            return rows
        async def get(chat_id, project):
            calls.append(chat_id)
            return {"id": chat_id, "sessionId": "native-" + chat_id, "title": chat_id}
        runtime.list_sessions, runtime.get_session = listing, get
        setup = TelegramOnboarding(projects=("/project",), store=store, runtime=runtime)
        home = await setup.begin_for_project("42", "original request", 11, "/project")
        assert [b["text"] for b in buttons(home)] == ["新建对话", "继续最近对话", "选择其他对话"]
        assert "PRIVATE_HISTORY" not in str(home)
        page = await setup.consume("42", button(home, "选择其他对话"))
        assert len([b for b in buttons(page) if ":s@" in b["callback_data"]]) == 5
        for _ in range(7):
            page = await setup.consume("42", button(page, "下一页"))
        choice = next(b["callback_data"] for b in buttons(page) if ":s@" in b["callback_data"])
        rows.reverse()
        resumed = await setup.consume("42", choice)
        assert calls == ["chat-4"]  # Snapshot row 35, even after backing list reorder.
        assert resumed["text"] == "original request"
        assert resumed["update_id"] == 11
        assert "失效" in (await setup.consume("42", choice))["text"]
    asyncio.run(scenario())


def test_project_pages_include_every_project_without_changing_old_binding():
    async def scenario():
        store = Store()
        store.set_binding("42", "/project-0", "old")
        setup = TelegramOnboarding(projects=tuple(f"/project-{i}" for i in range(40)), store=store, runtime=Runtime())
        page = setup.begin("42", "request", 1)
        negative = buttons(page)[0]["callback_data"].rsplit("@", 1)[0] + "@-1"
        assert "无效" in (await setup.consume("42", negative))["text"]
        for _ in range(7):
            page = await setup.consume("42", button(page, "下一页"))
        assert "/project-39" in page["text"]
        assert len([b for b in buttons(page) if ":p@" in b["callback_data"]]) == 5
        await setup.consume("42", buttons(page)[0]["callback_data"])
        assert store.binding["session_id"] == "old"
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["list", "create", "get"])
def test_cancel_during_await_never_rebinds_or_resumes(operation):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        store, runtime = Store(), Runtime()
        store.set_binding("42", "/project", "old")
        if operation == "get":
            runtime.sessions["old"] = {"id": "old", "sessionId": "sess-old", "title": "Existing", "projectPath": "/project"}
        setup = TelegramOnboarding(projects=("/project",), store=store, runtime=runtime)
        method = {"list": "list_sessions", "create": "create_session", "get": "get_session"}[operation]
        original = getattr(runtime, method)
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        if operation == "list":
            runtime.list_sessions = held
            task = asyncio.create_task(setup.begin_for_project("42", "request", 1, "/project"))
        else:
            home = await setup.begin_for_project("42", "request", 1, "/project")
            setattr(runtime, method, held)
            task = asyncio.create_task(setup.consume("42", button(home, "新建对话" if operation == "create" else "继续最近对话")))
        await asyncio.wait_for(entered.wait(), 1)
        setup.cancel("42")
        release.set()
        result = await task
        assert not result.get("resume")
        assert store.binding["session_id"] == "old"
        assert not store.watches
    asyncio.run(scenario())


def test_pending_stop_reports_old_runtime_stop_and_does_not_steer_accidentally():
    async def scenario():
        store, runtime = Store(), Runtime()
        store.set_binding("42", "/project", "old")
        calls = []
        async def stop(*args):
            calls.append(args)
            return {"status": "requested"}
        runtime.stop = stop
        dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/project",), store=store, runtime=runtime)
        await dispatcher.dispatch(update("切换工程"))
        blocked = await dispatcher.dispatch(update("additional words", 8))
        assert "完成当前" in blocked["text"] and not runtime.submits
        stopped = await dispatcher.dispatch(update("停止任务", 9))
        assert "原需求未执行" in stopped["text"] and "停止已请求" in stopped["text"]
        assert calls == [("old", "/project")]
    asyncio.run(scenario())


def test_start_shows_binding_and_plain_text_for_project_only_shows_session_choices():
    async def scenario():
        store, runtime = Store(), Runtime()
        store.set_binding("42", "/project", "old")
        dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/project",), store=store, runtime=runtime)
        response = await dispatcher.dispatch(update("/start"))
        assert "Existing" in response["text"] and response["reply_markup"] == {"remove_keyboard": True}
        assert not dispatcher.onboarding.has_pending("42")
        store.set_binding("42", "", "")
        response = await dispatcher.dispatch(update("check materials", 10))
        assert "任务已接收" in response["text"]
        assert store.get_binding("42")["project_root"] == ""
    asyncio.run(scenario())


def test_binding_save_failure_does_not_resume():
    async def scenario():
        store = Store()
        store.set_binding = lambda *args: False
        runtime = Runtime()
        runtime.sessions["existing"] = {"id": "existing", "sessionId": "sess-existing", "title": "Existing", "projectPath": "/project"}
        setup = TelegramOnboarding(projects=("/project",), store=store, runtime=runtime)
        home = await setup.begin_for_project("42", "do work", 3, "/project")
        result = await setup.consume("42", button(home, "继续最近对话"))
        assert not result.get("resume") and "未保存" in result["text"]
    asyncio.run(scenario())
