"""The fixed public VRCForge Unity MCP tool-name contract."""

from __future__ import annotations

import re
from typing import Any

CORE_IDENTITY = "vrcforge.unity-core"
HANDSHAKE_PROTOCOL = "vrcforge.core-handshake.v1"
PRODUCT_VERSION = "1.10.1"
TOOL_CONTRACT_VERSION = "191"
PREVIOUS_CORE_TOOL_CONTRACT_VERSION = "190"

EXPECTED_TOOL_NAMES = frozenset(
    {
        "vrc_inspect_editor_capabilities", "vrc_inspect_material_properties", "vrc_execute_unity_editor_menu",
        "vrc_start_runtime_observation", "vrc_get_runtime_observation",
        "vrc_add_component", "vrc_add_modular_avatar_component", "vrc_add_outfit_part", "vrc_add_wardrobe_outfit",
        "vrc_apply_blendshapes", "vrc_apply_clothing_fx", "vrc_apply_material_tuning", "vrc_apply_parameter_optimization",
        "vrc_atomic_reference_rename", "vrc_avatar_upload_readiness", "vrc_build_and_upload_avatar", "vrc_build_parameter_bit_packed_clone", "vrc_build_test_avatar", "vrc_capture_scene_view",
        "vrc_configure_aao_merge_physbone", "vrc_convert_unity_constraint", "vrc_create_component_feature", "vrc_create_gameobject", "vrc_create_safe_backup", "vrc_delete_gameobject",
        "vrc_duplicate_scene_object", "vrc_duplicate_project_asset", "vrc_relocate_generated_assets", "vrc_duplicate_scene_asset", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control",
        "vrc_ensure_expression_parameter", "vrc_export_blendshapes", "vrc_export_vrm", "vrc_find_assets",
        "vrc_get_asset_info", "vrc_get_compile_errors", "vrc_get_ndmf_errors", "vrc_get_execution_targets", "vrc_get_gameobject", "vrc_get_property", "vrc_gesture_manager_enter_play_mode", "vrc_gesture_manager_set_parameter", "vrc_import_unitypackage",
        "vrc_inspect_modular_avatar_component", "vrc_inspect_primitive_basis_fixture", "vrc_inspect_skinned_mesh_bone_usage", "vrc_inspect_skinned_mesh_deformation", "vrc_remap_skinned_mesh_bone", "vrc_instantiate_prefab",
        "vrc_manage_expression_menu", "vrc_manage_expression_parameters", "vrc_manage_fx_animator", "vrc_manage_wardrobe",
        "vrc_poll_job", "vrc_prepare_checkpoint", "vrc_read_avatar_descriptor", "vrc_read_vrchat_sdk_builder_alerts", "vrc_refresh_asset_database",
        "vrc_reload_after_checkpoint_restore", "vrc_reload_primitive_basis_fixture", "vrc_remove_component",
        "vrc_rename_gameobject", "vrc_reparent_gameobject", "vrc_restore_safe_backup", "vrc_revert_removed_component", "vrc_rollback_avatar_parameters",
        "vrc_save_scene_object_as_prefab", "vrc_save_current_scene", "vrc_save_new_scene", "vrc_scene_save", "vrc_scene_transition", "vrc_select_scene_object", "vrc_scan_animation_bindings", "vrc_scan_avatar_controls", "vrc_scan_avatar_items",
        "vrc_scan_avatar_materials", "vrc_scan_avatar_parameters", "vrc_scan_avatar_performance", "vrc_scan_fx_animator",
        "vrc_scan_inbound_reference_closure", "vrc_scan_thry_avatar_performance", "vrc_scan_wardrobe", "vrc_set_constraint_sources", "vrc_set_gameobject_active",
        "vrc_set_material_shader", "vrc_set_material_texture", "vrc_set_renderer_material_slot", "vrc_flatten_material_variant", "vrc_set_play_mode", "vrc_set_property", "vrc_set_texture_import_settings", "vrc_setup_outfit", "vrc_texture_patch",
        "vrc_toggle_scene_object", "vrc_unpack_prefab", "vrc_user_adjustment_handoff", "vrc_write_animation_curve", "vrc_write_avatar_descriptor",
        "vrc_list_user_tools", "vrc_invoke_user_tool",
    }
)
SCAN_SCOPE_TOOL_NAMES = frozenset({"vrc_capture_scan_scope", "vrc_validate_scan_scope"})
EXPECTED_TOOL_COUNT = 101

# Contract revisions describe the discovered tool surface; protocol-range
# negotiation decides whether the App and Core can communicate. Revision 191
# adds NDMF error inspection to the revision-190 surface.
PREVIOUS_CORE_UPGRADE_MISSING_TOOLS = frozenset(
    {"vrc_get_ndmf_errors"}
)
PREVIOUS_CORE_TOOL_NAMES = EXPECTED_TOOL_NAMES - PREVIOUS_CORE_UPGRADE_MISSING_TOOLS
PREVIOUS_CORE_TOOL_COUNT = len(PREVIOUS_CORE_TOOL_NAMES)

# These are the only packaged descriptors that Core permits on the direct
# read lane. Every other tool requires an App preview/safety/write capability,
# even when its name sounds observational.
READ_ONLY_TOOL_NAMES = frozenset(
    {
        "vrc_inspect_editor_capabilities", "vrc_inspect_material_properties",
        "vrc_get_runtime_observation",
        "vrc_find_assets",
        "vrc_get_asset_info",
        "vrc_get_compile_errors",
        "vrc_get_execution_targets",
        "vrc_get_ndmf_errors",
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
        "vrc_list_user_tools",
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


def is_direct_read_call(tool_name: str, arguments: dict[str, Any]) -> bool:
    """Mirror Core's strict direct-read contract, never grant write authority.

    Mixed tools are reads only for the complete no-output/no-refresh payload
    accepted by VRCForgeMcpCoreServer.IsStrictNoWritePayloadRead. Core validates
    again at dispatch. Unknown fields and write variants retain exact claims.
    """
    if tool_name in READ_ONLY_TOOL_NAMES:
        return True
    if not isinstance(arguments, dict):
        return False
    a = arguments

    def exact(*keys: str) -> bool:
        return set(a) == set(keys)

    def string(key: str) -> bool:
        return isinstance(a.get(key), str)

    def boolean(key: str) -> bool:
        return type(a.get(key)) is bool

    def integer(key: str, minimum: int, maximum: int) -> bool:
        return type(a.get(key)) is int and minimum <= a[key] <= maximum

    def strings(key: str, maximum: int = 2000) -> bool:
        return isinstance(a.get(key), list) and len(a[key]) <= maximum and all(isinstance(v, str) for v in a[key])

    def digest(key: str) -> bool:
        return string(key) and re.fullmatch(r"[0-9a-fA-F]{64}", a[key]) is not None

    if tool_name == "vrc_export_blendshapes":
        return exact("outputPath", "refreshAssets", "returnPayloadOnly") and a.get("outputPath") == "" and a.get("refreshAssets") is False and a.get("returnPayloadOnly") is True
    if tool_name in {"vrc_scan_avatar_controls", "vrc_scan_avatar_parameters", "vrc_scan_wardrobe", "vrc_scan_thry_avatar_performance"}:
        return exact("avatarPath", "outputPath") and string("avatarPath") and a.get("outputPath") == ""
    if tool_name == "vrc_scan_avatar_materials":
        shape = exact("avatarPath", "outputPath", "refreshAssets") or (
            exact("avatarPath", "outputPath", "refreshAssets", "includeTextures") and boolean("includeTextures")) or (
            exact("avatarPath", "outputPath", "refreshAssets", "materialIds", "includeTextures") and strings("materialIds") and boolean("includeTextures"))
        return shape and string("avatarPath") and a.get("outputPath") == "" and a.get("refreshAssets") is False
    if tool_name == "vrc_scan_avatar_items":
        required = {"avatarPath", "outputPath", "maxItems", "refreshAssets"}
        return required <= set(a) <= required | {"offset", "expectedSnapshotDigest"} and string("avatarPath") and a.get("outputPath") == "" and integer("maxItems", 1, 2000) and (
            "offset" not in a or integer("offset", 0, 2**31 - 1)) and (
            "expectedSnapshotDigest" not in a or digest("expectedSnapshotDigest")) and a.get("refreshAssets") is False
    if tool_name == "vrc_scan_fx_animator":
        return exact("avatarPath", "outputPath", "controllerPath", "refreshAssets") and string("avatarPath") and a.get("outputPath") == "" and string("controllerPath") and a.get("refreshAssets") is False
    if tool_name == "vrc_scan_animation_bindings":
        selection_fields = {"bindingView", "bindingSelectors", "bindingOffset", "bindingLimit", "clipOffset", "keyOffset", "maxTotalKeys", "expectedSnapshotDigest"}
        if selection_fields.intersection(a):
            if a.get("bindingView", "details") not in ("summary", "index", "details"):
                return False
            selectors = a.get("bindingSelectors", [])
            if not isinstance(selectors, list) or len(selectors) > 32:
                return False
            for row in selectors:
                if not isinstance(row, dict) or not row or not set(row) <= {"path", "propertyName", "componentType"} or not {"path", "propertyName"}.intersection(row):
                    return False
                if any(not isinstance(v, str) or len(v.encode("utf-16-le")) // 2 > 1024 or (k != "path" and not v) for k, v in row.items()):
                    return False
            for key, lower, upper in (("bindingOffset", 0, 2**31-1), ("bindingLimit", 1, 256), ("clipOffset", 0, 2**31-1), ("keyOffset", 0, 2**31-1), ("maxTotalKeys", 1, 4096)):
                if key in a and not integer(key, lower, upper):
                    return False
            if "expectedSnapshotDigest" in a and not digest("expectedSnapshotDigest"):
                return False
            a = {k: v for k, v in a.items() if k not in selection_fields}
        required = {"avatarPath", "outputPath", "controllerPath", "clipPaths", "includeAllProjectClips", "includeBindingDetails", "maxClips", "refreshAssets"}
        return required <= set(a) <= required | {"maxKeysPerBinding"} and (
            "maxKeysPerBinding" not in a or integer("maxKeysPerBinding", 1, 2000)) and string("avatarPath") and a.get("outputPath") == "" and string("controllerPath") and strings("clipPaths") and boolean("includeAllProjectClips") and boolean("includeBindingDetails") and integer("maxClips", 1, 2000) and a.get("refreshAssets") is False
    if tool_name == "vrc_scan_avatar_performance":
        return exact("avatarPath", "outputPath", "isMobile") and string("avatarPath") and a.get("outputPath") == "" and boolean("isMobile")
    if tool_name == "vrc_capture_scene_view":
        shape = exact("statusOnly", "requirePlayMode") or (
            exact("statusOnly", "requirePlayMode", "captureMode") and string("captureMode") and a["captureMode"].strip().lower() in {"auto", "scene_view", "game_view"}) or (
            exact("statusOnly", "requirePlayMode", "avatarPath", "includeGestureManagerParameters", "gestureManagerParameterNames", "gestureManagerParameterPrefix") and string("avatarPath") and boolean("includeGestureManagerParameters") and strings("gestureManagerParameterNames", 128) and string("gestureManagerParameterPrefix") and len(a["gestureManagerParameterPrefix"].encode("utf-16-le")) // 2 <= 256)
        return shape and a.get("statusOnly") is True and boolean("requirePlayMode")
    return False

if len(EXPECTED_TOOL_NAMES) != EXPECTED_TOOL_COUNT:  # pragma: no cover - source-contract invariant.
    raise RuntimeError("VRCForge Unity MCP tool contract is incomplete.")
if len(PREVIOUS_CORE_TOOL_NAMES) != PREVIOUS_CORE_TOOL_COUNT:  # pragma: no cover
    raise RuntimeError("VRCForge previous Core upgrade contract is incomplete.")
if not READ_ONLY_TOOL_NAMES < EXPECTED_TOOL_NAMES:  # pragma: no cover - source-contract invariant.
    raise RuntimeError("VRCForge Unity MCP read-only contract is invalid.")
if not READ_ONLY_TOOL_NAMES < PLANNING_TOOL_NAMES < EXPECTED_TOOL_NAMES:  # pragma: no cover
    raise RuntimeError("VRCForge Unity MCP planning exposure contract is invalid.")
