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


def _configured_service(path, base_url: str) -> ProviderConfigurationService:
    path.write_text(
        json.dumps({
            "api": {
                "provider": "custom",
                "api_key": "fixture-loop-key",
                "base_url": base_url,
                "model": "loop-model",
                "api_type": "chat_completions",
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
def _planner_loopback(responses: list[dict[str, object]], records: list[dict[str, object]]) -> Iterator[str]:
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


def test_persisted_provider_shape_drives_real_multiturn_loop_to_finish(tmp_path) -> None:
    records: list[dict[str, object]] = []
    responses = [
        {"action": "skill", "skill_tool": "unity_scan_materials", "skill_params": {}, "continueLoop": True},
        {"action": "skill", "skill_tool": "health", "skill_params": {}, "continueLoop": True},
        {
            "action": "reply",
            "reply": "The configured planner completed both inspections.",
            "continueLoop": False,
            "completion_claim": {
                "satisfied": True,
                "evidence_action_ids": [
                    canonical_action_id("skill", "vrcforge_scan_materials", {}),
                    canonical_action_id("skill", "vrcforge_health", {}),
                ],
            },
        },
    ]
    with _planner_loopback(responses, records) as base_url:
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
        "vrcforge_scan_materials",
        "vrcforge_health",
    ]
    assert all(step["status"] == "executed" for step in result["steps"])
    assert len(records) == 3
    assert all(record["path"] == "/v1/chat/completions" for record in records)
    assert all(record["authorization"] == "Bearer fixture-loop-key" for record in records)
    assert all(record["body"]["model"] == "loop-model" for record in records)
    assert "vrcforge_scan_materials completed" in json.dumps(records[1]["body"]["messages"])
    assert "vrcforge_health completed" in json.dumps(records[2]["body"]["messages"])
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
