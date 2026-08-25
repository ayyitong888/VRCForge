from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tool_block(source: str, class_name: str, next_marker: str | None = None) -> str:
    start = source.index(f"public static class {class_name}")
    end = source.index(next_marker, start) if next_marker else len(source)
    return source[start:end]


def test_set_property_returns_persisted_before_after_and_bounded_affected() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(source, "SetPropertyTool")

    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("var readbackValue = ComponentCrudCore.GetMemberValue")
    assert save_index < readback_index
    assert "before = ComponentCrudCore.DescribeValue(oldValue)" in block
    assert "after = ComponentCrudCore.DescribeValue(readbackValue)" in block
    assert "after = ComponentCrudCore.DescribeValue(unchangedReadbackValue)" in block
    assert "items = new[] { goPath }" in block
    assert "handle = objectId" in block


def test_duplicate_project_asset_returns_fresh_readback_and_bounded_affected() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs"
    ).read_text(encoding="utf-8")
    block = _tool_block(source, "DuplicateProjectAssetTool")

    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("createdEvidence = ReadCreatedEvidenceWithRetry")
    assert save_index < readback_index
    assert "before = beforePayload" in block
    assert "after = afterPayload" in block
    assert "fileDigest = createdEvidence.File.Digest" in block
    assert "items = affectedItems.Take(20).ToArray()" in block
    assert "handle = createdEvidence.Guid" in block


def test_material_shader_returns_disk_readback_and_renderer_impact() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/MaterialShaderTool.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(source, "MaterialShaderTool", "public static class MaterialTextureTool")

    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("AssetDatabase.LoadAssetAtPath<Material>")
    assert save_index < readback_index
    assert "before = beforePayload" in block
    assert "after = afterPayload" in block
    assert "shader = readbackShader" in block
    assert "count = sharedImpact.loadedRendererSlotCount" in block
    assert "items = sharedImpact.loadedRendererSlots.Take(20).ToArray()" in block
    assert "handle = materialEvidence.assetGuid" in block


def test_parameter_optimization_returns_reimported_before_after() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/AvatarParameterWriter.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(
        source,
        "AvatarParameterOptimizationApplier",
        "public static class AvatarParameterRollbackTool",
    )

    save_index = block.index("AssetDatabase.SaveAssets();")
    import_index = block.index("ImportAssetOptions.ForceSynchronousImport")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>")
    assert save_index < import_index < readback_index
    assert "before.Add(DescribeParameter(parameter))" in block
    assert "before," in block
    assert "after," in block
    assert "items = after.Take(20).ToArray()" in block
    assert "handle = AssetDatabase.AssetPathToGUID(assetPath)" in block


def test_parameter_rollback_returns_reimported_before_after() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/AvatarParameterWriter.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(source, "AvatarParameterRollbackTool")

    save_index = block.index("AssetDatabase.SaveAssets();")
    import_index = block.index("ImportAssetOptions.ForceSynchronousImport")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>")
    assert save_index < import_index < readback_index
    assert "var before =" in block
    assert "var after = readbackAsset.parameters" in block
    assert "items = affectedNames.Take(20).ToArray()" in block
    assert "handle = AssetDatabase.AssetPathToGUID(assetPath)" in block


def test_manage_expression_parameters_returns_reimported_state() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs"
    ).read_text(encoding="utf-8")
    block = _tool_block(
        source,
        "ManageExpressionParametersTool",
        "public static class ManageExpressionMenuTool",
    )

    save_index = block.index("AssetDatabase.SaveAssets();")
    import_index = block.index("ImportAssetOptions.ForceSynchronousImport")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>")
    assert save_index < import_index < readback_index
    assert "var before = DescribeParameters(asset)" in block
    assert "var after = DescribeParameters(readbackAsset)" in block
    assert "items = affectedNames.Take(20).ToArray()" in block
    assert "handle = AssetDatabase.AssetPathToGUID(assetPath)" in block


def test_write_avatar_descriptor_returns_persisted_descriptor_state() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs"
    ).read_text(encoding="utf-8")
    block = _tool_block(
        source,
        "WriteAvatarDescriptorTool",
        "public static class WriteAnimationCurveTool",
    )

    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("var readbackDescriptor =")
    assert save_index < readback_index
    assert "before = JToken.Parse(beforeJson)" in block
    assert "after = JToken.Parse(EditorJsonUtility.ToJson(readbackDescriptor))" in block
    assert "after = JToken.Parse(unchangedJson)" in block
    assert "items = plan.changedFields.Take(20).ToArray()" in block
    assert "handle = descriptorGlobalObjectId" in block


def test_export_blendshapes_returns_fresh_file_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/BlendshapeExporter.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(source, "BlendshapeExporter")

    write_index = block.index("File.WriteAllText")
    save_index = block.index("AssetDatabase.SaveAssets();", write_index)
    readback_index = block.index("var after = ReadFileSnapshot", save_index)
    assert write_index < save_index < readback_index
    assert "var before = ReadFileSnapshot" in block
    assert "before," in block
    assert "after," in block
    assert "items = new[] { exportResult.outputPath }" in block
    assert "handle = exportResult.outputPath" in block
