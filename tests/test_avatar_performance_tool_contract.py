from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets" / "VRCForge" / "Editor" / "AvatarPerformanceTool.cs").read_text(encoding="utf-8")


def test_vrchat_sdk_3104_performance_type_names_are_exact() -> None:
    assert 'FindType("VRC.SDKBase.Validation.Performance.Stats.AvatarPerformanceStats")' in SOURCE
    assert 'FindType("VRC.SDKBase.Validation.Performance.AvatarPerformance")' in SOURCE
    assert 'FindType("VRC.SDKBase.Validation.Performance.AvatarPerformanceCategory")' in SOURCE
    assert 'FindType("VRC.SDKBase.Validation.Performance.AvatarPerformanceStats")' not in SOURCE
