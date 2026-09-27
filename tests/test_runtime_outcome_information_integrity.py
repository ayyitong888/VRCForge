"""Recovery evidence must survive the actual native/provider request boundary."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_runtime_native_turn import NativeRuntimeTurn
from runtime_planner_service import RuntimePlannerService
from tests.test_native_runtime_gateway import setup_gateway
from vrchat_blendshape_agent import build_openai_compatible_request_payload


TAIL = "RECOVERY_INSTRUCTION_END_SENTINEL"


@pytest.mark.parametrize("outcome", [
    {"status": "needs_correction", "summary": "Explain the failed completion. " * 12 + TAIL},
    {"status": "failed", "observed": {"detail": "ordinary fact " * 600}, "nextAction": TAIL},
    {"status": "failed", "summary": "diagnosis " * 30,
     "error": {"likelyCauses": ["cause " * 100], "nextActions": ["followup " * 100]},
     "observed": {"detail": "ordinary fact " * 360}, "nextAction": TAIL},
    {"status": "failed", "error": {"nextActions": ["Investigate exact target. " * 30 + TAIL]}},
])
def test_complete_recovery_reaches_native_provider_payload(tmp_path, outcome):
    gateway, model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Recorded."}])
    planner = gateway.runtime_planner
    loop = []
    owner = NativeRuntimeTurn(gateway.runtime_sessions, planner, "integrity", "turn", "offline",
                              "Audit", loop, None, None, None)
    step = {"kind": "skill", "tool": "runtime_completion_gate", "status": "failed",
            "actionId": "recovery-action", "result": None, "outcome": deepcopy(outcome)}
    owner.admit({"role": "assistant", "content": None, "tool_calls": [{
        "id": "rejected", "type": "function", "function": {
            "name": "vrcforge_runtime_action", "arguments": "{}"}}]})
    owner.next_receipt()
    loop.append(step)
    owner.settle()
    planner._llm_plan_agent_turn("Audit", observe={}, history=[], loop_state=loop,
        exposure_layer="planning", project_context_active=False, internal_tool_blocks={"core"},
        native_turn=owner, propagate_provider_errors=True)
    request = model.requests[-1]
    payload = build_openai_compatible_request_payload(SimpleNamespace(
        llm_model="fixture", llm_provider="custom", gemini_thinking_level="", llm_max_output_tokens=None),
        "", native_messages=[{"role": "system", "content": request["instructions"]}, *request["messages"]],
        native_tools=request["tools"])
    assert TAIL in planner.native_result_observation(step)["observation"]
    assert TAIL in json.dumps(owner.messages())
    assert TAIL in json.dumps(payload)


def test_complete_canonical_json_keeps_redaction_and_typed_facts():
    outcome = {"status": "failed", "observed": {"detail": "ordinary fact " * 600,
        "credential": "Bearer secret-value-1234567890", "path": "C:\\private\\avatar.asset",
        "quoted": "line with \"quotes\"\nand newline", "password": "private-value"},
        "committed": False, "mutationStarted": False, "nextAction": TAIL}
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    observation = planner._llm_loop_step_observation({"tool": "runtime_completion_gate",
        "kind": "skill", "status": "failed", "result": None, "outcome": outcome})
    canonical, _ = json.JSONDecoder().raw_decode(observation.split("canonicalOutcome=", 1)[1])
    assert canonical["nextAction"] == TAIL
    assert canonical["committed"] is False and canonical["mutationStarted"] is False
    assert canonical["observed"]["quoted"] == outcome["observed"]["quoted"]
    assert "secret-value" not in observation
    assert "private-value" not in observation
    assert "private\\\\avatar" not in observation
    assert "<redacted>" in observation and "<path redacted>" in observation


@pytest.mark.parametrize("value", [
    ("Bearer nested-secret-1234567890", {"password": "private-tuple-value"}),
    SimpleNamespace(detail="Bearer object-secret-1234567890", path="C:\\private\\avatar.asset"),
])
def test_canonical_nonstandard_json_values_still_pass_through_redaction(value):
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    observation = planner._llm_loop_step_observation({"tool": "runtime_completion_gate",
        "result": None, "outcome": {"status": "failed", "observed": value}})
    canonical, _ = json.JSONDecoder().raw_decode(observation.split("canonicalOutcome=", 1)[1])
    assert canonical["status"] == "failed"
    assert "nested-secret" not in observation and "object-secret" not in observation
    assert "private-tuple-value" not in observation
    assert "<redacted>" in observation
