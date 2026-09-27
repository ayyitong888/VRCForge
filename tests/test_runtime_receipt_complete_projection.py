"""Complete owner projections precede the shared model-output page policy."""
import json
from types import SimpleNamespace

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService, planner_read_output_evidence, sanitize_planner_observation_text
from agent_tool_result_reader import bind_tool_result_context, read_tool_result, retain_model_information


TAIL = "OWNER_PROJECTED_INFORMATION_END_SENTINEL"
LONG = "ordinary inspected fact " * 1500 + TAIL


@pytest.mark.parametrize("tool,result,extra", [
    ("vrcforge_ask_user", {"questionId": "question", "answer": LONG}, {}),
    ("vrcforge_list_memory", {"ok": True, "memories": [
        {"memoryId": str(i), "scope": "user", "kind": "fact", "text": LONG} for i in range(15)]}, {}),
    ("vrcforge_read_installed_skill", {"ok": True, "name": "guide", "instructions": LONG}, {}),
    ("vrcforge_fixture_skill", {"ok": True}, {"skillContext": {"name": "guide", "instructions": LONG}}),
    ("vrcforge_read_recent_logs", {"ok": True, "source": "disk", "offset": 0,
        "file": "vrcforge_2026-09-27_01-00-00_1.log", "logs": [{"message": LONG}]}, {}),
    ("vrcforge_agent_desktop_action", {"ok": True}, {"desktopVision": {"status": "analyzed", "text": LONG}}),
    ("vrcforge_get_compile_errors", {"captureComplete": True, "errorCount": 1,
        "errors": [{"message": LONG, "file": "Assets/Example.cs", "line": 2}]}, {}),
])
def test_unbound_complete_projection_does_not_destroy_owner_payload(tool, result, extra):
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._desktop = type("Desktop", (), {"summarize_action_result": lambda self, value: "Desktop action read."})()
    observation = planner._llm_loop_step_observation({"tool": tool, "kind": "skill", "status": "executed",
        "result": result, "outcome": {"status": "ok"}, **extra})
    assert TAIL in observation
    if tool == "vrcforge_list_memory":
        assert '"memoryId":"14"' in observation


@pytest.mark.parametrize("tool,result,key", [
    ("vrcforge_read_text_file", {"path": "Assets/Note.txt", "text": LONG}, "text"),
    ("vrcforge_web_fetch", {"url": "https://example.org", "text": LONG}, "text"),
    ("shell", {"stdout": LONG, "stderr": ""}, "stdout"),
])
def test_typed_read_owner_projection_is_complete_before_paging(tool, result, key):
    evidence = planner_read_output_evidence(tool, result)
    assert evidence[key] == LONG
    assert evidence["sourceTruncated"] is False
    assert evidence["truncated"] is False
    assert evidence["omittedChars"] == 0


def test_complete_projection_keeps_private_memory_fields_excluded():
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    observation = planner._llm_loop_step_observation({"tool": "vrcforge_list_memory", "result": {
        "ok": True, "private": "DO_NOT_EXPOSE", "memories": [{"memoryId": "mem", "text": LONG,
            "embedding": "DO_NOT_EXPOSE", "scope": "user", "kind": "fact"}]}})
    assert "DO_NOT_EXPOSE" not in observation
    assert TAIL in observation


def test_shared_outlet_pages_complete_owner_text_for_native_and_legacy():
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._catalog = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot())
    step = {"index": 0, "actionId": "read-memory", "tool": "vrcforge_list_memory", "result": {
        "ok": True, "memories": [{"memoryId": "mem", "text": LONG, "scope": "user", "kind": "fact"}]},
        "outcome": {"status": "failed", "nextAction": "Do not retry the write."}}
    complete = planner.complete_model_information(step)
    step.update(retain_model_information("session", "turn", "", step, {"text": complete}, sanitize_planner_observation_text))
    observation = planner._llm_loop_step_observation(step)
    assert "Do not retry the write." in observation
    assert TAIL not in observation
    assert observation.count("modelInformationPage=") == 1
    assert '"page":' not in observation
    assert observation == planner.native_result_observation(step)["observation"]
    prompt = planner._build_llm_plan_prompt("Inspect memory", [], loop_state=[step])
    assert observation in prompt
    page = step["modelInformationRead"]["page"]
    pieces = [page["items"][0]["value"]]
    with bind_tool_result_context("session", "turn", "", [step]):
        while page["hasMore"]:
            page = read_tool_result(page["nextRequest"]["arguments"], sanitize=sanitize_planner_observation_text)
            pieces.append(page["items"][0]["value"])
    assert "".join(pieces) == complete
    assert TAIL in pieces[-1]


def test_shared_outlet_small_payload_is_not_duplicated():
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._catalog = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot())
    step = {"index": 0, "tool": "vrcforge_ask_user", "result": {"questionId": "q", "answer": "Unique answer."}}
    complete = planner.complete_model_information(step)
    step.update(retain_model_information("session", "turn", "", step, {"text": complete}, sanitize_planner_observation_text))
    assert planner._llm_loop_step_observation(step) == complete
    assert complete.count("Unique answer.") == 1
