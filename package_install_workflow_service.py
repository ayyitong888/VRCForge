from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


class CreateApplyRequestPort(Protocol):
    def __call__(
        self,
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
    ) -> dict[str, Any]: ...


class PreparedInstallPort(Protocol):
    def __call__(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]: ...


@dataclass(frozen=True, slots=True)
class PackageInstallWorkflowPorts:
    """Frozen package-manager capabilities; the service owns no child process."""

    selected_project_path: Callable[[], str]
    locate_managers: Callable[[], list[dict[str, Any]]]
    detect_package: Callable[[Path | None, list[str]], dict[str, Any]]
    addon_frameworks: dict[str, dict[str, Any]]
    optimizer_dependencies: tuple[dict[str, Any], ...] | list[dict[str, Any]]
    summarize_debug: Callable[[Any], Any]
    read_compile_errors: Callable[[dict[str, Any]], dict[str, Any]]
    redact_support: Callable[[Any], Any]
    create_apply_request: CreateApplyRequestPort


@dataclass(frozen=True, slots=True)
class PackageInstallApprovedWriteHandler:
    """Bounded child-process capability held only by the approval registry."""

    prepare: PreparedInstallPort
    execute: Callable[[dict[str, Any]], dict[str, Any]]


class PackageInstallWorkflowService:
    """Own VPM status/plan/request/diagnostic front doors.

    The approved request preparer freezes the CLI, argv, project identity and
    lifecycle policy. The executor owns the one bounded child process. Both stay
    explicit ports so this front-door owner cannot bypass approval or start a
    process during status, planning, diagnostics, or request creation. They are
    grouped separately in ``PackageInstallApprovedWriteHandler``.
    """

    def __init__(self, ports: PackageInstallWorkflowPorts) -> None:
        self._ports = ports

    @staticmethod
    def _params(params: dict[str, Any] | None) -> dict[str, Any]:
        return params or {}

    def _project_path(self, params: dict[str, Any]) -> str:
        return str(
            params.get("project_path")
            or params.get("projectPath")
            or self._ports.selected_project_path()
            or ""
        ).strip()

    def _optimizer_package_catalog(self) -> dict[str, dict[str, str]]:
        catalog: dict[str, dict[str, str]] = {}
        for dependency in self._ports.optimizer_dependencies:
            repository = str(dependency.get("vpmRepository") or "")
            label = str(
                dependency.get("label")
                or dependency.get("displayName")
                or dependency.get("id")
                or ""
            )
            for package_id in dependency.get("packageIds") or []:
                key = str(package_id or "").strip().lower()
                if key:
                    catalog[key] = {
                        "dependencyId": str(dependency.get("id") or ""),
                        "label": label,
                        "repository": repository,
                        "docsLink": str(dependency.get("docsLink") or ""),
                    }
        return catalog

    def _select_strategy(
        self,
        params: dict[str, Any],
        managers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        package_id = str(params.get("package_id") or params.get("packageId") or "").strip().lower()
        preferred = str(
            params.get("preferredManager") or params.get("preferred_manager") or ""
        ).strip().lower()
        allow_agent = bool(
            params.get("allowAgentManagedDownload")
            or params.get("allow_agent_managed_download")
        )
        package_meta = self._optimizer_package_catalog().get(package_id, {})
        command_installers = sorted(
            [
                manager
                for manager in managers
                if manager.get("supportsCommandInstall")
                and manager.get("name") in {"vrc-get"}
            ],
            key=lambda item: 0 if item.get("source") == "vrcforge-managed" else 1,
        )
        ui_handoff = sorted(
            [manager for manager in managers if manager.get("supportsUiHandoff")],
            key=lambda item: {"vcc": 0, "alcom": 1}.get(str(item.get("name") or ""), 9),
        )
        if preferred:
            command_installers.sort(key=lambda item: 0 if item.get("name") == preferred else 1)
            ui_handoff.sort(key=lambda item: 0 if item.get("name") == preferred else 1)
        selected_cli = command_installers[0] if command_installers else None
        selected_handoff = ui_handoff[0] if ui_handoff else None
        execution_strategy = (
            "command"
            if selected_cli
            else "agent_download"
            if allow_agent and not selected_handoff
            else "manual_handoff"
        )
        strategy = "ui_handoff" if selected_handoff else execution_strategy
        return {
            "schema": "vrcforge.package_install_plan.v1",
            "packageId": package_id,
            "repository": str(
                params.get("repository")
                or params.get("vpmRepository")
                or package_meta.get("repository")
                or ""
            ),
            "package": package_meta,
            "includePrerelease": bool(
                params.get("includePrerelease")
                or params.get("include_prerelease")
                or params.get("prerelease")
            ),
            "strategy": strategy,
            "executionStrategy": execution_strategy,
            "preferredManager": selected_handoff or selected_cli,
            "commandInstaller": selected_cli,
            "uiHandoff": selected_handoff,
            "managers": managers,
            "allowAgentManagedDownload": allow_agent,
            "directManifestEditing": False,
            "requiresApproval": True,
            "requiresCheckpoint": True,
            "message": (
                "Use the selected ALCOM/VCC handoff first; supervised command install is also available after approval."
                if selected_handoff and selected_cli
                else "Use the selected ALCOM/VCC handoff first."
                if selected_handoff
                else "Use the selected VPM CLI after approval."
                if selected_cli
                else "No VPM package manager is available; let an external agent prepare a supervised package-manager download/install plan."
            ),
            "agentManagedDownload": {
                "available": allow_agent and selected_cli is None and selected_handoff is None,
                "allowedTargets": [
                    "install ALCOM or VCC",
                    "install VCC/vpm CLI",
                    "install vrc-get",
                    "download package manager from official source",
                ],
                "disallowedTargets": [
                    "directly edit Packages/manifest.json",
                    "copy optimizer source into VRCForge",
                    "bypass approval/checkpoint",
                ],
                "nextTool": "vrcforge_request_apply",
            },
        }

    def package_manager_status(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = self._params(params)
        project_value = self._project_path(normalized)
        project_path = Path(project_value) if project_value else None
        managers = self._ports.locate_managers()
        packages = {
            framework: self._ports.detect_package(project_path, list(spec["packageIds"]))
            for framework, spec in self._ports.addon_frameworks.items()
        }
        usable = sorted(
            [
                manager
                for manager in managers
                if manager.get("supportsCommandInstall")
                and manager.get("name") in {"vrc-get"}
            ],
            key=lambda item: 0 if item.get("source") == "vrcforge-managed" else 1,
        )
        ui_handoff = sorted(
            [manager for manager in managers if manager.get("supportsUiHandoff")],
            key=lambda item: {"vcc": 0, "alcom": 1}.get(str(item.get("name") or ""), 9),
        )
        return {
            "ok": True,
            "projectPath": project_value,
            "managers": managers,
            "preferredCli": usable[0] if usable else None,
            "preferredCommandInstaller": usable[0] if usable else None,
            "preferredUiHandoff": ui_handoff[0] if ui_handoff else None,
            "canInstall": bool(usable) and bool(project_value),
            "canRequestInstall": bool(project_value),
            "packages": packages,
            "knownOptimizationPackages": self._optimizer_package_catalog(),
            "installPolicy": {
                "managerPriority": [
                    "ALCOM/VCC UI handoff when a human wants to manage repositories visually",
                    "VCC vpm CLI for non-interactive supervised installs",
                    "vrc-get CLI for non-interactive supervised installs",
                    "agent-managed download/install plan when no package manager is available",
                ],
                "directManifestEditing": False,
                "requiresApprovalCheckpoint": True,
            },
            "hint": (
                "VRCForge detects ALCOM/VCC for user handoff first. Non-interactive installs use the VCC vpm CLI "
                "or vrc-get after approval; if neither exists, VRCForge returns an agent-managed download plan."
            ),
        }

    def plan_install(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = self._params(params)
        package_id = str(
            normalized.get("package_id") or normalized.get("packageId") or ""
        ).strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9._-]{1,100}$", package_id):
            return {
                "ok": False,
                "error": (
                    "packageId must be a valid VPM package id, for example "
                    "nadena.dev.modular-avatar."
                ),
            }
        project_value = self._project_path(normalized)
        managers = self._ports.locate_managers()
        strategy = self._select_strategy(normalized, managers)
        package_state = None
        if project_value and Path(project_value).is_dir():
            package_state = self._ports.detect_package(Path(project_value), [package_id])
        return {
            "ok": True,
            **strategy,
            "readOnly": True,
            "planOnly": True,
            "projectPath": project_value,
            "packageState": package_state,
            "canExecuteCommandInstall": bool(strategy.get("commandInstaller"))
            and bool(project_value),
            "canCreateInstallRequest": bool(project_value),
        }

    def diagnose_install(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = self._params(params)
        project_value = self._project_path(normalized)
        package_id = str(
            normalized.get("packageId") or normalized.get("package_id") or ""
        ).strip().lower()
        max_compile_errors = int(
            normalized.get("maxCompileErrors")
            or normalized.get("max_compile_errors")
            or 30
        )
        raw_text = "\n".join(
            str(normalized.get(key) or "")
            for key in (
                "stdoutSummary",
                "stdout_summary",
                "stderrSummary",
                "stderr_summary",
                "logText",
                "log_text",
            )
        )
        safe_text = str(self._ports.summarize_debug(raw_text))[:5000]
        warnings: list[str] = []
        try:
            package_status = self.package_manager_status({"projectPath": project_value})
        except Exception as exc:  # noqa: BLE001 - diagnostics survive partial failures.
            package_status = {"ok": False, "error": str(exc)}
            warnings.append(f"Package manager status failed: {exc}")
        try:
            compile_errors = self._ports.read_compile_errors(
                {"projectPath": project_value, "maxErrors": max_compile_errors}
            )
        except Exception as exc:  # noqa: BLE001
            compile_errors = {"ok": False, "error": str(exc)}
            warnings.append(f"Unity compile-error reader failed: {exc}")
        symptoms = self._classify_symptoms(safe_text, compile_errors, package_status)
        suggestions = self._build_fix_suggestions(symptoms, package_status, package_id)
        return {
            "ok": True,
            "schema": "vrcforge.package_install_diagnostics.v1",
            "readOnly": True,
            "projectPath": project_value,
            "packageId": package_id,
            "packageManager": self._ports.redact_support(package_status),
            "compileErrors": self._ports.redact_support(compile_errors),
            "symptoms": symptoms,
            "warnings": warnings,
            "suggestedFixPlans": suggestions,
            "repairPolicy": {
                "automaticRepair": False,
                "supervisedRepairOnly": True,
                "requiresPreviewApprovalCheckpointValidationRollback": True,
            },
        }

    @staticmethod
    def _classify_symptoms(
        log_text: str,
        compile_errors: dict[str, Any],
        package_status: dict[str, Any],
    ) -> list[dict[str, str]]:
        patterns: tuple[tuple[str, str, str, str], ...] = (
            ("network", r"\b(timeout|timed out|network|connection|ssl|tls|proxy|dns|unable to resolve)\b", "Package source/network failure", "Retry after checking network/proxy settings, then rerun package status."),
            ("manifest", r"\b(manifest|packages-lock|lock file|json|parse|invalid character|could not parse)\b", "Project manifest or lock-file problem", "Use the package manager UI/CLI to restore packages; any manifest edit must be a supervised repair plan."),
            ("dependency", r"\b(dependency|dependencies|version conflict|conflict|incompatible|resolution|resolve packages)\b", "Package dependency resolution problem", "Inspect Packages/manifest.json and packages-lock.json, then plan a dependency repair with checkpoint."),
            ("permission", r"\b(access denied|permission denied|unauthorized|read-only|being used by another process|locked)\b", "Filesystem permission or lock problem", "Close tools holding the project, check write permissions, then retry."),
            ("compile", r"\b(cs\d{4}|compile error|compilation failed|compiler|assembly)\b", "Unity compile error after package import", "Open the compile errors and generate a separate supervised fix plan."),
            ("unitypackage", r"\b(importpackage|unitypackage|assetdatabase\.importpackage|failed to import)\b", "UnityPackage import problem", "Inspect the UnityPackage/folder first, then import through VRCForge with checkpoint and rollback proof."),
        )
        status_error = ""
        if not package_status.get("ok"):
            status_error = json.dumps(
                {
                    "error": package_status.get("error"),
                    "hint": package_status.get("hint"),
                    "output": package_status.get("output"),
                },
                ensure_ascii=False,
            )
        haystack = (
            f"{log_text}\n{json.dumps(compile_errors, ensure_ascii=False)}\n{status_error}"
        ).lower()
        symptoms = [
            {"code": code, "title": title, "suggestion": suggestion}
            for code, pattern, title, suggestion in patterns
            if re.search(pattern, haystack, flags=re.IGNORECASE)
        ]
        if package_status.get("ok") and not package_status.get("preferredCli"):
            symptoms.append(
                {
                    "code": "no_vpm_cli",
                    "title": "No command-line VPM installer detected",
                    "suggestion": (
                        "Use the package manager UI, or install vrc-get/VCC CLI before "
                        "command-line package installs."
                    ),
                }
            )
        if not symptoms:
            symptoms.append(
                {
                    "code": "unknown",
                    "title": "No known package-install signature matched",
                    "suggestion": (
                        "Export a support bundle or rerun with debug logging enabled to "
                        "capture more context."
                    ),
                }
            )
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for symptom in symptoms:
            if symptom["code"] not in seen:
                seen.add(symptom["code"])
                unique.append(symptom)
        return unique

    @staticmethod
    def _build_fix_suggestions(
        symptoms: list[dict[str, str]],
        package_status: dict[str, Any],
        package_id: str,
    ) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        codes = {item.get("code") for item in symptoms}
        if "compile" in codes:
            suggestions.append({"id": "explain_compile_errors", "risk": "read_only", "tool": "vrcforge_get_compile_errors", "summary": "Read Unity compile errors and create a separate fix plan."})
        if {"manifest", "dependency"} & codes:
            suggestions.append({"id": "dependency_repair_plan", "risk": "plan_only", "tool": "vrcforge_package_manager_status", "summary": "Compare package manager status with manifest/lock state before any repair."})
        if "unitypackage" in codes:
            suggestions.append({"id": "unitypackage_import_plan", "risk": "plan_only", "tool": "vrcforge_plan_outfit_import", "summary": "Inspect the package and build a supervised import plan with rollback proof."})
        if package_id and package_status.get("preferredCli"):
            suggestions.append({"id": "retry_vpm_install_request", "risk": "approval_required", "tool": "vrcforge_install_vpm_package", "summary": f"Retry package install for {package_id} only through the approval/checkpoint path."})
        if not suggestions:
            suggestions.append({"id": "support_bundle", "risk": "read_only", "tool": "vrcforge_support_bundle", "summary": "Collect redacted diagnostics before attempting repair."})
        return suggestions

    def request_install(
        self,
        params: dict[str, Any] | None = None,
        *,
        agent_name: str = "external-agent",
    ) -> dict[str, Any]:
        normalized = self._params(params)
        plan = self.plan_install(normalized)
        if not plan.get("ok"):
            return plan
        if not plan.get("canExecuteCommandInstall"):
            return {
                "ok": False,
                "status": "blocked",
                "error": (
                    "No supported non-interactive VPM CLI is available for package install. "
                    "Use the UI handoff or prepare an agent-managed package-manager install first."
                ),
                "installPlan": plan,
            }

        package = plan.get("package") if isinstance(plan.get("package"), dict) else {}
        optimizer_package = bool(package.get("dependencyId"))
        never_auto_approve = bool(
            normalized.get("neverAutoApprove") or normalized.get("never_auto_approve")
        )
        explicit_request = bool(
            normalized.get("requiresExplicitApproval")
            or normalized.get("requires_explicit_approval")
            or never_auto_approve
        )
        explicit_policy: dict[str, Any] = {}
        if optimizer_package or explicit_request:
            explicit_policy = {
                "requires_explicit_approval": True,
                "never_auto_approve": never_auto_approve,
                "explicit_approval_reason": (
                    "Doctor package repair requires explicit user approval in every execution mode."
                    if never_auto_approve
                    else "Package install requests require explicit user approval when requested by policy."
                    if explicit_request
                    else "Optimizer package install requests require explicit user approval even when global auto mode is enabled."
                ),
            }

        return self._ports.create_apply_request(
            {
                "target_tool": "vrcforge_install_vpm_package",
                "arguments": {
                    "projectPath": plan.get("projectPath"),
                    "packageId": plan.get("packageId"),
                    "repository": plan.get("repository") or "",
                    "preferredManager": str(
                        normalized.get("preferredManager")
                        or normalized.get("preferred_manager")
                        or ""
                    ),
                    "includePrerelease": bool(
                        normalized.get("includePrerelease")
                        or normalized.get("include_prerelease")
                        or normalized.get("prerelease")
                    ),
                    "packageVersion": str(
                        normalized.get("packageVersion")
                        or normalized.get("package_version")
                        or ""
                    ).strip(),
                },
                "reason": (
                    f"Install VPM package {plan.get('packageId')} through VRCForge "
                    "supervised package manager flow."
                ),
                "preview": plan,
                "agent_name": agent_name,
                **explicit_policy,
            },
            internal_wrapper=True,
        )
