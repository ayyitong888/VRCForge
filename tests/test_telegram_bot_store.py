import sqlite3

import pytest

from telegram_bot_store import TelegramBotStore, TelegramStoreError, TelegramStoreFullError


def test_watch_destinations_survive_route_switch_and_reopen_with_bot_owner_isolation(tmp_path):
    path = tmp_path / "watches.db"
    with TelegramBotStore(path, "bot-a") as store:
        assert store.watch_session("42", "/project", "runtime-first")
        store.set_binding("42", "/project", "chat-first")
        assert store.watch_session("42", "/project", "runtime-second")
        store.set_binding("42", "/project", "chat-second")
        assert not store.watch_session("43", "/project", "runtime-first")
        assert not store.watch_session("42", "/other-project", "runtime-first")
    with TelegramBotStore(path, "bot-a") as reopened:
        assert reopened.get_binding("42")["session_id"] == "chat-second"
        assert {row["session_id"] for row in reopened.watches()} == {"runtime-first", "runtime-second"}
        assert {row["route"] for row in reopened.watches()} == {"42"}
    with TelegramBotStore(path, "bot-b") as other:
        assert other.watches() == []
        assert other.watch_session("43", "/project", "runtime-first")
        assert other.watches() == [{"route": "43", "project_root": "/project", "session_id": "runtime-first"}]


def test_admit_is_deduplicated_atomically_and_recovers_pending(tmp_path):
    path = tmp_path / "telegram.sqlite3"
    store = TelegramBotStore(path, "bot-a")
    admitted = store.admit_update(4, {"text": "hello"})
    duplicate = store.admit_update(4, {"text": "changed"})
    assert admitted.admitted and not admitted.duplicate
    assert not duplicate.admitted and duplicate.duplicate
    assert store.get_offset() == 5
    assert store.next_pending()[0].payload == {"text": "hello"}
    store.close()
    store = TelegramBotStore(path, "bot-a")
    assert [item.update_id for item in store.next_pending()] == [4]
    assert store.mark_dispatched(4)
    assert not store.mark_dispatched(4)
    assert store.next_pending() == []
    store.close()


def test_pending_capacity_rolls_back_insert_and_offset(tmp_path):
    store = TelegramBotStore(tmp_path / "state.db", "bot-a", max_pending=1)
    store.admit_update(1, {})
    with pytest.raises(TelegramStoreFullError) as caught:
        store.admit_update(2, {})
    assert caught.value.queue == "pending"
    assert store.get_offset() == 2
    assert [item.update_id for item in store.next_pending()] == [1]
    store.close()


def test_outbox_dedup_claim_and_unknown_are_never_automatically_resent(tmp_path):
    path = tmp_path / "state.db"
    store = TelegramBotStore(path, "bot-a")
    assert store.enqueue_outbox("event-1", {"text": "final"})
    assert not store.enqueue_outbox("event-1", {"text": "duplicate"})
    item = store.claim_outbox()[0]
    assert item.payload == {"text": "final"}
    assert store.mark_outbox_unknown(item.key)
    assert store.claim_outbox() == []
    assert not store.mark_outbox_sent(item.key)
    store.close()


def test_outbox_retry_is_explicit_and_sent_is_terminal(tmp_path):
    store = TelegramBotStore(tmp_path / "state.db", "bot-a", max_outbox=2)
    store.enqueue_outbox("retry", {})
    item = store.claim_outbox()[0]
    assert store.mark_outbox_failed(item.key, retryable=True)
    assert store.claim_outbox()[0].key == "retry"
    assert store.mark_outbox_sent(item.key)
    assert store.claim_outbox() == []
    store.close()


def test_outbox_capacity_and_namespace_isolation(tmp_path):
    path = tmp_path / "state.db"
    first = TelegramBotStore(path, "bot-a", max_outbox=1)
    second = TelegramBotStore(path, "bot-b", max_outbox=1)
    first.enqueue_outbox("same-key", {"bot": "a"})
    second.enqueue_outbox("same-key", {"bot": "b"})
    with pytest.raises(TelegramStoreFullError):
        first.enqueue_outbox("other", {})
    assert second.claim_outbox()[0].payload == {"bot": "b"}
    first.close()
    second.close()


def test_no_token_column_and_close_lifecycle(tmp_path):
    path = tmp_path / "state.db"
    store = TelegramBotStore(path, "identity-without-token")
    store.close()
    store.close()
    with pytest.raises(TelegramStoreError):
        store.get_offset()
    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(telegram_updates)")}
    connection.close()
    assert "token" not in columns


def test_route_bindings_are_namespace_isolated_and_replaceable(tmp_path):
    path = tmp_path / "state.db"
    first = TelegramBotStore(path, "bot-a")
    second = TelegramBotStore(path, "bot-b")
    assert first.get_binding("route") is None
    first.set_binding("route", "D:/project-a", "session-a")
    assert first.get_binding("route") == {"project_root": "D:/project-a", "session_id": "session-a"}
    first.set_binding("route", "D:/project-b", "session-b")
    assert first.get_binding("route") == {"project_root": "D:/project-b", "session_id": "session-b"}
    assert second.get_binding("route") is None
    first.close()
    second.close()
