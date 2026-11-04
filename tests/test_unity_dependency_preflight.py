import json

import pytest

from package_install_workflow_service import PackageDetectionPorts, PackageDetectionService
from unity_dependency_preflight import build_unity_dependency_preflight_report


@pytest.mark.parametrize("package_id,key", [
    ("com.vrchat.avatars", "vrchatSdk"),
    ("com.vrchat.base", "vrchatSdk"),
    ("com.unity.nuget.newtonsoft-json", "newtonsoft"),
])
def test_unreadable_required_package_remains_unknown_with_actual_file_error(tmp_path, package_id, key):
    packages = tmp_path / "Packages"
    broken = packages / package_id / "package.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{broken", encoding="utf-8")
    dependencies = {name: "1.0.0" for name in (
        "com.vrchat.avatars", "com.vrchat.base", "com.unity.nuget.newtonsoft-json"
    ) if name != package_id}
    (packages / "manifest.json").write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    detector = PackageDetectionService(PackageDetectionPorts(
        path_exists=lambda path: path.exists(),
        read_utf8_sig_text=lambda path: path.read_text(encoding="utf-8-sig"),
    ))
    result = build_unity_dependency_preflight_report(tmp_path, detect_package=detector.detect)
    assert result["ready"] is None
    assert result["status"] == "unknown"
    assert result["missing"] == []
    assert result["blockerCode"] == ""
    assert result["checks"][key]["installed"] is None
    assert result["checks"][key]["readErrors"][0]["path"] == str(broken)
    assert result["checks"][key]["readErrors"][0]["error"]


@pytest.mark.parametrize("present", [True, False])
def test_readable_package_facts_keep_installed_and_missing_semantics(tmp_path, present):
    packages = tmp_path / "Packages"
    packages.mkdir()
    dependencies = {name: "1.0.0" for name in (
        "com.vrchat.avatars", "com.vrchat.base", "com.unity.nuget.newtonsoft-json"
    )} if present else {}
    (packages / "manifest.json").write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    detector = PackageDetectionService(PackageDetectionPorts(
        path_exists=lambda path: path.exists(),
        read_utf8_sig_text=lambda path: path.read_text(encoding="utf-8-sig"),
    ))
    result = build_unity_dependency_preflight_report(tmp_path, detect_package=detector.detect)
    assert result["ready"] is present
    assert result["status"] == ("ok" if present else "error")
    assert result["unknown"] == []
    assert result["missing"] == ([] if present else ["vrchat_sdk", "newtonsoft"])
