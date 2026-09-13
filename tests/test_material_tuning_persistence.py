"""Focused source contract for semantic material persistence."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/MaterialTuningApplier.cs").read_text(encoding="utf-8-sig")

def _save_block():
    start = SOURCE.index("                if (saveAssets)\n                {")
    return SOURCE[start:SOURCE.index("                var applied", start)]

def test_semantic_save_verifies_independent_disk_before_import():
    block = _save_block()
    assert "Undo.FlushUndoRecordObjects" not in block
    assert block.index("SaveAssetIfDirty") < block.index("RequirePersistedMaterial") < block.index("ImportAsset")
    assert "group.First().target.material" in block
    assert "AssetDatabase.SaveAssets" not in block

def test_semantic_persisted_readback_reports_distinct_failures():
    assert "readback_asset_null" in SOURCE
    assert "asset_guid_changed" in SOURCE
    assert "readback_asset_dirty" in SOURCE

def test_save_assets_false_does_not_enter_persistence_lane():
    block = _save_block()
    assert "RequirePersistedMaterial" in block
    assert "saveAssets && UnityMaterialKeywordEdit.HasUnsavedChanges(material)" in SOURCE

def test_failed_mutation_keeps_existing_recovery_path():
    assert SOURCE.index("recovery.Begin();") < SOURCE.index("WriteAnimationCurveTool.EditFailure")
