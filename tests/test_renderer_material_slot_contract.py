from pathlib import Path
import re

import pytest

from renderer_material_slot_assignment import (
    RendererMaterialSlotError,
    bind_authoritative_preview,
    build_wrapper_arguments,
    validate_apply_result,
)


ROOT = Path(__file__).parents[1]
EDITOR = ROOT / "Assets" / "VRCForge" / "Editor"
TOOL = EDITOR / "RendererMaterialSlotTool.cs"
CONTRACT = EDITOR / "MCP" / "VRCForgeMcpToolContract.cs"
SERVER = EDITOR / "MCP" / "VRCForgeMcpCoreServer.cs"


def read(path):
    return path.read_text(encoding="utf-8")


def test_raw_core_tool_is_in_exact_contract_and_preview_lane():
    contract = read(CONTRACT)
    server = read(SERVER)
    assert re.search(r' ToolCount = (?:9[5-9]|[1-9][0-9]{2,});', contract)
    assert re.search(r' ToolContractVersion = "[0-9]+";', contract)
    assert '{ "vrc_set_renderer_material_slot", "VRCForge.Editor.RendererMaterialSlotTool" }' in contract
    assert '"vrc_set_renderer_material_slot"' in server


def test_tool_is_generic_and_uses_stable_renderer_identity():
    tool = read(TOOL)
    assert 'toolId: "vrc_set_renderer_material_slot"' in tool
    assert 'RendererComponentIdentity.Create' in tool
    assert 'rendererComponentId' in tool
    assert 'SceneObjectCopyCore.ResolveSavedScene' in tool
    assert 'SceneObjectCopyCore.ResolveUniqueGameObject' in tool
    assert 'hierarchyPathIsDisplayOnly = true' in tool
    assert 'sharedMaterials' in tool


def test_apply_binds_scene_material_digests_and_persisted_readback():
    tool = read(TOOL)
    for field in ("expectedScenePath", "expectedSceneGuid", "expectedSceneHandle",
                  "expectedSceneFileDigest", "expectedBeforeMaterialAssetPath",
                  "expectedBeforeMaterialGuid", "expectedBeforeMaterialFileDigest",
                  "expectedBeforeMaterialShader", "expectedBeforeMaterialRenderQueue",
                  "expectedNewMaterialGuid", "expectedNewMaterialFileDigest"):
        assert field in tool
    assert 'EditorSceneManager.SaveScene' in tool
    assert 'AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)' in tool
    assert 'persistedScene.FileDigest' in tool
    assert 'persistedReadback' in tool
    assert 'Undo.RegisterCompleteObjectUndo' in tool
    assert 'VerifySlots' in tool


def test_material_path_is_assets_mat_main_persistent_and_reparse_checked():
    tool = read(TOOL)
    assert 'p.StartsWith("Assets/"' in tool
    assert 'p.EndsWith(".mat"' in tool
    assert 'AssetDatabase.IsMainAsset' in tool
    assert 'EditorUtility.IsPersistent' in tool
    assert 'FileAttributes.ReparsePoint' in tool
    assert 'FileAttributes.ReadOnly' in tool


def test_undo_does_not_claim_restored_without_saved_prestate_digest():
    tool = read(TOOL)
    rollback = tool.split("if (mutationStarted)", 1)[1].split("private static object Payload", 1)[0]
    assert "!EditorSceneManager.SaveScene(target.Scene.Scene)" in rollback
    assert "restoredTarget.Scene.FileDigest, beforeSceneDigest" in rollback
    assert rollback.index("!EditorSceneManager.SaveScene") < rollback.index("restored = true")


def _preview():
    return {
        "schema": "vrcforge.renderer_material_slot.v1",
        "preview": True,
        "mutationStarted": False,
        "applied": False,
        "committed": False,
        "sceneSaved": False,
        "persistedReadback": False,
        "rendererPath": "Root/Avatar/Body",
        "rendererComponentId": "a" * 64,
        "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
        "rendererComponentIndex": 0,
        "scenePath": "Assets/2.unity",
        "sceneGuid": "b" * 32,
        "sceneHandle": 7,
        "sceneFileDigest": "c" * 64,
        "slotIndex": 0,
        "beforeMaterial": {
            "name": "OpaqueGenerated",
            "shader": "Hidden/lilToonOutline",
            "renderQueue": 2000,
            "assetPath": "Assets/VRCForge/Generated/OpaqueGenerated.mat",
            "assetGuid": "d" * 32,
            "fileDigest": "e" * 64,
        },
        "newMaterial": {
            "name": "Expressions",
            "shader": "Hidden/lilToonTransparent",
            "renderQueue": 2459,
            "assetPath": "Assets/Voidcat/Sapphy/Materials/liltoon/Expressions.mat",
            "assetGuid": "f" * 32,
            "fileDigest": "1" * 64,
        },
        "newMaterialAssetPath": "Assets/Voidcat/Sapphy/Materials/liltoon/Expressions.mat",
        "newMaterialAssetGuid": "f" * 32,
        "newMaterialFileDigest": "1" * 64,
    }


def test_gateway_binds_authoritative_preview_to_exact_apply_fields():
    wrapper = build_wrapper_arguments(
        {
            "projectPath": "D:/UnityProject",
            "rendererPath": "Root/Avatar/Body",
            "slotIndex": 0,
            "newMaterialAssetPath": "Assets/Voidcat/Sapphy/Materials/liltoon/Expressions.mat",
        }
    )
    canonical, approval = bind_authoritative_preview(wrapper, _preview())
    arguments = canonical["arguments"]
    assert arguments["rendererComponentId"] == "a" * 64
    assert arguments["expectedSceneFileDigest"] == "c" * 64
    assert arguments["expectedBeforeMaterialFileDigest"] == "e" * 64
    assert arguments["expectedNewMaterialFileDigest"] == "1" * 64
    assert approval["freshReadbackRequired"] is True


def test_gateway_rejects_noop_or_non_fresh_apply_receipts():
    preview = _preview()
    preview["newMaterial"]["assetPath"] = preview["beforeMaterial"]["assetPath"]
    preview["newMaterial"]["assetGuid"] = preview["beforeMaterial"]["assetGuid"]
    preview["newMaterialAssetPath"] = preview["beforeMaterial"]["assetPath"]
    preview["newMaterialAssetGuid"] = preview["beforeMaterial"]["assetGuid"]
    wrapper = build_wrapper_arguments(
        {
            "rendererPath": "Root/Avatar/Body",
            "slotIndex": 0,
            "newMaterialAssetPath": preview["newMaterialAssetPath"],
        }
    )
    with pytest.raises(RendererMaterialSlotError, match="already uses"):
        bind_authoritative_preview(wrapper, preview)

    apply = _preview()
    apply.update(
        {
            "preview": False,
            "mutationStarted": True,
            "applied": True,
            "committed": True,
            "sceneSaved": True,
            "persistedReadback": True,
        }
    )
    arguments = {
        "rendererComponentId": "a" * 64,
        "slotIndex": 0,
        "newMaterialAssetPath": apply["newMaterialAssetPath"],
        "expectedSceneFileDigest": "c" * 64,
    }
    with pytest.raises(RendererMaterialSlotError, match="fresh saved-scene digest"):
        validate_apply_result(arguments, apply)
