"""Block membership for the canonical registered tools, with no handlers or state."""
from __future__ import annotations


EXTERNAL_MCP_DEFAULT_TOOL_BLOCK = "core"

EXTERNAL_MCP_TOOL_BLOCK_BRANCHES: dict[str, tuple[str, ...]] = {
    "integrations": (
        "integrations/modular-avatar",
        "integrations/vrcfury",
        "integrations/gesture-manager",
    ),
    "skills": ("skills/installed", "skills/vsk"),
}

EXTERNAL_MCP_TOOL_BLOCK_ROOTS = (
    "core",
    "project",
    "avatar",
    "assets",
    "materials",
    "integrations",
    "skills",
    "optimization",
    "checkpoint",
    "diagnostics",
    "encryption",
)

EXTERNAL_MCP_TOOL_BLOCKS = frozenset(
    {
        "core",
        "project",
        "avatar",
        "assets",
        "materials",
        "integrations/modular-avatar",
        "integrations/vrcfury",
        "integrations/gesture-manager",
        "skills/installed",
        "skills/vsk",
        "optimization",
        "checkpoint",
        "diagnostics",
        "encryption",
    }
)

EXTERNAL_MCP_READ_TOOL_BLOCKS: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            "vrcforge_external_tool_blocks",
            "vrcforge_health",
            "vrcforge_unity_status",
            "vrcforge_unity_tools",
            "vrcforge_get_compile_errors",
            "vrcforge_list_execution_targets",
            "vrcforge_bind_execution_target",
            "vrcforge_refresh_execution_target",
            "vrcforge_list_avatars",
            "vrcforge_get_gameobject",
            "vrcforge_get_property",
            "vrcforge_inspect_skinned_mesh_deformation",
        }
    ),
    "project": frozenset(
        {
            "vrcforge_diagnose_package_install_errors",
            "vrcforge_core_upgrade_status",
            "vrcforge_package_install_plan",
            "vrcforge_package_manager_status",
            "vrcforge_project_lifecycle_status",
            "vrcforge_project_create_plan",
            "vrcforge_project_catalog_registration_status",
            "vrcforge_scan_project_index",
        }
    ),
    "avatar": frozenset(
        {
            "vrcforge_plan_face_tuning",
            "vrcforge_preview_blendshape_apply",
            "vrcforge_preview_write_avatar_descriptor",
            "vrcforge_read_avatar_descriptor",
            "vrcforge_scan_avatar_controls",
            "vrcforge_scan_avatar_items",
            "vrcforge_scan_avatar_performance",
            "vrcforge_scan_blendshapes",
            "vrcforge_preview_atomic_reference_rename",
            "vrcforge_preview_constraint_sources",
            "vrcforge_preview_ensure_animator_state",
            "vrcforge_preview_ensure_expression_menu_control",
            "vrcforge_preview_ensure_expression_parameter",
            "vrcforge_preview_manage_expression_menu",
            "vrcforge_preview_manage_expression_parameters",
            "vrcforge_preview_manage_fx_animator",
            "vrcforge_preview_scene_object_duplicate",
            "vrcforge_preview_write_animation_curve",
            "vrcforge_inspect_skinned_mesh_bone_usage",
            "vrcforge_scan_animation_bindings",
            "vrcforge_scan_fx_animator",
            "vrcforge_scan_inbound_reference_closure",
            "vrcforge_scan_parameters",
            "vrcforge_avatar_upload_readiness",
            "vrcforge_get_avatar_upload_status",
            "vrcforge_preview_unity_constraint_conversion",
        }
    ),
    "assets": frozenset(
        {
            "vrcforge_get_unitypackage_import_status",
            "vrcforge_inspect_outfit_package",
            "vrcforge_plan_outfit_import",
            "vrcforge_preview_add_outfit",
            "vrcforge_preview_add_outfit_part",
            "vrcforge_preview_add_wardrobe_outfit",
            "vrcforge_preview_create_wardrobe",
            "vrcforge_preview_manage_wardrobe",
            "vrcforge_scan_wardrobe",
            "vrcforge_find_assets",
            "vrcforge_get_asset_info",
            "vrcforge_preview_scene_object_prefab",
            "vrcforge_preview_project_asset_duplicate",
            "vrcforge_preview_scene_asset_duplicate",
        }
    ),
    "materials": frozenset(
        {
            "vrcforge_plan_shader_tuning",
            "vrcforge_preview_material_shader_assignment",
            "vrcforge_preview_material_texture_assignment",
            "vrcforge_preview_renderer_material_slot",
            "vrcforge_preview_shader_apply",
            "vrcforge_scan_materials",
            "vrcforge_preview_texture_import_settings",
        }
    ),
    "integrations/modular-avatar": frozenset(
        {
            "vrcforge_inspect_modular_avatar_component",
            "vrcforge_preview_add_modular_avatar_component",
            "vrcforge_preview_setup_outfit",
            "vrcforge_scan_modular_avatar",
        }
    ),
    "integrations/vrcfury": frozenset(
        {
            "vrcforge_preview_component_feature",
            "vrcforge_scan_vrcfury",
        }
    ),
    "integrations/gesture-manager": frozenset(
        {
            "vrcforge_gesture_manager_status",
        }
    ),
    "skills/installed": frozenset(
        {"vrcforge_list_installed_skills", "vrcforge_read_installed_skill"}
    ),
    "skills/vsk": frozenset(
        {"vrcforge_preflight_skill_package", "vrcforge_preview_path_to_skill"}
    ),
    "optimization": frozenset(
        {
            "vrcforge_optimization_aao_hidden_body_cut_plan",
            "vrcforge_optimization_aao_trace_plan",
            "vrcforge_optimization_baseline_scan",
            "vrcforge_optimization_dependency_doctor",
            "vrcforge_optimization_lac_profile_plan",
            "vrcforge_optimization_ma2bt_convertibility_plan",
            "vrcforge_optimization_ma2bt_skipped_reasons",
            "vrcforge_optimization_ma_responsive_layer_audit",
            "vrcforge_optimization_material_slot_audit",
            "vrcforge_optimization_mesh_triangle_audit",
            "vrcforge_optimization_meshia_simplify_plan",
            "vrcforge_optimization_parameter_animator_usage",
            "vrcforge_optimization_parameter_behavior_regression",
            "vrcforge_optimization_parameter_budget_audit",
            "vrcforge_optimization_parameter_compressibility_plan",
            "vrcforge_optimization_parameter_inventory",
            "vrcforge_optimization_parameter_menu_map",
            "vrcforge_optimization_parameter_path_to_skill",
            "vrcforge_optimization_parameter_vrcfury_compressor_plan",
            "vrcforge_optimization_performance_tools_report",
            "vrcforge_optimization_physbone_audit",
            "vrcforge_optimization_physbone_reduce_plan",
            "vrcforge_optimization_plan",
            "vrcforge_optimization_profile_diff",
            "vrcforge_optimization_rollback_verify",
            "vrcforge_optimization_shader_adapter_registry",
            "vrcforge_scan_thry_avatar_performance",
            "vrcforge_optimization_target_profile",
            "vrcforge_optimization_texture_vram_audit",
            "vrcforge_optimization_ttt_atlas_plan",
            "vrcforge_optimization_upload_gate_audit",
            "vrcforge_optimization_upload_gate_fix_plan",
            "vrcforge_optimization_validation_delta",
            "vrcforge_optimization_visual_regression_plan",
            "vrcforge_optimization_vrcfury_compatibility_report",
            "vrcforge_preview_parameter_bit_packing",
        }
    ),
    "checkpoint": frozenset(
        {
            "vrcforge_list_checkpoints",
            "vrcforge_preview_restore_backup",
            "vrcforge_preview_restore_checkpoint",
            "vrcforge_preview_interrupted_apply_recovery",
            "vrcforge_export_interrupted_apply_incident_bundle",
            "vrcforge_list_interrupted_apply_recoveries",
        }
    ),
    "diagnostics": frozenset(
        {
            "vrcforge_build_test_readiness",
            "vrcforge_capture_status",
            "vrcforge_get_build_test_status",
            "vrcforge_inspect_primitive_basis_fixture",
            "vrcforge_read_vrchat_sdk_builder_alerts",
            "vrcforge_read_recent_logs",
            "vrcforge_run_validation_report",
            "vrcforge_vision_audit",
        }
    ),
    "encryption": frozenset(
        {
            "vrcforge_avatar_encryption_addon_status",
            "vrcforge_avatar_encryption_plan",
            "vrcforge_avatar_encryption_preview",
            "vrcforge_avatar_encryption_research_report",
            "vrcforge_avatar_encryption_scan",
        }
    ),
}

EXTERNAL_MCP_WRITE_TOOL_BLOCKS: dict[str, frozenset[str]] = {
    "project": frozenset(
        {
            "vrcforge_create_project",
            "vrcforge_install_unity_core",
            "vrcforge_restore_unity_core",
            "vrcforge_install_vpm_package",
            "vrcforge_refresh_asset_database",
            "vrcforge_register_project",
            "vrcforge_register_project_catalog",
            "vrcforge_rollback_project_catalog_registration",
            "vrcforge_rollback_project_lifecycle",
            "vrcforge_select_project",
            "vrcforge_set_play_mode",
            "vrcforge_confirm_unity_reload_dialog",
            "vrcforge_scene_transition",
        }
    ),
    "avatar": frozenset(
        {
            "vrcforge_atomic_reference_rename",
            "vrcforge_build_and_upload_avatar",
            "vrcforge_apply_blendshapes",
            "vrcforge_run_face_tuning",
            "vrcforge_undo_blendshapes",
            "vrcforge_write_avatar_descriptor",
            "vrcforge_add_component",
            "vrcforge_apply_clothing_fx",
            "vrcforge_apply_tuning_preset",
            "vrcforge_create_gameobject",
            "vrcforge_delete_gameobject",
            "vrcforge_duplicate_scene_object",
            "vrcforge_ensure_animator_state",
            "vrcforge_ensure_expression_menu_control",
            "vrcforge_ensure_expression_parameter",
            "vrcforge_export_vrm",
            "vrcforge_manage_expression_menu",
            "vrcforge_manage_expression_parameters",
            "vrcforge_manage_fx_animator",
            "vrcforge_reapply_tuning_history",
            "vrcforge_remove_component",
            "vrcforge_rename_gameobject",
            "vrcforge_reparent_gameobject",
            "vrcforge_rollback_parameters",
            "vrcforge_scene_save",
            "vrcforge_save_new_scene",
            "vrcforge_user_adjustment_handoff",
            "vrcforge_select_scene_object",
            "vrcforge_set_gameobject_active",
            "vrcforge_set_constraint_sources",
            "vrcforge_convert_unity_constraint",
            "vrcforge_set_property",
            "vrcforge_toggle_scene_object",
            "vrcforge_remap_skinned_mesh_bone",
            "vrcforge_capture_multi_screenshot",
            "vrcforge_write_animation_curve",
        }
    ),
    "assets": frozenset(
        {
            "vrcforge_add_outfit",
            "vrcforge_add_outfit_part",
            "vrcforge_add_wardrobe_outfit",
            "vrcforge_import_outfit_package",
            "vrcforge_create_wardrobe",
            "vrcforge_manage_wardrobe",
            "vrcforge_instantiate_prefab",
            "vrcforge_unpack_prefab",
            "vrcforge_duplicate_project_asset",
            "vrcforge_duplicate_scene_asset",
            "vrcforge_save_scene_object_as_prefab",
        }
    ),
    "materials": frozenset(
        {
            "vrcforge_apply_shader_tuning",
            "vrcforge_apply_shader_tuning_preset",
            "vrcforge_reapply_shader_tuning_history",
            "vrcforge_restore_shader_tuning",
            "vrcforge_set_texture_import_settings",
            "vrcforge_set_material_shader",
            "vrcforge_set_material_texture",
            "vrcforge_set_renderer_material_slot",
            "vrcforge_texture_patch",
        }
    ),
    "integrations/modular-avatar": frozenset(
        {
            "vrcforge_add_modular_avatar_component",
            "vrcforge_setup_outfit",
        }
    ),
    "integrations/vrcfury": frozenset({"vrcforge_create_component_feature"}),
    "integrations/gesture-manager": frozenset(
        {
            "vrcforge_gesture_manager_enter_play_mode",
            "vrcforge_gesture_manager_set_parameter",
        }
    ),
    "skills/installed": frozenset({"vrcforge_create_installed_skill"}),
    "skills/vsk": frozenset(
        {
            "vrcforge_import_skill_package",
            "vrcforge_export_skill_package",
            "vrcforge_set_skill_package_enabled",
            "vrcforge_write_path_to_skill",
        }
    ),
    "optimization": frozenset(
        {
            "vrcforge_apply_parameter_optimization",
            "vrcforge_build_parameter_bit_packed_clone",
        }
    ),
    "checkpoint": frozenset(
        {
            "vrcforge_create_safe_backup",
            "vrcforge_restore_checkpoint",
            "vrcforge_restore_safe_backup",
            "vrcforge_resolve_interrupted_apply_recovery",
        }
    ),
    "diagnostics": frozenset({"vrcforge_build_test_avatar", "vrcforge_capture_screenshot"}),
}
