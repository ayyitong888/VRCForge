import asyncio

from telegram_bot_commands import TelegramCommandDispatcher
from telegram_bot_store import TelegramBotStore
from tests.test_telegram_bot_commands import Runtime, update
from tests.test_telegram_session_commands import click


class Port:
    def __init__(self):
        self.mode = "approval"
        self.updates = []

    async def read(self):
        return {"executionMode": self.mode}

    async def update(self, mode):
        self.updates.append(mode)
        self.mode = mode
        return {"ok": True, "permission": {"executionMode": mode}}


def test_owner_permission_card_shares_authority_and_consumes_once(tmp_path):
    async def run():
        from telegram_bot_permissions import TelegramPermissionController
        port = Port()
        with TelegramBotStore(tmp_path / "permission.db", "bot") as store:
            commands = TelegramCommandDispatcher(owner_id=42, projects=(), store=store,
                runtime=Runtime(), permissions=TelegramPermissionController(port))
            assert await commands.dispatch(update("/permissions", sender=43)) is None
            card = await commands.dispatch(update("/permissions"))
            assert "所有会话" in card["messages"][0]["text"]
            assert not port.updates
            buttons = [b for row in card["messages"][0]["reply_markup"]["inline_keyboard"] for b in row]
            full = next(b for b in buttons if "Full Access" in b["text"])
            assert await commands.dispatch(click(full["callback_data"], owner=43)) is None
            changed = await commands.dispatch(click(full["callback_data"]))
            assert changed["messages"][0]["edit_message_id"] == 71
            assert changed["messages"][0]["reply_markup"] == {"inline_keyboard": []}
            assert "Full Access" in changed["messages"][0]["text"]
            assert port.updates == ["roslyn_full_auto"]
            await commands.dispatch(click(full["callback_data"]))
            assert port.updates == ["roslyn_full_auto"]
            await commands.dispatch(update("/start", update_id=4))
            assert "Full Access" in (await commands.dispatch(update("/status")))["text"]
    asyncio.run(run())


def test_permission_expiry_desktop_change_and_cancel_are_not_writes(tmp_path):
    async def run():
        from telegram_bot_permissions import TelegramPermissionController
        now = [0]
        port = Port()
        controller = TelegramPermissionController(port, clock=lambda: now[0])
        with TelegramBotStore(tmp_path / "expiry.db", "bot") as store:
            commands = TelegramCommandDispatcher(owner_id=42, projects=(), store=store,
                runtime=Runtime(), permissions=controller)
            async def choice():
                card = await commands.dispatch(update("/permissions"))
                return card["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            stale = await choice()
            port.mode = "auto"
            assert "已变化" in (await commands.dispatch(click(stale)))["text"]
            expired = await choice()
            now[0] += 601
            assert "失效" in (await commands.dispatch(click(expired)))["text"]
            cancelled = await choice()
            await commands.dispatch(update("/cancel"))
            assert "失效" in (await commands.dispatch(click(cancelled)))["text"]
            assert not port.updates
    asyncio.run(run())
