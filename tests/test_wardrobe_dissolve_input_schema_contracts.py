"""Regression coverage for the public inputs used by wardrobe dissolve discovery."""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema, canonical_unity_write_tool_input_schema
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


def _assert_valid(schema: dict, value: dict) -> None:
    Draft202012Validator(schema).validate(value)


def _assert_invalid(schema: dict, value: dict) -> None:
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(value)


def test_wardrobe_dissolve_read_inputs_are_real_and_bounded() -> None:
    _assert_valid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_materials"], {
        "projectPath": "C:/Unity/Avatar", "avatarPath": "Avatar", "outputPath": "Assets/scan.json",
        "refreshAssets": False, "materialIds": ["mat-guid"], "includeTextures": False,
        "categoryOverrides": {"liltoon": "clothes"},
    })
    _assert_valid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_wardrobe"], {"avatarPath": "Avatar", "outputPath": ""})
    _assert_valid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_avatar_items"], {
        "avatarPath": "Avatar", "outputPath": "", "maxItems": 2000, "refreshAssets": False,
    })
    _assert_invalid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_avatar_items"], {"maxItems": 2001})
    _assert_valid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_find_assets"], {
        "query": "outfit", "typeName": "Prefab", "folder": "Assets/Outfits", "limit": 50,
    })
    _assert_invalid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_asset_info"], {})
    _assert_valid(UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_asset_info"], {"guid": "a" * 32})


def test_shader_and_clothing_inputs_keep_instruction_changes_locks_and_item_aliases() -> None:
    plan = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_plan_shader_tuning"]
    _assert_valid(plan, {"projectPath": "C:/Unity/Avatar", "instruction": "set dissolve", "lockedMaterials": ["m"]})
    _assert_invalid(plan, {"projectPath": "C:/Unity/Avatar", "request": "set dissolve"})
    preview = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_shader_apply"]
    _assert_valid(preview, {"projectPath": "C:/Unity/Avatar", "changes": [{"material_id": "m", "semantic_property": "x", "after": 1.0}]})
    clothing = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_apply_clothing_fx"]
    _assert_valid(clothing, {"projectPath": "C:/Unity/Avatar", "items": [{"displayName": "Hat", "sampleObjectPath": "Avatar/Hat"}]})
    _assert_invalid(clothing, {"projectPath": "C:/Unity/Avatar", "items": [{"unknown": "x"}]})


def test_project_asset_preview_and_apply_share_the_real_identity_fields() -> None:
    preview = canonical_unity_read_tool_input_schema("vrcforge_preview_project_asset_duplicate")
    write = canonical_unity_write_tool_input_schema("vrcforge_duplicate_project_asset")
    value = {"projectPath": "C:/Unity/Avatar", "sourceAssetPath": "Assets/A.mat", "destinationAssetPath": "Assets/VRCForgeGenerated/M.mat", "preview": True, "overwrite": False}
    target = {"schema": "vrcforge.execution_target.v1", "namespace": "vrcforge", "scope": "project", "project": {}, "editor": {}}
    _assert_valid(preview, {**value, "executionTarget": target})
    _assert_valid(write, {**value, "preview": False, "executionTarget": target})
    assert "executionTarget" in write["properties"]
    assert "executionTarget" in write["required"]
    _assert_invalid(preview, {**value, "overwrite": True})
