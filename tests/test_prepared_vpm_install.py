from __future__ import annotations

from pathlib import Path

import pytest

import dashboard_server
from package_install_workflow_service import (
    PackageDetectionService,
    PackageInstallApprovedWriteHandler,
    PackageInstallWorkflowService,
    PackageManagerDiscoveryService,
    VpmPackageInstallExecutor,
    VpmPackageInstallPreparer,
    resolve_project_path,
    select_sealed_vpm_version,
)


ROOT = Path(__file__).parents[1]


def test_dashboard_composes_typed_package_lifecycle_and_approved_handler() -> None:
    assert isinstance(
        dashboard_server.PACKAGE_MANAGER_DISCOVERY,
        PackageManagerDiscoveryService,
    )
    assert isinstance(dashboard_server.PACKAGE_DETECTION, PackageDetectionService)
    assert isinstance(
        dashboard_server.PACKAGE_INSTALL_WORKFLOWS,
        PackageInstallWorkflowService,
    )
    assert isinstance(
        dashboard_server.VPM_PACKAGE_INSTALL_PREPARER,
        VpmPackageInstallPreparer,
    )
    assert isinstance(
        dashboard_server.VPM_PACKAGE_INSTALL_EXECUTOR,
        VpmPackageInstallExecutor,
    )
    assert isinstance(
        dashboard_server.PACKAGE_INSTALL_APPROVED_WRITE,
        PackageInstallApprovedWriteHandler,
    )

    handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001
        "vrcforge_install_vpm_package"
    ]
    assert (
        handler.request_preparer
        is dashboard_server.PACKAGE_INSTALL_APPROVED_WRITE.prepare
    )
    assert handler.requires_approved_execution_context is False
    assert (
        "vrcforge_install_vpm_package"
        not in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
    )


def test_dashboard_has_no_package_lifecycle_facade_or_monkeypatch_seam() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    for forbidden in (
        "VPM_PACKAGE_ID_RE",
        "KNOWN_VPM_CLI_NAMES",
        "_SEMVER_RE",
        "def detect_addon_package(",
        "def locate_vpm_package_managers(",
        "def package_manager_status_sync(",
        "def _select_package_install_strategy(",
        "def package_install_plan_sync(",
        "def request_package_install_sync(",
        "def prepare_vpm_package_install_request(",
        "def install_vpm_package_sync(",
        "def diagnose_package_install_errors_sync(",
        "def _classify_package_install_symptoms(",
        "def _build_package_install_fix_suggestions(",
    ):
        assert forbidden not in source
    assert "def _package_entry_version(" in source
    assert "def _is_unity_project_root(" in source


def test_public_project_path_resolution_preserves_explicit_path_priority() -> None:
    assert resolve_project_path({"projectPath": "E:/explicit"}, "E:/selected") == "E:/explicit"
    assert resolve_project_path({"project_path": "E:/snake"}, "E:/selected") == "E:/snake"
    assert resolve_project_path({}, "E:/selected") == "E:/selected"


def test_vpm_semver_selection_preserves_stable_and_exact_prerelease_policy() -> None:
    info = {
        "versions": [
            {"version": "1.9.0-beta.2"},
            {"version": "1.9.0-beta.10"},
            {"version": "1.8.9"},
            {"version": "1.9.0", "yanked": True},
            {"version": "2.0"},
        ]
    }
    assert select_sealed_vpm_version(info, "", False) == "1.8.9"
    assert select_sealed_vpm_version(info, "", True) == "1.9.0-beta.10"
    assert (
        select_sealed_vpm_version(info, "1.9.0-beta.2", True)
        == "1.9.0-beta.2"
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        select_sealed_vpm_version(info, "1.9.0-beta.2", False)
