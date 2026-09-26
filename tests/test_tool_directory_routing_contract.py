from __future__ import annotations

import dashboard_server
import pytest

from internal_tool_blocks import (
    build_internal_tool_block_tree,
    canonical_tool_block_description,
    canonical_tool_owner,
    internal_tool_block_for_name,
    _CORE_TOOLS,
)


_OWNER_CONTRACT = {
    "vrcforge_avatar_upload_readiness": "diagnostics_build/build_runtime",
    "vrcforge_get_avatar_upload_status": "diagnostics_build/build_runtime",
    "vrcforge_build_and_upload_avatar": "diagnostics_build/build_runtime",
    "vrcforge_build_test_avatar": "diagnostics_build/build_runtime",
    "vrcforge_scene_transition": "avatar_structure/hierarchy_components",
    "vrcforge_set_play_mode": "diagnostics_build/build_runtime",
    "vrcforge_avatar_encryption_liltoon_apply_request": "behavior/interaction_generated_systems",
    "vrcforge_avatar_encryption_poiyomi_apply_request": "behavior/interaction_generated_systems",
    "vrcforge_avatar_encryption_remove_request": "behavior/interaction_generated_systems",
    "vrcforge_scan_avatar_performance": "diagnostics_build/validation_performance",
    "vrcforge_scan_thry_avatar_performance": "diagnostics_build/validation_performance",
    "vrcforge_apply_clothing_fx": "behavior/parameters_menus_layers",
    "vrcforge_apply_tuning_preset": "behavior/face_eye_lipsync",
}


def _planner_alias(name: str) -> str:
    return name.removeprefix("vrcforge_").join(("unity_", ""))


def test_live_confirmed_names_have_explicit_stable_owners() -> None:
    for runtime_name, expected_owner in _OWNER_CONTRACT.items():
        assert canonical_tool_owner("avatar", runtime_name) == expected_owner
        assert canonical_tool_owner("avatar", _planner_alias(runtime_name)) == expected_owner


def test_directory_projection_and_runtime_catalog_agree() -> None:
    leaves = [
        {"name": name, "block": "unity/avatar", "mode": "write"}
        for name in _OWNER_CONTRACT
    ]
    directory = build_internal_tool_block_tree(loaded_blocks={"core"}, leaves=leaves)
    by_leaf = {
        child["name"]: set(child["toolNames"])
        for root in directory["blocks"]
        for child in root["children"]
    }
    for runtime_name, expected_owner in _OWNER_CONTRACT.items():
        assert runtime_name in by_leaf[expected_owner]

    catalog = dashboard_server._RuntimePlannerCatalog().read(
        "execution", project_context_active=True
    )
    actual = {tool.runtime_name: tool.block for tool in catalog.routable_tools}
    for runtime_name, expected_owner in _OWNER_CONTRACT.items():
        assert actual[runtime_name] == expected_owner


def test_directory_descriptions_cover_confirmed_capabilities() -> None:
    descriptions = {
        block: canonical_tool_block_description(block)
        for block in (
            "project_environment",
            "project_environment/files",
            "project_environment/shell",
            "behavior",
            "behavior/interaction_generated_systems",
            "avatar_structure",
            "avatar_structure/hierarchy_components",
            "diagnostics_build",
            "diagnostics_build/compile_logs",
            "diagnostics_build/validation_performance",
            "diagnostics_build/build_runtime",
            "behavior/face_eye_lipsync",
            "behavior/parameters_menus_layers",
        )
    }
    assert "installed Skill" in descriptions["project_environment/files"]
    assert "Computer Use" in descriptions["project_environment/shell"]
    assert "avatar protection" in descriptions["behavior/interaction_generated_systems"]
    assert "attachment" in descriptions["diagnostics_build/validation_performance"]
    assert "property" in descriptions["diagnostics_build/compile_logs"]
    assert "play mode" in descriptions["diagnostics_build/build_runtime"]
    assert "face-tuning" in descriptions["behavior/face_eye_lipsync"]
    assert "clothing FX" in descriptions["behavior/parameters_menus_layers"]


def test_core_tools_remain_resident_but_are_not_claimed_by_lazy_leaves() -> None:
    core_names = [
        "web_search",
        "read_text_file",
        "read_tool_result",
    ]
    directory = build_internal_tool_block_tree(
        loaded_blocks={"core"},
        leaves=[
            {"name": name, "block": "core", "mode": "read"}
            for name in core_names
        ]
        + [{"name": "vrcforge_build_test_avatar", "block": "unity/avatar", "mode": "write"}],
    )
    lazy_names = {
        name
        for root in directory["blocks"]
        for child in root["children"]
        for name in child["toolNames"]
    }
    assert not lazy_names.intersection(core_names)
    assert "vrcforge_build_test_avatar" in lazy_names
    assert all(
        internal_tool_block_for_name("vrcforge_" + name, "unity") == "core"
        for name in core_names
    )
    catalog = dashboard_server._RuntimePlannerCatalog().read(
        "planning", project_context_active=True
    )
    actual = {tool.runtime_name: tool.block for tool in catalog.visible_tools}
    assert actual["vrcforge_web_search"] == "core"
    assert actual["vrcforge_read_text_file"] == "core"
    assert "core reads remain resident" in canonical_tool_block_description("diagnostics_build")
    assert "Computer Use" in canonical_tool_block_description("project_environment")
    assert "attachment inspection" in canonical_tool_block_description("diagnostics_build")


@pytest.mark.parametrize(
    ("exposure_layer", "project_context_active"),
    [
        ("planning", False),
        ("planning", True),
        ("execution", False),
        ("execution", True),
    ],
)
def test_full_catalog_reaches_the_same_non_core_leaf(
    exposure_layer: str, project_context_active: bool
) -> None:
    catalog = dashboard_server._RuntimePlannerCatalog().read(
        exposure_layer, project_context_active=project_context_active
    )
    expected = {
        tool.name: tool.block
        for tool in catalog.visible_tools
        if tool.runtime_name != "vrcforge_list_internal_tool_blocks"
        and tool.block != "core"
    }
    core_runtime_names = {
        tool.runtime_name
        for tool in catalog.visible_tools
        if tool.block == "core"
    }
    leaves = dashboard_server._internal_tool_block_leaves(
        exposure_layer, project_context_active=project_context_active
    )
    directory = build_internal_tool_block_tree(
        loaded_blocks={"core"}, leaves=leaves
    )
    actual = {
        tool_name: leaf["name"]
        for root in directory["blocks"]
        for leaf in root["children"]
        for tool_name in leaf["toolNames"]
    }

    assert set(actual) == set(expected)
    assert all(actual[name] == owner for name, owner in expected.items())
    assert not set(actual).intersection(core_runtime_names)
    assert core_runtime_names
    native_core = (
        set(_CORE_TOOLS)
        | set(dashboard_server.RUNTIME_PLANNER_CORE_AGENT_TOOLS)
    )
    assert core_runtime_names <= native_core | {"vrcforge_list_internal_tool_blocks"}
