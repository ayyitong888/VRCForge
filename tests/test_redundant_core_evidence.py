"""Regression checks for repeated Core evidence reads in one call."""
from pathlib import Path

from test_curve_fx_authoring_runtime_contract import method


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def test_single_material_slot_apply_uses_the_resolved_scene_evidence():
    source = _source("Assets/VRCForge/Editor/RendererMaterialSlotTool.cs")
    handle = method(source, "public static object HandleCommand")
    before_mutation = handle[:handle.index("Undo.IncrementCurrentGroup();")]
    assert "var current = SceneObjectCopyCore.ResolveSavedScene" not in before_mutation
    apply_fields = method(source, "private static void RequireApplyFields(")
    assert "RequireEqual(s.FileDigest, p, \"expectedSceneFileDigest\")" in apply_fields


def test_batch_apply_builds_the_current_plan_once_before_mutation():
    source = _source("Assets/VRCForge/Editor/Generic/UnityRendererMaterialSlotsBatch.cs")
    handle = method(source, "internal static object HandleCommand")
    assert handle.count("Build(rows)") == 1
    assert "Verify(request[\"expectedBatchPlan\"], plan.Evidence)" in handle


def test_component_feature_preview_reuses_the_verified_snapshot():
    source = _source("Assets/VRCForge/Editor/ComponentFeatureWriterTool.cs")
    handle = method(source, "public static object HandleCommand")
    assert "snapshot.ToPayload(snapshot.Scene)" in handle
    assert "snapshot.ToPayload(ComponentFeatureWriteCore.ResolveSavedScene" not in handle


def test_execution_target_digest_is_not_a_format_only_gate():
    source = _source("Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs")
    validate = method(source, "private static bool ValidateExecutionTargetContext(")
    assert 'IsLowerHex(RequiredBoundedString(context, "executionTargetDigest", 64), 64)' not in validate
    assert 'projectId = RequiredBoundedString(project, "projectId", 64)' in validate
    assert 'string.Equals(projectId, descriptorProjectId, StringComparison.Ordinal)' in validate


def test_descriptor_reuses_project_id_calculated_for_current_root():
    source = _source("Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs")
    descriptor = method(source, "private static void WriteDescriptor(")
    assert descriptor.count("ComputeProjectId(root)") == 1
    assert '["projectId"] = descriptorProjectId' in descriptor
