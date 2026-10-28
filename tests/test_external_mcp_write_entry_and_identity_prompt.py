"""External MCP write entry and identity-lock prompt regressions.

Replays the 2026-10-25 external-agent blockers:
- bind -> prompts/get(identityLockUri) must validate ref-form anchors;
- gateway rejections must reach stdio hosts as structured errors, not
  "Internal MCP server error";
- external stdio exposure must have a Gateway-config execution entry.
"""
import importlib
import json

from pathlib import Path

import agent_gateway
import execution_target
import pytest

from agent_gateway import AgentGateway
from execution_target import execution_target_digest, project_identity
from mcp_prompt_registry import McpPromptError
from mcp_resource_registry import McpResourceError
from tools.vrcforge_agent_mcp_stdio import ExternalHttpBridgeError, VRCForgeBridge


def _target(path: Path) -> dict:
    root = str(path.resolve())
    project_id = project_identity(root)
    return {
        "schema": "vrcforge.execution_target.v1",
        "scope": "project",
        "namespace": f"vrcforge://projects/{project_id}",
        "project": {"root": root, "projectId": project_id},
        "editor": {"unityPid": 123, "processStartTime": "1.0", "coreInstanceId": "core-1"},
    }


def _gateway(tmp_path: Path, monkeypatch) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    skill = {
        "name": "fixture-workflow",
        "source": "user",
        "skillType": "package",
        "instructions": "Inspect the exact target before acting.",
        "allowedTools": [],
    }
    monkeypatch.setattr(
        type(gateway._skills), "build_skill_registry", lambda *a, **kw: {"skills": [skill]}
    )
    return gateway


def _inject_reference_binding(gateway: AgentGateway, target: dict, session: str) -> dict:
    """Install the owner binding a real vrcforge_bind_execution_target creates."""
    handle = "vrcforge-target-fixture"
    gateway._execution_target_bindings._bindings[handle] = {
        "target": dict(target),
        "digest": execution_target_digest(target),
        "projectRoot": target["project"]["root"],
        "sessionContext": session,
    }
    return {"schema": "vrcforge.execution_target_ref.v1", "ref": handle}


@pytest.fixture
def live_core_checks_off(monkeypatch):
    """Skip on-disk Core descriptor and psutil checks inside binding resolution."""
    monkeypatch.setattr(
        execution_target,
        "validate_runtime_execution_target",
        lambda target, *, project_root, required_scope, refresh_scene_snapshot=True: dict(target),
    )


def test_ref_form_identity_anchor_publishes_real_digest(tmp_path, monkeypatch, live_core_checks_off):
    gateway = _gateway(tmp_path, monkeypatch)
    target = _target(tmp_path / "project")
    reference = _inject_reference_binding(gateway, target, "fixture-session")
    real_digest = execution_target_digest(target)
    # The bind handler reports executionTarget/executionTargetDigest only in
    # its nested result; the outer envelope carries neither.
    result = {
        "ok": True,
        "operationId": "bind-1",
        "result": {
            "ok": True,
            "executionTarget": dict(reference),
            "executionTargetDigest": real_digest,
            "scope": "project",
        },
    }
    with gateway.resource_context("fixture-session"):
        gateway.publish_mcp_tool_result_resource(
            "vrcforge_bind_execution_target", {"projectPath": target["project"]["root"]},
            result, source_mode="test_fixture",
        )
        identity_lock_uri = result["resources"]["identityLockUri"]
        record = gateway.read_mcp_resource(identity_lock_uri)["structuredContent"]
    assert record["identity"]["executionTargetDigest"] == real_digest
    assert record["data"]["executionTargetDigest"] == real_digest


def test_bound_reference_identity_lock_resolves_prompts_get(tmp_path, monkeypatch, live_core_checks_off):
    gateway = _gateway(tmp_path, monkeypatch)
    target = _target(tmp_path / "project")
    reference = _inject_reference_binding(gateway, target, "fixture-session")
    result = {
        "ok": True,
        "operationId": "bind-2",
        "result": {
            "ok": True,
            "executionTarget": dict(reference),
            "executionTargetDigest": execution_target_digest(target),
            "scope": "project",
        },
    }
    with gateway.resource_context("fixture-session"):
        gateway.publish_mcp_tool_result_resource(
            "vrcforge_bind_execution_target", {"projectPath": target["project"]["root"]},
            result, source_mode="test_fixture",
        )
        arguments = {
            "identityLockUri": result["resources"]["identityLockUri"],
            "sessionContextUri": result["resources"]["operationReceiptUri"],
        }
        prompt = gateway.get_mcp_prompt("fixture-workflow", arguments)
        context = prompt["structuredContent"]["context"]
        assert context["identityLockUri"] == arguments["identityLockUri"]
        assert context["status"] == "ready_for_planning"


def test_stale_reference_binding_still_fails_closed_for_prompts_get(tmp_path, monkeypatch, live_core_checks_off):
    gateway = _gateway(tmp_path, monkeypatch)
    target = _target(tmp_path / "project")
    reference = _inject_reference_binding(gateway, target, "fixture-session")
    result = {
        "ok": True,
        "operationId": "bind-3",
        "result": {
            "ok": True,
            "executionTarget": dict(reference),
            "executionTargetDigest": execution_target_digest(target),
            "scope": "project",
        },
    }
    with gateway.resource_context("fixture-session"):
        gateway.publish_mcp_tool_result_resource(
            "vrcforge_bind_execution_target", {"projectPath": target["project"]["root"]},
            result, source_mode="test_fixture",
        )
        # A gateway restart or binding eviction makes the ref unresolvable.
        gateway._execution_target_bindings._bindings.clear()
        arguments = {"identityLockUri": result["resources"]["identityLockUri"]}
        with pytest.raises((McpResourceError, McpPromptError)):
            gateway.get_mcp_prompt("fixture-workflow", arguments)


def test_stdio_get_prompt_projects_gateway_rejection(monkeypatch):
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    class Bridge:
        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, exposure_layer="planning", tool_blocks=None, tool_names=None):
            return {"tools": []}

        def call_tool(self, name, arguments, **kwargs):
            return {"ok": True}

        def get_prompt(self, name, arguments):
            body = json.dumps(
                {"jsonrpc": "2.0", "id": "x", "error": {
                    "code": -32602,
                    "message": "Identity Resource anchors do not match its ExecutionTarget",
                }}
            )
            raise ExternalHttpBridgeError(status_code=400, path="/mcp", body=body)

    captured = {}
    monkeypatch.setattr(module, "run_stdio_loop", lambda router: captured.setdefault("router", router))
    monkeypatch.setattr(module, "run_standard_stdio_loop", lambda router: captured.setdefault("router", router))
    module.run_stdio_server(Bridge(), protocol_profile="mcp-1x", exposure_layer="planning")
    router = captured["router"]
    router.handle({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    })
    response = router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "vrcforge_get_prompt", "arguments": {"name": "vrcforge-avatar-wardrobe", "arguments": {}}},
    })
    assert "error" not in response or response.get("error", {}).get("code") != -32603
    structured = response["result"]["structuredContent"]
    assert structured["ok"] is False
    error_value = structured["error"]
    error_text = error_value.get("message", "") if isinstance(error_value, dict) else str(error_value)
    assert "Identity Resource anchors do not match its ExecutionTarget" in error_text
    assert structured["errorCode"] == "http_400"


def test_stdio_prompt_method_surfaces_gateway_message(monkeypatch):
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    class Bridge:
        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, exposure_layer="planning", tool_blocks=None, tool_names=None):
            return {"tools": []}

        def call_tool(self, name, arguments, **kwargs):
            return {"ok": True}

        def get_prompt(self, name, arguments):
            raise ExternalHttpBridgeError(
                status_code=400, path="/mcp", body=json.dumps({"error": {"code": -32602, "message": "gateway said no"}})
            )

    captured = {}
    monkeypatch.setattr(module, "run_stdio_loop", lambda router: captured.setdefault("router", router))
    monkeypatch.setattr(module, "run_standard_stdio_loop", lambda router: captured.setdefault("router", router))
    module.run_stdio_server(Bridge(), protocol_profile="mcp-1x", exposure_layer="planning")
    router = captured["router"]
    router.handle({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    })
    response = router.handle({
        "jsonrpc": "2.0", "id": 2, "method": "prompts/get",
        "params": {"name": "vrcforge-avatar-wardrobe", "arguments": {}},
    })
    assert response["error"]["code"] == -32602
    assert "gateway said no" in response["error"]["message"]


def test_exposure_layer_resolves_from_gateway_config(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_gateway.json"
    config_path.write_text(
        json.dumps({"token": "t", "external_mcp_exposure_layer": "execution"}), encoding="utf-8"
    )
    monkeypatch.delenv("VRCFORGE_MCP_EXPOSURE_LAYER", raising=False)
    bridge = VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config_path,
        timeout_seconds=5,
        start_runtime=False,
    )
    assert bridge.resolve_exposure_layer(None) == "execution"
    assert bridge.resolve_exposure_layer("planning") == "planning"
    monkeypatch.setenv("VRCFORGE_MCP_EXPOSURE_LAYER", "execution")
    missing = VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=tmp_path / "missing.json",
        timeout_seconds=5,
        start_runtime=False,
    )
    assert missing.resolve_exposure_layer(None) == "execution"
    monkeypatch.delenv("VRCFORGE_MCP_EXPOSURE_LAYER", raising=False)
    assert missing.resolve_exposure_layer(None) == "planning"


def test_gateway_config_persists_external_mcp_exposure_layer(tmp_path):
    config_path = tmp_path / "config" / "gateway.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"enabled": True, "token": "t", "external_mcp_exposure_layer": "execution"}), encoding="utf-8"
    )
    gateway = AgentGateway(config_path, tmp_path / "audit")
    config = gateway.ensure_config()
    assert config.external_mcp_exposure_layer == "execution"
    config.external_mcp_exposure_layer = agent_gateway.EXPOSURE_LAYER_PLANNING
    gateway.save_config(config)
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["external_mcp_exposure_layer"] == "planning"

    config_path.write_text(
        json.dumps({"enabled": True, "token": "t", "external_mcp_exposure_layer": "sudo"}), encoding="utf-8"
    )
    assert gateway.ensure_config().external_mcp_exposure_layer == "planning"


def test_http_bridge_error_extracts_jsonrpc_message_not_envelope_repr():
    body = json.dumps(
        {"jsonrpc": "2.0", "id": "x", "error": {
            "code": -32602,
            "message": "Identity Resource anchors do not match its ExecutionTarget",
            "data": {"schema": "vrcforge.external_tool_error.v1", "errorCode": "mcp_jsonrpc_-32602"},
        }}
    )
    exc = ExternalHttpBridgeError(status_code=400, path="/mcp", body=body)
    assert str(exc) == "Identity Resource anchors do not match its ExecutionTarget"
    assert exc.raw_result["error"]["code"] == -32602


def test_exception_cause_chain_messages_are_bounded():
    from external_tool_result_contract import external_exception_details

    inner = ValueError("x" * 2000)
    outer = RuntimeError("boundary failure")
    outer.__cause__ = inner
    details = external_exception_details(outer)
    messages = [entry["message"] for entry in details.get("causes", details.get("chain", []))] or None
    chain = details.get("causeChain") or details.get("causes") or []
    assert chain, details.keys()
    for entry in chain:
        assert len(entry["message"]) <= 308
    assert any(entry["message"].endswith("…") for entry in chain)


def test_budget_pause_cancels_waiting_turn_questions(tmp_path):
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "cfg.json", tmp_path / "audit")
    gateway.questions.create({
        "question": "选择哪套方案?",
        "sourceTool": "vrcforge_ask_user",
        "sessionId": "s-budget",
        "_runtimeTaskSeed": {"sessionId": "s-budget", "turnId": "t-budget", "clientTurnId": "ct-budget"},
    })
    assert gateway.questions.list(session_id="s-budget")
    gateway._cancel_turn_questions_at_pause("s-budget", "t-budget", "ct-budget")
    statuses = {
        str(item.get("status"))
        for item in gateway.questions._project(include_answered=True).values()
        if isinstance(item, dict)
    }
    assert statuses == {"cancelled"}, statuses
    # Idempotent on repeat pauses and harmless without questions.
    gateway._cancel_turn_questions_at_pause("s-budget", "t-budget", "ct-budget")
    gateway._cancel_turn_questions_at_pause("other", "none", "none")
