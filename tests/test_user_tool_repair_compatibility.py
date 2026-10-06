"""Source integration matrix; not installed-App/Core upgrade acceptance."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from skill_packages import PackageDisabledError, PackageSecurityError, SkillPackageService
from tests.test_user_unity_tool_service import _installed
from user_unity_tool_gateway import invoke_user_tool, list_user_tools
import user_unity_tool_service as service_module


def setup_package(tmp_path, *, repairs=True):
    baseline = {"sha256": "a" * 64}
    declarations = [{"toolId": "vrc_get_property", "coreVersion": "1.8.6",
                     "toolContractVersion": "161", "coreSourceTreeSha256": baseline["sha256"]}]
    service, project = _installed(tmp_path, repairs=declarations if repairs else None,
                                  core_tree_identity=lambda _: baseline)
    plan = service.prepare_installed("com.example.tool", project)
    service.apply(plan)
    package = {"packageId": "com.example.tool", "packageDigest": plan["installed"]["sha256"],
               "available": True, "enabled": True, "status": "ready", "reasons": [],
               "tools": [{"toolId": "example.tool", "available": True, "reason": "ready"}]}
    return service, project, baseline, package


def catalog(service, project, package, **arguments):
    return list_user_tools({"projectPath": str(project), **arguments}, service,
                           Mock(return_value={"tools": [deepcopy(package)]}))["tools"][0]


def disk_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("code,status", [
    ("ready", "valid"), ("core_baseline_mismatch", "expired"),
    ("repair_tool_unknown", "expired"), ("schema_hash_mismatch", "conflict"),
    ("source_hash_mismatch", "conflict"), ("package_digest_mismatch", "conflict"),
    ("package_identity_mismatch", "conflict"), ("pending_compile", "unverified"),
    ("stale_generated_stamp", "unverified"), ("disabled", "unverified"),
])
def test_running_core_reason_matrix_preserves_existing_status_and_bytes(tmp_path, code, status):
    service, project, _, package = setup_package(tmp_path)
    if code != "ready":
        package.update(available=False, status="unavailable")
        package["tools"][0].update(available=False, reason=code)
    before = disk_bytes(tmp_path)
    row = catalog(service, project, package)
    assert row["available"] == package["available"] and row["status"] == package["status"]
    report = row["repairCompatibility"]
    assert report["status"] == status
    assert report["repairDeclared"] is True
    assert report["scope"] == "installed_package_and_running_core_catalog"
    assert report["fixVerification"]["status"] == "unverified"
    assert report["absorption"]["status"] == "unverified"
    assert "official_only" in report["absorption"]["requiredEvidence"]
    assert disk_bytes(tmp_path) == before


def test_source_baseline_mismatch_has_typed_hash_evidence_and_keeps_files(tmp_path):
    service, project, baseline, package = setup_package(tmp_path)
    baseline["sha256"] = "b" * 64
    before = disk_bytes(tmp_path)
    with pytest.raises(ValueError, match="core source tree baseline mismatch") as caught:
        service.verified_descriptor("com.example.tool", project)
    assert caught.value.code == "baseline_mismatch"
    assert caught.value.expected == "a" * 64
    assert caught.value.observed == "b" * 64
    row = catalog(service, project, package)
    assert row["available"] is False
    report = row["repairCompatibility"]
    assert report["status"] == "expired"
    assert report["baseline"] == {"scope": "disk_source_tree", "expected": "a" * 64, "observed": "b" * 64}
    assert disk_bytes(tmp_path) == before


def test_real_installed_package_tamper_is_conflict_and_not_rewritten(tmp_path):
    service, project, _, package = setup_package(tmp_path)
    source = service.package_service.skill_store / "com.example.tool/versions/1.0.0/Tool.cs"
    source.write_text("// user changed source", encoding="utf-8")
    before = disk_bytes(tmp_path)
    row = catalog(service, project, package)
    assert row["available"] is False
    assert row["repairCompatibility"]["status"] == "conflict"
    assert disk_bytes(tmp_path) == before


def test_new_app_service_keeps_store_enabled_package_and_unchanged_core_usable(tmp_path):
    service, project, baseline, package = setup_package(tmp_path)
    before = disk_bytes(tmp_path)
    newer_store = SkillPackageService(tmp_path / "store", vrcforge_version="1.8.7")
    newer = service_module.UserUnityToolService(newer_store, core_tree_identity=lambda _: baseline)
    row = catalog(newer, project, package)
    assert row["available"] is True
    assert row["repairCompatibility"]["status"] == "valid"
    assert row["packageDigest"] == package["packageDigest"]
    assert disk_bytes(tmp_path) == before


def test_ordinary_extension_does_not_claim_to_be_a_repair_or_absorbed(tmp_path):
    service, project, _, package = setup_package(tmp_path, repairs=False)
    row = catalog(service, project, package, absorbed=True, repairCompatibility={"status": "absorbed"})
    report = row["repairCompatibility"]
    assert row["available"] is True and report["status"] == "valid"
    assert report["repairDeclared"] is False
    assert report["absorption"]["status"] == "unverified"


@pytest.mark.parametrize("repairs", [False, True])
def test_real_store_disable_blocks_dispatch_despite_enabled_core_record_and_reenable_recovers(tmp_path, repairs):
    service, project, _, package = setup_package(tmp_path, repairs=repairs)
    before = disk_bytes(project)
    arguments = {"projectPath": str(project), "packageId": package["packageId"],
                 "packageDigest": package["packageDigest"], "toolId": "example.tool", "arguments": {}}
    invoke = Mock(return_value={"ok": True})
    service.package_service.set_enabled(package["packageId"], False)
    row = catalog(service, project, package)
    assert row["enabled"] is False
    assert row["disabledBy"] == "vrcforge_package_store"
    assert row["coreEnabled"] is True
    assert row["available"] is False and row["status"] == "unavailable"
    assert all(tool["available"] is False for tool in row["tools"])
    assert all(tool["coreReason"] == "ready" for tool in row["tools"])
    assert all(tool["reason"] == "disabled" for tool in row["tools"])
    assert "disabled" in row["reasons"]
    assert "disabled" in row["repairCompatibility"]["reasonCodes"]
    assert row["repairCompatibility"]["status"] == "unverified"
    with pytest.raises(PackageDisabledError, match="not enabled") as exc_info:
        invoke_user_tool(arguments, service, invoke)
    assert exc_info.value.code == "disabled"
    invoke.assert_not_called()
    assert disk_bytes(project) == before
    service.package_service.set_enabled(package["packageId"], True)
    assert catalog(service, project, package)["available"] is True
    assert invoke_user_tool(arguments, service, invoke) == {"ok": True}
    invoke.assert_called_once()
    assert disk_bytes(project) == before


def test_missing_store_entry_remains_generic_security_error(tmp_path):
    service, project, _, _ = setup_package(tmp_path)
    with pytest.raises(PackageSecurityError, match="not installed") as exc_info:
        service.verified_descriptor("com.missing.tool", project)
    assert not isinstance(exc_info.value, PackageDisabledError)


@pytest.mark.parametrize("error", [ValueError("core source tree baseline mismatch. password=private-value"),
                                   ValueError("core_baseline_mismatch"), ValueError("source_hash_mismatch"),
                                   PackageSecurityError("disabled token=private-value")])
def test_uncoded_failure_is_not_classified_by_english_or_leaked(tmp_path, error):
    service, project, _, package = setup_package(tmp_path)
    service.verified_descriptor = Mock(side_effect=error)
    report = catalog(service, project, package)["repairCompatibility"]
    assert report["status"] == "unverified"
    assert report["repairDeclared"] is None
    assert "private-value" not in json.dumps(report)
