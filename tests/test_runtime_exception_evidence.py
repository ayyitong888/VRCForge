"""Core errors and typed exceptions must reach the internal model with corrections."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

import dashboard_server as dashboard
import vrchat_blendshape_agent as agent
from tests.test_native_runtime_gateway import setup_gateway, call, run
from tests.test_unity_mcp_core_routing import _settings


def core_failure(*, large=False):
    data = {
        "nextAction": "Inspect the exact component members and correct MissingField before retrying.",
        "failureLayer": "unity_editor_tool", "failurePhase": "property_read",
        "gameObjectPath": "Avatar/Body", "componentType": "UnityEngine.SkinnedMeshRenderer",
        "propertyPath": "MissingField",
        "diagnostics": {"handlerException": {"type": "System.MissingFieldException",
            "message": "Field MissingField does not exist.", "stackTrace": "ComponentCrudCore.ResolveMember"}},
        "token": "fixture-secret-never-model",
    }
    if large:
        data["additionalEvidence"] = "long retained evidence " * 6000
    return {"isError": True, "content": [{"type": "text", "text": "Get property failed: Field MissingField does not exist."}],
        "structuredContent": {"success": False, "code": "component_property_read_failed",
            "error": "Get property failed: Field MissingField does not exist.", "data": data}}


@pytest.mark.parametrize("lane", ["scalar", "direct_exception", "entrypoint_exception"])
def test_original_core_error_reaches_next_native_request_and_external_caller(tmp_path, lane):
    raw = core_failure()
    descriptor = tmp_path / "Library/VRCForge/mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")
    params = {"gameObjectPath": "Avatar/Body", "componentType": "UnityEngine.SkinnedMeshRenderer", "propertyPath": "MissingField"}
    name = "vrcforge_get_property" if lane == "scalar" else "fixture_exception"
    gateway, model, _ = setup_gateway(tmp_path, [call("read", name, params),
        {"role": "assistant", "content": "Correct the missing field before retrying."}])

    class CoreClient:
        def __init__(self, *args, **kwargs): pass
        def call_tool(self, *args, **kwargs): return deepcopy(raw)

    def throw(_params):
        raise agent.UnityMcpError("Unity MCP Core rejected the tool execution.",
            cause_code="unity_core_tool_rejected", core_tool="vrc_get_property", raw_result=deepcopy(raw))

    handler = dashboard.read_component_property_sync if lane == "scalar" else throw
    if lane == "entrypoint_exception":
        gateway.register_tool("fixture_leaf", "When to use: read. When NOT to use: write.", "plan/preview", throw)
        handler = lambda values: gateway.runtime_skills._execute_skill_entrypoint(
            {"allowedTools": ["fixture_leaf"]}, "fixture_leaf", values, "replay", gateway.ensure_config(), "")
    gateway.register_tool(name, "When to use: read. When NOT to use: write.", "plan/preview", handler)
    with patch.object(dashboard, "load_dashboard_settings", lambda *_args, **_kwargs: _settings(tmp_path)), \
         patch.object(agent, "UnityMcpCoreClient", CoreClient), \
         patch.object(agent, "probe_unity_reload_dialog", lambda *_args: {}):
        result = run(gateway, tmp_path, maxAgenticTurns=3)
        receipt = next(m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "read")
        observation = json.loads(receipt["content"])["observations"][0]
        assert observation["outcome"]["status"] == "failed"
        for expected in ("component_property_read_failed", "MissingField", "correct MissingField", "System.MissingFieldException"):
            assert expected in receipt["content"]
        assert "fixture-secret-never-model" not in receipt["content"]
        assert result["plan"]["nextStep"] != "done"
        config = gateway.ensure_config()
        config.enabled = True
        gateway.save_config(config)
        external = gateway.call_tool(name, params)
        external_wire = json.dumps(external)
        for expected in ("component_property_read_failed", "MissingField", "correct MissingField", "System.MissingFieldException"):
            assert expected in external_wire


def test_exception_raw_evidence_uses_existing_model_result_paging(tmp_path):
    raw = core_failure(large=True)
    def throw(_params):
        raise agent.UnityMcpError("Unity MCP Core rejected the tool execution.",
            cause_code="unity_core_tool_rejected", raw_result=raw)
    gateway, model, _ = setup_gateway(tmp_path, [call("read", "fixture_exception", {}),
        {"role": "assistant", "content": "The missing field must be corrected."}])
    gateway.register_tool("fixture_exception", "When to use: read. When NOT to use: write.", "plan/preview", throw)
    result = run(gateway, tmp_path, maxAgenticTurns=3)
    receipt = next(m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "read")
    assert len(receipt["content"]) < 30000
    assert "fixture-secret-never-model" not in receipt["content"]
    assert any(step.get("resultRead") or step.get("modelInformationRead") for step in result["steps"])
    assert "component_property_read_failed" in receipt["content"]
    assert "correct MissingField" in receipt["content"]
    retained = next(step["result"] for step in result["steps"] if step.get("tool") == "fixture_exception" and step.get("result"))
    assert retained["structuredContent"]["data"]["diagnostics"]["handlerException"]["type"] == "System.MissingFieldException"


def test_typed_parameter_error_details_and_correction_reach_next_request(tmp_path):
    def reject(_params):
        error = ValueError("componentIndex must be a nonnegative integer.")
        error.code = "component_index_invalid"
        error.details = {"field": "componentIndex", "observed": -1, "expected": "nonnegative integer",
            "nextAction": "Read the current component index and replace componentIndex=-1 before retrying."}
        raise error
    gateway, model, _ = setup_gateway(tmp_path, [call("invalid", "fixture_validation", {"componentIndex": -1}),
        {"role": "assistant", "content": "Correct componentIndex before retrying."}])
    gateway.register_tool("fixture_validation", "When to use: inspect. When NOT to use: write.", "plan/preview", reject)
    assert gateway.runtime_skills.execute("fixture_validation", {"componentIndex": -1}, "internal")["outcome"]["cause"]["category"] == "ValueError"
    run(gateway, tmp_path, maxAgenticTurns=3)
    message = next(m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "invalid")
    outcome = json.loads(message["content"])["observations"][0]["outcome"]
    assert outcome["status"] == "failed"
    assert "ValueError" in message["content"]
    assert "component_index_invalid" in message["content"]
    assert "nonnegative integer" in message["content"]
    assert "replace componentIndex=-1" in message["content"]


def test_exception_wrapper_does_not_replace_explicit_domain_cause_category():
    from tests.test_agent_runtime_skill_executor import FakeTool, make_executor

    raw = core_failure()
    raw["structuredContent"]["data"]["failureCause"] = {
        "code": "component_property_read_failed", "category": "component_member",
        "failureLayer": "unity_editor_tool", "failurePhase": "property_read",
        "message": "Field MissingField does not exist.",
    }
    def reject(_params):
        raise agent.UnityMcpError("Core rejected this read.", raw_result=raw)
    tool = FakeTool("fixture_exception", handler=reject)
    executor, _ = make_executor(tools={tool.name: tool})
    result = executor.execute(tool.name, {}, "internal")
    assert result["outcome"]["cause"]["category"] == "component_member"
    assert result["outcome"]["cause"]["layer"] == "unity_editor_tool"
    assert "correct MissingField" in result["outcome"]["nextAction"]
