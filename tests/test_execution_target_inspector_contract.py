from __future__ import annotations

import re
from pathlib import Path

import unity_mcp_tool_contract as contract


ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = (ROOT / "Assets" / "VRCForge" / "Editor" / "ExecutionTargetInspector.cs").read_text(encoding="utf-8-sig")
CORE_SERVER = (ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8-sig")


def test_execution_target_tool_is_fixed_read_only_core_contract() -> None:
    assert contract.TOOL_CONTRACT_VERSION == "91"
    assert contract.EXPECTED_TOOL_COUNT == 92
    assert "vrc_get_execution_targets" in contract.EXPECTED_TOOL_NAMES
    assert "vrc_get_execution_targets" in contract.READ_ONLY_TOOL_NAMES
    assert "vrc_get_execution_targets" not in contract.PREVIOUS_CORE_TOOL_NAMES
    assert "vrc_set_renderer_material_slot" not in contract.PREVIOUS_CORE_TOOL_NAMES
    assert len(contract.PREVIOUS_CORE_TOOL_NAMES) == 89
    assert '{ "vrc_get_execution_targets", "VRCForge.Editor.ExecutionTargetInspector" }' in (
        ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs"
    ).read_text(encoding="utf-8-sig")


def test_inspector_emits_complete_identity_fields_and_never_mutates_scene() -> None:
    for field in (
        '"vrcforge.execution_target.v1"', '"projectId"', '"unityPid"',
        '"processStartTime"', '"coreInstanceId"', '"assetPath"',
        '"absolutePath"', '"guid"', '"revision"', '"digest"',
        '"globalObjectId"', '"exactHierarchyPath"', '"namespace"',
        '"resolutionCandidateCount"', '"ambiguous"',
        '"targets"',
    ):
        assert field in INSPECTOR
    assert "GlobalObjectId.GetGlobalObjectIdSlow" in INSPECTOR
    assert "GlobalObjectId.GlobalObjectIdentifierToObjectSlow" in INSPECTOR
    assert "Selection.activeGameObject" not in INSPECTOR
    assert "VRCForgeMcpCoreServer.CurrentInstanceId" in INSPECTOR
    assert "GetField(" not in INSPECTOR
    assert "File.GetLastWriteTimeUtc" in INSPECTOR
    assert "SHA256.Create" in INSPECTOR
    assert "EditorSceneManager.SaveScene" not in INSPECTOR
    assert "SceneManager.MoveGameObjectToScene" not in INSPECTOR
    assert "AssetDatabase.SaveAssets" not in INSPECTOR
    assert "explicitly requested Avatar" in INSPECTOR
    assert "parameters.avatarGlobalObjectId" in INSPECTOR
    assert '"execution_target_avatar_unavailable"' in INSPECTOR
    assert "ResolveObject(parameters.objectGlobalObjectId, avatar) == null" in INSPECTOR


def test_component_scope_supports_bounded_avatar_discovery_with_exact_targets() -> None:
    assert 'public int? maxItems' in INSPECTOR
    assert 'public int? offset' in INSPECTOR
    assert 'DiscoverComponents(avatar, componentType, parameters.componentType)' in INSPECTOR
    assert '"totalCandidateCount"' in INSPECTOR
    assert '"hasMore"' in INSPECTOR
    assert '"complete"' in INSPECTOR
    assert 'DescribeGameObject(component.gameObject)' in INSPECTOR
    assert 'DescribeComponent(component)' in INSPECTOR
    assert 'execution_target_avatar_required' in INSPECTOR
    assert 'execution_target_paging_invalid' in INSPECTOR
    assert 'candidateSetHash' in INSPECTOR
    assert 'nextOffset' in INSPECTOR
    assert 'ComputeCandidateSetHash(allComponents)' in INSPECTOR
    assert 'ThenBy(component => GlobalObjectId.GetGlobalObjectIdSlow(component).ToString()' in INSPECTOR


def test_inspector_is_discoverable_by_the_existing_editor_registry_contract() -> None:
    declaration = re.search(
        r'\[VRCForgeCommand\([\s\S]{0,500}?toolId:\s*"vrc_get_execution_targets"[\s\S]{0,500}?\)\]\s*public static class ExecutionTargetInspector',
        INSPECTOR,
    )
    assert declaration
    assert 'Access = VRCForgeCommandAccess.ReadOnly' in declaration.group(0)
    assert "SnapshotExact" in CORE_SERVER


def test_core_validator_allows_exact_standalone_object_but_keeps_avatar_guard() -> None:
    block = CORE_SERVER[CORE_SERVER.index("private static bool ValidateExecutionTargetContext"):CORE_SERVER.index("private static bool ValidateExecutionTargetScene")]
    assert '(scope == "object" || scope == "component") && target["avatar"] != null' in block
    assert 'targetObject.scene.path.Replace' in block
    assert 'avatarObject != null && targetObject != avatarObject' in block
