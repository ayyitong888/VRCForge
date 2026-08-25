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
