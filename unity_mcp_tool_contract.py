"""The fixed public VRCForge Unity MCP tool-name contract."""

from __future__ import annotations

CORE_IDENTITY = "vrcforge.unity-core"
HANDSHAKE_PROTOCOL = "vrcforge.core-handshake.v1"
PRODUCT_VERSION = "1.7.10"
TOOL_CONTRACT_VERSION = "84"
PREVIOUS_CORE_TOOL_CONTRACT_VERSION = "83"

EXPECTED_TOOL_NAMES = frozenset(
    {
        "vrc_add_component", "vrc_add_modular_avatar_component", "vrc_add_outfit_part", "vrc_add_wardrobe_outfit",
        "vrc_apply_blendshapes", "vrc_apply_clothing_fx", "vrc_apply_material_tuning", "vrc_apply_parameter_optimization",
        "vrc_atomic_reference_rename", "vrc_avatar_upload_readiness", "vrc_build_and_upload_avatar", "vrc_build_parameter_bit_packed_clone", "vrc_build_test_avatar", "vrc_capture_scene_view",
        "vrc_convert_unity_constraint", "vrc_create_component_feature", "vrc_create_gameobject", "vrc_create_safe_backup", "vrc_delete_gameobject",
        "vrc_duplicate_scene_object", "vrc_duplicate_project_asset", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control",
        "vrc_ensure_expression_parameter", "vrc_export_blendshapes", "vrc_export_vrm", "vrc_find_assets",
        "vrc_get_asset_info", "vrc_get_compile_errors", "vrc_get_gameobject", "vrc_get_property", "vrc_gesture_manager_enter_play_mode", "vrc_gesture_manager_set_parameter", "vrc_import_unitypackage",
        "vrc_inspect_modular_avatar_component", "vrc_inspect_primitive_basis_fixture", "vrc_inspect_skinned_mesh_bone_usage", "vrc_inspect_skinned_mesh_deformation", "vrc_remap_skinned_mesh_bone", "vrc_instantiate_prefab",
        "vrc_manage_expression_menu", "vrc_manage_expression_parameters", "vrc_manage_fx_animator", "vrc_manage_wardrobe",
        "vrc_poll_job", "vrc_prepare_checkpoint", "vrc_read_avatar_descriptor", "vrc_read_vrchat_sdk_builder_alerts", "vrc_refresh_asset_database",
        "vrc_reload_after_checkpoint_restore", "vrc_reload_primitive_basis_fixture", "vrc_remove_component",
        "vrc_rename_gameobject", "vrc_reparent_gameobject", "vrc_restore_safe_backup", "vrc_rollback_avatar_parameters",
        "vrc_save_scene_object_as_prefab", "vrc_save_current_scene", "vrc_save_new_scene", "vrc_select_scene_object", "vrc_scan_animation_bindings", "vrc_scan_avatar_controls", "vrc_scan_avatar_items",
        "vrc_scan_avatar_materials", "vrc_scan_avatar_parameters", "vrc_scan_avatar_performance", "vrc_scan_fx_animator",
        "vrc_scan_inbound_reference_closure", "vrc_scan_thry_avatar_performance", "vrc_scan_wardrobe", "vrc_set_constraint_sources", "vrc_set_gameobject_active",
        "vrc_set_material_shader", "vrc_set_material_texture", "vrc_set_play_mode", "vrc_set_property", "vrc_set_texture_import_settings", "vrc_setup_outfit",
        "vrc_toggle_scene_object", "vrc_unpack_prefab", "vrc_write_animation_curve", "vrc_write_avatar_descriptor",
    }
)
EXPECTED_TOOL_COUNT = 82

# Contract revisions describe the discovered tool surface; protocol-range
# negotiation decides whether the App and Core can communicate. Revision 84
# adds one preview-bound persistent material texture-slot assignment atom.
PREVIOUS_CORE_UPGRADE_MISSING_TOOLS = frozenset(
    {"vrc_set_material_texture"}
)
PREVIOUS_CORE_TOOL_NAMES = EXPECTED_TOOL_NAMES - PREVIOUS_CORE_UPGRADE_MISSING_TOOLS
PREVIOUS_CORE_TOOL_COUNT = len(PREVIOUS_CORE_TOOL_NAMES)

# These are the only packaged descriptors that Core permits on the direct
# read lane. Every other tool requires an App preview/safety/write capability,
# even when its name sounds observational.
READ_ONLY_TOOL_NAMES = frozenset(
    {
        "vrc_find_assets",
        "vrc_get_asset_info",
        "vrc_get_compile_errors",
        "vrc_get_gameobject",
        "vrc_get_property",
        "vrc_inspect_skinned_mesh_bone_usage",
        "vrc_inspect_skinned_mesh_deformation",
        "vrc_inspect_modular_avatar_component",
        "vrc_inspect_primitive_basis_fixture",
        "vrc_poll_job",
        "vrc_read_avatar_descriptor",
        "vrc_read_vrchat_sdk_builder_alerts",
        "vrc_avatar_upload_readiness",
        "vrc_scan_inbound_reference_closure",
    }
)

# Tools visible before the agent explicitly enters execution mode. The extra
# names are mixed-capability inspectors whose direct Core lane accepts only an
# exact no-write payload; output-producing variants remain App-approved.
PLANNING_TOOL_NAMES = READ_ONLY_TOOL_NAMES | frozenset(
    {
        "vrc_capture_scene_view",
        "vrc_export_blendshapes",
        "vrc_scan_animation_bindings",
        "vrc_scan_avatar_controls",
        "vrc_scan_avatar_items",
        "vrc_scan_avatar_materials",
        "vrc_scan_avatar_parameters",
        "vrc_scan_avatar_performance",
        "vrc_scan_fx_animator",
        "vrc_scan_inbound_reference_closure",
        "vrc_scan_thry_avatar_performance",
        "vrc_scan_wardrobe",
    }
)

if len(EXPECTED_TOOL_NAMES) != EXPECTED_TOOL_COUNT:  # pragma: no cover - source-contract invariant.
    raise RuntimeError("VRCForge Unity MCP tool contract is incomplete.")
if len(PREVIOUS_CORE_TOOL_NAMES) != PREVIOUS_CORE_TOOL_COUNT:  # pragma: no cover
    raise RuntimeError("VRCForge previous Core upgrade contract is incomplete.")
if not READ_ONLY_TOOL_NAMES < EXPECTED_TOOL_NAMES:  # pragma: no cover - source-contract invariant.
    raise RuntimeError("VRCForge Unity MCP read-only contract is invalid.")
if not READ_ONLY_TOOL_NAMES < PLANNING_TOOL_NAMES < EXPECTED_TOOL_NAMES:  # pragma: no cover
    raise RuntimeError("VRCForge Unity MCP planning exposure contract is invalid.")
