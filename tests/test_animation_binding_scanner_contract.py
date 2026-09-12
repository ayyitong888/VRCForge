from pathlib import Path


def test_animation_payload_declares_the_reported_key_limit() -> None:
    source = (Path(__file__).parents[1] / "Assets/VRCForge/Editor/AssetTools.cs").read_text(encoding="utf-8-sig")
    payload = source.split("private class AnimationBindingsPayload", 1)[1].split("private class AnimationBindingsSummary", 1)[0]
    assert "public int max_keys_per_binding;" in payload


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/AssetTools.cs").read_text(encoding="utf-8")


def test_explicit_clip_paths_are_an_exact_selector_before_avatar_discovery() -> None:
    """Regression: the old resolver appended controller/avatar clips to clipPaths."""
    resolver = SOURCE[SOURCE.index("private static List<AnimationClip> ResolveClips") : SOURCE.index("private static ClipBindingItem ScanClip")]
    assert "if (result.Count > 0)" in resolver
    assert "return result;" in resolver[resolver.index("if (result.Count > 0)") :]


def test_animation_binding_scan_projects_bounded_float_and_object_curve_data() -> None:
    """Regression: binding-only output could not verify dissolve endpoints or references."""
    assert "maxKeysPerBinding" in SOURCE
    assert "AnimationUtility.GetEditorCurve(clip, binding)" in SOURCE
    assert "AnimationUtility.GetObjectReferenceCurve(clip, binding)" in SOURCE
    for field in (
        "keyframe_count",
        "keys_truncated",
        "curve_pre_wrap_mode",
        "curve_post_wrap_mode",
        "inTangent",
        "outTangent",
        "inWeight",
        "outWeight",
        "weightedMode",
        "object_reference_key_count",
        "object_reference_keys_truncated",
        "loop_time",
        "loop_blend",
    ):
        assert f"public" in SOURCE and field in SOURCE


def test_curve_details_are_omitted_in_compact_mode_but_counts_remain() -> None:
    populate = SOURCE[SOURCE.index("private static void PopulateCurveData") : SOURCE.index("private static string ClassifyBinding")]
    assert "if (!includeBindingDetails)" in populate
    assert "item.keyframe_count = keys.Length" in populate
    assert "item.object_reference_key_count = references.Length" in populate
    assert "Take(maxKeysPerBinding)" in populate


def test_curve_projection_preserves_numeric_time_value_tangent_and_weight_semantics() -> None:
    populate = SOURCE[SOURCE.index("item.keys = keys.Take") : SOURCE.index("return;", SOURCE.index("item.keys = keys.Take"))]
    for expression in (
        "time = key.time",
        "value = key.value",
        "inTangent = key.inTangent",
        "outTangent = key.outTangent",
        "inWeight = key.inWeight",
        "outWeight = key.outWeight",
        "weightedMode = key.weightedMode.ToString()",
    ):
        assert expression in populate


def test_read_schema_documents_exact_clip_priority_and_bounded_key_limit() -> None:
    schema = (ROOT / "unity_read_input_schemas.py").read_text(encoding="utf-8")
    segment = schema[schema.index('"vrcforge_scan_animation_bindings"') : schema.index('"vrcforge_scan_avatar_controls"')]
    assert '"maxKeysPerBinding"' in segment
    assert '"maximum": 2000' in segment
    assert "these are the only clips scanned" in segment
