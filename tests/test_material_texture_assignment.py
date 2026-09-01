from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import dashboard_server
import unity_mcp_tool_contract
from agent_gateway import (
    canonical_unity_read_tool_input_schema,
    canonical_unity_write_tool_input_schema,
)
from material_texture_assignment import (
    APPROVAL_PREVIEW_SCHEMA,
    ASSIGNMENT_SCHEMA,
    MaterialTextureAssignmentError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    validate_apply_result,
)


def _wrapper() -> dict[str, object]:
    return {
        "projectPath": str(Path.cwd()),
        "toolName": "vrc_set_material_texture",
        "arguments": {
            "materialAssetPath": "Assets/Avatar/Face.mat",
            "propertyName": "_MainTex",
            "textureAssetPath": "Assets/Avatar/FaceFixed.png",
        },
    }


def _preview() -> dict[str, object]:
    return {
        "schema": ASSIGNMENT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "wouldChange": True,
        "saved": False,
        "persistedReadback": False,
        "projectPath": str(Path.cwd()),
        "materialAssetPath": "Assets/Avatar/Face.mat",
        "materialAssetGuid": "a" * 32,
        "materialFileDigestBefore": "b" * 64,
        "materialFileDigestAfter": "b" * 64,
        "propertyName": "_MainTex",
        "beforeTextureAssetPath": "Assets/Avatar/FaceOriginal.png",
        "beforeTextureAssetGuid": "c" * 32,
        "textureAssetPath": "Assets/Avatar/FaceFixed.png",
        "textureAssetGuid": "d" * 32,
        "textureFileDigest": "e" * 64,
        "afterTextureAssetPath": "Assets/Avatar/FaceFixed.png",
        "afterTextureAssetGuid": "d" * 32,
    }


def _apply() -> dict[str, object]:
    result = _preview()
    result.update(
        preview=False,
        changed=True,
        saved=True,
        persistedReadback=True,
        committed=True,
        commitState="committed",
        materialFileDigestAfter="f" * 64,
    )
    return result


def test_wrapper_normalizes_one_flat_material_texture_slot_assignment() -> None:
    wrapper = build_wrapper_arguments(
        {
            "projectPath": "D:/Unity/Avatar",
            "materialAssetPath": "Assets/Avatar/Face.mat",
            "propertyName": "_MainTex",
            "textureAssetPath": "Assets/Avatar/FaceFixed.png",
        }
    )

    assert wrapper == {
        "projectPath": "D:/Unity/Avatar",
        "toolName": "vrc_set_material_texture",
        "arguments": {
            "materialAssetPath": "Assets/Avatar/Face.mat",
            "propertyName": "_MainTex",
            "textureAssetPath": "Assets/Avatar/FaceFixed.png",
        },
    }


def test_preview_removes_caller_preconditions_and_is_strictly_read_only() -> None:
    preview = build_preview_arguments(
        {
            "materialAssetPath": "Assets/Avatar/Face.mat",
            "propertyName": "_MainTex",
            "textureAssetPath": "Assets/Avatar/FaceFixed.png",
            "expectedMaterialFileDigest": "spoofed",
            "saveAssets": True,
            "unexpected": "must not cross",
        }
    )

    assert preview == {
        "materialAssetPath": "Assets/Avatar/Face.mat",
        "propertyName": "_MainTex",
        "textureAssetPath": "Assets/Avatar/FaceFixed.png",
        "preview": True,
        "saveAssets": False,
    }


def test_authoritative_preview_freezes_exact_material_texture_and_previous_slot() -> None:
    canonical, preview = bind_authoritative_preview(_wrapper(), _preview())
    nested = canonical["arguments"]

    assert preview["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert preview["materialAssetPath"] == "Assets/Avatar/Face.mat"
    assert preview["textureAssetPath"] == "Assets/Avatar/FaceFixed.png"
    assert nested["expectedProjectPath"] == str(Path.cwd())
    assert nested["expectedMaterialAssetGuid"] == "a" * 32
    assert nested["expectedMaterialFileDigest"] == "b" * 64
    assert nested["expectedBeforeTextureAssetPath"] == "Assets/Avatar/FaceOriginal.png"
    assert nested["expectedBeforeTextureAssetGuid"] == "c" * 32
    assert nested["expectedTextureAssetGuid"] == "d" * 32
    assert nested["expectedTextureFileDigest"] == "e" * 64
    assert nested["preview"] is False
    assert nested["saveAssets"] is True


@pytest.mark.parametrize(
    "mutator",
    (
        lambda result: result.update(materialAssetPath="../Face.mat"),
        lambda result: result.update(materialAssetGuid="invalid"),
        lambda result: result.update(propertyName="_Unexpected"),
        lambda result: result.update(textureAssetPath="Packages/Other/Face.png"),
        lambda result: result.update(textureFileDigest="f" * 63),
        lambda result: result.update(changed=True),
        lambda result: result.update(saved=True),
        lambda result: result.update(materialFileDigestAfter="f" * 64),
        lambda result: result.update(afterTextureAssetGuid="f" * 32),
    ),
)
def test_authoritative_preview_rejects_spoofed_or_mutating_receipts(mutator) -> None:
    result = _preview()
    mutator(result)

    with pytest.raises(MaterialTextureAssignmentError):
        bind_authoritative_preview(_wrapper(), result)


def test_apply_requires_persisted_exact_material_and_texture_readback() -> None:
    canonical, _ = bind_authoritative_preview(_wrapper(), _preview())

    result = validate_apply_result(canonical["arguments"], _apply())

    assert result["persistedReadback"] is True
    assert result["afterTextureAssetGuid"] == "d" * 32


@pytest.mark.parametrize(
    "mutator",
    (
        lambda result: result.update(preview=True),
        lambda result: result.update(saved=False),
        lambda result: result.update(persistedReadback=False),
        lambda result: result.update(committed=False),
        lambda result: result.update(afterTextureAssetGuid="f" * 32),
        lambda result: result.update(materialFileDigestBefore="f" * 64),
        lambda result: result.update(textureFileDigest="f" * 64),
    ),
)
def test_apply_rejects_unverified_or_drifted_persistence(mutator) -> None:
    canonical, _ = bind_authoritative_preview(_wrapper(), _preview())
    result = _apply()
    mutator(result)

    with pytest.raises(MaterialTextureAssignmentError):
        validate_apply_result(canonical["arguments"], result)


def test_material_texture_tool_is_shared_supervised_and_execution_only() -> None:
    name = "vrcforge_set_material_texture"
    gateway = dashboard_server.AGENT_GATEWAY
    handler = gateway._write_handlers[name]

    assert handler.pre_write_checkpoint_required is True
    assert handler.requires_approved_execution_context is True
    assert gateway.external_mcp_tool_block_for_name(name, write=True) == "materials"
    schema = canonical_unity_write_tool_input_schema(name)
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "projectPath",
        "materialAssetPath",
        "propertyName",
        "textureAssetPath",
    ]
    assert "_MainTex" in schema["properties"]["propertyName"]["enum"]
    assert canonical_unity_read_tool_input_schema(
        "vrcforge_preview_material_texture_assignment"
    ) == schema

    planning = dashboard_server._RuntimePlannerCatalog().read("planning")
    execution = dashboard_server._RuntimePlannerCatalog().read("execution")
    assert name not in {tool.runtime_name for tool in planning.visible_tools}
    tool = next(item for item in execution.visible_tools if item.runtime_name == name)
    assert tool.block == "unity/materials"
    assert "vrcforge_preview_material_texture_assignment" in gateway._tools
    assert "vrc_set_material_texture" in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
    assert unity_mcp_tool_contract.TOOL_CONTRACT_VERSION == "86"
    assert unity_mcp_tool_contract.EXPECTED_TOOL_COUNT == 85


def test_csharp_texture_tool_guards_property_and_rolls_back_failed_mutations() -> None:
    source = Path("Assets/VRCForge/Editor/MaterialShaderTool.cs").read_text(encoding="utf-8")

    assert 'toolId: "vrc_set_material_texture"' in source
    assert '"_MainTex"' in source
    assert "material.HasProperty(propertyName)" in source
    assert "material.GetTexturePropertyNames()" in source
    assert "AssetDatabase.LoadAssetAtPath<Texture2D>(textureAssetPath)" in source
    assert "material.SetTexture(propertyName, texture)" in source
    assert "AssetDatabase.SaveAssets();" in source
    assert "persisted.GetTexture(propertyName)" in source
    assert "RestoreTexturePreState" in source
    assert 'commitState = restored ? "rolled_back" : "unknown"' in source
