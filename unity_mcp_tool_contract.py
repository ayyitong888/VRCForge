"""The fixed public VRCForge Unity MCP tool-name contract."""

from __future__ import annotations


EXPECTED_TOOL_NAMES = frozenset(
    {
        "vrc_add_component", "vrc_add_modular_avatar_component", "vrc_add_outfit_part", "vrc_add_wardrobe_outfit",
        "vrc_apply_blendshapes", "vrc_apply_clothing_fx", "vrc_apply_material_tuning", "vrc_apply_parameter_optimization",
        "vrc_atomic_reference_rename", "vrc_build_parameter_bit_packed_clone", "vrc_capture_scene_view",
        "vrc_create_component_feature", "vrc_create_gameobject", "vrc_create_safe_backup", "vrc_delete_gameobject",
        "vrc_duplicate_scene_object", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control",
        "vrc_ensure_expression_parameter", "vrc_export_blendshapes", "vrc_export_vrm", "vrc_find_assets",
        "vrc_get_asset_info", "vrc_get_compile_errors", "vrc_get_gameobject", "vrc_get_property", "vrc_import_unitypackage",
        "vrc_inspect_modular_avatar_component", "vrc_inspect_primitive_basis_fixture", "vrc_instantiate_prefab",
        "vrc_manage_expression_menu", "vrc_manage_expression_parameters", "vrc_manage_fx_animator", "vrc_manage_wardrobe",
        "vrc_prepare_checkpoint", "vrc_read_avatar_descriptor", "vrc_refresh_asset_database",
        "vrc_reload_after_checkpoint_restore", "vrc_reload_primitive_basis_fixture", "vrc_remove_component",
        "vrc_rename_gameobject", "vrc_reparent_gameobject", "vrc_restore_safe_backup", "vrc_rollback_avatar_parameters",
        "vrc_save_scene_object_as_prefab", "vrc_scan_animation_bindings", "vrc_scan_avatar_controls", "vrc_scan_avatar_items",
        "vrc_scan_avatar_materials", "vrc_scan_avatar_parameters", "vrc_scan_avatar_performance", "vrc_scan_fx_animator",
        "vrc_scan_thry_avatar_performance", "vrc_scan_wardrobe", "vrc_set_constraint_sources", "vrc_set_gameobject_active",
        "vrc_set_material_shader", "vrc_set_property", "vrc_set_texture_import_settings", "vrc_setup_outfit",
        "vrc_toggle_scene_object", "vrc_unpack_prefab", "vrc_write_animation_curve", "vrc_write_avatar_descriptor",
    }
)
EXPECTED_TOOL_COUNT = 64

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
        "vrc_inspect_modular_avatar_component",
        "vrc_inspect_primitive_basis_fixture",
        "vrc_read_avatar_descriptor",
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
        "vrc_scan_thry_avatar_performance",
        "vrc_scan_wardrobe",
    }
)

if len(EXPECTED_TOOL_NAMES) != EXPECTED_TOOL_COUNT:  # pragma: no cover - source-contract invariant.
    raise RuntimeError("VRCForge Unity MCP tool contract is incomplete.")
if not READ_ONLY_TOOL_NAMES < EXPECTED_TOOL_NAMES:  # pragma: no cover - source-contract invariant.
    raise RuntimeError("VRCForge Unity MCP read-only contract is invalid.")
if not READ_ONLY_TOOL_NAMES < PLANNING_TOOL_NAMES < EXPECTED_TOOL_NAMES:  # pragma: no cover
    raise RuntimeError("VRCForge Unity MCP planning exposure contract is invalid.")
