from copy import deepcopy
import json
import pytest

from agent_tool_result_reader import PAGE_SCHEMA, TOOL_NAME, result_continuation
from runtime_planner_service import RuntimePlannerService


def test_result_read_continuation_uses_catalog_planner_name_without_changing_runtime_name():
    step = {
        "index": 0,
        "actionId": "action-read",
        "tool": "fixture_read_tool",
        "result": {"items": [{"name": "fixture"}]},
    }

    continuation = result_continuation(
        "session",
        "turn",
        "project",
        step,
        lambda value, *_args: str(value),
    )

    assert continuation["nextRequest"]["tool"] == TOOL_NAME
    assert continuation["nextRequest"]["arguments"]["resultRef"].startswith("result_")
    assert TOOL_NAME == "vrcforge_read_tool_result"


@pytest.mark.parametrize("page", [False, True])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("project_active", [False, True])
def test_observation_projects_catalog_name_without_mutating_retained_result(page, native, project_active):
    from dashboard_server import _RuntimePlannerCatalog

    service = RuntimePlannerService(catalog=_RuntimePlannerCatalog(), desktop=None)
    step = {
        "tool": "fixture_read_tool",
        "result": {"items": [{"name": "fixture"}]},
        "resultRead": {
            "resultRef": "result_" + "a" * 32,
            "nextRequest": {"tool": TOOL_NAME, "arguments": {"resultRef": "result_" + "a" * 32}},
        },
    }
    if page:
        step["tool"] = TOOL_NAME
        step["result"] = {"schema": PAGE_SCHEMA, "items": [{"name": "fixture"}],
                          "hasMore": True, **step.pop("resultRead")}
    original = deepcopy(step)
    observation = (service.native_result_observation(step)["observation"] if native
                   else service._llm_loop_step_observation(step))
    assert '"tool":"read_tool_result"' in observation
    request, _ = service._build_native_plan_request(
        [{"role": "tool", "tool_call_id": "read", "content": observation}],
        observe={}, exposure_layer="planning", project_context_active=project_active,
        project_path="", internal_tool_blocks=["core"], global_instructions="", project_instructions="",
    )
    names = {item["function"]["name"] for item in request["tools"]}
    marker = "; retainedResultPage=" if page else "; resultContinuation="
    next_call = json.loads(request["messages"][0]["content"].split(marker)[1])["nextRequest"]
    assert next_call["tool"] in names
    assert next_call["arguments"] == original["result" if page else "resultRead"]["nextRequest"]["arguments"]
    assert step == original
