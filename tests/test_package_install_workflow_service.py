from __future__ import annotations

from typing import Any

from package_install_workflow_service import (
    PackageInstallApprovedWriteHandler,
    PackageInstallWorkflowPorts,
    PackageInstallWorkflowService,
)


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
            summarize_debug=lambda value: value,
            read_compile_errors=read_compile_errors,
            redact_support=lambda value: value,
            create_apply_request=create_apply_request,
        )
    )


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
