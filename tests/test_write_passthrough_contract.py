from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tool_block(source: str, class_name: str, next_marker: str | None = None) -> str:
    start = source.index(f"public static class {class_name}")
    end = source.index(next_marker, start) if next_marker else len(source)
    return source[start:end]


def _assert_expression_persistence_save_import_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8")
    start = source.index("internal static object SaveAndVerify(")
    save = source.index("AssetDatabase.SaveAssetIfDirty(asset);", start)
    reimport = source.index("AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport", save)
    readback = source.index("var actual = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path)", reimport)
    verify = source.index("JToken.DeepEquals(expected[path]", readback)
    assert start < save < reimport < readback < verify
    assert "Expression asset GUID changed" in source[verify:]
    assert "items = affectedPaths.Take(20).ToArray()" in source[verify:]
    assert "handle = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(root))" in source[verify:]


def test_set_property_returns_persisted_before_after_and_bounded_affected() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(source, "SetPropertyTool")

    assert "AssetDatabase.SaveAssets();" not in block
    save_index = block.index("ComponentCrudCore.SaveAndResolveScene(beforeScene)")
    readback_index = block.index("var readbackValue = ComponentCrudCore.GetMemberValue")
    assert save_index < readback_index
    helper_start = source.index("internal static SavedSceneSnapshot SaveAndResolveScene(")
    scene_save = source.index("EditorSceneManager.SaveScene(beforeScene.Scene)", helper_start)
    scene_readback = source.index("var afterScene = SceneObjectCopyCore.ResolveSavedScene(", scene_save)
    assert helper_start < scene_save < scene_readback
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

    assert "AssetDatabase.SaveAssets();" not in block
    save_index = block.index("AssetDatabase.SaveAssetIfDirty(copiedAsset);")
    readback_index = block.index("createdEvidence = ReadCreatedEvidenceWithRetry", save_index)
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

    save_index = block.index("AssetDatabase.SaveAssetIfDirty(target.material)")
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

    assert "AssetDatabase.SaveAssets();" not in block
    save_index = block.index("ExpressionWritePersistence.SaveAndVerify(")
    _assert_expression_persistence_save_import_readback()
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>")
    assert save_index < readback_index
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

    assert "AssetDatabase.SaveAssets();" not in block
    save_index = block.index("ExpressionWritePersistence.SaveAndVerify(")
    _assert_expression_persistence_save_import_readback()
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>")
    assert save_index < readback_index
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

    assert "AssetDatabase.SaveAssets();" not in block
    save_index = block.index("ExpressionWritePersistence.SaveAndVerify(persistenceBefore, asset, descriptor, false, false)")
    import_index = block.index("ImportAssetOptions.ForceSynchronousImport")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>")
    assert save_index < import_index < readback_index
    assert "var before = DescribeParameters(asset)" in block
    assert "var after = DescribeParameters(readbackAsset)" in block
    assert 'affected = JObject.FromObject(persistedReadback)["affected"]' in block
    _assert_expression_persistence_save_import_readback()


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
    assert "GlobalObjectId.GetGlobalObjectIdSlow(unchangedDescriptor)" in block
    assert "string.Equals(unchangedJson, beforeJson" in block
    assert "unchangedScene.FileDigest, beforeScene.FileDigest" in block
    assert block.count("verified = true") == 2
    assert block.count("saved = true") == 2
    assert block.count("readback = new { persisted = true") == 2
    assert block.count("count = plan.changedFields.Count") == 2
    assert "count = plan.changedFields.Length" not in block
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


def test_scan_avatar_materials_returns_fresh_file_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/ShaderMaterialScanner.cs").read_text(
        encoding="utf-8"
    )
    block = _tool_block(source, "ShaderMaterialScanner")

    write_index = block.index("File.WriteAllText")
    save_index = block.index("AssetDatabase.SaveAssets();", write_index)
    readback_index = block.index("var after = ReadFileSnapshot", save_index)
    assert write_index < save_index < readback_index
    assert "var before = ReadFileSnapshot" in block
    assert "before = beforeSnapshot" in block
    assert "after = afterSnapshot" in block
    assert "items = new[] { payload.outputPath }" in block
    assert "handle = payload.outputPath" in block


def test_manage_expression_menu_returns_reimported_state() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs"
    ).read_text(encoding="utf-8")
    block = _tool_block(
        source,
        "ManageExpressionMenuTool",
        "public static class ManageFxAnimatorTool",
    )

    assert "AssetDatabase.SaveAssets();" not in block
    save_index = block.index("ExpressionWritePersistence.SaveAndVerify(persistenceBefore, root, descriptor, rootWasMissing, true, target)")
    import_index = block.index("ImportAssetOptions.ForceSynchronousImport")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionsMenu>")
    assert save_index < import_index < readback_index
    assert "var before = DescribeMenu" in block
    assert "var after = DescribeMenu" in block
    assert 'affected = JObject.FromObject(persistedReadback)["affected"]' in block
    _assert_expression_persistence_save_import_readback()


def test_scan_animation_bindings_returns_fresh_file_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/AssetTools.cs").read_text(encoding="utf-8")
    block = _tool_block(source, "AssetTools")

    write_index = block.index("File.WriteAllText")
    save_index = block.index("AssetDatabase.SaveAssets();", write_index)
    readback_index = block.index("var after = ReadFileSnapshot", save_index)
    assert write_index < save_index < readback_index
    assert "var before = ReadFileSnapshot" in block
    assert "payload.before = beforeSnapshot" in block
    assert "payload.after = afterSnapshot" in block
    assert "items = new[] { payload.outputPath }" in block
    assert "handle = payload.outputPath" in block


def test_scan_avatar_items_returns_fresh_file_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/GameObjectTools.cs").read_text(encoding="utf-8")
    block = _tool_block(source, "GameObjectTools")
    write_index = block.index("File.WriteAllText")
    save_index = block.index("AssetDatabase.SaveAssets();", write_index)
    readback_index = block.index("var after = ReadFileSnapshot", save_index)
    assert write_index < save_index < readback_index
    assert "var before = ReadFileSnapshot" in block
    assert "payload.before = beforeSnapshot" in block
    assert "payload.after = afterSnapshot" in block
    assert "items = new[] { payload.outputPath }" in block
    assert "handle = payload.outputPath" in block


def test_scan_fx_animator_returns_fresh_file_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/ComponentTools.cs").read_text(encoding="utf-8")
    block = _tool_block(source, "ComponentTools")
    write_index = block.index("File.WriteAllText")
    save_index = block.index("AssetDatabase.SaveAssets();", write_index)
    readback_index = block.index("var after = ReadFileSnapshot", save_index)
    assert write_index < save_index < readback_index
    assert "var before = ReadFileSnapshot" in block
    assert "payload.before = beforeSnapshot" in block
    assert "payload.after = afterSnapshot" in block
    assert "items = new[] { payload.outputPath }" in block
    assert "handle = payload.outputPath" in block
