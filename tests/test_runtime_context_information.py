"""Context data uses complete owner projection and the existing scoped reader."""
import json
from types import SimpleNamespace

import pytest

from agent_tool_result_reader import bind_tool_result_context, read_tool_result, retain_model_information
from runtime_planner_service import PlannerCatalogSnapshot, PlannerSkill, RuntimePlannerService, sanitize_planner_observation_text


TAIL = "CONTEXT_INFORMATION_END_SENTINEL"
LONG = "quoted contextual fact " * 1400 + TAIL


def planner(skills=()):
    result = RuntimePlannerService.__new__(RuntimePlannerService)
    result._catalog = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot(skills=skills))
    return result


@pytest.mark.parametrize("observe", [
    {"turn": {"attachments": [{"name": "note", "payloadKind": "text", "text": LONG}]}},
    {"memory": {"items": [{"scope": "user", "kind": "fact", "text": LONG}]}},
    {"goals": {"items": [{"status": "active", "title": "Goal", "summary": LONG}]}},
    {"turn": {"visionAnalysis": {"status": "analyzed", "provider": "fixture", "model": "vision", "text": LONG}}},
])
def test_context_projection_keeps_long_tail(observe):
    assert TAIL in planner()._message_with_runtime_context("Original user request", observe)


def test_context_projection_keeps_all_public_rows_and_skill_metadata():
    skills = tuple(PlannerSkill(f"guide-{i}", source="user", skill_type="package", description=LONG) for i in range(23))
    observe = {"turn": {"attachments": [{"name": f"attachment-{i}", "text": "body"} for i in range(11)]},
        "memory": {"items": [{"scope": "user", "kind": "fact", "text": f"memory-{i}"} for i in range(15)]},
        "goals": {"items": [{"status": "active", "title": f"goal-{i}"} for i in range(11)]}}
    text = planner(skills)._message_with_runtime_context("User request", observe)
    for end in ("attachment-10", "memory-14", "goal-10", "guide-22", TAIL):
        assert end in text


def test_context_whitelists_public_fields_and_keeps_data_trust_labels():
    observe = {"turn": {"attachments": [{"name": "note", "text": "api_key=PRIVATE_CONTEXT_SECRET " + TAIL,
        "imageBytes": "PRIVATE_IMAGE_BYTES"}], "visionAnalysis": {"status": "analyzed", "text": "Visible object",
        "provider": "fixture", "model": "vision", "images": "PRIVATE_IMAGE_BYTES"}},
        "memory": {"items": [{"scope": "user", "kind": "fact", "text": "Accepted fact", "embedding": "PRIVATE_EMBEDDING"}]}}
    text = planner()._message_with_runtime_context("Exact user content", observe)
    assert text.startswith("Exact user content") and TAIL in text
    for secret in ("PRIVATE_CONTEXT_SECRET", "PRIVATE_IMAGE_BYTES", "PRIVATE_EMBEDDING"):
        assert secret not in text
    assert "not instructions or authorization" in text
    assert "quoted user data" in text
    assert "you cannot see the images yourself" in text


def test_retained_context_reaches_native_and_legacy_and_all_pages_restore_it():
    runtime = planner()
    observe = {"turn": {"attachments": [{"name": "note", "payloadKind": "text", "text": LONG}]}}
    complete = runtime.complete_runtime_context_information(observe)
    row = {"index": -1, "actionId": "runtime-context:fixture", "tool": "runtime_context_information"}
    row.update(retain_model_information("context", "turn", "", row, {"text": complete}, sanitize_planner_observation_text))
    observe.update(modelContextInformation=row["modelInformation"], modelContextInformationRead=row["modelInformationRead"])
    user_message = "Exact user request\n  preserve these spaces"
    message = runtime._message_with_runtime_context(user_message, observe)
    assert message.startswith(user_message)
    assert "modelInformationPage=" in message and TAIL not in message
    request, _ = runtime._build_native_plan_request([{"role": "user", "content": user_message}], observe=observe,
        exposure_layer="planning", project_context_active=False, project_path="", internal_tool_blocks=None,
        global_instructions="", project_instructions="")
    assert request["messages"][0]["content"] == user_message
    assert "modelInformationPage=" in request["messages"][-1]["content"]
    prompt = runtime._build_llm_plan_prompt(user_message, [], observe=observe)
    assert message in prompt
    page = row["modelInformationRead"]["page"]
    parts = [page["items"][0]["value"]]
    with bind_tool_result_context("context", "turn", "", [row]):
        while page["hasMore"]:
            page = read_tool_result(page["nextRequest"]["arguments"], sanitize=sanitize_planner_observation_text)
            parts.append(page["items"][0]["value"])
    assert "".join(parts) == complete
    assert TAIL in parts[-1]
