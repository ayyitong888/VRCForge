from copy import deepcopy

import pytest

from internal_tool_selection_recovery import selection_correction_matches


def test_only_complete_matching_loader_success_can_supersede_selection_failure():
    failure = {
        "error": {"code": "internal_tool_selection_invalid"},
        "data": {
            "expectedBlock": "avatar_structure/hierarchy_components",
            "tool": "unity_scan_avatar_items",
            "requestedTools": ["unity_scan_avatar_items", "unity_get_hierarchy"],
        },
    }
    correction = {
        "block": "avatar_structure/hierarchy_components",
        "tools": ["unity_get_hierarchy", "unity_scan_avatar_items"],
    }
    assert selection_correction_matches(failure, "vrcforge_load_internal_tool_block", correction)
    assert not selection_correction_matches(failure, "vrcforge_scan_avatar_items", correction)
    partial = {**correction, "tools": ["unity_scan_avatar_items"]}
    assert not selection_correction_matches(failure, "vrcforge_load_internal_tool_block", partial)
    wrong_block = {**correction, "block": "behavior/parameters_menus_layers"}
    assert not selection_correction_matches(failure, "vrcforge_load_internal_tool_block", wrong_block)
    unrelated = deepcopy(failure)
    unrelated["error"]["code"] = "permission_denied"
    assert not selection_correction_matches(unrelated, "vrcforge_load_internal_tool_block", correction)


@pytest.mark.parametrize("tools", [None, [], [None], [{}], "unity_scan_avatar_items"])
def test_invalid_correction_identity_cannot_clear_failure(tools):
    failure = {"errorCode": "internal_tool_selection_invalid", "data": {
        "expectedBlock": "avatar_structure/hierarchy_components",
        "tool": "unity_scan_avatar_items", "requestedTools": ["unity_scan_avatar_items"],
    }}
    assert not selection_correction_matches(failure, "vrcforge_load_internal_tool_block", {
        "block": "avatar_structure/hierarchy_components", "tools": tools,
    })
