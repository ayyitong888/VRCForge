import asyncio

from channel_session_controls import SessionControlError
from telegram_bot_commands import TelegramCommandDispatcher
from telegram_bot_menu import MENU_COMMANDS
from telegram_bot_store import TelegramBotStore
from tests.test_telegram_bot_commands import Runtime, update


class ControlsRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.selected = []
        self.compactions = []
        self.failure = None
        self.options = [{"provider": "test", "model": f"model-{i}", "label": f"Model {i}"} for i in range(19)]

    async def model_options(self, chat, project):
        if self.failure:
            raise self.failure
        return self.options

    async def set_model(self, chat, project, provider, model):
        self.selected.append((chat, project, provider, model))
        return {"provider": provider, "model": model}

    async def compact(self, chat, project, instructions):
        if self.failure:
            raise self.failure
        self.compactions.append((chat, project, instructions))
        return {"status": "applied"}


def click(data, *, owner=42, chat=42, kind="private"):
    return {"update_id": 20, "callback_query": {"id": "callback",
        "from": {"id": owner}, "data": data,
        "message": {"message_id": 71, "chat": {"id": chat, "type": kind}}}}


def buttons(result):
    return [row for group in result["messages"][0]["reply_markup"]["inline_keyboard"] for row in group]


def test_menu_operations_have_discoverable_help_and_legacy_aliases(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "menu.db", "bot") as store:
            runtime = ControlsRuntime()
            dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store, runtime=runtime)
            help_reply = await dispatcher.dispatch(update("/help"))
            for command in MENU_COMMANDS:
                assert f"/{command['command']}" in help_reply["text"]
            assert help_reply["reply_markup"] == {"remove_keyboard": True}
            assert "直接发文字" in help_reply["text"] and "/use" in help_reply["text"]
            project = await dispatcher.dispatch(update("/project"))
            assert "/allowed" in project["text"] and buttons(project)
            await dispatcher.dispatch(update("/cancel"))
            await dispatcher.dispatch(update("/project 1"))
            await dispatcher.dispatch(update("/session use s1"))
            chooser = await dispatcher.dispatch(update("/session"))
            assert "Existing" in chooser["messages"][0]["text"]
            reply = await dispatcher.dispatch(update("继续当前对话", update_id=11))
            assert reply["control"]["status"] == "started"
            assert runtime.calls[-1][0][0] == "s1"
            new = await dispatcher.dispatch(update("/session new 另一个", update_id=12))
            assert "另一个" in new["text"] and store.get_binding("42")["session_id"] == "s2"
            assert "s1" in runtime.sessions
    asyncio.run(run())


def test_model_picker_paginates_edits_original_and_only_changes_bound_session(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "models.db", "bot") as store:
            store.set_binding("42", "/allowed", "s1")
            runtime = ControlsRuntime()
            dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store, runtime=runtime)
            page = await dispatcher.dispatch(update("/model"))
            assert len([b for b in buttons(page) if ":select:" in b["callback_data"]]) == 8
            next_button = next(b for b in buttons(page) if b["text"] == "下一页")
            page2 = await dispatcher.dispatch(click(next_button["callback_data"]))
            assert page2["messages"][0]["edit_message_id"] == 71
            selected = buttons(page2)[0]
            for bad in (click(selected["callback_data"], owner=43), click(selected["callback_data"], kind="group")):
                assert await dispatcher.dispatch(bad) is None
            assert not runtime.selected
            reply = await dispatcher.dispatch(click(selected["callback_data"]))
            assert "model-8" in reply["text"]
            assert runtime.selected == [("s1", "/allowed", "test", "model-8")]
            await dispatcher.dispatch(click(selected["callback_data"]))
            assert len(runtime.selected) == 1
            stale = buttons(await dispatcher.dispatch(update("/model")))[0]
            await dispatcher.dispatch(update("/new", update_id=30))
            assert "过期" in (await dispatcher.dispatch(click(stale["callback_data"])))["text"]
            assert len(runtime.selected) == 1
    asyncio.run(run())


def test_model_picker_cancel_expiry_and_plain_chat_do_not_block(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "cancel.db", "bot") as store:
            store.set_binding("42", "/allowed", "s1")
            runtime = ControlsRuntime()
            dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store, runtime=runtime)
            chosen = buttons(await dispatcher.dispatch(update("/model")))[0]
            await dispatcher.dispatch(update("/cancel"))
            assert "过期" in (await dispatcher.dispatch(click(chosen["callback_data"])))["text"]
            chosen = buttons(await dispatcher.dispatch(update("/model")))[0]
            reply = await dispatcher.dispatch(update("保持原来的模型继续", update_id=100))
            assert reply["control"]["status"] == "started"
            assert "过期" in (await dispatcher.dispatch(click(chosen["callback_data"])))["text"]
            chosen = buttons(await dispatcher.dispatch(update("/model")))[0]
            dispatcher.session_commands.clock = lambda: float("inf")
            assert "过期" in (await dispatcher.dispatch(click(chosen["callback_data"])))["text"]
            assert not runtime.selected
    asyncio.run(run())


def test_compact_scoped_instructions_busy_failure_and_no_false_success(tmp_path):
    async def run():
        with TelegramBotStore(tmp_path / "compact.db", "bot") as store:
            store.set_binding("42", "/allowed", "s1")
            runtime = ControlsRuntime()
            dispatcher = TelegramCommandDispatcher(owner_id=42, projects=("/allowed",), store=store, runtime=runtime)
            assert await dispatcher.dispatch(update("/compact", sender=43)) is None
            reply = await dispatcher.dispatch(update("/compact 保留衣服设置"))
            assert "已压缩" in reply["text"]
            assert runtime.compactions == [("s1", "/allowed", "保留衣服设置")]
            runtime.failure = SessionControlError("active_run", "PRIVATE-SECRET")
            reply = await dispatcher.dispatch(update("/compact"))
            assert "/status" in reply["text"] and "PRIVATE" not in reply["text"]
            runtime.failure = RuntimeError("PRIVATE-SECRET")
            assert "未完成" in (await dispatcher.dispatch(update("/compact")))["text"]
            async def cancelled(*_):
                return {"status": "cancelled"}
            runtime.compact = cancelled
            assert "已取消" in (await dispatcher.dispatch(update("/compact")))["text"]
            async def unconfirmed(*_):
                return {"ok": True}
            runtime.compact = unconfirmed
            assert "未完成" in (await dispatcher.dispatch(update("/compact")))["text"]
    asyncio.run(run())
