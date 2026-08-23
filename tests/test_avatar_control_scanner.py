from __future__ import annotations

from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "Assets"
    / "VRCForge"
    / "Editor"
    / "AvatarControlScanner.cs"
)


def _method_source(source: str, name: str, next_name: str) -> str:
    start = source.index(name)
    end = source.index(next_name, start)
    return source[start:end]


def test_avatar_control_scan_keeps_complete_recursive_menu_contract() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8-sig")
    menu_reader = _method_source(
        source,
        "private static List<ControlItem> ReadExpressionMenuItems",
        "private static void TraverseMenu",
    )
    traversal = _method_source(
        source,
        "private static void TraverseMenu",
        "private static List<ControlItem> ReadParameterOnlyItems",
    )

    # Regression: a root branch named 功能 must not disappear merely because
    # neither it nor its Breast size RadialPuppet is wardrobe-keyword-shaped.
    assert "return allControls;" in menu_reader
    assert "IsWardrobeCandidate" not in menu_reader
    assert ".Take(" not in menu_reader
    assert "TraverseMenu(subMenu, menuPath, parameterMap, items, visited, depth + 1);" in traversal
    assert 'menuPath = string.IsNullOrWhiteSpace(parentPath) ? name : $"{parentPath}/{name}"' in traversal


def test_avatar_control_scan_emits_radial_subparameters_once() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8-sig")
    traversal = _method_source(
        source,
        "private static void TraverseMenu",
        "private static List<ControlItem> ReadParameterOnlyItems",
    )
    sub_parameter_reader = _method_source(
        source,
        "private static string[] ReadControlSubParameterNames",
        "private static bool IsWardrobeCandidate",
    )

    # This is the payload field needed for the concrete
    # root -> 功能 -> Breast size (RadialPuppet) contract.
    assert "var subParameters = ReadControlSubParameterNames(control);" in traversal
    assert "subParameters = subParameters," in traversal
    assert 'GetMemberValue(control, "subParameters") as IEnumerable' in sub_parameter_reader
    assert 'GetMemberValue(item, "name")' in sub_parameter_reader
    assert ".Distinct(StringComparer.Ordinal)" in sub_parameter_reader
    assert "public string[] subParameters = Array.Empty<string>();" in source


def test_avatar_control_scan_does_not_truncate_deduplicated_menu_items() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8-sig")
    handle = _method_source(
        source,
        "public static object HandleCommand",
        "private static Component ResolveAvatarDescriptor",
    )

    assert ".GroupBy(item =>" in handle
    assert ".Select(group => group.First())" in handle
    assert ".Take(120)" not in handle
