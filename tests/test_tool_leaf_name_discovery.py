from internal_tool_blocks import (
    build_internal_tool_block_tree,
    project_internal_tool_block_level,
)


LEAF = "behavior/parameters_menus_layers"
FIXTURE_TOOLS = [
    {"name": "vrcforge_scan_wardrobe", "block": LEAF},
    {"name": "vrcforge_scan_parameters", "block": LEAF},
    {"name": "vrcforge_preview_create_wardrobe", "block": LEAF},
]


def test_root_directory_does_not_expose_global_tool_names():
    raw = build_internal_tool_block_tree(loaded_blocks={"core"}, leaves=FIXTURE_TOOLS)
    projected = project_internal_tool_block_level(raw)

    assert all("toolNames" not in row for row in projected["blocks"])
    assert all("loadCall" not in row for row in projected["blocks"])


def test_selected_category_exposes_exact_leaf_tool_names_without_loading():
    raw = build_internal_tool_block_tree(selector="behavior", loaded_blocks={"core"}, leaves=FIXTURE_TOOLS)
    projected = project_internal_tool_block_level(raw)

    leaf = next(row for row in projected["blocks"] if row["id"] == LEAF)
    assert leaf["toolNames"] == sorted(item["name"] for item in FIXTURE_TOOLS)
    assert "loadCall" not in leaf
    assert leaf["expandArguments"] == {"block": LEAF}


def test_selected_leaf_tool_list_path_still_matches_fixture_names():
    selected = build_internal_tool_block_tree(selector=LEAF, loaded_blocks={"core"}, leaves=FIXTURE_TOOLS)
    names = [item["name"] for item in selected["tree"]["tools"]]

    assert names == sorted(item["name"] for item in FIXTURE_TOOLS)
