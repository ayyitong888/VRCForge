import asyncio
import json

import httpx

from telegram_bot_menu import MENU_COMMANDS, TelegramBotMenu
from telegram_bot_transport import TelegramBotTransport


def owner_update(owner=42, chat=42, kind="private", callback=False):
    message = {"from": {"id": owner}, "chat": {"id": chat, "type": kind}, "text": "/help"}
    return {"callback_query": {"from": message["from"], "message": message}} if callback else {"message": message}


def test_menu_fixed_transport_chat_scope_and_no_default_menu():
    async def scenario():
        requests = []
        def respond(request):
            assert request.url.host == "api.telegram.org"
            requests.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
            return httpx.Response(200, json={"ok": True, "result": True})
        transport = TelegramBotTransport("123:synthetic", transport=httpx.MockTransport(respond))
        menu = TelegramBotMenu(transport, 42)
        try:
            menu.request_sync()
            await menu._task
            assert requests == [("setMyCommands", {"commands": list(MENU_COMMANDS), "scope": {"type": "chat", "chat_id": 42}}),
                                ("setChatMenuButton", {"chat_id": 42, "menu_button": {"type": "commands"}})]
            assert [row["command"] for row in MENU_COMMANDS] == "status session new project model compact history stop steer permissions approvals questions answer preview cancel help".split()
            menu.observe_update(owner_update())
            assert len(requests) == 2
        finally:
            await menu.close()
            await transport.close()
    asyncio.run(scenario())


def test_authentication_first_private_retry_and_thirty_second_backoff():
    async def scenario():
        now, calls = [0], []
        class Transport:
            async def set_my_commands(self, commands, *, owner_id):
                calls.append(owner_id)
                raise ValueError("synthetic private transport detail")
        menu = TelegramBotMenu(Transport(), 42, clock=lambda: now[0])
        menu.request_sync()
        await menu._task
        assert menu.error_code == "menu_registration_failed"
        for update in (owner_update(owner=43), owner_update(chat=43), owner_update(kind="group"), owner_update(owner=43, callback=True)):
            menu.observe_update(update)
        assert len(calls) == 1
        menu.observe_update(owner_update(callback=True))
        await menu._task
        assert len(calls) == 2
        for _ in range(50):
            menu.observe_update(owner_update())
        assert len(calls) == 2
        now[0] = 30
        menu.observe_update(owner_update())
        await menu._task
        assert len(calls) == 3
        await menu.close()
        menu.request_sync(authenticated=True)
        assert menu._task is None
    asyncio.run(scenario())


def test_timeout_single_task_pending_owner_retry_and_close_cancel():
    async def scenario():
        calls, cancelled = [], []
        class Transport:
            async def set_my_commands(self, commands, *, owner_id):
                calls.append(owner_id)
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)
        menu = TelegramBotMenu(Transport(), 42, timeout=0.02)
        menu.request_sync()
        first_task = menu._task
        for _ in range(50):
            menu.observe_update(owner_update())
            assert menu._task is first_task
        await asyncio.wait_for(first_task, 0.5)
        assert len(calls) == len(cancelled) == 2
        assert menu.error_code == "menu_registration_failed"
        await menu.close()
        menu = TelegramBotMenu(Transport(), 42)
        menu.request_sync()
        await asyncio.sleep(0)
        await asyncio.wait_for(menu.close(), 0.2)
        assert menu._task is None
    asyncio.run(scenario())
