import json

from runtime_planner_service import PlannerCatalogSnapshot, PlannerModelResult, PlannerTool, planner_tool_input_schema
from test_runtime_planner_service import FakeCatalog, FakeModel, service


def test_load_schema_exposes_optional_unique_exact_tool_names():
    schema = planner_tool_input_schema("vrcforge_load_internal_tool_block")
    assert schema["required"] == ["block"]
    assert schema["properties"]["tools"] == {
        "type": "array", "items": {"type": "string", "minLength": 1},
        "minItems": 1, "uniqueItems": True,
        "description": "Optional exact tool names from this block's directory. Load only tools needed next; omit to load the whole block.",
    }


def fixture():
    schema = {"type": "object", "properties": {"avatarPath": {"type": "string"}}, "required": ["avatarPath"]}
    tools = tuple(PlannerTool(name=name, runtime_name="vrcforge_" + name, description=name + " description", category="read", block=block, input_schema=schema)
                  for name, block in [("core_probe", "core"), ("wanted_inspect", "domain/read"), ("other_inspect", "domain/read"), ("unloaded_inspect", "domain/other")])
    return service(catalog=FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools)))


def test_subset_projects_only_selected_alias_and_keeps_core_complete_schema():
    prompt = fixture()._build_llm_plan_prompt("inspect", [], internal_tool_blocks=["domain/read"],
        observe={"internalToolSelections": {"core": [], "domain/read": ["wanted_inspect"]}})
    assert "wanted_inspect description" in prompt
    assert "other_inspect description" not in prompt
    assert "unloaded_inspect description" not in prompt
    assert "core_probe description" in prompt
    assert '"required":["avatarPath"]' in prompt
    assert "Load only the exact tools needed next" in prompt
    assert "tools" in prompt


def test_whole_or_absent_selection_preserves_full_loaded_block():
    for selections in ({}, {"domain/read": None}):
        prompt = fixture()._build_llm_plan_prompt("inspect", [], internal_tool_blocks=["domain/read"],
            observe={"internalToolSelections": selections})
        assert "wanted_inspect description" in prompt
        assert "other_inspect description" in prompt


def test_load_receipt_retains_selected_names_and_whole_mode():
    for selected in (["wanted_inspect"], None):
        observation = fixture()._llm_loop_step_observation({"tool": "vrcforge_load_internal_tool_block", "status": "executed",
            "result": {"ok": True, "status": "loaded", "block": "domain/read", "loadedBlocks": ["core", "domain/read"],
                       "selectionMode": "whole" if selected is None else "subset", "selectedTools": selected}})
        receipt = json.loads(observation.split("toolBlockReceipt=", 1)[1])
        assert receipt["selectedTools"] == selected
        assert receipt["selectionMode"] == ("whole" if selected is None else "subset")


def test_runtime_owned_parameter_selection_reaches_model_without_mutating_observe():
    planner = fixture()
    model = FakeModel(PlannerModelResult('{"action":"reply","reply":"ready"}'))
    planner._model = model
    observe = {"internalToolSelections": {"domain/read": None}}
    planner.plan_agent_turn("inspect", {"_internalToolBlocks": ["domain/read"],
        "_internalToolSelections": {"domain/read": ["wanted_inspect"]}}, observe)
    assert "wanted_inspect description" in model.prompts[0]
    assert "other_inspect description" not in model.prompts[0]
    assert observe == {"internalToolSelections": {"domain/read": None}}


def test_selection_cannot_expose_tools_outside_visible_catalog_or_activation_gate():
    hidden = PlannerTool(name="hidden_probe", runtime_name="vrcforge_hidden_probe", description="hidden probe description",
        category="read", block="domain/read", requires_user_activation=True)
    planner = service(catalog=FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=(hidden,), computer_use_model_invocable=False)))
    prompt = planner._build_llm_plan_prompt("inspect", [], internal_tool_blocks=["domain/read"],
        observe={"internalToolSelections": {"domain/read": ["hidden_probe", "unregistered_write"]}})
    assert "hidden probe description" not in prompt
    assert "- unregistered_write" not in prompt
