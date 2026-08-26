from __future__ import annotations

from types import SimpleNamespace

import dashboard_server
from agent_gateway import (
    canonical_unity_read_tool_input_schema,
    canonical_unity_write_tool_input_schema,
)
from runtime_planner_service import bounded_planner_tool_schema, validate_planner_tool_arguments
from profiled_tool_registry import CapabilityProfile, ToolSet

from internal_tool_blocks import (
    build_internal_tool_block_tree,
    internal_tool_block_for_name,
    resolve_internal_tool_block_selector,
)
from path_to_skill_controller import (
    PATH_TO_SKILL_PREVIEW_INPUT_SCHEMA,
    PATH_TO_SKILL_WRITE_INPUT_SCHEMA,
)


def test_internal_index_tree_is_independent_and_unity_is_nested() -> None:
    leaves = [
        {"name": "vrcforge_read_text_file", "block": "files", "mode": "read"},
        {"name": "vrcforge_get_compile_errors", "block": "unity/diagnostics", "mode": "read"},
    ]
    root = build_internal_tool_block_tree(loaded_blocks={"core"}, leaves=leaves)

    assert root["schema"] == "vrcforge.internal_tool_blocks.v1"
    assert [(node["index"], node["name"]) for node in root["tree"]["children"]] == [
        ("1", "core"),
        ("2", "files"),
        ("3", "web"),
        ("4", "desktop"),
        ("5", "shell"),
        ("6", "attachments"),
        ("7", "diagnostics"),
        ("8", "unity"),
    ]
    assert all("tools" not in node for node in root["tree"]["children"])
    directory = {item["name"]: item for item in root["blocks"]}
    assert directory["files"]["toolNames"] == ["vrcforge_read_text_file"]
    assert directory["files"]["loadCall"] == {
        "skill_tool": "load_internal_tool_block",
        "skill_params": {"block": "files"},
    }
    assert directory["unity/diagnostics"]["toolNames"] == ["vrcforge_get_compile_errors"]
    assert "inputSchema" not in str(root)

    unity = build_internal_tool_block_tree(selector="8", loaded_blocks={"core"}, leaves=leaves)
    assert [node["index"] for node in unity["tree"]["children"]] == [
        "8.1",
        "8.2",
        "8.3",
        "8.4",
        "8.5",
        "8.6",
        "8.7",
        "8.8",
        "8.9",
        "8.10",
    ]
    assert resolve_internal_tool_block_selector("8.3") == "unity/avatar"
    assert resolve_internal_tool_block_selector("unity/avatar") == "unity/avatar"
    assert resolve_internal_tool_block_selector("8") == ""
    assert resolve_internal_tool_block_selector("8.6") == ""
    assert (
        resolve_internal_tool_block_selector("8.6.1")
        == "unity/integrations/modular-avatar"
    )
    assert (
        resolve_internal_tool_block_selector("unity/integrations/vrcfury")
        == "unity/integrations/vrcfury"
    )
    assert [item["name"] for item in unity["blocks"]] == [
        "unity/core",
        "unity/project",
        "unity/avatar",
        "unity/assets",
        "unity/materials",
        "unity/integrations/modular-avatar",
        "unity/integrations/vrcfury",
        "unity/integrations/ndmf",
        "unity/integrations/gesture-manager",
        "unity/optimization",
        "unity/checkpoint",
        "unity/diagnostics",
        "unity/encryption",
    ]

    integrations = build_internal_tool_block_tree(
        selector="8.6",
        loaded_blocks={"core"},
        leaves=leaves,
    )
    assert integrations["tree"]["name"] == "unity/integrations"
    assert [node["index"] for node in integrations["tree"]["children"]] == [
        "8.6.1",
        "8.6.2",
        "8.6.3",
        "8.6.4",
    ]


def test_internal_blocks_classify_general_tools_without_exposing_them_externally() -> None:
    assert internal_tool_block_for_name("vrcforge_read_text_file", "general") == "files"
    assert internal_tool_block_for_name("vrcforge_web_search", "general") == "web"
    assert internal_tool_block_for_name("vrcforge_agent_desktop_action", "core") == "desktop"
    assert internal_tool_block_for_name("vrcforge_execute_shell", "core") == "shell"
    assert internal_tool_block_for_name("vrcforge_health", "unity") == "diagnostics"
    assert internal_tool_block_for_name("vrcforge_capture_screenshot", "unity") == "attachments"
    assert internal_tool_block_for_name("vrcforge_request_apply", "unity") == "diagnostics"
    assert (
        internal_tool_block_for_name("vrcforge_scan_vrcfury", "unity")
        == "unity/integrations/vrcfury"
    )
    assert (
        internal_tool_block_for_name("vrcforge_scan_modular_avatar", "unity")
        == "unity/integrations/modular-avatar"
    )
    assert (
        internal_tool_block_for_name("vrcforge_gesture_manager_status", "unity")
        == "unity/integrations/gesture-manager"
    )
    assert (
        internal_tool_block_for_name("vrcforge_gesture_manager_enter_play_mode", "unity")
        == "unity/integrations/gesture-manager"
    )
    assert (
        internal_tool_block_for_name("vrcforge_gesture_manager_set_parameter", "unity")
        == "unity/integrations/gesture-manager"
    )
    assert internal_tool_block_for_name("vrcforge_install_vpm_package", "unity") == "unity/project"


def test_path_to_skill_creator_is_lazy_shared_and_reuses_controller_owners() -> None:
    gateway = dashboard_server.AGENT_GATEWAY
    preview_name = "vrcforge_preview_path_to_skill"
    write_name = "vrcforge_write_path_to_skill"

    preview = gateway._tools[preview_name]
    writer = gateway._write_handlers[write_name]
    assert preview.handler.__self__ is dashboard_server.PATH_TO_SKILL_PREVIEW
    assert writer.handler.__self__ is dashboard_server.PATH_TO_SKILL_WRITE
    assert writer.risk_level == "medium"
    assert writer.pre_write_checkpoint_required is True
    assert internal_tool_block_for_name(preview_name, "general") == "diagnostics"
    assert internal_tool_block_for_name(write_name, "general") == "diagnostics"
    assert "when to use:" in preview.description.casefold()
    assert "when not to use:" in preview.description.casefold()
    assert "when to use:" in writer.description.casefold()
    assert "when not to use:" in writer.description.casefold()
    preview_result = preview.handler(
        {
            "summary": {
                "status": "passed",
                "workflow": "internal_creator_probe",
                "steps": ["inspect", "verify"],
            },
            "packageId": "community.path-to-skill.internal-probe",
        }
    )
    assert preview_result["ok"] is True
    assert preview_result["dryRun"] is True
    assert preview_result["manifest"]["id"] == "community.path-to-skill.internal-probe"

    planning = dashboard_server._RuntimePlannerCatalog().read("planning")
    execution = dashboard_server._RuntimePlannerCatalog().read("execution")
    planning_by_runtime = {tool.runtime_name: tool for tool in planning.visible_tools}
    execution_by_runtime = {tool.runtime_name: tool for tool in execution.visible_tools}
    planning_routable = {tool.runtime_name: tool for tool in planning.routable_tools}
    assert planning_by_runtime[preview_name].block == "diagnostics"
    assert planning_by_runtime[preview_name].input_schema == bounded_planner_tool_schema(
        PATH_TO_SKILL_PREVIEW_INPUT_SCHEMA
    )
    assert write_name not in planning_by_runtime
    assert planning_routable[write_name].write is True
    assert execution_by_runtime[write_name].block == "diagnostics"
    assert execution_by_runtime[write_name].input_schema == bounded_planner_tool_schema(
        PATH_TO_SKILL_WRITE_INPUT_SCHEMA
    )
    assert validate_planner_tool_arguments(
        execution_by_runtime[write_name].input_schema,
        {"summary": {"status": "passed", "steps": ["inspect"]}},
    )["ok"] is False
    assert validate_planner_tool_arguments(
        execution_by_runtime[write_name].input_schema,
        {
            "summary": {"status": "passed", "steps": ["inspect"]},
            "exportVsk": True,
            "confirmExport": False,
        },
    )["ok"] is False

    external_planning = {
        item["name"]
        for item in gateway.build_external_mcp_tools("planning", tool_blocks=["skills/vsk"])
    }
    external_execution = {
        item["name"]
        for item in gateway.build_external_mcp_tools("execution", tool_blocks=["skills/vsk"])
    }
    assert preview_name in external_planning
    assert write_name not in external_planning
    assert {preview_name, write_name} <= external_execution


def test_shared_unity_facades_reuse_external_blocks_and_schemas_inside() -> None:
    projection = lambda name: SimpleNamespace(
        model_name=name,
        internal_name=name,
        capabilities=(),
        tool_set=SimpleNamespace(value="unity"),
    )

    for tool in dashboard_server.AGENT_GATEWAY._tools.values():
        external_block = dashboard_server.AGENT_GATEWAY.external_mcp_tool_block_for_name(
            tool.name,
            write=False,
        )
        if not external_block:
            continue
        internal = dashboard_server._runtime_planner_tool(tool, projection(tool.name))
        assert internal.block == f"unity/{external_block}"
        canonical = canonical_unity_read_tool_input_schema(tool.name)
        assert internal.input_schema == bounded_planner_tool_schema(canonical)

    for handler in dashboard_server.AGENT_GATEWAY._write_handlers.values():
        external_block = dashboard_server.AGENT_GATEWAY.external_mcp_tool_block_for_name(
            handler.name,
            write=True,
        )
        if not external_block:
            continue
        internal = dashboard_server._runtime_planner_write_tool(
            handler,
            projection(handler.name),
        )
        assert internal.block == f"unity/{external_block}"
        canonical = canonical_unity_write_tool_input_schema(handler.name)
        assert internal.input_schema == bounded_planner_tool_schema(canonical)


def test_five_atomic_facades_are_registered_for_both_internal_and_external_execution() -> None:
    expected = {
        "vrcforge_atomic_reference_rename",
        "vrcforge_set_constraint_sources",
        "vrcforge_save_scene_object_as_prefab",
        "vrcforge_set_material_shader",
        "vrcforge_build_parameter_bit_packed_clone",
    }
    registered = dashboard_server.AGENT_GATEWAY.approval_transactions.registered_write_target_names()
    assert expected <= registered
    assert all(
        dashboard_server.AGENT_GATEWAY.external_mcp_tool_block_for_name(name, write=True)
        for name in expected
    )


def test_all_shared_unity_atoms_keep_internal_external_contract_parity() -> None:
    gateway = dashboard_server.AGENT_GATEWAY
    external_catalog = {
        item["name"]: item
        for item in gateway.build_external_mcp_tools("execution", tool_blocks=["*"])
    }
    projections = {
        item.internal_name: item
        for item in dashboard_server._RUNTIME_PROFILED_TOOL_REGISTRY.project(
            CapabilityProfile.UNITY_PROJECT
        )
        if item.tool_set is ToolSet.UNITY
    }
    external_reads = {
        name: tool
        for name, tool in gateway._tools.items()
        if dashboard_server._runtime_tool_set(name) is ToolSet.UNITY
        if gateway.external_mcp_tool_block_for_name(name, write=False)
    }
    external_writes = {
        name: handler
        for name, handler in gateway._write_handlers.items()
        if dashboard_server._runtime_tool_set(name) is ToolSet.UNITY
        if name not in gateway._tools
        and gateway.external_mcp_tool_block_for_name(name, write=True)
    }

    # VPM installation is an external sealed backend wrapper, not a Unity Core
    # atom. Every actual shared Unity atom must exist in both model surfaces.
    external_only = {"vrcforge_install_vpm_package"}
    assert set(external_reads) <= set(projections)
    assert set(external_writes) - set(projections) == external_only
    shared_names = set(external_reads) | (set(external_writes) - external_only)
    assert shared_names <= set(projections)
    assert all(
        not gateway.external_mcp_tool_block_for_name(name, write=False)
        and not gateway.external_mcp_tool_block_for_name(name, write=True)
        for name in set(projections) - shared_names
    )

    for name, tool in external_reads.items():
        projection = projections[name]
        internal = dashboard_server._runtime_planner_tool(tool, projection)
        assert projection.handler is tool.handler
        assert internal.name.startswith("unity_")
        assert internal.runtime_name == name
        assert internal.block == (
            "unity/" + gateway.external_mcp_tool_block_for_name(name, write=False)
        )
        canonical = canonical_unity_read_tool_input_schema(name)
        assert external_catalog[name]["inputSchema"] == canonical
        assert internal.input_schema == bounded_planner_tool_schema(canonical)

    for name, handler in external_writes.items():
        if name in external_only:
            continue
        projection = projections[name]
        internal = dashboard_server._runtime_planner_write_tool(handler, projection)
        assert projection.handler is handler.handler
        assert internal.name.startswith("unity_")
        assert internal.runtime_name == name
        assert internal.block == (
            "unity/" + gateway.external_mcp_tool_block_for_name(name, write=True)
        )
        canonical = canonical_unity_write_tool_input_schema(name)
        assert external_catalog[name]["inputSchema"] == canonical
        assert internal.input_schema == bounded_planner_tool_schema(canonical)

    wrapper_schema = canonical_unity_write_tool_input_schema("vrcforge_install_vpm_package")
    assert external_catalog["vrcforge_install_vpm_package"]["inputSchema"] == wrapper_schema


def test_internal_shared_facades_reject_inputs_the_external_contract_rejects() -> None:
    projection = lambda name: SimpleNamespace(
        model_name=name,
        internal_name=name,
        capabilities=(),
        tool_set=SimpleNamespace(value="unity"),
    )
    handlers = dashboard_server.AGENT_GATEWAY._write_handlers
    constraint = dashboard_server._runtime_planner_write_tool(
        handlers["vrcforge_set_constraint_sources"],
        projection("vrcforge_set_constraint_sources"),
    )
    rename = dashboard_server._runtime_planner_write_tool(
        handlers["vrcforge_atomic_reference_rename"],
        projection("vrcforge_atomic_reference_rename"),
    )

    assert validate_planner_tool_arguments(
        constraint.input_schema,
        {
            "projectPath": "P",
            "scenePath": "Assets/A.unity",
            "gameObjectPath": "Avatar/Constraint",
            "constraintKind": "parent",
            "componentIndex": 0,
            "sources": [{"wrong": 1}],
        },
    )["ok"] is False
    assert validate_planner_tool_arguments(
        rename.input_schema,
        {
            "projectPath": "P",
            "operationKind": "game_object",
            "scenePath": "Assets/A.unity",
            "avatarPath": "Avatar",
        },
    )["ok"] is False


def test_internal_schema_preserves_patterns_and_fx_delete_parameter_branch() -> None:
    job_schema = bounded_planner_tool_schema(
        canonical_unity_read_tool_input_schema("vrcforge_get_build_test_status")
    )
    assert job_schema["properties"]["jobId"]["pattern"] == "^[0-9a-fA-F]{32}$"
    assert validate_planner_tool_arguments(
        job_schema,
        {"projectPath": "P", "jobId": "not-a-job-id"},
    )["ok"] is False

    canonical = canonical_unity_write_tool_input_schema("vrcforge_manage_fx_animator")
    internal = bounded_planner_tool_schema(canonical)
    assert internal == bounded_planner_tool_schema(
        canonical_unity_read_tool_input_schema("vrcforge_preview_manage_fx_animator")
    )
    assert validate_planner_tool_arguments(
        internal,
        {"projectPath": "P", "action": "delete_parameter"},
    )["ok"] is False
    assert validate_planner_tool_arguments(
        internal,
        {"projectPath": "P", "action": "delete_parameter", "parameterName": "Unused"},
    )["ok"] is True
