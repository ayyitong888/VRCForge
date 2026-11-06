"""Offline truncation acceptance through auto-bound runtime planning."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import dashboard_server
import pytest

from agent_task_loop import canonical_action_id
from chatgpt_integration.adapter import build_responses_input
from provider_configuration_service import ProviderApiConfig
from telegram_bot_host import TelegramBotHost
from telegram_bot_store import TelegramBotStore
from tests.test_agent_configured_provider_loop import _configured_service, _isolated_gateway
from tests.test_native_history_storage import owner as history_owner
from vrchat_blendshape_agent import LlmPlanResponse
import vrchat_blendshape_agent


CONTINUE = "上条回复因长度上限中断，请从断点直接继续，不要重复已写内容。"


class _Compat404(RuntimeError):
    status_code = 404


class _Transport:
    async def get_updates(self, **_kwargs):
        return []

    async def send_message(self, *_args, **_kwargs):
        return [{"ok": True}]

    async def close(self):
        return None


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "call_tool", "arguments": json.dumps({"tool": name, "arguments": arguments})},
    }


def _reply(text: str, evidence: list[str] | None = None) -> LlmPlanResponse:
    args = {"action": "reply", "reply": text, "completion_claim": {
        "satisfied": True, "evidence_action_ids": evidence or [],
    }}
    return LlmPlanResponse(
        text="", reasoning={}, finish_reason="tool_calls",
        assistant_message={"role": "assistant", "content": None, "tool_calls": [{
            "id": "final", "type": "function", "function": {
                "name": "vrcforge_runtime_action", "arguments": json.dumps(args),
            },
        }]},
    )


def _read_evidence(path: str) -> list[str]:
    return [canonical_action_id("skill", "vrcforge_read_text_file", {"path": path})]


def _run_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: list, *, execute=None, output_limit=None):
    calls: list[dict] = []
    iterator = iter(outputs)

    def dispatch(settings, api_type, prompt, *args, **kwargs):
        stream_callback = args[1] if len(args) > 1 else None
        native_messages = args[4] if len(args) > 4 else kwargs.get("native_messages")
        native_tools = args[5] if len(args) > 5 else kwargs.get("native_tools")
        calls.append({
            "api_type": api_type,
            "settings": settings,
            "prompt": prompt,
            "native_messages": deepcopy(native_messages),
            "native_tools": deepcopy(native_tools),
        })
        output = next(iterator)
        if isinstance(output, BaseException):
            raise output
        if callable(output):
            output = output(calls[-1], stream_callback)
        return output

    monkeypatch.setattr(vrchat_blendshape_agent, "_request_llm_protocol_with_metadata", dispatch)
    base_url = "http://127.0.0.1:9/v1"
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "auto-provider.json"
    configured = _configured_service(config_path, base_url, "auto")
    invoked: list[tuple[str, dict]] = []

    def execute_skill(_owner, tool, params, agent_name=None, owner_id=""):
        invoked.append((tool, deepcopy(params)))
        return {
            "ok": True, "tool": tool, "status": "executed", "result": {"ok": True, "marker": tool},
            "outcome": {"status": "ok", "summary": tool + " completed",
                        "verification": {"state": "not_required", "checks": []}},
        }

    with _isolated_gateway(tmp_path) as gateway:
        with monkeypatch.context() as scoped:
            scoped.setattr(dashboard_server, "PROVIDER_CONFIGURATION", configured)
            if output_limit is not None:
                scoped.setattr(type(dashboard_server.PROVIDER_MODEL_CATALOG), "resolve_output_limit",
                               lambda _self, _config: output_limit)
            if execute is None:
                scoped.setattr(type(gateway.runtime_skills), "execute", execute_skill)
            else:
                scoped.setattr(type(gateway.runtime_skills), "execute", execute)
            result = gateway.runtime_message({
                "message": "Use the configured provider and complete the task.",
                "provider": "custom", "model": "loop-model", "session_id": "auto-trunc-session",
                "client_turn_id": "auto-trunc-turn", "maxAgenticTurns": 8,
            })
        snapshot = next(iter(gateway.runtime_sessions._native_conversations.values()), None)
        restored = None
        if snapshot:
            restored = history_owner(gateway.audit_dir / "native-history").native_conversation(
                "auto-trunc-session", binding=snapshot["binding"],
            )
    return result, calls, invoked, restored


def test_auto_binding_continuation_is_hidden_restorable_and_telegram_shows_one_reply(tmp_path, monkeypatch):
    config = ProviderApiConfig("custom", "fixture-key", "http://127.0.0.1:9/v1", "loop-model", "auto")
    assert config.native_binding()

    result, calls, _, restored = _run_auto(tmp_path, monkeypatch, [
        LlmPlanResponse(text="", reasoning={}, finish_reason="tool_calls",
                        assistant_message={"role": "assistant", "content": None, "tool_calls": [
                            _tool_call("read-first", "read_text_file", {"path": "first.txt"}),
                        ]}),
        LlmPlanResponse(
            text="第一段", reasoning={}, finish_reason="length",
            assistant_message={"role": "assistant", "content": "第一段", "tool_calls": []},
            truncation={"maxOutputTokens": 8},
        ),
        _reply("第二段", _read_evidence("first.txt")),
    ])

    assert result["plan"]["nextStep"] == "done", result["plan"]
    assert result["plan"]["reply"] == "第一段第二段"
    assert [entry["api_type"] for entry in calls] == ["responses", "responses", "responses"]
    hidden = [message for message in calls[2]["native_messages"] if message.get("hidden")]
    assert [message["content"] for message in hidden] == [CONTINUE]
    assert restored and any(message.get("hidden") and message["content"] == CONTINUE for message in restored["messages"])
    assert any(message.get("provider_protocol") == "responses" for message in restored["messages"]
               if message.get("role") == "assistant")
    wire = build_responses_input(calls[2]["native_messages"])
    assert CONTINUE in json.dumps(wire, ensure_ascii=False)
    assert '"hidden"' not in json.dumps(wire, ensure_ascii=False)

    async def telegram_projection():
        with TelegramBotStore(tmp_path / "telegram.db", "bot") as store:
            host = TelegramBotHost(_Transport(), store, lambda _event: asyncio.sleep(0), 7, poll_timeout=0)
            assert host.emit({"event": "turn_returned", "sessionId": "auto-trunc-session",
                              "turnId": "t", "clientTurnId": "c", "result": result,
                              "metadata": {"route": "7"}})
            queued = store.claim_outbox()
            assert [item.payload["text"] for item in queued] == ["第一段第二段"]
            assert CONTINUE not in repr([item.payload for item in queued])
            for item in queued:
                store.mark_outbox_failed(item.key, retryable=True)
            await host.stop()

        with TelegramBotStore(tmp_path / "telegram.db", "bot") as restored_store:
            replayed = restored_store.claim_outbox()
            assert [item.payload["text"] for item in replayed] == ["第一段第二段"]
            assert CONTINUE not in repr([item.payload for item in replayed])

    asyncio.run(telegram_projection())


def test_auto_two_complete_calls_and_truncated_third_leave_no_orphan_in_next_history(tmp_path, monkeypatch):
    dropped = {"id": "call-3", "name": "read_text_file", "arguments": "{\"path\":",
               "reason": "输出长度上限，调用块未结束", "suggestion": "拆成更小的调用重发"}
    first = LlmPlanResponse(
        text="", reasoning={}, finish_reason="length",
        assistant_message={"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call-1", "read_text_file", {"path": "one.txt"}),
            _tool_call("call-2", "read_text_file", {"path": "two.txt"}),
        ]},
        truncation={"discardedToolCalls": [dropped], "maxOutputTokens": 8},
    )
    result, calls, invoked, _ = _run_auto(tmp_path, monkeypatch, [
        first, _reply("已读取两份文件", _read_evidence("one.txt") + _read_evidence("two.txt")),
    ])
    assert result["plan"]["nextStep"] == "done"
    assert len(invoked) == 2
    assert [params["path"] for _tool, params in invoked] == ["one.txt", "two.txt"]
    history = calls[1]["native_messages"]
    call_ids = {call["id"] for message in history for call in message.get("tool_calls", [])}
    result_ids = {message["tool_call_id"] for message in history if message.get("role") == "tool"}
    assert call_ids == result_ids == {"call-1", "call-2"}
    assert all(call.get("id") != "call-3" for message in history for call in message.get("tool_calls", []))
    assert all(message.get("tool_call_id") != "call-3" for message in history if message.get("role") == "tool")
    assert any(message.get("hidden") and "call-3" in message["content"] for message in history)


def test_auto_stops_after_three_continuations_at_output_limit(tmp_path, monkeypatch):
    truncated = [LlmPlanResponse(
        text=f"part-{index}", reasoning={}, finish_reason="length",
        assistant_message={"role": "assistant", "content": f"part-{index}", "tool_calls": []},
        truncation={"maxOutputTokens": 8},
    ) for index in range(4)]
    result, calls, _, _ = _run_auto(tmp_path, monkeypatch, truncated)
    assert len(calls) == 4
    assert result["plan"]["reason"] == "output_length_limit"
    assert "输出长度上限" in result["plan"]["reply"]
    assert result["plan"]["nextStep"] == "output_length_limit"


def test_auto_empty_body_increases_cap_without_hidden_continue(tmp_path, monkeypatch):
    def empty(cap):
        return LlmPlanResponse(
            text="", reasoning={}, finish_reason="length",
            assistant_message={"role": "assistant", "content": None, "tool_calls": []},
            truncation={"maxOutputTokens": cap, "modelMaxOutputTokens": 32},
        )
    result, calls, _, _ = _run_auto(tmp_path, monkeypatch, [
        _Compat404("unsupported endpoint"), empty(8), empty(16), empty(32),
    ], output_limit=32)
    assert result["plan"]["nextStep"] == "output_length_limit"
    assert [call["api_type"] for call in calls] == ["responses", "chat_completions", "chat_completions", "chat_completions"]
    assert [call["settings"].llm_max_output_tokens for call in calls] == [None, None, 16, 32]
    for call in calls[1:]:
        assert not any(message.get("hidden") for message in call["native_messages"])
        # No empty assistant receipt is stored, so this route pin is carried in turn state.
        assert not any(message.get("role") == "assistant" and not message.get("content")
                       and not message.get("tool_calls") for message in call["native_messages"])


def test_auto_protocol_fallback_is_allowed_only_before_output(tmp_path, monkeypatch):
    def after_output(_call, callback):
        callback("partial bytes")
        raise _Compat404("unsupported endpoint")

    outputs = [
        _Compat404("unsupported endpoint"),
        LlmPlanResponse(text="", reasoning={}, finish_reason="tool_calls",
                        assistant_message={"role": "assistant", "content": None, "tool_calls": [
                            _tool_call("fallback-read", "read_text_file", {"path": "fallback.txt"}),
                        ]}),
        _reply("fallback worked", _read_evidence("fallback.txt")),
    ]
    result, calls, _, _ = _run_auto(tmp_path / "before", monkeypatch, outputs)
    assert result["plan"]["nextStep"] == "done"
    assert [call["api_type"] for call in calls] == ["responses", "chat_completions", "chat_completions"]

    result, calls, _, _ = _run_auto(tmp_path / "after", monkeypatch, [after_output, _reply("must not run")])
    assert len(calls) == 1
    assert calls[0]["api_type"] == "responses"
    assert result["plan"]["nextStep"] == "planner_failed"


@pytest.mark.parametrize("first_protocol", ["responses", "chat_completions", "messages", "generate_content"])
def test_auto_dispatch_keeps_same_binding_for_supported_protocol_candidate(tmp_path, monkeypatch, first_protocol):
    candidates = ["responses", "chat_completions", "messages", "generate_content"]
    index = candidates.index(first_protocol)
    errors = [_Compat404("unsupported endpoint") for _ in range(index)]
    first_tool = LlmPlanResponse(
        text="", reasoning={}, finish_reason="tool_calls",
        assistant_message={"role": "assistant", "content": None, "tool_calls": [
            _tool_call("dispatch-read", "read_text_file", {"path": "dispatch.txt"}),
        ]},
    )
    result, calls, _, _ = _run_auto(tmp_path / first_protocol, monkeypatch, [
        *errors, first_tool, _reply("ok", _read_evidence("dispatch.txt")),
    ])
    assert result["plan"]["nextStep"] == "done"
    assert [call["api_type"] for call in calls] == [*candidates[:index], first_protocol, first_protocol]
    assert all(call["settings"].llm_api_type == call["api_type"] for call in calls)
