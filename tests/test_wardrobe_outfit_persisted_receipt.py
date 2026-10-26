from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WRITER_PATH = ROOT / "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs"


def test_add_outfit_only_returns_verified_persisted_receipt_after_full_readback():
    source = WRITER_PATH.read_text(encoding="utf-8")
    save_index = source.index("saveScope.Save(")
    completed_index = source.index("return VRCForgeToolResult.Completed(", save_index)
    finalization = source[save_index:completed_index]
    for required in (
        "VerifyClipReadback(",
        "VerifyWardrobeFxReadback(",
        "VerifyMenuReadback(",
        "VerifySceneDefaultReadback(",
    ):
        assert required in finalization
    for field in (
        'schema = "vrcforge.wardrobe_outfit_write.receipt.v1"',
        "verified = true",
        "persistedReadback = true",
        "committed = true",
        'commitState = "committed"',
        "readback =",
    ):
        assert field in source[completed_index:source.index("catch (Exception ex)", completed_index)]


def _extract_method(source: str, name: str) -> str:
    match = re.search(r"private static (?:object|bool|void|AnimationClip|List<AnimatorState>) " + name + r"\s*\(", source)
    assert match, f"missing executable verifier: {name}"
    opening = source.index("{", match.end())
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]
    raise AssertionError(f"unclosed method {name}")


def test_persisted_verifier_methods_are_extractable_for_fixture_harness(tmp_path):
    """Check source method boundaries only; this is not a C# compile or runtime test."""
    source = WRITER_PATH.read_text(encoding="utf-8")
    methods = "\n".join(
        _extract_method(source, name)
        for name in (
            "VerifyClipReadback",
            "VerifyWardrobeFxReadback",
            "VerifyMenuReadback",
            "FindStatesForReceipt",
            "VerifySceneDefaultReadback",
        )
    )
    (tmp_path / "extracted_methods.txt").write_text(methods, encoding="utf-8")
    assert methods.count("private static") == 5


def test_wardrobe_verifier_calls_match_types_and_checks_prefab_override_identity():
    source = WRITER_PATH.read_text(encoding="utf-8")
    assert "private static AnimationClip VerifyClipReadback(string path, IEnumerable<string> onObjects, IEnumerable<string> offObjects)" in source
    assert "VerifyWardrobeFxReadback(readbackController, wardrobeLayer.name," in source
    assert "PrefabUtility.GetNearestPrefabInstanceRoot" in source
    assert "PrefabUtility.GetCorrespondingObjectFromSource" in source
    assert "PrefabUtility.RecordPrefabInstancePropertyModifications(go)" in source
    capture = source.index("prefabObjectIds[targetPath] = new PrefabSceneDefaultIdentity")
    mutation = source.index("mutationStarted = true;")
    assert capture < mutation
    verifier = _extract_method(source, "VerifySceneDefaultReadback")
    assert "!u!1001 &" in verifier
    assert "sourceFileId" in verifier and "m_IsActive" in verifier
    assert 'expectedStates[item.Key] ? "1" : "0"' in verifier
    assert '" + expectedValue' in verifier


def _capture_prefab_instance_id(object_id: int, prefab_id: int):
    """Evaluate the actual capture field choices; not a Unity execution test."""
    source = WRITER_PATH.read_text(encoding="utf-8")
    start = source.index("var instanceId = GlobalObjectId.GetGlobalObjectIdSlow(instanceRoot);")
    end = source.index("sourceFileId = sourceFileId,", start)
    capture = source[start:end]
    guard = re.search(r"instanceId\.(target\w+Id) == 0", capture)
    assignment = re.search(r"instanceRootFileId = instanceId\.(target\w+Id)", capture)
    assert guard and assignment
    fields = {"targetObjectId": object_id, "targetPrefabId": prefab_id}
    if fields[guard.group(1)] == 0:
        return None
    return fields[assignment.group(1)]


def test_prefab_capture_selects_instance_block_not_source_object():
    # Distinct IDs model the real GlobalObjectId contract; the second instance
    # deliberately has the same source object and a different saved override.
    captured = _capture_prefab_instance_id(7000000000000000001, 1001)
    yaml = """--- !u!1001 &1001
PrefabInstance:
  m_Modification:
    m_Modifications:
    - target: {fileID: 7000000000000000001, guid: aabb, type: 3}
      propertyPath: m_IsActive
      value: 0
--- !u!1001 &1002
PrefabInstance:
  m_Modification:
    m_Modifications:
    - target: {fileID: 7000000000000000001, guid: aabb, type: 3}
      propertyPath: m_IsActive
      value: 1
"""
    def instance_body(instance_id):
        match = re.search(
            r"(?m)^--- !u!1001 &" + str(instance_id)
            + r"\n(?P<body>[\s\S]*?)(?=^--- !u!|\Z)", yaml
        )
        assert match, "capture must resolve a PrefabInstance, not the source object ID"
        return match.group("body")

    assert "value: 0" in instance_body(captured)
    sibling = _capture_prefab_instance_id(7000000000000000001, 1002)
    assert "value: 0" not in instance_body(sibling)


def test_prefab_capture_rejects_missing_instance_id_before_mutation():
    assert _capture_prefab_instance_id(7000000000000000001, 0) is None
