"""Editor plugin controls reuse the existing approval, identity and Core routes."""
import jsonschema
import pytest

from unity_editor_extension_tools import EDITOR_MENU_SCHEMA, editor_menu_request
from mcp_tool_descriptor import identity_scope
from unity_mcp_tool_contract import READ_ONLY_TOOL_NAMES, EXPECTED_TOOL_NAMES
from internal_tool_blocks import canonical_tool_owner


@pytest.mark.parametrize("selection", [[], ["GlobalObjectId_V1-2-a-1-0"]])
def test_explicit_menu_request_keeps_selection_and_only_core_arguments(selection):
    args = {"projectPath": "project", "menuItemPath": "GameObject/Plugin/Setup",
            "selectionGlobalObjectIds": selection}
    jsonschema.validate(args, EDITOR_MENU_SCHEMA)
    assert editor_menu_request(args) == {"menuItemPath": args["menuItemPath"], "selectionGlobalObjectIds": selection}


@pytest.mark.parametrize("selection", [None, "current", [""], ["id", "id"], [str(i) for i in range(33)]])
def test_menu_request_rejects_implicit_ambiguous_selection(selection):
    with pytest.raises(ValueError):
        editor_menu_request({"menuItemPath": "GameObject/Plugin/Setup", "selectionGlobalObjectIds": selection})


def test_read_discovery_and_approved_execution_are_separate_project_tools():
    assert {"vrc_inspect_editor_capabilities", "vrc_inspect_material_properties"} <= READ_ONLY_TOOL_NAMES
    assert "vrc_execute_unity_editor_menu" in EXPECTED_TOOL_NAMES - READ_ONLY_TOOL_NAMES
    for suffix in ("inspect_editor_capabilities", "inspect_material_properties", "execute_unity_editor_menu"):
        assert identity_scope("vrcforge_" + suffix, write=True) == "project"
    assert canonical_tool_owner("project", "vrcforge_execute_unity_editor_menu") == "project_environment/assets_packages"


def test_gateway_menu_plan_and_checkpoint_dispatch(monkeypatch, tmp_path):
    import dashboard_server as server
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_execute_unity_editor_menu"]
    args = {"projectPath": str(tmp_path), "menuItemPath": "GameObject/Plugin/Setup", "selectionGlobalObjectIds": []}
    assert handler.requires_approved_execution_context
    assert handler.pre_write_checkpoint_required
    assert handler.approved_execution_plan_builder(args) == [("vrc_execute_unity_editor_menu", editor_menu_request(args))]
    calls = []
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda root: calls.append(root) or {"ok": True})
    assert handler.checkpoint_prepare_handler(tmp_path, {**args, "_checkpointTargetTool": handler.name}) == {"ok": True}
    assert calls == [tmp_path]
