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


def test_toggle_scene_object_reports_memory_readback_without_changing_save_behavior() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/AvatarControlScanner.cs").read_text(
        encoding="utf-8"
    )
    block = source[source.index("public static class SceneObjectToggler") :]

    write_index = block.index("target.gameObject.SetActive(active);")
    readback_index = block.index("var afterActive = target.gameObject.activeSelf;", write_index)
    assert write_index < readback_index
    assert block.count("AssetDatabase.SaveAssets();") == 1
    assert "before = beforeActive" in block
    assert "after = afterActive" in block
    assert "pending = !saveAssets" in block
    assert 'note = saveAssets ? "已修改并落盘" : "已修改，尚未落盘"' in block


def test_material_tuning_reports_adapter_memory_readback_without_changing_save_behavior() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/MaterialTuningApplier.cs").read_text(
        encoding="utf-8"
    )
    apply_index = source.index("adapter.TryApplyChange")
    readback_index = source.index("adapter.ReadSupportedProperties(target.material)", apply_index)
    assert apply_index < readback_index
    assert source.count("AssetDatabase.SaveAssets();") == 1
    assert "before = beforeValues" in source
    assert "after = afterValues" in source
    assert "pending = !saveAssets" in source
    assert 'note = saveAssets ? "已修改并落盘" : "已修改，尚未落盘"' in source


def test_add_modular_avatar_component_reports_memory_state_without_adding_saveassets() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/MAComponentWriter.cs").read_text(
        encoding="utf-8"
    )
    write_index = source.index("var component = Undo.AddComponent")
    readback_index = source.index("var after = DescribeComponents(target, componentType);", write_index)
    assert write_index < readback_index
    assert "AssetDatabase.SaveAssets();" not in source
    assert "var before = DescribeComponents(target, componentType);" in source
    assert "before," in source
    assert "after," in source
    assert "pending = !sceneSaved" in source
    assert 'note = sceneSaved ? "已修改并落盘" : "已修改，尚未落盘"' in source


def test_instantiate_prefab_reports_fresh_scene_memory_state_without_saving() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs"
    ).read_text(encoding="utf-8")
    block = source[
        source.index("public static class InstantiatePrefabTool") :
        source.index("public static class UnpackPrefabTool")
    ]
    write_index = block.index("PrefabUtility.InstantiatePrefab")
    readback_index = block.index("var readbackInstance = ComponentCrudCore.ResolveGameObject(goPath);", write_index)
    assert write_index < readback_index
    assert "AssetDatabase.SaveAssets();" not in block
    assert "before = new" in block
    assert "after = new" in block
    assert "pending = true" in block
    assert 'note = "已修改，尚未落盘"' in block


def test_unpack_prefab_reports_fresh_scene_memory_state_without_saving() -> None:
    source = (
        ROOT / "Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs"
    ).read_text(encoding="utf-8")
    block = source[source.index("public static class UnpackPrefabTool") :]
    write_index = block.index("PrefabUtility.UnpackPrefabInstance")
    readback_index = block.index("var readbackObject = ComponentCrudCore.ResolveGameObject(goPath);", write_index)
    assert write_index < readback_index
    assert "AssetDatabase.SaveAssets();" not in block
    assert "before = new" in block
    assert "after = new" in block
    assert "pending = true" in block
    assert 'note = "已修改，尚未落盘"' in block
