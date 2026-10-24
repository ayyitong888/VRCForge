"""The question contract reaches both aliases and both planner transports."""
import ast
from pathlib import Path
import pytest

import agent_question_service
from agent_gateway import AgentGateway
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
    assert "specific capability or permission gap" in prompt


def test_call_user_requires_a_verified_need_for_human_help():
    instruction = planner_tool_input_schema("vrcforge_call_user")["properties"]["question"]["description"]
    assert "exceptional human handoff" in instruction
    assert "specific capability or permission gap" in instruction
    assert "A failed tool call alone" in instruction
    assert "confirm the requested control and action exist" in instruction
    assert "routine technical checks" in instruction
    assert "same unresolved issue" in instruction
    assert "independent work" in instruction


@pytest.mark.parametrize("tool", ["vrcforge_ask_user", "vrcforge_call_user"])
def test_registered_tool_description_carries_guidance_through_real_projection(tmp_path, tool):
    source = ast.parse((Path(__file__).resolve().parents[1] / "dashboard_server.py").read_text(encoding="utf-8"))
    registration = next(node for node in ast.walk(source) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr == "register_tool"
                        and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == tool)
    description = eval(compile(ast.Expression(registration.args[1]), "registered-question-description", "eval"), vars(agent_question_service))
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.register_tool(tool, description, "plan/preview", lambda params: {})
    descriptor = gateway.shared_agent_tool_descriptor(tool, write=False, config=config)
    assert "everyday language" in descriptor["description"]
    assert descriptor["whenToUse"] and descriptor["whenNotToUse"]
    assert "already known decisions" in descriptor["whenNotToUse"] if tool == "vrcforge_ask_user" else "routine checks" in descriptor["whenNotToUse"]
    assert "without inspecting the current project" not in descriptor["description"]
    if tool == "vrcforge_call_user":
        assert "specific capability or permission gap" in descriptor["description"]
        assert "A failed tool call alone" in descriptor["description"]
        assert "confirm the requested control and action exist" in descriptor["description"]
        external = next(row for row in gateway.build_external_mcp_tools() if row["name"] == tool)
        assert "specific capability or permission gap" in external["description"]
