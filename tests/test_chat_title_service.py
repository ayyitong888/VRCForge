from contextlib import contextmanager
import asyncio
import json
from threading import Event

import pytest

from chat_title_service import (
    ChatTitleService,
    build_chat_title_prompt,
    parse_chat_title,
)
from provider_configuration_service import ProviderApiConfig


def _config() -> ProviderApiConfig:
    return ProviderApiConfig(
        provider="openai",
        api_key="memory-only-key",
        base_url="http://provider.invalid/v1",
        model="title-model",
    )


def test_parse_chat_title_accepts_json_and_bounds_noise():
    assert parse_chat_title('{"title":"  Avatar\\nsetup  "}') == "Avatar setup"
    assert parse_chat_title("A useful title") == ""
    assert parse_chat_title('["A useful title"]') == ""
    assert parse_chat_title('{"title":""}') == ""


def test_service_uses_one_tool_free_isolated_probe():
    calls = []

    @contextmanager
    def bind_runtime_config(**kwargs):
        calls.append(("bind", kwargs))
        yield _config()

    def probe(config, prompt, **kwargs):
        calls.append(("probe", config, prompt, kwargs))
        return '{"title":"Wardrobe import"}'

    result = ChatTitleService(bind_runtime_config, probe).generate(
        message="Help me import a new wardrobe into this avatar.",
        provider="openai",
        model="title-model",
    )

    assert result.ok is True
    assert result.title == "Wardrobe import"
    assert calls[0] == ("bind", {"provider": "openai", "model": "title-model"})
    assert calls[1][3]["structured"] is True
    assert calls[1][3]["max_output_tokens"] == 64
    assert "<user_message>" in calls[1][2]
    assert "history" not in calls[1][2].lower()


def test_service_failure_is_best_effort_and_does_not_expose_error():
    @contextmanager
    def bind_runtime_config(**_kwargs):
        yield _config()

    def probe(_config, _prompt, **_kwargs):
        raise RuntimeError("provider failure containing a secret")

    result = ChatTitleService(bind_runtime_config, probe).generate(message="hello")

    assert result.ok is False
    assert result.title == ""
    assert result.reason == "provider_failed"


def test_prompt_is_bounded_and_does_not_add_runtime_tools():
    prompt = build_chat_title_prompt("x" * 20_000)
    assert len(prompt) < 12_500
    assert "tools" not in prompt.lower()


def test_title_source_survives_production_storage_and_snapshot_transport(tmp_path, monkeypatch):
    import dashboard_server as server
    from chat_snapshot_transport import snapshot_page
    from dashboard_api_models import ChatTranscriptsRequest

    monkeypatch.setattr(server, "chat_transcripts_path", lambda: tmp_path / "chats.json")
    monkeypatch.setattr(server, "chat_project_index_path", lambda: tmp_path / "projects.json")
    chats = [
        {"id": "auto-chat", "title": "First request", "titleSource": "auto", "items": [
            {"id": "auto-user", "type": "user", "text": "first"},
        ]},
        {"id": "manual-chat", "title": "User title", "titleSource": "manual", "items": [
            {"id": "manual-user", "type": "user", "text": "first"},
        ]},
    ]
    result, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=chats))
    assert result["ok"] is True
    saved = json.loads((tmp_path / "chats.json").read_text(encoding="utf-8"))["chats"]
    assert [chat["titleSource"] for chat in saved] == ["auto", "manual"]

    page = snapshot_page({"chats": saved})
    assert [chat["titleSource"] for chat in json.loads(page["text"])["chats"]] == ["auto", "manual"]


def test_title_source_integrity_rejects_unknown_values():
    from session_store_integrity import is_valid_chat_record

    chat = {"id": "chat", "title": "title", "titleSource": "auto", "items": []}
    assert is_valid_chat_record(chat)
    chat["titleSource"] = "provider"
    assert not is_valid_chat_record(chat)


def test_chat_title_route_signals_worker_when_request_is_cancelled(monkeypatch):
    async def scenario():
        import dashboard_server as server
        from dashboard_api_models import ChatTitleRequest

        called = Event()
        worker_cancelled = Event()

        class BlockingTitleService:
            def generate(self, **kwargs):
                called.set()
                kwargs["cancel_event"].wait(2)
                worker_cancelled.set()
                return type("Result", (), {"ok": False, "title": "", "reason": "cancelled"})()

        monkeypatch.setattr(server, "CHAT_TITLE_SERVICE", BlockingTitleService())
        task = asyncio.create_task(server.app_chat_title(ChatTitleRequest(message="hello")))
        assert await asyncio.to_thread(called.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(worker_cancelled.wait, 1)

    asyncio.run(scenario())
