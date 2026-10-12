import asyncio
import base64
import hashlib

import pytest

from telegram_bot_host import TelegramBotHost
from telegram_bot_store import TelegramBotStore
from telegram_bot_transport import TelegramBotError


class FakeTransport:
    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.sent = []
        self.closed = False

    async def get_updates(self, **kwargs):
        if self.updates:
            return self.updates.pop(0)
        await asyncio.sleep(0.01)
        return []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return [{"ok": True}]

    async def close(self):
        self.closed = True


def update(update_id, user=7, chat_type="private", text="hello"):
    return {"update_id": update_id, "message": {"from": {"id": user}, "chat": {"id": 100, "type": chat_type}, "text": text}}


def run(coro):
    return asyncio.run(coro)


def callback_update(number, *, owner=7, chat=7, message_id=55):
    return {"update_id": number, "callback_query": {"id": f"cb-{number}", "from": {"id": owner},
        "data": "setup:page:2", "message": {"message_id": message_id, "chat": {"id": chat, "type": "private"}}}}


def test_callback_menu_edits_original_and_slow_ack_does_not_block_control(tmp_path):
    async def case():
        edits, acks = [], []
        ack_started, stop_seen = asyncio.Event(), asyncio.Event()
        transport = FakeTransport([[callback_update(1)]])
        async def ack(callback_id):
            acks.append(callback_id)
            ack_started.set()
            await asyncio.sleep(60)
        async def edit(chat_id, message_id, text, **kwargs):
            edits.append((chat_id, message_id, text, kwargs))
        transport.answer_callback_query = ack
        transport.edit_message_text = edit
        async def dispatch(value):
            if value.get("callback_query"):
                return {"messages": [{"text": "page two", "edit_message_id": 55,
                    "reply_markup": {"inline_keyboard": [[{"text": "Next", "callback_data": "setup:page:3"}]]}}]}
            stop_seen.set()
            return {"text": "stop requested"}
        with TelegramBotStore(tmp_path / "edit.db", "bot") as store:
            host = TelegramBotHost(transport, store, dispatch, 7, poll_timeout=0)
            await host.start()
            try:
                await asyncio.wait_for(ack_started.wait(), 0.8)
                transport.updates.append([update(2, text="/stop")])
                await asyncio.wait_for(stop_seen.wait(), 0.8)
                for _ in range(80):
                    if edits:
                        break
                    await asyncio.sleep(0.01)
                assert edits[0][:3] == (7, 55, "page two")
                journal = dict(store.pending_dispatches())
                assert journal[1]["messages"][0]["edit_message_id"] == 55
                assert "inline_keyboard" in edits[0][3]["reply_markup"]
                assert not any(text == "page two" for _, text in transport.sent)
                assert acks == ["cb-1"]
            finally:
                await asyncio.wait_for(host.close(), 0.8)
            assert not host._tasks
    run(case())


def test_invalid_callback_edit_target_and_foreign_owner_never_edit_or_ack(tmp_path):
    async def case():
        edits, acks = [], []
        transport = FakeTransport([[callback_update(1, owner=8), callback_update(2, chat=100), update(3), callback_update(4)]])
        async def ack(value):
            acks.append(value)
        async def edit(*args, **kwargs):
            edits.append(args)
        transport.answer_callback_query, transport.edit_message_text = ack, edit
        async def dispatch(value):
            return {"messages": [{"text": "normal message", "edit_message_id": 999,
                "reply_markup": {"inline_keyboard": []}}]}
        with TelegramBotStore(tmp_path / "invalid-edit.db", "bot") as store:
            host = TelegramBotHost(transport, store, dispatch, 7, poll_timeout=0)
            await host.start()
            try:
                await asyncio.sleep(0.35)
                assert not edits and acks == ["cb-4"]
                assert transport.sent
                assert all("edit_message_id" not in message for _, result in store.pending_dispatches()
                           for message in result.get("messages", []))
            finally:
                await host.close()
    run(case())


def test_callback_ack_capacity_is_fixed_and_failure_isolated(tmp_path):
    async def case():
        transport = FakeTransport()
        acks = []
        async def ack(value):
            acks.append(value)
            raise RuntimeError("network down")
        transport.answer_callback_query = ack
        async def dispatch(value):
            return None
        with TelegramBotStore(tmp_path / "acks.db", "bot") as store:
            host = TelegramBotHost(transport, store, dispatch, 7, poll_timeout=0)
            for index in range(12):
                host._admit_update(callback_update(index))
            assert host._callback_acks.qsize() == 8
            await host.start()
            try:
                await asyncio.wait_for(host._callback_acks.join(), 0.8)
                assert len(acks) == 8
                assert host.status()["channelError"] is None
            finally:
                await host.close()
    run(case())


def test_normal_route_serializes_while_stop_and_steer_bypass_long_dispatch(tmp_path):
    async def case():
        first_started, second_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        controls = {"/stop": asyncio.Event(), "/steer change": asyncio.Event()}
        async def dispatch(value):
            text = value["message"]["text"]
            if text == "first":
                first_started.set()
                await release.wait()
            elif text == "second":
                second_started.set()
            else:
                controls[text].set()
        transport = FakeTransport([[update(1, text="first")]])
        with TelegramBotStore(tmp_path / "serial.db", "bot") as store:
            host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=2, poll_timeout=0)
            await host.start()
            try:
                await asyncio.wait_for(first_started.wait(), 0.8)
                transport.updates.append([update(2, text="second")])
                await asyncio.sleep(0.05)
                assert not second_started.is_set(), "same-route normal dispatch ran concurrently"
                transport.updates.append([update(3, text="/stop"), update(4, text="/steer change")])
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in controls.values())), 0.8)
                assert not release.is_set() and not second_started.is_set()
                release.set()
                await asyncio.wait_for(second_started.wait(), 0.8)
            finally:
                release.set()
                await host.close()
    run(case())


@pytest.mark.parametrize("plan", [{"reply": "native reply"}, {"summary": "native summary"}])
def test_emit_extracts_real_native_plan_text(tmp_path, plan):
    with TelegramBotStore(tmp_path / "native.db", "bot") as store:
        host = TelegramBotHost(FakeTransport(), store, None, 7)
        assert host.emit({"event": "turn_returned", "sessionId": "s", "clientTurnId": "t",
            "metadata": {"route": "7"}, "result": {"ok": True, "plan": plan}})
        assert store.claim_outbox()[0].payload["text"] == next(iter(plan.values()))


def test_owner_filter_before_persistence_and_dispatch(tmp_path):
    async def case():
        transport = FakeTransport([[update(1), update(2, user=8), update(3, chat_type="group")]])
        store = TelegramBotStore(tmp_path / "state.db", "bot")
        seen = []

        async def dispatch(value):
            seen.append(value["update_id"])
            return None

        host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=1, poll_timeout=0)
        await host.start()
        await asyncio.sleep(0.1)
        await host.stop()
        assert seen == [1]
        assert transport.closed

    run(case())


def test_dispatch_result_is_split_into_independent_outbox_keys(tmp_path):
    async def case():
        transport = FakeTransport([[update(1)]])
        store = TelegramBotStore(tmp_path / "state.db", "bot")

        async def dispatch(value):
            return {"route": "r", "text": "x" * 4097, "control": "turn_returned"}

        host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=1, poll_timeout=0)
        await host.start()
        await asyncio.sleep(0.1)
        await host.stop()
        assert len(transport.sent) == 2
        assert [len(text) for _, text in transport.sent] == [4096, 1]

    run(case())


def test_network_send_marks_unknown_and_does_not_resend(tmp_path):
    async def case():
        transport = FakeTransport([[update(1)]])

        async def send_message(chat_id, text):
            raise TelegramBotError("network_error", "safe")

        transport.send_message = send_message
        store = TelegramBotStore(tmp_path / "state.db", "bot")

        async def dispatch(value):
            return {"route": "r", "text": "hello", "control": "turn_returned"}

        host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=1, poll_timeout=0)
        await host.start()
        await asyncio.sleep(0.1)
        assert store.claim_outbox() == []
        await host.stop()

    run(case())


def test_stop_cancels_long_dispatch_without_waiting(tmp_path):
    async def case():
        transport = FakeTransport([[update(1)]])
        store = TelegramBotStore(tmp_path / "state.db", "bot")
        started = asyncio.Event()

        async def dispatch(value):
            started.set()
            await asyncio.sleep(60)
            return None

        host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=1, poll_timeout=0)
        await host.start()
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(host.stop(), 1)
        assert transport.closed

    run(case())


def test_outbox_full_journals_result_without_reexecuting_business(tmp_path):
    async def case():
        transport = FakeTransport([[update(1)]])
        store = TelegramBotStore(tmp_path / "state.db", "bot", max_outbox=1)
        assert store.enqueue_outbox("already-full", {"chat_id": 100, "text": "existing"})
        calls = 0

        async def dispatch(value):
            nonlocal calls
            calls += 1
            return {"route": "r", "text": "hello"}

        host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=1, poll_timeout=0)
        await host.start()
        await asyncio.sleep(0.15)
        assert calls == 1
        assert host.status()["channelError"] == "notification_store_error"
        await host.stop()

    run(case())


def test_emit_projects_safe_runtime_events_without_network(tmp_path):
    async def case():
        transport = FakeTransport()
        store = TelegramBotStore(tmp_path / "state.db", "bot")

        async def dispatch(value):
            return None

        host = TelegramBotHost(transport, store, dispatch, 7, poll_timeout=0)
        event = {
            "event": "turn_returned",
            "sessionId": "session-1",
            "clientTurnId": "turn-1",
            "result": {"reply": "done"},
            "metadata": {"route": "7"},
        }
        assert host.emit(event)
        second = {**event, "clientTurnId": "turn-2", "result": {"reply": "done-2"}}
        assert host.emit(second)
        assert not host.emit({"event": "waiting_approval", "metadata": {"route": "7"}})
        assert not host.emit({"event": "turn_returned", "result": {"reply": "wrong"}, "metadata": {"route": "8"}})
        assert not transport.sent
        assert [item.payload["text"] for item in store.claim_outbox()] == ["done", "done-2"]
        await host.stop()

    run(case())


def test_preview_delivery_uses_bound_bytes_and_rejects_changed_digest(tmp_path):
    async def case():
        transport = FakeTransport([[update(10)]])
        photos = []
        delivered = asyncio.Event()

        async def send_photo(chat_id, image, caption):
            photos.append((chat_id, image, caption))
            delivered.set()

        transport.send_photo = send_photo
        store = TelegramBotStore(tmp_path / "photos.db", "bot")
        image = b"\x89PNG\r\n\x1a\nfixture"

        async def dispatch(value):
            return {"route": "7", "messages": [{"text": "approved", "photo": base64.b64encode(image).decode(),
                "sha256": hashlib.sha256(image).hexdigest()}]}

        host = TelegramBotHost(transport, store, dispatch, 7, poll_timeout=0)
        await host.start()
        await asyncio.wait_for(delivered.wait(), 2)
        assert photos == [(100, image, "approved")]
        assert not transport.sent
        rejected = asyncio.Event()
        mark_unknown = store.mark_outbox_unknown
        def observed_unknown(key):
            result = mark_unknown(key)
            if key == "changed":
                rejected.set()
            return result
        store.mark_outbox_unknown = observed_unknown
        store.enqueue_outbox("changed", {"chat_id": 100, "text": "bad", "photo": base64.b64encode(image).decode(), "sha256": "wrong"})
        await asyncio.wait_for(rejected.wait(), 2)
        assert len(photos) == 1
        assert store.claim_outbox() == []
        await host.stop()
        store.close()

    run(case())


@pytest.mark.parametrize("stop_text", ["/stop", "停止任务"])
def test_setup_buttons_serialize_with_messages_while_stop_bypasses(tmp_path, stop_text):
    async def case():
        entered, release, selected, stopped = (asyncio.Event() for _ in range(4))
        async def dispatch(value):
            if value.get("callback_query"):
                selected.set()
            elif value["message"]["text"] == stop_text:
                stopped.set()
            else:
                entered.set()
                await release.wait()
        transport = FakeTransport([[update(1, text="first")]])
        with TelegramBotStore(tmp_path / "setup-route.db", "bot") as store:
            host = TelegramBotHost(transport, store, dispatch, 7, max_inflight=2, poll_timeout=0)
            await host.start()
            try:
                await asyncio.wait_for(entered.wait(), 1)
                transport.updates.append([{"update_id": 2, "callback_query": {
                    "from": {"id": 7}, "message": {"chat": {"id": 100, "type": "private"}},
                    "data": "setup:opaque-nonce"}}, update(3, text=stop_text)])
                await asyncio.wait_for(stopped.wait(), 1)
                assert not selected.is_set()
                release.set()
                await asyncio.wait_for(selected.wait(), 1)
            finally:
                release.set()
                await host.close()
    run(case())
