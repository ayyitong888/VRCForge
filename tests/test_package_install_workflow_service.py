from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from package_install_workflow_service import (
    PackageDetectionPorts,
    PackageDetectionService,
    PackageInstallApprovedWriteHandler,
    PackageInstallWorkflowPorts,
    PackageInstallWorkflowService,
)


def test_assets_package_json_is_detected_with_matching_package_identity(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    (project / "Assets" / "lilToon").mkdir(parents=True)
    (project / "Packages").mkdir()
    package_json = project / "Assets" / "lilToon" / "package.json"
    package_json.write_text(
        json.dumps({"name": "jp.lilxyzw.liltoon", "version": "1.4.1"}),
        encoding="utf-8",
    )
    detector = PackageDetectionService(
        PackageDetectionPorts(
            path_exists=lambda path: path.exists(),
            read_utf8_sig_text=lambda path: path.read_text(encoding="utf-8"),
        )
    )
    result = detector.detect(project, ["other.package", "jp.lilxyzw.liltoon"])
    assert result["installed"] is True
    assert result["packageId"] == "jp.lilxyzw.liltoon"
    assert result["version"] == "1.4.1"
    assert result["source"] == "assets"


def _service(
    calls: list[tuple[Any, ...]],
    plan: dict[str, Any],
) -> PackageInstallWorkflowService:
    project_path = str(plan.get("projectPath") or "E:/avatar")
    managers: list[dict[str, Any]] = []
    if plan.get("canExecuteCommandInstall"):
        managers.append(
            {
                "name": "vrc-get",
                "path": "C:/vrc-get.exe",
                "source": "PATH",
                "supportsCommandInstall": True,
                "supportsUiHandoff": False,
            }
        )

    package = plan.get("package") if isinstance(plan.get("package"), dict) else {}
    dependency_id = str(package.get("dependencyId") or "")
    optimizer_dependencies = (
        [
            {
                "id": dependency_id,
                "packageIds": [str(plan.get("packageId") or "")],
            }
        ]
        if dependency_id
        else []
    )

    def locate_managers() -> list[dict[str, Any]]:
        calls.append(("locate",))
        return managers

    def detect_package(_project_path: Any, package_ids: list[str]) -> dict[str, Any]:
        calls.append(("detect", package_ids))
        package_state = plan.get("packageState")
        if isinstance(package_state, dict):
            return {**package_state, "packageIds": package_ids}
        return {"installed": False, "packageIds": package_ids}

    def read_compile_errors(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("compile-errors", params))
        return {"ok": True, "errors": []}

    def create_apply_request(
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
    ) -> dict[str, Any]:
        calls.append(("approval", params, internal_wrapper))
        return {"ok": True, "approval": params, "internalWrapper": internal_wrapper}

    return PackageInstallWorkflowService(
        PackageInstallWorkflowPorts(
            selected_project_path=lambda: project_path,
            locate_managers=locate_managers,
            detect_package=detect_package,
            addon_frameworks={},
            optimizer_dependencies=optimizer_dependencies,
            summarize_debug=plan.get("summarizeDebug", lambda value: value),
            read_compile_errors=read_compile_errors,
            redact_support=lambda value: value,
            create_apply_request=create_apply_request,
        )
    )


def test_package_plan_exposes_explicit_upgrade_and_target_version(tmp_path: Path) -> None:
    calls: list[tuple[Any, ...]] = []
    project = tmp_path / "avatar"
    project.mkdir()
    service = _service(
        calls,
        {
            "canExecuteCommandInstall": True,
            "projectPath": str(project),
            "packageId": "com.example.package",
            "packageState": {
                "installed": True,
                "packageId": "com.example.package",
                "version": "1.2.3",
                "source": "vpm",
            },
        },
    )
    plan = service.plan_install(
        {"packageId": "com.example.package", "packageVersion": "1.3.0", "upgrade": True}
    )

    assert plan["packageVersion"] == "1.3.0"
    assert plan["installedVersion"] == "1.2.3"
    assert plan["upgradeRequested"] is True
    assert plan["compatibilityAction"] == "upgrade"
    assert plan["canPrepareUpgradeRequest"] is True


def test_assets_package_upgrade_is_fail_closed_until_migration_is_planned(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Any, ...]] = []
    project = tmp_path / "avatar"
    project.mkdir()
    service = _service(
        calls,
        {
            "canExecuteCommandInstall": True,
            "projectPath": str(project),
            "packageId": "jp.lilxyzw.liltoon",
            "packageState": {
                "installed": True,
                "packageId": "jp.lilxyzw.liltoon",
                "version": "1.4.1",
                "source": "assets",
            },
        },
    )

    plan = service.plan_install(
        {"packageId": "jp.lilxyzw.liltoon", "packageVersion": "2.3.4", "upgrade": True}
    )
    assert plan["compatibilityAction"] == "migration_required"
    assert plan["canPrepareUpgradeRequest"] is False
    result = service.request_install(
        {"packageId": "jp.lilxyzw.liltoon", "packageVersion": "2.3.4", "upgrade": True}
    )
    assert result["status"] == "blocked"
    assert result["migrationRequired"] is True
    assert not any(call[0] == "approval" for call in calls)


def test_plan_failure_and_missing_cli_never_create_an_approval() -> None:
    calls: list[tuple[Any, ...]] = []
    failed = _service(calls, {"ok": False, "error": "bad package"})
    result = failed.request_install({"packageId": "!"})
    assert result["ok"] is False
    assert "valid VPM package id" in result["error"]
    assert not any(call[0] == "approval" for call in calls)

    calls.clear()
    blocked = _service(
        calls,
        {"ok": True, "canExecuteCommandInstall": False, "packageId": "nadena.dev.modular-avatar"},
    )
    result = blocked.request_install({"packageId": "nadena.dev.modular-avatar"})
    assert result["status"] == "blocked"
    assert result["installPlan"]["canExecuteCommandInstall"] is False
    assert not any(call[0] == "approval" for call in calls)


def test_request_freezes_exact_install_arguments_and_internal_wrapper() -> None:
    calls: list[tuple[Any, ...]] = []
    plan = {
        "ok": True,
        "canExecuteCommandInstall": True,
        "projectPath": "E:/avatar",
        "packageId": "nadena.dev.modular-avatar",
        "repository": "",
        "package": {},
    }
    service = _service(calls, plan)
    result = service.request_install(
        {
            "packageId": "nadena.dev.modular-avatar",
            "preferred_manager": "vrc-get",
            "include_prerelease": True,
            "package_version": "1.12.3",
        },
        agent_name="desktop-agent",
    )
    approval = result["approval"]

    assert result["internalWrapper"] is True
    assert approval["target_tool"] == "vrcforge_install_vpm_package"
    assert approval["agent_name"] == "desktop-agent"
    assert approval["arguments"] == {
        "projectPath": "E:/avatar",
        "packageId": "nadena.dev.modular-avatar",
        "repository": "",
        "preferredManager": "vrc-get",
        "includePrerelease": True,
        "packageVersion": "1.12.3",
    }
    assert "requires_explicit_approval" not in approval


def test_optimizer_and_doctor_requests_keep_existing_explicit_approval_policy() -> None:
    optimizer_plan = {
        "ok": True,
        "canExecuteCommandInstall": True,
        "projectPath": "E:/avatar",
        "packageId": "com.anatawa12.avatar-optimizer",
        "package": {"dependencyId": "avatar-optimizer"},
    }
    calls: list[tuple[Any, ...]] = []
    service = _service(calls, optimizer_plan)
    approval = service.request_install(
        {"packageId": "com.anatawa12.avatar-optimizer"}
    )["approval"]
    assert approval["requires_explicit_approval"] is True
    assert approval["never_auto_approve"] is False
    assert approval["explicit_approval_reason"].startswith("Optimizer package install")

    calls.clear()
    doctor_plan = {**optimizer_plan, "package": {}}
    doctor = _service(calls, doctor_plan)
    approval = doctor.request_install(
        {
            "packageId": "com.anatawa12.avatar-optimizer",
            "neverAutoApprove": True,
        }
    )["approval"]
    assert approval["requires_explicit_approval"] is True
    assert approval["never_auto_approve"] is True
    assert approval["explicit_approval_reason"].startswith("Doctor package repair")


def test_installed_package_defaults_to_use_first_and_never_creates_approval(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Any, ...]] = []
    project = tmp_path / "avatar"
    project.mkdir()
    service = _service(
        calls,
        {
            "canExecuteCommandInstall": True,
            "projectPath": str(project),
            "packageId": "com.anatawa12.avatar-optimizer",
            "package": {"dependencyId": "aao"},
            "packageState": {
                "installed": True,
                "packageId": "com.anatawa12.avatar-optimizer",
                "version": "1.2.3",
                "source": "vpm",
            },
        },
    )

    result = service.request_install(
        {
            "packageId": "com.anatawa12.avatar-optimizer",
            "packageVersion": "9.9.9",
        }
    )

    assert result["ok"] is True
    assert result["status"] == "use_installed"
    assert result["approvalCreated"] is False
    assert result["packageState"]["version"] == "1.2.3"
    assert not any(call[0] == "approval" for call in calls)


def test_installed_package_rejects_caller_supplied_upgrade_evidence(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Any, ...]] = []
    project = tmp_path / "avatar"
    project.mkdir()
    service = _service(
        calls,
        {
            "canExecuteCommandInstall": True,
            "projectPath": str(project),
            "packageId": "com.example.optimizer",
            "package": {"dependencyId": "fixture"},
            "packageState": {
                "installed": True,
                "packageId": "com.example.optimizer",
                "version": "1.2.3",
                "source": "vpm",
            },
            "summarizeDebug": lambda value: str(value).replace(
                "secret-token", "[redacted]"
            ),
        },
    )

    failure_text = "secret-token missing optimizer type"
    result = service.request_install(
        {
            "packageId": "com.example.optimizer",
            "runtimeCompatibilityFailure": {
                "kind": "runtime_incompatibility",
                "operation": "configure_optimizer",
                "packageId": "com.example.optimizer",
                "originalError": failure_text,
            },
        }
    )
    assert result["ok"] is True
    assert result["status"] == "use_installed"
    assert result["approvalCreated"] is False
    assert "caller-supplied evidence" in result["message"]
    assert not any(call[0] == "approval" for call in calls)


def test_diagnostics_preserve_only_bounded_redacted_original_error_and_digest() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(
        calls,
        {
            "projectPath": "E:/avatar",
            "summarizeDebug": lambda value: str(value).replace(
                "secret-token", "[redacted]"
            ),
        },
    )
    raw_error = "secret-token " + ("incompatible package runtime " * 300)

    result = service.diagnose_install(
        {
            "projectPath": "E:/avatar",
            "packageId": "com.example.optimizer",
            "stderrSummary": raw_error,
        }
    )

    assert result["originalError"].startswith("[redacted] incompatible")
    assert "secret-token" not in result["originalError"]
    assert len(result["originalError"]) <= 2000
    assert result["originalErrorSha256"] == hashlib.sha256(
        result["originalError"].encode("utf-8")
    ).hexdigest()


def test_status_diagnostics_and_prepared_execution_are_separate_ports() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(
        calls,
        {
            "ok": True,
            "packageId": "nadena.dev.modular-avatar",
            "canExecuteCommandInstall": True,
        },
    )
    params = {"projectPath": "E:/avatar"}

    assert service.package_manager_status(params)["ok"]
    assert service.diagnose_install(params)["ok"]

    def prepare(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
        calls.append(("prepare", arguments, preview))
        return {**arguments, "prepared": True}, preview

    def execute(arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(("execute", arguments))
        return {"ok": True, "arguments": arguments}

    handlers = PackageInstallApprovedWriteHandler(prepare=prepare, execute=execute)
    assert handlers.prepare(params, {"preview": True}) == (
        {**params, "prepared": True},
        {"preview": True},
    )
    assert handlers.execute(params)["arguments"] == params
    assert [call[0] for call in calls][-2:] == ["prepare", "execute"]

def test_legacy_upgrade_plan_reports_exact_preserved_differences(monkeypatch, tmp_path: Path) -> None:
    import package_install_workflow_service as module
    root = tmp_path / 'Assets' / 'lilToon'
    root.mkdir(parents=True)
    service = _service([], {'projectPath': str(tmp_path), 'canExecuteCommandInstall': True,
        'packageState': {'installed': True, 'source': 'assets', 'version': '1.4.1', 'path': str(root / 'package.json')}})
    evidence = {'ok': False, 'treeDigest': 'actual', 'baselineDigest': 'official',
        'missing': [], 'modified': ['Shader/custom.shader'], 'unknown': ['user.txt']}
    monkeypatch.setattr(module, 'verify_legacy_baseline', lambda *_args: evidence)
    result = service.plan_install({'packageId': 'jp.lilxyzw.liltoon', 'packageVersion': '2.3.4',
        'upgrade': True, 'legacyBaselineArchive': str(tmp_path / 'official.unitypackage'),
        'legacyBaselineAssetsRoot': str(root)})
    assert result['legacyBaselineVerification'] == evidence
    assert result['compatibilityAction'] == 'migration_required'
    assert result['canPrepareUpgradeRequest'] is False


def test_legacy_upgrade_opt_in_preserves_differences(monkeypatch, tmp_path):
    import package_install_workflow_service as module
    project=tmp_path/'avatar'; project.mkdir()
    root=project/'Assets'/'legacy'
    service=_service([], {'projectPath':str(project),'canExecuteCommandInstall':True,
        'packageState':{'installed':True,'source':'assets','version':'1.0.0','path':str(root/'package.json')}})
    report={'ok':False,'treeDigest':'frozen','baselineDigest':'official','unknown':['keep.txt'],'modified':[],'missing':[]}
    monkeypatch.setattr(module,'verify_legacy_baseline',lambda *_: report)
    args={'packageId':'com.example.package','packageVersion':'2.0.0','upgrade':True,
          'legacyBaselineArchive':str(tmp_path/'old.unitypackage'),'legacyBaselineAssetsRoot':str(root)}
    assert not service.plan_install(args)['canPrepareUpgradeRequest']
    result=service.plan_install({**args,'preserveLegacyFiles':True})
    assert result['canPrepareUpgradeRequest']
    assert result['legacyPreservationRequired']
    assert result['legacyBaselineVerification']['unknown']==['keep.txt']
