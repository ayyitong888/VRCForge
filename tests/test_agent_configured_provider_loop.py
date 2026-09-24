"""Configured Provider -> real protocol -> bounded Agent loop integration proof."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from typing import Iterator
from unittest.mock import patch

import dashboard_server
from agent_task_loop import canonical_action_id
from provider_configuration_service import ProviderConfigurationPersistencePorts, ProviderConfigurationService


def _configured_service(path, base_url: str, api_type: str = "chat_completions") -> ProviderConfigurationService:
    path.write_text(
        json.dumps({
            "api": {
                "provider": "custom",
                "api_key": "fixture-loop-key",
                "base_url": base_url,
                "model": "loop-model",
                "api_type": api_type,
            }
        }),
        encoding="utf-8",
    )
    settings = type("FixtureSettings", (), {
        "llm_provider": "custom",
        "llm_api_key": "fallback-unused",
        "llm_model": "fallback-unused-model",
        "gemini_thinking_level": "",
    })()
    persistence = ProviderConfigurationPersistencePorts(
        config_path=path,
        load_runtime_settings=lambda: settings,
        atomic_write_json=lambda target, payload: target.write_text(json.dumps(payload), encoding="utf-8"),
        path_is_reparse_or_link=lambda _path: False,
    )
    return ProviderConfigurationService(
        persistence,
        dashboard_server.PROVIDER_CONFIGURATION._policy,
        RLock(),
    )


@contextmanager
def _isolated_gateway(tmp_path) -> Iterator[object]:
    gateway = dashboard_server.AGENT_GATEWAY
    old_paths = (gateway.config_path, gateway.audit_dir)
    was_accepting = bool(getattr(dashboard_server._RUNTIME_PLANNER_MODEL, "_accepting", False))
    gateway.configure_paths(tmp_path / "gateway-config.json", tmp_path / "gateway-audit")
    if not was_accepting:
        dashboard_server._RUNTIME_PLANNER_MODEL.start()
    try:
        yield gateway
    finally:
        if not was_accepting:
            dashboard_server._RUNTIME_PLANNER_MODEL.shutdown(timeout_seconds=1.0)
        gateway.configure_paths(*old_paths)


@contextmanager
def _planner_loopback(
    responses: list[dict[str, object]],
    records: list[dict[str, object]],
    *,
    native: bool = False,
) -> Iterator[str]:
    def native_call(response: dict[str, object]) -> tuple[str, dict[str, object]]:
        action = str(response.get("action") or "reply")
        if action == "reply":
            return "vrcforge_runtime_action", response
        if action == "enter_execution":
            return "vrcforge_runtime_action", response
        if action == "write":
            name = str(response.get("write_tool") or "")
            arguments = response.get("write_params") or {}
        else:
            name = str(response.get("skill_tool") or "")
            arguments = response.get("skill_params") or {}
        return name, arguments if isinstance(arguments, dict) else {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            records.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "body": json.loads(raw.decode("utf-8")),
                }
            )
            if self.path != "/v1/chat/completions" or len(records) > len(responses):
                self.send_response(404)
                self.end_headers()
                return
            payload = responses[len(records) - 1]
            if native:
                function_name, arguments = native_call(payload)
                if function_name == "vrcforge_runtime_action":
                    arguments = {key: payload[key] for key in (
                        "action", "reply", "summary", "shell_command", "shell_params",
                        "completion_claim", "correction_for_action_id",
                    ) if key in payload}
                encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                midpoint = max(1, len(encoded) // 2)
                events = [
                    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": f"call-{len(records)}", "type": "function", "function": {"name": function_name, "arguments": encoded[:midpoint]}}]}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": encoded[midpoint:]}}]}, "finish_reason": "tool_calls"}]},
                    {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
                ]
            else:
                encoded = json.dumps(payload, ensure_ascii=False)
                midpoint = max(1, len(encoded) // 2)
                events = [
                    {"choices": [{"index": 0, "delta": {"content": encoded[:midpoint]}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"content": encoded[midpoint:]}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
                ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for event in events:
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, name="configured-planner-loopback", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@contextmanager
def _provider_protocol_loopback(api_type: str, responses: list[dict[str, object]], records: list[dict[str, object]]) -> Iterator[str]:
    """Serve the native wire format consumed by one real provider SDK."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            records.append({
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "x_api_key": self.headers.get("x-api-key", ""),
                "anthropic_version": self.headers.get("anthropic-version", ""),
                "x_goog_api_key": self.headers.get("x-goog-api-key", ""),
                "body": json.loads(raw.decode("utf-8")),
            })
            if len(records) > len(responses):
                self.send_response(404)
                self.end_headers()
                return
            value = json.dumps(responses[len(records) - 1], ensure_ascii=False)
            midpoint = max(1, len(value) // 2)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if api_type == "responses":
                events = [
                    {"type": "response.output_text.delta", "delta": value[:midpoint]},
                    {"type": "response.output_text.delta", "delta": value[midpoint:]},
                    {"type": "response.completed", "response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": value}]}], "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}}},
                ]
                for event in events:
                    self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
            elif api_type == "messages":
                events = [
                    ("message_start", {"type": "message_start", "message": {"id": "msg_fixture", "type": "message", "role": "assistant", "content": [], "model": "loop-model", "usage": {"input_tokens": 11, "output_tokens": 0}}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": value[:midpoint]}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": value[midpoint:]}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                for event, data in events:
                    self.wfile.write(("event: " + event + "\ndata: " + json.dumps(data) + "\n\n").encode())
            else:
                # google-genai's streaming REST endpoint emits one SSE candidate per chunk.
                for text in (value[:midpoint], value[midpoint:]):
                    data = {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}}], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18}}
                    self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, name=f"configured-{api_type}-loopback", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_persisted_provider_shape_drives_real_multiturn_loop_to_finish(tmp_path) -> None:
    records: list[dict[str, object]] = []
    responses = [
        {"action": "skill", "skill_tool": "read_text_file", "skill_params": {"path": "ProjectSettings/ProjectVersion.txt"}, "continueLoop": True},
        {"action": "skill", "skill_tool": "read_text_file", "skill_params": {"path": "Packages/manifest.json"}, "continueLoop": True},
        {
            "action": "reply",
            "reply": "The configured planner completed both inspections.",
            "continueLoop": False,
            "completion_claim": {
                "satisfied": True,
                "evidence_action_ids": [
                    canonical_action_id("skill", "vrcforge_read_text_file", {"path": "ProjectSettings/ProjectVersion.txt"}),
                    canonical_action_id("skill", "vrcforge_read_text_file", {"path": "Packages/manifest.json"}),
                ],
            },
        },
    ]
    with _planner_loopback(responses, records, native=True) as base_url:
        config_path = tmp_path / "provider-config.json"
        configured_service = _configured_service(config_path, base_url)

        def execute_skill(_owner, tool, _params, agent_name=None, owner_id=""):
            return {
                "ok": True,
                "tool": tool,
                "status": "executed",
                "result": {"ok": True, "marker": tool},
                "outcome": {
                    "status": "ok",
                    "summary": tool + " completed",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with _isolated_gateway(tmp_path) as gateway:
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured_service), patch.object(
                type(gateway.runtime_skills), "execute", autospec=True, side_effect=execute_skill
            ):
                result = gateway.runtime_message(
                    {
                        "message": "Inspect the configured runtime and finish when both checks are complete.",
                        "provider": "custom",
                        "model": "loop-model",
                        "session_id": "configured-provider-loop-session",
                        "client_turn_id": "configured-provider-loop-turn",
                        "maxAgenticTurns": 4,
                    }
                )

    assert result["plan"]["nextStep"] == "done", {
        "plan": {key: result["plan"].get(key) for key in ("nextStep", "plannerFailure", "reply", "summary", "skillTool", "skillParams")},
        "records": records,
        "steps": result.get("steps"),
    }
    assert result["plan"]["reply"] == "The configured planner completed both inspections."
    assert [step["tool"] for step in result["steps"]] == [
        "vrcforge_read_text_file",
        "vrcforge_read_text_file",
    ]
    assert all(step["status"] == "executed" for step in result["steps"])
    assert len(records) == 3
    assert all(record["path"] == "/v1/chat/completions" for record in records)
    assert all(record["authorization"] == "Bearer fixture-loop-key" for record in records)
    assert all(record["body"]["model"] == "loop-model" for record in records)
    assert "vrcforge_read_text_file completed" in json.dumps(records[1]["body"]["messages"])
    assert "vrcforge_read_text_file completed" in json.dumps(records[2]["body"]["messages"])
    assert result["contextUsage"]["cumulativeInputTokens"] == 33
    assert result["contextUsage"]["cumulativeOutputTokens"] == 21
    assert result["contextUsage"]["cumulativeTotalTokens"] == 54
    assert "fixture-loop-key" not in json.dumps(result)


def test_real_provider_protocol_failure_cannot_finish_as_success(tmp_path) -> None:
    records: list[dict[str, object]] = []
    with _planner_loopback([], records) as base_url:
        configured_service = _configured_service(tmp_path / "provider-config.json", base_url)

        with _isolated_gateway(tmp_path) as gateway:
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured_service):
                result = gateway.runtime_message(
                    {
                        "message": "Use the configured provider.",
                        "provider": "custom",
                        "model": "loop-model",
                        "session_id": "configured-provider-failure-session",
                        "client_turn_id": "configured-provider-failure-turn",
                        "maxAgenticTurns": 1,
                    }
                )

    assert result["plan"]["nextStep"] != "done"
    assert result["plan"]["nextStep"] in {"provider_failed", "tool_failed", "planner_failed"}


def _run_non_chat_protocol_loop(tmp_path, api_type: str, protocol_name: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    responses = [
        {"action": "skill", "skill_tool": "health", "skill_params": {}, "continueLoop": True},
        {"action": "reply", "reply": f"{protocol_name} loop finished.", "continueLoop": False, "completion_claim": {"satisfied": True, "evidence_action_ids": [canonical_action_id("skill", "vrcforge_health", {})]}},
    ]
    with _provider_protocol_loopback(api_type, responses, records) as base_url:
        configured_service = _configured_service(tmp_path / f"{api_type}-provider-config.json", base_url, api_type)

        def execute_skill(_owner, tool, _params, agent_name=None, owner_id=""):
            return {"ok": True, "tool": tool, "status": "executed", "result": {"ok": True, "marker": tool}, "outcome": {"status": "ok", "summary": tool + " completed", "verification": {"state": "not_required", "checks": []}}}

        with _isolated_gateway(tmp_path) as gateway:
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured_service), patch.object(type(gateway.runtime_skills), "execute", autospec=True, side_effect=execute_skill):
                result = gateway.runtime_message({"message": f"Run the {protocol_name} configured provider loop.", "provider": "custom", "model": "loop-model", "session_id": f"{api_type}-session", "client_turn_id": f"{api_type}-turn", "maxAgenticTurns": 3})
    return result, records


def test_responses_protocol_loopback_supports_multiturn_finish(tmp_path) -> None:
    result, records = _run_non_chat_protocol_loop(tmp_path, "responses", "Responses")
    assert result["plan"]["nextStep"] == "done"
    assert result["plan"]["reply"] == "Responses loop finished."
    assert len(result["steps"]) == 1
    assert all(step["status"] == "executed" for step in result["steps"])
    assert len(records) == 2
    assert all(record["path"] == "/v1/responses" for record in records)
    assert all(record["authorization"] == "Bearer fixture-loop-key" for record in records)
    assert all(record["body"]["model"] == "loop-model" for record in records)
    assert "vrcforge_health completed" in json.dumps(records[1]["body"])
    assert result["contextUsage"]["cumulativeInputTokens"] == 22
    assert result["contextUsage"]["cumulativeOutputTokens"] == 14
    assert result["contextUsage"]["cumulativeTotalTokens"] == 36
    assert "fixture-loop-key" not in json.dumps(result)


def test_anthropic_messages_protocol_loopback_supports_multiturn_finish(tmp_path) -> None:
    result, records = _run_non_chat_protocol_loop(tmp_path, "messages", "Anthropic")
    assert result["plan"]["nextStep"] == "done"
    assert result["plan"]["reply"] == "Anthropic loop finished."
    assert len(result["steps"]) == 1
    assert all(step["status"] == "executed" for step in result["steps"])
    assert len(records) == 2
    assert all(record["path"].split("?", 1)[0].endswith("/messages") for record in records), records
    assert all(record["authorization"] == "" for record in records)
    assert all(record["x_api_key"] == "fixture-loop-key" for record in records)
    assert all(record["anthropic_version"] for record in records)
    assert all(record["body"]["model"] == "loop-model" for record in records)
    assert "vrcforge_health completed" in json.dumps(records[1]["body"])
    assert result["contextUsage"]["cumulativeInputTokens"] == 22
    assert result["contextUsage"]["cumulativeOutputTokens"] == 14
    assert result["contextUsage"]["cumulativeTotalTokens"] == 36
    assert "fixture-loop-key" not in json.dumps(result)


def test_gemini_generate_content_protocol_loopback_supports_multiturn_finish(tmp_path) -> None:
    result, records = _run_non_chat_protocol_loop(tmp_path, "generate_content", "Gemini")
    assert result["plan"]["nextStep"] == "done"
    assert result["plan"]["reply"] == "Gemini loop finished."
    assert len(result["steps"]) == 1
    assert all(step["status"] == "executed" for step in result["steps"])
    assert len(records) == 2
    assert all("/models/loop-model:streamGenerateContent" in record["path"] for record in records)
    assert all(record["x_goog_api_key"] == "fixture-loop-key" or "key=fixture-loop-key" in record["path"] for record in records)
    assert all(record["body"]["contents"] for record in records)
    assert "vrcforge_health completed" in json.dumps(records[1]["body"])
    assert result["contextUsage"]["cumulativeInputTokens"] == 22
    assert result["contextUsage"]["cumulativeOutputTokens"] == 14
    assert result["contextUsage"]["cumulativeTotalTokens"] == 36
    assert "fixture-loop-key" not in json.dumps(result)
