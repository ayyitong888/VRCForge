import json

import pytest

from test_runtime_planner_service import service


@pytest.mark.parametrize("operation,status,loaded", [
    ("load", "loaded", ["core", "behavior/animator_parameters", "avatar_structure/hierarchy_components"]),
    ("unload", "unloaded", ["core", "avatar_structure/hierarchy_components"]),
])
def test_lifecycle_receipt_preserves_exact_block_and_result_snapshot(operation, status, loaded):
    step = {"tool": f"vrcforge_{operation}_internal_tool_block", "status": "executed",
            "actionId": "action_load_fixture", "outcome": {"status": "ok", "summary": "generic description"},
            "result": {"ok": True, "status": status, "block": "behavior/animator_parameters",
                       "loadedBlocks": loaded, "toolCount": 12, "privateDump": "do-not-project"}}
    observation = service()._llm_loop_step_observation(step)
    receipt = json.loads(observation.split("toolBlockReceipt=", 1)[1])
    assert receipt["block"] == "behavior/animator_parameters"
    assert receipt["loadedBlocks"] == loaded
    assert receipt["status"] == status
    assert receipt["toolCount"] == 12
    assert receipt["snapshotScope"] == "after_this_action"
    assert "privateDump" not in observation
    assert "generic description" not in observation


def test_old_list_snapshot_is_labelled_historical_and_current_state_is_explicit():
    step = {"tool": "vrcforge_list_internal_tool_blocks", "result": {"loadedBlocks": ["core"]}}
    observation = service()._llm_loop_step_observation(step)
    assert "snapshot at this action" in observation
    prompt = service()._build_llm_plan_prompt("inspect active behavior", [], loop_state=[step],
        internal_tool_blocks=["core", "behavior/animator_parameters"])
    assert 'Current loaded tool blocks: ["behavior/animator_parameters","core"]' in prompt
    assert "historical snapshots" in prompt


def test_unity_selection_rule_uses_binding_inspection_without_hardcoded_tool_or_avatar():
    prompt = service()._build_llm_plan_prompt("inspect", [], project_context_active=True)
    assert "current scene, component bindings, or Avatar behavior" in prompt
    assert "filenames do not prove current bindings" in prompt
    assert "Explicit file-content or file-location tasks" in prompt
    general = service()._build_llm_plan_prompt("inspect", [], project_context_active=False)
    assert "current scene, component bindings, or Avatar behavior" not in general
