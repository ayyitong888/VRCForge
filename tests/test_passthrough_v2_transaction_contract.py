from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_restore_safe_backup_returns_bounded_transaction_with_fresh_file_receipts() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/PrefabTools.cs").read_text(
        encoding="utf-8"
    )
    copy_index = source.index("File.Copy(backupFilePath, targetPath, true);")
    readback_index = source.index("item.after_sha256 = ComputeSha256(targetPath);", copy_index)
    assert copy_index < readback_index
    assert "catch (RestoreTransactionException ex)" in source
    assert "assets_touched = planned.Count" in source
    assert "items = transactionItems.Take(20).ToArray()" in source
    assert "handle = manifestPath" in source
    assert 'status = "succeeded"' in source
    assert 'status = "failed"' in source
    assert "rolled_back = false" in source


def test_create_safe_backup_returns_bounded_transaction_with_copied_file_readback() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/ConsoleTools.cs").read_text(
        encoding="utf-8"
    )
    copy_index = source.index("File.Copy(sourceFullPath, backupFullPath, true);")
    readback_index = source.index("item.after_sha256 = ComputeSha256(backupFullPath);", copy_index)
    assert copy_index < readback_index
    assert "catch (SafeBackupTransactionException ex)" in source
    assert "assets_touched = transactionItems.Count" in source
    assert "items = transactionItems.Take(20).ToArray()" in source
    assert "handle = manifestPath" in source
    assert 'status = "succeeded"' in source
    assert 'status = "failed"' in source
    assert "rolled_back = false" in source


def test_ensure_expression_parameter_returns_transaction_from_persisted_readback() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    ).read_text(encoding="utf-8")
    block = source[
        source.index("public static class EnsureExpressionParameterTool") :
        source.index("public static class EnsureExpressionMenuControlTool")
    ]
    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionParameters>", save_index)
    assert save_index < readback_index
    assert "assets_touched = transactionItems.Count" in block
    assert "items = transactionItems.Take(20).ToArray()" in block
    assert "handle = transactionHandle" in block
    assert 'Status = "succeeded"' in block
    assert 'Status = "failed"' in block
    assert "RolledBack = false" in block


def test_ensure_animator_state_returns_transaction_from_controller_readback() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    ).read_text(encoding="utf-8")
    block = source[source.index("public static class EnsureAnimatorStateTool") :]
    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("LoadAssetAtPath<AnimatorController>", save_index)
    assert save_index < readback_index
    assert "assets_touched = transactionItems.Count" in block
    assert "items = transactionItems.Take(20).ToArray()" in block
    assert "handle = transactionHandle" in block
    assert "DescribeAnimatorState" in block
    assert 'Status = "succeeded"' in block
    assert 'Status = "failed"' in block


def test_ensure_expression_menu_control_returns_changed_asset_transaction() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    ).read_text(encoding="utf-8")
    block = source[
        source.index("public static class EnsureExpressionMenuControlTool") :
        source.index("public static class EnsureAnimatorStateTool")
    ]
    save_index = block.index("AssetDatabase.SaveAssets();")
    readback_index = block.index("LoadAssetAtPath<VRCExpressionsMenu>", save_index)
    assert save_index < readback_index
    assert "CaptureMenuGraph(root)" in block
    assert "assets_touched = transactionItems.Count" in block
    assert "items = transactionItems.Take(20).ToArray()" in block
    assert "handle = transactionHandle" in block
    assert 'status = failure == null ? "succeeded" : "failed"' in block


def test_add_wardrobe_outfit_returns_bounded_multi_asset_transaction() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs").read_text(
        encoding="utf-8"
    )
    save_index = source.index("AssetDatabase.SaveAssets();")
    controller_readback = source.index("LoadAssetAtPath<AnimatorController>", save_index)
    clip_readback = source.index("LoadAssetAtPath<AnimationClip>", save_index)
    assert save_index < controller_readback
    assert save_index < clip_readback
    assert "assets_touched = transactionItems.Count" in source
    assert "items = transactionItems.Take(20).ToArray()" in source
    assert "handle = transactionHandle" in source
    assert 'Status = "succeeded"' in source
    assert 'Status = "failed"' in source
    assert "RolledBack = false" in source


def test_apply_clothing_fx_returns_bounded_multi_asset_transaction() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/ClothingFxAuthor.cs").read_text(
        encoding="utf-8"
    )
    save_index = source.index("AssetDatabase.SaveAssets();")
    controller_readback = source.index("LoadAssetAtPath<AnimatorController>", save_index)
    assert save_index < controller_readback
    assert "assets_touched = transactionItems.Count" in source
    assert "items = transactionItems.Take(20).ToArray()" in source
    assert "handle = AssetDir" in source
    assert 'Status = "succeeded"' in source
    assert 'Status = "failed"' in source
    assert "RolledBack = false" in source
