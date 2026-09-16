from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from agent_tool_result_contract import normalize_agent_tool_result

import dashboard_server
from agent_gateway import (
    canonical_unity_read_tool_input_schema,
    canonical_unity_write_tool_input_schema,
)
from runtime_planner_service import bounded_planner_tool_schema, validate_planner_tool_arguments
from profiled_tool_registry import CapabilityProfile, ToolSet

from internal_tool_blocks import (
    CANONICAL_TOOL_BLOCKS,
    CANONICAL_TOOL_LEAVES,
    INTERNAL_LOADABLE_TOOL_BLOCKS,
    build_internal_tool_block_tree,
    internal_tool_block_for_name,
    normalize_internal_tool_blocks,
    resolve_internal_tool_block_selector,
)


def test_canonical_parent_blocks_are_routing_only_and_never_loadable() -> None:
    for parent in CANONICAL_TOOL_BLOCKS:
        assert parent not in INTERNAL_LOADABLE_TOOL_BLOCKS
        assert resolve_internal_tool_block_selector(parent) == ""
        assert parent not in normalize_internal_tool_blocks([parent])


def test_canonical_leaf_core_and_legacy_aliases_remain_loadable() -> None:
    for leaf in CANONICAL_TOOL_LEAVES:
        assert leaf in INTERNAL_LOADABLE_TOOL_BLOCKS
        assert resolve_internal_tool_block_selector(leaf) == leaf
        assert leaf in normalize_internal_tool_blocks([leaf])
    assert resolve_internal_tool_block_selector("core") == "core"
    assert resolve_internal_tool_block_selector("avatar") == "avatar_structure/hierarchy_components"
    assert resolve_internal_tool_block_selector("materials") == "appearance/materials_shaders"


def test_dashboard_parent_load_rejects_without_touching_session_state(monkeypatch) -> None:
    calls = []

    def sentinel(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("routing-only parent reached runtime session state")

    monkeypatch.setattr(
        type(dashboard_server.AGENT_GATEWAY.runtime_sessions),
        "load_internal_tool_block",
        sentinel,
    )
    for parent in CANONICAL_TOOL_BLOCKS:
        result = dashboard_server.load_internal_tool_block(
            {"sessionId": "internal-block-regression", "block": parent}
        )
        assert result["ok"] is False
        assert result["status"] == "failed"
        assert result["errorCode"] == "internal_tool_block_selector_invalid"
        assert result["toolRoutingStarted"] is False
        normalized = normalize_agent_tool_result(
            result, fallback_summary="load_internal_tool_block", write=False
        )
        assert normalized["status"] == "failed"
        expected_children = [f"{parent}/{leaf}" for leaf in CANONICAL_TOOL_BLOCKS[parent]["children"]]
        assert [child["name"] for child in result["availableChildren"]] == expected_children
        assert all(
            child["loadCall"] == {
                "skill_tool": "load_internal_tool_block",
                "skill_params": {"block": child["name"]},
            }
            for child in result["availableChildren"]
        )
        assert all(
            f"block={child['name']}" in " ".join(result["nextActions"])
            for child in result["availableChildren"]
        )
    assert calls == []


def test_internal_index_tree_is_independent_and_unity_is_nested() -> None:
    leaves = [
        {"name": "vrcforge_read_text_file", "block": "files", "mode": "read"},
        {"name": "vrcforge_get_compile_errors", "block": "unity/diagnostics", "mode": "read"},
    ]
    root = build_internal_tool_block_tree(loaded_blocks={"core"}, leaves=leaves)

    assert root["schema"] == "vrcforge.internal_tool_blocks.v1"
    assert [node["name"] for node in root["tree"]["children"]] == [
        "avatar_structure", "appearance", "behavior", "project_environment",
        "diagnostics_build", "research",
    ]
    assert all("tools" not in node for node in root["tree"]["children"])
    directory = {item["name"]: item for item in root["blocks"]}
    files = next(child for child in directory["project_environment"]["children"] if child["name"] == "project_environment/files")
    assert files["toolNames"] == ["vrcforge_read_text_file"]
    assert files["loadCall"] == {
        "skill_tool": "load_internal_tool_block",
        "skill_params": {"block": "project_environment/files"},
    }
    assert any("vrcforge_get_compile_errors" in child["toolNames"] for child in directory["diagnostics_build"]["children"])
    assert "inputSchema" not in str(root)

    assert resolve_internal_tool_block_selector("project_environment/files") == "project_environment/files"
    assert resolve_internal_tool_block_selector("files") == "project_environment/files"
    assert resolve_internal_tool_block_selector("research/web_research") == "research/web_research"


def test_visual_artifact_routing_does_not_misclassify_expression_trigger_as_behavior() -> None:
    appearance = CANONICAL_TOOL_BLOCKS["appearance"]["routing"]
    behavior = CANONICAL_TOOL_BLOCKS["behavior"]["routing"]
    face = CANONICAL_TOOL_LEAVES["behavior/face_eye_lipsync"]["routing"]
    renderers = CANONICAL_TOOL_LEAVES["appearance/renderers"]["routing"]
    materials = CANONICAL_TOOL_LEAVES["appearance/materials_shaders"]["routing"]

    assert "expression triggers" in " ".join(appearance["useWhen"])
    assert "route those to Appearance" in " ".join(behavior["doNotUse"])
    assert "solid-red" in " ".join(face["doNotUse"])
    assert "wrong material assignment" in " ".join(renderers["useWhen"])
    assert "solid red" in " ".join(materials["useWhen"])


def test_root_routing_strings_are_serialized_as_single_entries() -> None:
    from internal_tool_blocks import canonical_tool_block_description

    for block_id, spec in CANONICAL_TOOL_BLOCKS.items():
        routing = spec["routing"]
        assert isinstance(routing["doNotUse"], tuple)
        assert len(routing["doNotUse"]) == 1
        description = canonical_tool_block_description(block_id)
        assert "m / a / t / e / r" not in description


def test_internal_blocks_classify_general_tools_without_exposing_them_externally() -> None:
    assert internal_tool_block_for_name("vrcforge_read_text_file", "general") == "core"
    assert internal_tool_block_for_name("vrcforge_web_search", "general") == "research/web_research"
    assert internal_tool_block_for_name("vrcforge_agent_desktop_action", "core") == "project_environment/shell"
    assert internal_tool_block_for_name("vrcforge_execute_shell", "core") == "project_environment/shell"
    assert internal_tool_block_for_name("vrcforge_health", "unity") == "diagnostics_build/compile_logs"
    assert internal_tool_block_for_name("vrcforge_capture_screenshot", "unity") == "diagnostics_build/validation_performance"
    assert internal_tool_block_for_name("vrcforge_request_apply", "unity") == "diagnostics_build/compile_logs"
    assert (
        internal_tool_block_for_name("vrcforge_scan_vrcfury", "unity")
        == "behavior/interaction_generated_systems"
    )
    assert (
        internal_tool_block_for_name("vrcforge_scan_modular_avatar", "unity")
        == "behavior/interaction_generated_systems"
    )
    assert (
        internal_tool_block_for_name("vrcforge_gesture_manager_status", "unity")
        == "behavior/interaction_generated_systems"
    )
    assert (
        internal_tool_block_for_name("vrcforge_gesture_manager_enter_play_mode", "unity")
        == "behavior/interaction_generated_systems"
    )
    assert (
        internal_tool_block_for_name("vrcforge_gesture_manager_set_parameter", "unity")
        == "behavior/interaction_generated_systems"
    )
    assert internal_tool_block_for_name("vrcforge_install_vpm_package", "unity") == "project_environment/assets_packages"


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
    assert internal_tool_block_for_name(preview_name, "general") == "project_environment/files"
    assert internal_tool_block_for_name(write_name, "general") == "project_environment/files"
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
    assert planning_by_runtime[preview_name].block == "project_environment/files"
    preview_schema = gateway.shared_agent_tool_descriptor(
        preview_name,
        write=False,
    )["inputSchema"]
    assert planning_by_runtime[preview_name].input_schema == bounded_planner_tool_schema(preview_schema)
    assert write_name not in planning_by_runtime
    assert planning_routable[write_name].write is True
    assert execution_by_runtime[write_name].block == "project_environment/files"
    write_schema = gateway.shared_agent_tool_descriptor(
        write_name,
        write=True,
    )["inputSchema"]
    assert execution_by_runtime[write_name].input_schema == bounded_planner_tool_schema(write_schema)
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
        assert internal.block == dashboard_server.canonical_tool_owner(f"unity/{external_block}", internal.name)
        canonical = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
            tool.name,
            write=False,
            block=external_block,
        )["inputSchema"]
        assert internal.input_schema == bounded_planner_tool_schema(canonical)
        assert internal.definition_digest

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
        assert internal.block == dashboard_server.canonical_tool_owner(f"unity/{external_block}", internal.name)
        canonical = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
            handler.name,
            write=True,
            block=external_block,
        )["inputSchema"]
        assert internal.input_schema == bounded_planner_tool_schema(canonical)
        assert internal.definition_digest


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
        assert internal.block == dashboard_server.canonical_tool_owner(
            "unity/" + gateway.external_mcp_tool_block_for_name(name, write=False), internal.name
        )
        canonical = external_catalog[name]["inputSchema"]
        assert external_catalog[name]["inputSchema"] == canonical
        assert internal.input_schema == bounded_planner_tool_schema(canonical)
        assert internal.definition_digest == external_catalog[name]["definitionDigest"]
        assert internal.description == external_catalog[name]["description"]
        assert external_catalog[name]["canonicalName"]
        assert "When to use:" in internal.description
        assert "When NOT to use:" in internal.description
        assert "Negative example:" in internal.description

    for name, handler in external_writes.items():
        if name in external_only:
            continue
        projection = projections[name]
        internal = dashboard_server._runtime_planner_write_tool(handler, projection)
        assert projection.handler is handler.handler
        assert internal.name.startswith("unity_")
        assert internal.runtime_name == name
        assert internal.block == dashboard_server.canonical_tool_owner(
            "unity/" + gateway.external_mcp_tool_block_for_name(name, write=True), internal.name
        )
        canonical = external_catalog[name]["inputSchema"]
        assert external_catalog[name]["inputSchema"] == canonical
        assert internal.input_schema == bounded_planner_tool_schema(canonical)
        assert internal.definition_digest == external_catalog[name]["definitionDigest"]
        assert internal.description == external_catalog[name]["description"]
        assert external_catalog[name]["canonicalName"]
        assert external_catalog[name]["_meta"]["permission"] == "Write"

    wrapper_schema = gateway.shared_agent_tool_descriptor(
        "vrcforge_install_vpm_package",
        write=True,
    )["inputSchema"]
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
    assert "executionTarget" in internal["properties"]
    assert bounded_planner_tool_schema(canonical) == bounded_planner_tool_schema(
        canonical_unity_read_tool_input_schema("vrcforge_preview_manage_fx_animator")
    )
    assert validate_planner_tool_arguments(
        internal,
        {"projectPath": "P", "action": "delete_parameter"},
    )["ok"] is False
    assert validate_planner_tool_arguments(
        internal,
        {
            "projectPath": "P",
            "action": "delete_parameter",
            "parameterName": "Unused",
            "executionTarget": {
                "schema": "vrcforge.execution_target.v1",
                "namespace": "fixture",
                "scope": "avatar",
                "project": {},
                "editor": {},
            },
        },
    )["ok"] is True


def test_registered_loaded_block_exposes_every_advertised_tool_schema():
    planner = dashboard_server.AGENT_GATEWAY._runtime_planner
    catalog = planner._catalog.read("planning", project_context_active=True)
    leaves = dashboard_server._internal_tool_block_leaves("planning", project_context_active=True)
    tree = build_internal_tool_block_tree(loaded_blocks=["core"], leaves=leaves)
    for branch in tree["blocks"]:
        for leaf in branch["children"]:
            if not leaf["toolNames"]:
                continue
            prompt = planner._build_llm_plan_prompt("Inspect only", [], exposure_layer="planning", project_context_active=True, internal_tool_blocks=[leaf["name"]])
            for name in leaf["toolNames"]:
                tool = next(t for t in catalog.visible_tools if t.name == name)
                if not tool.requires_user_activation:
                    assert f"- {name}" in prompt, (leaf["name"], name, tool.block)
    status = next(t for t in catalog.visible_tools if t.name == "unity_status")
    prompt = planner._build_llm_plan_prompt("Inspect only", [], exposure_layer="planning", project_context_active=True, internal_tool_blocks=[status.block])
    status_line = next(line for line in prompt.splitlines() if line.startswith("- unity_status "))
    assert "schema=" in status_line
