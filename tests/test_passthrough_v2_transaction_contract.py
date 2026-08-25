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
