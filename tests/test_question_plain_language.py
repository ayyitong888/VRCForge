"""The question contract reaches both aliases and both planner transports."""
import pytest

from runtime_planner_service import planner_tool_input_schema
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema
from tests.native_planner_fixture import NativePlannerFixture
from tests.test_runtime_planner_service import NativeModel, service


@pytest.mark.parametrize("tool", ["vrcforge_ask_user", "vrcforge_call_user"])
def test_question_and_choices_explain_the_human_task(tool):
    fields = planner_tool_input_schema(tool)["properties"]
    assert canonical_unity_read_tool_input_schema(tool)["properties"] == fields
    instruction = fields["question"]["description"]
    assert "everyday language" in instruction
    assert "why the user's help is needed" in instruction
    assert "short numbered steps" in instruction
    assert "exact visible names" in instruction
    assert "diagnostic details" in instruction
    assert "user's language" in fields["header"]["description"]
    choice = fields["options"]["items"]["anyOf"][1]["properties"]
    assert "everyday language" in choice["label"]["description"]
    assert "consequence" in choice["description"]["description"]


@pytest.mark.parametrize("layer", ["planning", "execution"])
@pytest.mark.parametrize("native", [False, True])
def test_actual_planner_request_guides_all_user_question_text(layer, native):
    if native:
        model = NativeModel({"role": "assistant", "content": "No change made."})
        service(model=model).plan_agent_turn(
            "Inspect the outfit", {}, {}, exposure_layer=layer,
            native_turn=NativePlannerFixture([{"role": "user", "content": "Inspect the outfit"}]),
        )
        prompt = model.requests[0]["instructions"]
    else:
        prompt = service()._build_llm_plan_prompt("Inspect the outfit", [], exposure_layer=layer)
    assert "everyday language" in prompt
    assert "ask_user and call_user" in prompt
    assert "why the user's help is needed" in prompt
    assert "exact visible names" in prompt
    assert "diagnostic details" in prompt
