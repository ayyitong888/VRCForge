import json

from runtime_planner_service import RuntimePlannerService
from runtime_planner_service import sanitize_planner_observation_text
from agent_tool_result_reader import retain_model_information, bind_tool_result_context, read_tool_result


def test_source_limit_is_visible_before_large_tool_body():
    step = {"tool": "fixture_scan", "result": {
        "items": [{"name": "object" * 5000}],
        "summary": {"truncated": True, "itemCount": 2000},
    }}
    text = RuntimePlannerService.complete_model_information(None, step)
    assert text.startswith('sourceCompleteness=')
    receipt = json.loads(text.split('; ', 1)[0].split('=', 1)[1])
    assert receipt == [{"jsonPointer": "/summary", "facts": {"truncated": True, "itemCount": 2000}}]


def test_source_metadata_does_not_infer_completion_or_expose_arbitrary_fields():
    step = {"result": {"summary": {"truncated": False, "token": "private", "itemCount": "not-a-count"}}}
    receipt = RuntimePlannerService.model_source_completeness(step)
    assert receipt == [{"jsonPointer": "/summary", "facts": {"truncated": False}}]
    assert RuntimePlannerService.model_source_completeness({"result": {"items": []}}) == []


def test_every_page_carries_producer_limits_independently_of_page_completion():
    step = {"index": 0, "tool": "fixture_scan", "result": {
        "summary": {"truncated": True, "itemCount": 2000}}}
    facts = RuntimePlannerService.model_source_completeness(step)
    text = "safe source data " * 2400
    step.update(retain_model_information("owner", "turn", "project", step,
        {"text": text, "sourceCompleteness": facts}, sanitize_planner_observation_text))
    page = step["modelInformationRead"]["page"]
    pieces = []
    with bind_tool_result_context("owner", "turn", "project", [step]):
        while True:
            assert page["sourceCompleteness"] == facts
            pieces.append(page["items"][0]["value"])
            if not page["hasMore"]:
                break
            page = read_tool_result(page["nextRequest"]["arguments"], sanitize=sanitize_planner_observation_text)
    assert "".join(pieces) == text
    assert page["sourceCompleteness"][0]["facts"]["truncated"] is True
