from copy import deepcopy
import json

import pytest

from agent_tool_result_reader import TOOL_NAME, bind_tool_result_context, read_tool_result, result_continuation
from runtime_planner_service import RuntimePlannerService, sanitize_planner_observation_text as sanitize


def material_step(count=36, offset=0):
    rows = [{"material_id": f"mat_{i:03d}", "material_name": f"Material {i:03d}",
             "renderer_path": f"Avatar/Renderer{i:03d}", "shader_name": "Standard"}
            for i in range(76)]
    step = {"index": 0, "actionId": "material-scan", "tool": "vrcforge_scan_materials",
            "kind": "skill", "status": "executed", "result": {"ok": True, "index": rows}}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize)
    request = {"tool": TOOL_NAME, "arguments": {"resultRef": step["resultRead"]["resultRef"],
               "jsonPointer": "/index", "offset": offset + count, "limit": 20}}
    step["resultRead"]["nextRequest"] = request
    step["result"] = {"ok": True, "index": rows[offset:offset+count],
                      "paging": {"offset": offset, "returned": count, "total": 76,
                                 "hasMore": True, "nextRequest": deepcopy(request)},
                      "nextRequest": deepcopy(request)}
    step["scanSnapshot"] = {"persisted": True, "scope": "validated_scope", "freshness": "validated",
                            "scanScope": {"schema": "vrcforge.scan_scope.v1", "dependencyCoverage": "complete"}}
    return step, rows


@pytest.mark.parametrize("native", [False, True])
def test_hidden_first_36_rows_start_at_zero_and_exact_hint_covers_entire_index(native):
    step, rows = material_step()
    original = deepcopy(step)
    observation = RuntimePlannerService._llm_loop_step_observation(None, step, native_contract=native)
    summary, _ = json.JSONDecoder().raw_decode(observation.split("scanSummary=", 1)[1])
    continuation = json.loads(observation.split("resultContinuation=", 1)[1])
    assert "index" not in summary["fields"]
    request = continuation["nextRequest"]
    assert request["arguments"]["offset"] == 0
    # Follow the actual hint, rather than manually reconstructing the missing prefix.
    retained = {**step, "result": {"ok": True, "index": rows}}
    received = []
    with bind_tool_result_context("session", "turn", "project", [retained]):
        while request:
            page = read_tool_result(request["arguments"], sanitize=sanitize)
            received.extend(item["value"] for item in page["items"])
            request = page.get("nextRequest") if page["hasMore"] else None
    assert received == rows
    assert len({row["material_id"] for row in received}) == 76
    assert step == original


@pytest.mark.parametrize("count,offset", [(2, 0), (2, 20)])
def test_inline_page_continues_after_actual_visible_rows(count, offset):
    step, _ = material_step(count, offset)
    step["result"].pop("nextRequest")
    step["result"]["paging"].pop("nextRequest")
    observation = RuntimePlannerService._llm_loop_step_observation(None, step)
    summary, _ = json.JSONDecoder().raw_decode(observation.split("scanSummary=", 1)[1])
    continuation = json.loads(observation.split("resultContinuation=", 1)[1])
    assert summary["inlineComplete"] is True
    assert len(summary["fields"]["index"]) == count
    assert continuation["nextRequest"]["arguments"]["offset"] == offset + count


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("alias", [TOOL_NAME, "read_tool_result"])
def test_projection_aligns_all_hint_copies_with_visible_prefix(nested, alias):
    from agent_tool_result_reader import observation_result_continuation

    step, _ = material_step()
    step["resultRead"]["nextRequest"]["tool"] = alias
    pointer = "/data/index" if nested else "/index"
    step["resultRead"]["nextRequest"]["arguments"]["jsonPointer"] = pointer
    request = deepcopy(step["resultRead"]["nextRequest"])
    # Actual planner projection aliases the descriptor, not the source result.
    request["tool"] = "read_tool_result" if alias == TOOL_NAME else TOOL_NAME
    step["result"]["nextRequest"] = deepcopy(request)
    step["result"]["paging"]["nextRequest"] = deepcopy(request)
    if nested:
        step["result"] = {"data": step["result"]}
    public = deepcopy(step["result"])
    node = public["data"] if nested else public
    node["index"] = node["index"][:3]
    before = deepcopy((step, public))
    fields, continuation = observation_result_continuation(step, public, sanitize)
    visible = fields["data"] if nested else fields
    assert continuation["nextRequest"]["arguments"]["offset"] == 3
    assert visible["nextRequest"] == visible["paging"]["nextRequest"] == continuation["nextRequest"]
    assert visible["paging"]["returned"] == 36  # Original source-page facts stay source facts.
    assert (step, public) == before


def test_redacted_middle_member_cannot_advance_past_unseen_row():
    from agent_tool_result_reader import observation_result_continuation
    step, _ = material_step()
    public = deepcopy(step["result"])
    del public["index"][1]
    _, continuation = observation_result_continuation(step, public, sanitize)
    assert continuation["nextRequest"]["arguments"]["offset"] == 1


def test_hidden_nonzero_source_page_also_starts_at_zero():
    from agent_tool_result_reader import observation_result_continuation
    step, _ = material_step(36, 20)
    _, continuation = observation_result_continuation(step, {"ok": True}, sanitize)
    assert continuation["nextRequest"]["arguments"]["offset"] == 0


def test_complete_evidence_projection_keeps_cursor_after_delivered_rows():
    step, _ = material_step()
    before = deepcopy(step)
    observation = RuntimePlannerService.complete_model_information(None, step, native_contract=True)
    evidence, _ = json.JSONDecoder().raw_decode(observation.split("structuredEvidence=", 1)[1])
    continuation = json.loads(observation.split("resultContinuation=", 1)[1])
    assert len(evidence["data"]["index"]) == 36
    assert continuation["nextRequest"]["arguments"]["offset"] == 36
    assert step == before
