"""Large support evidence is recoverable without duplicating it as control text."""
import json
from types import SimpleNamespace

import pytest

from agent_tool_result_reader import bind_tool_result_context, read_tool_result, retain_model_information
from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService, sanitize_planner_observation_text


@pytest.mark.parametrize("support_field", ["observed", "expected", "delta", "evidence", "causeChain"])
def test_support_fields_are_paged_while_recovery_controls_remain_direct(support_field):
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._catalog = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot())
    support = "inspected supporting detail " * 1300 + "SUPPORTING_TAIL_SENTINEL"
    outcome = {"status": "failed", "summary": "Required context " * 20 + "SUMMARY_END",
        support_field: {"detail": support},
        "error": {"likelyCauses": ["Exact cause " * 80 + "CAUSE_END"],
                  "nextActions": ["Required recovery " * 80 + "ACTION_END"]},
        "nextAction": "Recover exact target; do not retry the write.",
        "recovery": {"required": True, "method": "explicit_readback"},
        "mutationStarted": False, "committed": False, "commitState": "unknown",
        "safeToRetry": False, "checkpointRecoveryRequired": True}
    step = {"index": 0, "actionId": "failed-action", "tool": "runtime_completion_gate",
            "status": "failed", "outcome": outcome, "result": None}
    complete = planner.complete_model_information(step)
    step.update(retain_model_information("control", "turn", "", step, {"text": complete}, sanitize_planner_observation_text))
    observation = planner._llm_loop_step_observation(step)
    direct = observation.split("; modelInformationPage=", 1)[0]
    canonical, _ = json.JSONDecoder().raw_decode(direct.split("canonicalOutcome=", 1)[1])
    assert support_field not in canonical
    assert "SUPPORTING_TAIL_SENTINEL" not in direct
    for marker in ("SUMMARY_END", "CAUSE_END", "ACTION_END", outcome["nextAction"]):
        assert marker in direct
    for field in ("status", "nextAction", "recovery", "mutationStarted", "committed", "commitState", "safeToRetry", "checkpointRecoveryRequired"):
        assert canonical[field] == outcome[field]
    page = step["modelInformationRead"]["page"]
    chunks = [page["items"][0]["value"]]
    with bind_tool_result_context("control", "turn", "", [step]):
        while page["hasMore"]:
            page = read_tool_result(page["nextRequest"]["arguments"], sanitize=sanitize_planner_observation_text)
            chunks.append(page["items"][0]["value"])
    assert "".join(chunks) == complete
    retained, _ = json.JSONDecoder().raw_decode(complete.split("canonicalOutcome=", 1)[1])
    assert retained[support_field]["detail"] == support
