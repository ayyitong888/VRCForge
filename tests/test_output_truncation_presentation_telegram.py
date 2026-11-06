import asyncio

from agent_output_truncation import CONTINUE_MESSAGE
from telegram_bot_host import TelegramBotHost
from telegram_bot_store import TelegramBotStore
from tests.test_native_runtime_gateway import run
from tests.test_output_truncation_loop import response, scripted


class FakeTransport:
    async def get_updates(self, **_kwargs):
        return []

    async def send_message(self, *_args, **_kwargs):
        return [{"ok": True}]

    async def close(self):
        return None


def test_telegram_completion_uses_only_the_merged_reply(tmp_path):
    async def scenario():
        gateway, model, _ = scripted(tmp_path / "runtime", [response("第一段", length=True), response("第二段")])
        result = run(gateway, tmp_path / "runtime")
        merged = "第一段第二段"
        assert result["plan"]["reply"] == merged
        assert len(model.requests) == 2
        assert any(item.get("hidden") and item.get("content") == CONTINUE_MESSAGE
                   for item in model.requests[1]["messages"])
        snapshot = next(iter(gateway.runtime_sessions._native_conversations.values()))
        from tests.test_native_history_storage import owner
        restored_native = owner(gateway.audit_dir / "native-history").native_conversation(
            "native-session", binding=snapshot["binding"],
        )
        assert restored_native and any(
            item.get("hidden") is True and item.get("content") == CONTINUE_MESSAGE
            for item in restored_native["messages"]
        )

        store_path = tmp_path / "telegram.db"
        with TelegramBotStore(store_path, "bot") as store:
            host = TelegramBotHost(FakeTransport(), store, lambda _event: asyncio.sleep(0), 7, poll_timeout=0)
            accepted = host.emit({
                "event": "turn_returned",
                "sessionId": "session-truncation",
                "turnId": "turn-truncation",
                "clientTurnId": "client-truncation",
                "result": result,
                "metadata": {"route": "7"},
            })
            assert accepted
            claimed = store.claim_outbox()
            messages = [item.payload["text"] for item in claimed]
            assert messages == [merged]
            assert CONTINUE_MESSAGE not in "\n".join(messages)
            for item in claimed:
                store.mark_outbox_failed(item.key, retryable=True)
            await host.stop()

        # The queued Telegram payload survives store reload and still contains
        # only the merged public response.
        with TelegramBotStore(store_path, "bot") as restored:
            replayed = restored.claim_outbox()
            assert [item.payload["text"] for item in replayed] == [merged]
            assert CONTINUE_MESSAGE not in repr([item.payload for item in replayed])

    asyncio.run(scenario())
