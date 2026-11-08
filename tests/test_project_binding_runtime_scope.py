from __future__ import annotations

import pytest

from agent_gateway import (
    AgentGatewayError,
    UNITY_READ_TOOL_INPUT_SCHEMAS,
    bind_runtime_unity_project,
    canonical_unity_read_tool_input_schema,
)


def test_optional_unity_read_is_bound_to_runtime_project() -> None:
    schema = canonical_unity_read_tool_input_schema("vrcforge_scan_fx_animator")
    bound = bind_runtime_unity_project(schema, {"avatarPath": "Avatar"}, r"D:\Projects\A")
    assert bound["projectPath"] == r"D:\Projects\A"


def test_explicit_unity_project_mismatch_is_rejected() -> None:
    schema = canonical_unity_read_tool_input_schema("vrcforge_scan_fx_animator")
    with pytest.raises(AgentGatewayError, match="does not match"):
        bind_runtime_unity_project(
            schema,
            {"projectPath": r"D:\Projects\B"},
            r"D:\Projects\A",
        )


def test_non_project_tool_arguments_are_unchanged() -> None:
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    arguments = {"value": "kept"}
    assert bind_runtime_unity_project(schema, arguments, r"D:\Projects\A") == arguments


def test_optional_project_read_schemas_are_known_and_project_bindable() -> None:
    optional = [
        name
        for name, schema in UNITY_READ_TOOL_INPUT_SCHEMAS.items()
        if "projectPath" in (schema.get("properties") or {})
        and "projectPath" not in (schema.get("required") or [])
    ]
    expected_optional = {
        'vrcforge_avatar_encryption_plan',
        'vrcforge_avatar_encryption_preview',
        'vrcforge_avatar_encryption_scan',
        'vrcforge_diagnose_package_install_errors',
        'vrcforge_find_assets',
        'vrcforge_find_files',
        'vrcforge_gesture_manager_status',
        'vrcforge_get_asset_info',
        'vrcforge_inspect_modular_avatar_component',
        'vrcforge_inspect_primitive_basis_fixture',
        'vrcforge_inspect_skinned_mesh_bone_usage',
        'vrcforge_list_avatars',
        'vrcforge_list_directory',
        'vrcforge_optimization_aao_hidden_body_cut_plan',
        'vrcforge_optimization_aao_trace_plan',
        'vrcforge_optimization_baseline_scan',
        'vrcforge_optimization_dependency_doctor',
        'vrcforge_optimization_lac_profile_plan',
        'vrcforge_optimization_ma2bt_convertibility_plan',
        'vrcforge_optimization_ma2bt_skipped_reasons',
        'vrcforge_optimization_ma_responsive_layer_audit',
        'vrcforge_optimization_material_slot_audit',
        'vrcforge_optimization_mesh_triangle_audit',
        'vrcforge_optimization_meshia_simplify_plan',
        'vrcforge_optimization_parameter_animator_usage',
        'vrcforge_optimization_parameter_behavior_regression',
        'vrcforge_optimization_parameter_budget_audit',
        'vrcforge_optimization_parameter_compressibility_plan',
        'vrcforge_optimization_parameter_inventory',
        'vrcforge_optimization_parameter_menu_map',
        'vrcforge_optimization_parameter_path_to_skill',
        'vrcforge_optimization_parameter_vrcfury_compressor_plan',
        'vrcforge_optimization_performance_tools_report',
        'vrcforge_optimization_physbone_audit',
        'vrcforge_optimization_physbone_reduce_plan',
        'vrcforge_optimization_plan',
        'vrcforge_optimization_profile_diff',
        'vrcforge_optimization_rollback_verify',
        'vrcforge_optimization_shader_adapter_registry',
        'vrcforge_optimization_texture_vram_audit',
        'vrcforge_optimization_ttt_atlas_plan',
        'vrcforge_optimization_upload_gate_audit',
        'vrcforge_optimization_upload_gate_fix_plan',
        'vrcforge_optimization_visual_regression_plan',
        'vrcforge_optimization_vrcfury_compatibility_report',
        'vrcforge_package_install_plan',
        'vrcforge_package_manager_status',
        'vrcforge_plan_face_tuning',
        'vrcforge_plan_outfit_import',
        'vrcforge_preview_blendshape_apply',
        'vrcforge_preview_parameter_bit_packing',
        'vrcforge_preview_restore_checkpoint',
        'vrcforge_project_catalog_registration_status',
        'vrcforge_read_local_image',
        'vrcforge_project_create_plan',
        'vrcforge_read_avatar_descriptor',
        'vrcforge_read_text_file',
        'vrcforge_run_validation_report',
        'vrcforge_scan_animation_bindings',
        'vrcforge_scan_avatar_controls',
        'vrcforge_scan_avatar_items',
        'vrcforge_scan_blendshapes',
        'vrcforge_scan_fx_animator',
        'vrcforge_scan_inbound_reference_closure',
        'vrcforge_scan_materials',
        'vrcforge_scan_modular_avatar',
        'vrcforge_scan_parameters',
        'vrcforge_scan_project_index',
        'vrcforge_scan_vrcfury',
        'vrcforge_scan_wardrobe',
        'vrcforge_search_text',
        'vrcforge_unity_status',
        'vrcforge_unity_tools',
    }
    assert set(optional) == expected_optional
    for name in optional:
        bound = bind_runtime_unity_project(
            canonical_unity_read_tool_input_schema(name), {}, r"D:\Projects\A"
        )
        assert bound["projectPath"] == r"D:\Projects\A"


def test_external_optional_project_read_fails_closed_after_two_scopes(tmp_path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway._guard_external_mcp_project_scope(  # noqa: SLF001 - focused policy test
        "vrcforge_scan_fx_animator", {"projectPath": str(tmp_path / "A")}
    )
    gateway._guard_external_mcp_project_scope(  # noqa: SLF001
        "vrcforge_scan_fx_animator", {"projectPath": str(tmp_path / "B")}
    )
    with pytest.raises(AgentGatewayError, match="multiple Unity project scopes") as rejected:
        gateway._guard_external_mcp_project_scope("vrcforge_list_avatars", {})  # noqa: SLF001
    details = rejected.value.external_error["details"]
    assert details["knownProjectPaths"] == sorted(gateway._external_mcp_project_paths)  # noqa: SLF001
    assert "choose the exact intended projectpath" in details["nextAction"].casefold()
    assert "do not guess" in details["nextAction"].casefold()


@pytest.mark.parametrize("registered_scope_count", [0, 1])
@pytest.mark.parametrize("tool_name", ["vrcforge_scan_blendshapes", "vrcforge_apply_blendshapes"])
def test_external_project_path_is_explicit_even_with_a_single_scope(tmp_path, tool_name, registered_scope_count):
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = str(tmp_path / "project")
    if registered_scope_count:
        gateway._register_runtime_project_scope(project)

    with pytest.raises(AgentGatewayError, match="arguments.projectPath") as rejected:
        gateway._guard_external_mcp_project_scope(tool_name, {})

    assert rejected.value.external_error["toolRoutingStarted"] is False
    assert rejected.value.external_error["mutationStarted"] is False
    assert rejected.value.external_error["commitState"] == "not_started"
    gateway._guard_external_mcp_project_scope(tool_name, {"projectPath": project})


def test_external_project_scope_rejects_conflicting_path_aliases(tmp_path):
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    with pytest.raises(AgentGatewayError, match="same project"):
        gateway._guard_external_mcp_project_scope(
            "vrcforge_scan_blendshapes", {"projectPath": str(tmp_path / "a"), "project_path": str(tmp_path / "b")},
        )


def test_external_project_scope_preserves_explicit_nested_paths_and_rejects_relative_paths(tmp_path):
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    with pytest.raises(AgentGatewayError, match="absolute projectPath"):
        gateway._guard_external_mcp_project_scope("vrcforge_scan_blendshapes", {"projectPath": "Other"})

    arguments = {"arguments": {"projectPath": str(tmp_path)}}
    gateway._guard_external_mcp_project_scope("vrcforge_preview_parameter_bit_packing", arguments)
    assert arguments["projectPath"] == str(tmp_path)


@pytest.mark.parametrize(("tool_name", "params"), [
    ("vrcforge_scan_blendshapes", {"scope": "all"}),
    ("vrcforge_request_apply", {"targetTool": "vrcforge_apply_blendshapes", "arguments": {"avatar": "Avatar"}}),
])
def test_legacy_external_tool_endpoint_rejects_missing_project(tmp_path, monkeypatch, tool_name, params):
    from fastapi.testclient import TestClient

    from agent_gateway import AgentGateway
    import dashboard_server

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(gateway, "call_tool", lambda *_: pytest.fail("External tool dispatched without a project"))

    client = TestClient(dashboard_server.app, client=("127.0.0.1", 51234))
    try:
        response = client.post(
            "/api/agent/tool/" + tool_name,
            headers={"Authorization": "Bearer " + config.token},
            json={"agent_name": "test", "params": params},
        )
    finally:
        client.close()

    assert response.status_code == 409, response.text
    assert "arguments.projectPath" in response.text


def test_global_project_catalog_status_bypasses_optional_project_scope_guard(tmp_path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway._register_runtime_project_scope(r"D:\Projects\A")  # noqa: SLF001
    gateway._register_runtime_project_scope(r"D:\Projects\B")  # noqa: SLF001

    gateway._guard_external_mcp_project_scope(  # noqa: SLF001
        "vrcforge_project_catalog_registration_status", {}
    )


def test_internal_scopes_also_make_external_optional_read_fail_closed(tmp_path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway._register_runtime_project_scope(r"D:\Projects\A")  # noqa: SLF001
    gateway._register_runtime_project_scope(r"D:\Projects\B")  # noqa: SLF001
    with pytest.raises(AgentGatewayError, match="multiple Unity project scopes"):
        gateway._guard_external_mcp_project_scope("vrcforge_scan_fx_animator", {})  # noqa: SLF001
