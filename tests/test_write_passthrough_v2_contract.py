from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_apply_blendshapes_reports_memory_readback_without_changing_save_behavior() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/BlendshapeApplier.cs").read_text(
        encoding="utf-8"
    )

    write_index = source.index("renderer.SetBlendShapeWeight")
    readback_index = source.index("var currentWeight = renderer.GetBlendShapeWeight", write_index)
    assert write_index < readback_index
    assert source.count("AssetDatabase.SaveAssets();") == 1
    assert "before = applied.Select" in source
    assert "after = applied.Select" in source
    assert "pending = !saveAssets" in source
    assert 'note = saveAssets ? "已修改并落盘" : "已修改，尚未落盘"' in source
