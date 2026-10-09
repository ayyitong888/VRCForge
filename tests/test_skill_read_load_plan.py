from copy import deepcopy

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService


class Catalog:
    def read(self, layer, *, project_context_active=True):
        assert layer == "planning"
        loader = PlannerTool("load_internal_tool_block", "Load", "read",
                             runtime_name="vrcforge_load_internal_tool_block")
        tools = [loader, PlannerTool("resident", "Read", "read")]
        if project_context_active:
            tools += [
                PlannerTool("descriptor", "Read", "read", runtime_name="vrcforge_descriptor", block="avatar/hierarchy"),
                PlannerTool("controls", "Read", "read", runtime_name="vrcforge_controls", block="avatar/hierarchy"),
                PlannerTool("fx", "Read", "read", runtime_name="vrcforge_fx", block="behavior/animation"),
                PlannerTool("write", "Write", "write", write=True, block="behavior/animation"),
                PlannerTool("advanced", "Read", "read", advanced=True, block="advanced"),
                PlannerTool("activated", "Read", "read", requires_user_activation=True, block="desktop"),
            ]
        return PlannerCatalogSnapshot(visible_tools=tuple(tools), routable_tools=(
            *tools, PlannerTool("hidden", "Hidden", "read", block="hidden")))


def skill_step():
    return {"tool": "vrcforge_read_installed_skill", "status": "done",
            "result": {"ok": True, "schema": "vrcforge.installed_skill.v1", "name": "fixture",
                       "allowedTools": ["vrcforge_descriptor", "controls", "vrcforge_fx", "controls",
                                        "resident", "write", "advanced", "activated", "hidden", "unknown"]}}


def test_skill_reads_offer_exact_read_only_leaf_loads_without_mutating_source():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    step = skill_step()
    before = deepcopy(step)
    result = planner.native_result_observation(step, project_context_active=True)
    assert result["skillReadLoads"] == [
        {"tool": "load_internal_tool_block", "arguments": {"block": "avatar/hierarchy", "tools": ["descriptor", "controls"]}},
        {"tool": "load_internal_tool_block", "arguments": {"block": "behavior/animation", "tools": ["fx"]}},
    ]
    assert "only task-relevant" in result["skillReadLoadPolicy"]
    assert step == before


@pytest.mark.parametrize("mode", ["general", "failed", "support", "other"])
def test_no_recipes_outside_successful_visible_skill_read(mode):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    step = skill_step()
    if mode == "failed":
        step["status"] = "failed"
    elif mode == "support":
        step["result"]["schema"] = "vrcforge.installed_skill_file.v1"
    elif mode == "other":
        step["tool"] = "vrcforge_read_text_file"
    result = planner.native_result_observation(step, project_context_active=mode != "general")
    assert "skillReadLoads" not in result


def test_ambiguous_alias_and_missing_loader_do_not_invent_recipes():
    from skill_tool_load_plan import skill_read_loads
    ambiguous = (
        PlannerTool("one", "Read", "read", runtime_name="shared", block="a"),
        PlannerTool("two", "Read", "read", runtime_name="shared", block="b"),
    )
    loader = Catalog().read("planning").visible_tools[0]
    assert skill_read_loads({"allowedTools": ["shared"]}, PlannerCatalogSnapshot(visible_tools=(loader, *ambiguous))) == []
    assert skill_read_loads({"allowedTools": ["one"]}, PlannerCatalogSnapshot(visible_tools=ambiguous)) == []


@pytest.mark.parametrize("active", [True, False])
def test_real_gateway_emits_advice_once_without_loading_or_rewriting_history(tmp_path, active):
    import json
    from tests.test_native_runtime_gateway import call, run, setup_gateway

    class RuntimeCatalog:
        def read(self, layer, *, project_context_active=True):
            snapshot = Catalog().read("planning", project_context_active=project_context_active)
            reader = PlannerTool("read_installed_skill", "Read Skill", "read",
                                 runtime_name="vrcforge_read_installed_skill")
            return PlannerCatalogSnapshot(visible_tools=(*snapshot.visible_tools, reader),
                                          routable_tools=(*snapshot.routable_tools, reader))

    gateway, model, _ = setup_gateway(tmp_path, [
        call("guide", "read_installed_skill", {"name": "fixture"}),
        {"role": "assistant", "content": "Guide read"},
        {"role": "assistant", "content": "Continued"},
    ])
    gateway.register_tool("vrcforge_read_installed_skill", "When to use: read Skill. When NOT to use: write.",
                          "read/debug", lambda args: skill_step()["result"])
    gateway.runtime_planner._catalog = RuntimeCatalog()
    run(gateway, tmp_path, _projectContextActive=active, maxAgenticTurns=2)
    assert len(model.requests) == 2
    receipt = next(m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "guide")
    observation = json.loads(receipt["content"])["observations"][0]
    assert ("skillReadLoads" in observation) == active
    assert gateway.runtime_sessions.internal_tool_blocks("native-session") == frozenset({"core"})
    assert model.requests[0]["tools"] == model.requests[1]["tools"]
    prefix = deepcopy(model.requests[1]["messages"][:-1])
    run(gateway, tmp_path, _projectContextActive=active, clientTurnId="later", message="Continue")
    assert model.requests[-1]["messages"][:len(prefix)] == prefix
