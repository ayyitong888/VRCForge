from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "Assets"
    / "VRCForge"
    / "Editor"
    / "Generic"
    / "UnityComponentCrud.cs"
).read_text(encoding="utf-8")


def test_describe_value_object_readback_has_persistent_and_scene_identity_contract():
    start = SOURCE.index("private static object DescribeUnityObject")
    end = SOURCE.index("internal static object DescribeValue(\n", start)
    source = SOURCE[start:end]
    assert "assetPath" in source
    assert "assetGuid" in source
    assert "localFileId" in source
    assert "GlobalObjectId.GetGlobalObjectIdSlow" in source
    assert "globalObjectId" in source
    assert "hierarchyPath" in source
    assert "instanceId" in source


def test_describe_value_object_readback_keeps_null_and_collection_dispatch():
    assert "case null:" in SOURCE
    assert "value is UnityEngine.Object || !(value is IEnumerable enumerable) || value is string" in SOURCE
    assert "items.Add(DescribeValue(item));" in SOURCE
