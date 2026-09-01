from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = (
    ROOT
    / "Assets"
    / "VRCForge"
    / "Editor"
    / "Generic"
    / "RevertRemovedComponentTool.cs"
).read_text(encoding="utf-8-sig")


def test_removed_component_revert_is_one_exact_prefab_override_atom() -> None:
    assert 'toolId: "vrc_revert_removed_component"' in TOOL
    assert "when-to-use:" in TOOL
    assert "when-NOT-to-use:" in TOOL
    assert "Negative example:" in TOOL
    assert "gameObjectPath" in TOOL
    assert "sourceComponentGlobalObjectId" in TOOL
    assert "componentType" in TOOL
    assert "sourceComponentIndex" in TOOL
    assert "componentType must be the exact fully-qualified component type name" in TOOL
    assert "gameObjectPath must be an exact full hierarchy path" in TOOL
    assert "PrefabUtility.GetRemovedComponents(instanceRoot)" in TOOL
    assert "item.containingInstanceGameObject == target" in TOOL
    assert "string.Equals(item.SourceGlobalObjectId, sourceGlobalObjectId, StringComparison.Ordinal)" in TOOL
    assert "item.SourceComponentIndex == sourceComponentIndex" in TOOL
    assert "Multiple removed-component overrides matched" in TOOL


def test_preview_precedes_mutation_and_reports_source_and_count_identity() -> None:
    preview = TOOL.index("if (p.preview ?? false)")
    mutation = TOOL.index("PrefabUtility.RevertRemovedComponent(")

    assert preview < mutation
    assert 'preview = true' in TOOL[preview:mutation]
    assert 'mutationStarted = false' in TOOL[preview:mutation]
    assert 'commitState = "preview_only"' in TOOL[preview:mutation]
    assert "sourceAssetPath" in TOOL[preview:mutation]
    assert "sourceAssetGuid" in TOOL[preview:mutation]
    assert "sourceLocalFileId" in TOOL[preview:mutation]
    assert "componentCount" in TOOL
    assert "removedComponentCount" in TOOL
    assert "removedTypeCount" in TOOL


def test_apply_uses_official_revert_undo_save_and_persisted_exact_readback() -> None:
    assert "Undo.IncrementCurrentGroup();" in TOOL
    assert "Undo.SetCurrentGroupName(\"Revert VRCForge removed component override\");" in TOOL
    assert "InteractionMode.UserAction" in TOOL
    assert "ComponentCrudCore.SaveAndResolveScene(beforeScene)" in TOOL
    assert "SceneObjectCopyCore.ResolveUniqueGameObject(" in TOOL
    assert "afterComponents.Length != beforeComponentCount + 1" in TOOL
    assert "afterRemoved.TotalCount != beforeRemoved.TotalCount - 1" in TOOL
    assert "afterRemoved.TypeCount != beforeRemoved.TypeCount - 1" in TOOL
    assert "restoredComponents.Length != 1" in TOOL
    assert "afterScene.FileDigest == beforeScene.FileDigest" in TOOL
    assert 'persistedReadback = true' in TOOL
    assert 'commitState = "committed"' in TOOL


def test_failure_after_mutation_reverts_and_verifies_original_removed_override() -> None:
    assert "ComponentCrudCore.RestoreFailedMutation(" in TOOL
    assert "restoredObject.GetComponents(componentType).Length == beforeComponentCount" in TOOL
    assert "restoredRemoved.TotalCount == beforeRemoved.TotalCount" in TOOL
    assert "restoredRemoved.TypeCount == beforeRemoved.TypeCount" in TOOL
    assert "restoredRemoved.Match != null" in TOOL
    assert '"removed_component_revert_failed_after_mutation"' in TOOL
    assert "ComponentCrudCore.FailedMutationResult(" in TOOL


def test_tool_never_edits_or_applies_to_the_prefab_asset() -> None:
    assert "ApplyRemovedComponent" not in TOOL
    assert "SaveAsPrefabAsset" not in TOOL
    assert "SavePrefabAsset" not in TOOL
    assert "AssetDatabase.DeleteAsset" not in TOOL
    assert "DestroyImmediate" not in TOOL


def test_tool_is_in_the_exact_supervised_core_contract_and_preview_lane() -> None:
    contract = (
        ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs"
    ).read_text(encoding="utf-8-sig")
    server = (
        ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"
    ).read_text(encoding="utf-8-sig")
    python_contract = (ROOT / "unity_mcp_tool_contract.py").read_text(encoding="utf-8-sig")

    assert 'internal const string ToolContractVersion = "86";' in contract
    assert "internal const int ToolCount = 85;" in contract
    assert '{ "vrc_revert_removed_component", "VRCForge.Editor.RevertRemovedComponentTool" }' in contract
    preview_block = server[server.index("PreviewTools =") : server.index("SafetyControlTools =")]
    assert '"vrc_revert_removed_component"' in preview_block
    assert 'TOOL_CONTRACT_VERSION = "86"' in python_contract
    assert "EXPECTED_TOOL_COUNT = 85" in python_contract
    assert '"vrc_revert_removed_component"' in python_contract
