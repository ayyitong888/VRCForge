import copy
import json

from agent_tool_result_reader import bind_tool_result_context, read_tool_result, result_continuation
from runtime_planner_service import RuntimePlannerService, sanitize_planner_observation_text as sanitize


def scan_step():
    step = {"index": 0, "actionId": "scan-action", "tool": "vrcforge_scan_fx_animator",
            "kind": "skill", "status": "executed",
            "result": {"parameters": [{"name": f"Parameter{i}", "type": "Int"} for i in range(600)],
                       "layers": [{"name": "Inventory", "states": ["Rest"]}],
                       "captureComplete": False,
                       "recognitionCoverage": {"candidateEnumerationComplete": False}}}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize)
    step["scanSnapshot"] = {"capturedAt": "2026-10-05T00:00:00Z", "persisted": True,
                            "scope": "current_turn", "freshness": "unverified",
                            "scanScope": {"tool": step["tool"], "dependencyCoverage": "unverified"}}
    return step


def test_scan_observation_is_summary_and_ref_not_inline_detail():
    step = scan_step()
    before = copy.deepcopy(step)
    text = RuntimePlannerService._llm_loop_step_observation(None, step, native_contract=True)
    assert len(text) < 6000
    assert "Parameter599" not in text
    assert '"captureComplete":false' in text
    assert '"candidateEnumerationComplete":false' in text
    assert "resultContinuation=" in text
    assert '"count":600' in text
    assert step == before
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = read_tool_result({"resultRef": step["resultRead"]["resultRef"],
                                "jsonPointer": "/parameters", "offset": 599, "limit": 1}, sanitize=sanitize)
    assert page["items"][0]["value"] == {"name": "Parameter599", "type": "Int"}
    assert page["hasMore"] is False


def test_existing_history_and_non_scan_results_are_not_reprojected():
    step = scan_step()
    step.pop("scanSnapshot")
    text = RuntimePlannerService._llm_loop_step_observation(None, step, native_contract=True)
    assert "Parameter599" in text
    step["scanSnapshot"] = {"persisted": True}
    step["tool"] = "vrcforge_load_internal_tool_block"
    assert "scanSummary" not in RuntimePlannerService._llm_loop_step_observation(None, step)


def test_summary_keeps_owner_completeness_without_full_owner_text():
    step = scan_step()
    step["modelInformation"] = {"retainedBy": "agent_tool_result_reader", "payload": {
        "text": "full evidence " * 3000,
        "sourceCompleteness": [{"jsonPointer": "", "facts": {"truncated": True}}]}}
    text = RuntimePlannerService._llm_loop_step_observation(None, step)
    assert len(text) < 6000 and "full evidence" not in text
    assert '"truncated":true' in text
    json.JSONDecoder().raw_decode(text.split("scanSummary=", 1)[1])


def test_owned_expired_reference_is_business_result_and_leaves_history_untouched():
    step = scan_step()
    old = copy.deepcopy(step)
    lookup = lambda *args: {"expired": True, "reason": "core_scope_unavailable",
                           "rescanScope": {"tool": step["tool"], "assetPaths": ["Assets/Example.controller"]}}
    with bind_tool_result_context("session", "later", "project", [], snapshot_lookup=lookup):
        page = read_tool_result({"resultRef": step["resultRead"]["resultRef"]}, sanitize=sanitize)
    assert page["ok"] is True and page["status"] == "needs_rescan"
    assert page["expired"] is True and page["items"] == []
    assert page["reason"] == "core_scope_unavailable"
    assert step == old


def test_scan_scope_never_promotes_execution_credentials_into_summary():
    step = scan_step()
    step["scanSnapshot"]["scanScope"]["arguments"] = {"executionTargetHandle": "owned-private-handle"}
    assert "owned-private-handle" not in RuntimePlannerService._llm_loop_step_observation(None, step)


def test_scan_that_raced_a_write_is_not_presented_as_usable_summary():
    step = scan_step()
    step["scanSnapshot"].update(expired=True, reason="mutation_during_scan")
    text = RuntimePlannerService._llm_loop_step_observation(None, step)
    assert '"expired":true' in text and '"status":"needs_rescan"' in text
    assert "mutation_during_scan" in text
