from types import SimpleNamespace
from runtime_planner_service import RuntimePlannerService, PlannerCatalogSnapshot, sanitize_planner_observation_text
from agent_tool_result_reader import result_continuation, retain_model_information
import pytest


def test_large_owner_page_exposes_existing_structured_reader_without_reading_text_tail():
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._catalog = SimpleNamespace(read=lambda *a, **k: PlannerCatalogSnapshot())
    step = {"index": 0, "actionId": "scan", "tool": "fixture_scan", "result": {
        "items": [{"name": "nested-value", "description": "retained evidence " * 5000}],
        "summary": {"truncated": True}}}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize_planner_observation_text)
    text = planner.complete_model_information(step)
    step.update(retain_model_information("session", "turn", "project", step,
        {"text": text}, sanitize_planner_observation_text))
    observation = planner._llm_loop_step_observation(step)
    assert 'resultContinuation=' in observation.split('; modelInformationPage=')[0]
    assert '"jsonPointer":"/items"' in observation.split('; modelInformationPage=')[0]
    assert observation == planner.native_result_observation(step)["observation"]


@pytest.mark.parametrize("tool", ["shell", "vrcforge_read_text_file", "vrcforge_capture_screenshot"])
def test_owner_only_tools_do_not_gain_structured_raw_navigation(tool):
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._catalog = SimpleNamespace(read=lambda *a, **k: PlannerCatalogSnapshot())
    step = {"index": 0, "tool": tool, "result": {"text": "private raw"},
        "resultRead": {"resultRef": "result_" + "a" * 32}}
    step.update(retain_model_information("session", "turn", "project", step,
        {"text": "safe retained text " * 2000}, sanitize_planner_observation_text))
    observation = planner._llm_loop_step_observation(step)
    assert 'resultContinuation=' not in observation.split('; modelInformationPage=')[0]
