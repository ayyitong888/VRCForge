from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from bounded_process import BoundedProcessResult
from prepared_file_imports import (
    capture_directory,
    capture_regular_file,
    verify_directory,
    verify_regular_file,
)
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    install_prepared_calls,
    prepared_call,
    prepared_evidence,
)


VPM_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,100}$")
KNOWN_VPM_CLI_NAMES = ("vrc-get",)
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_VPM_PROCESS_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_VPM_INSTALL_TIMEOUT_SECONDS = 300
_VPM_JSON_READBACK_MAX_BYTES = 4 * 1024 * 1024


class RunBoundedProcessPort(Protocol):
    def __call__(
        self,
        argv: list[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        timeout_seconds: int,
        max_output_bytes: int,
        creationflags: int,
    ) -> BoundedProcessResult: ...


@dataclass(frozen=True, slots=True)
class PackageManagerDiscoveryPorts:
    """Read-only machine observation used by manager discovery."""

    get_environment_value: Callable[[str], str]
    find_executable: Callable[[str], str | None]
    is_file: Callable[[Path], bool]


@dataclass(frozen=True, slots=True)
class PackageDetectionPorts:
    """Bounded project-file reads used by addon package detection."""

    path_exists: Callable[[Path], bool]
    read_utf8_sig_text: Callable[[Path], str]


@dataclass(frozen=True, slots=True)
class VpmPackageInstallPreparationPorts:
    """Approval-time capabilities, including two bounded read-only CLI probes."""

    resolve_project_path: Callable[[dict[str, Any]], str]
    locate_managers: Callable[[], list[dict[str, Any]]]
    select_strategy: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]]
    detect_package: Callable[[Path | None, list[str]], dict[str, Any]]
    process_environment: Callable[[], Mapping[str, str]]
    run_probe_process: RunBoundedProcessPort
    creationflags: int = 0


@dataclass(frozen=True, slots=True)
class VpmPackageInstallExecutionPorts:
    """One approved child-process capability with bounded captured output."""

    detect_package: Callable[[Path | None, list[str]], dict[str, Any]]
    process_environment: Callable[[], Mapping[str, str]]
    run_install_process: RunBoundedProcessPort
    creationflags: int = 0


def _normalize_manager_path(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _add_package_manager(
    managers: list[dict[str, Any]],
    *,
    name: str,
    path: str,
    kind: str,
    label: str,
    supports_command_install: bool,
    supports_ui_handoff: bool,
    source: str,
) -> None:
    normalized = _normalize_manager_path(path)
    if not normalized:
        return
    key = (name, normalized.lower(), kind)
    if any(
        (
            item.get("name"),
            str(item.get("path") or "").lower(),
            item.get("kind"),
        )
        == key
        for item in managers
    ):
        return
    managers.append(
        {
            "name": name,
            "label": label,
            "path": normalized,
            "kind": kind,
            "source": source,
            "supportsCommandInstall": supports_command_install,
            "supportsUiHandoff": supports_ui_handoff,
        }
    )


class PackageManagerDiscoveryService:
    """Locate existing package managers without starting a process."""

    def __init__(self, ports: PackageManagerDiscoveryPorts) -> None:
        self._ports = ports

    def _existing_app_paths(self, candidates: list[Path]) -> list[str]:
        paths: list[str] = []
        for candidate in candidates:
            try:
                if self._ports.is_file(candidate):
                    paths.append(str(candidate))
            except OSError:
                continue
        return paths

    def locate(self) -> list[dict[str, Any]]:
        managers: list[dict[str, Any]] = []
        managed_vrc_get = Path(
            self._ports.get_environment_value("VRCFORGE_VRC_GET_PATH") or ""
        )
        managed_candidates = [
            managed_vrc_get,
            Path(self._ports.get_environment_value("LOCALAPPDATA") or "")
            / "VRCForge"
            / "package-tools"
            / "vrc-get"
            / "v1.9.1"
            / "vrc-get.exe",
        ]
        for candidate in managed_candidates:
            try:
                if candidate and self._ports.is_file(candidate):
                    _add_package_manager(
                        managers,
                        name="vrc-get",
                        path=str(candidate),
                        kind="managed-cli",
                        label="VRCForge managed vrc-get CLI",
                        supports_command_install=True,
                        supports_ui_handoff=False,
                        source="vrcforge-managed",
                    )
            except OSError:
                continue

        cli_specs = {
            "vpm": ("VCC vpm CLI", True),
            "vrc-get": ("vrc-get CLI", True),
            "alcom": ("ALCOM CLI/UI", False),
        }
        for name, (label, supports_install) in cli_specs.items():
            path = self._ports.find_executable(name)
            if path:
                _add_package_manager(
                    managers,
                    name=name,
                    path=path,
                    kind="cli",
                    label=label,
                    supports_command_install=supports_install,
                    supports_ui_handoff=name == "alcom",
                    source="PATH",
                )

        local_app_data = Path(
            self._ports.get_environment_value("LOCALAPPDATA") or ""
        )
        program_files = Path(
            self._ports.get_environment_value("ProgramFiles") or ""
        )
        program_files_x86 = Path(
            self._ports.get_environment_value("ProgramFiles(x86)") or ""
        )
        for path in self._existing_app_paths(
            [
                local_app_data
                / "Programs"
                / "VRChat Creator Companion"
                / "CreatorCompanion.exe",
                local_app_data
                / "VRChat Creator Companion"
                / "CreatorCompanion.exe",
                program_files
                / "VRChat Creator Companion"
                / "CreatorCompanion.exe",
                program_files_x86
                / "VRChat Creator Companion"
                / "CreatorCompanion.exe",
            ]
        ):
            _add_package_manager(
                managers,
                name="vcc",
                path=path,
                kind="app",
                label="VRChat Creator Companion",
                supports_command_install=False,
                supports_ui_handoff=True,
                source="well-known-path",
            )
        for path in self._existing_app_paths(
            [
                local_app_data / "Programs" / "ALCOM" / "ALCOM.exe",
                local_app_data / "ALCOM" / "ALCOM.exe",
                program_files / "ALCOM" / "ALCOM.exe",
                program_files_x86 / "ALCOM" / "ALCOM.exe",
            ]
        ):
            _add_package_manager(
                managers,
                name="alcom",
                path=path,
                kind="app",
                label="ALCOM",
                supports_command_install=False,
                supports_ui_handoff=True,
                source="well-known-path",
            )
        return managers


class PackageDetectionService:
    """Detect an addon from embedded, VPM, then UPM project state."""

    def __init__(self, ports: PackageDetectionPorts) -> None:
        self._ports = ports

    def detect(
        self,
        project_path: Path | None,
        package_ids: list[str],
    ) -> dict[str, Any]:
        info: dict[str, Any] = {
            "installed": False,
            "packageId": "",
            "version": "",
            "source": "",
        }
        if project_path is None:
            info["warning"] = "No Unity project selected; package detection skipped."
            return info
        packages_dir = project_path / "Packages"
        for package_id in package_ids:
            embedded = packages_dir / package_id / "package.json"
            if self._ports.path_exists(embedded):
                try:
                    data = json.loads(self._ports.read_utf8_sig_text(embedded))
                except (OSError, json.JSONDecodeError):
                    data = {}
                info.update(
                    {
                        "installed": True,
                        "packageId": package_id,
                        "version": str(data.get("version") or ""),
                        "source": "embedded",
                    }
                )
                return info
        for manifest_name, source in (
            ("vpm-manifest.json", "vpm"),
            ("manifest.json", "upm"),
        ):
            manifest_path = packages_dir / manifest_name
            if not self._ports.path_exists(manifest_path):
                continue
            try:
                manifest = json.loads(self._ports.read_utf8_sig_text(manifest_path))
            except (OSError, json.JSONDecodeError):
                continue
            sections = (
                [manifest.get("locked"), manifest.get("dependencies")]
                if source == "vpm"
                else [manifest.get("dependencies")]
            )
            for section in sections:
                if not isinstance(section, dict):
                    continue
                for package_id in package_ids:
                    entry = section.get(package_id)
                    if entry is None:
                        continue
                    version = entry.get("version") if isinstance(entry, dict) else entry
                    info.update(
                        {
                            "installed": True,
                            "packageId": package_id,
                            "version": str(version or ""),
                            "source": source,
                        }
                    )
                    return info
        return info


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
        return resolve_project_path(params, self._ports.selected_project_path())

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

    def select_strategy(
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
        strategy = self.select_strategy(normalized, managers)
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


def resolve_project_path(
    params: Mapping[str, Any] | None,
    selected_project_path: str,
) -> str:
    """Resolve an explicit project path before the app-owned current selection."""

    normalized = params or {}
    return str(
        normalized.get("project_path")
        or normalized.get("projectPath")
        or selected_project_path
        or ""
    ).strip()


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_unity_project_root(path: Path) -> bool:
    return (
        (path / "Assets").is_dir()
        and (path / "Packages").is_dir()
        and (path / "ProjectSettings").is_dir()
    )


def _sealed_vpm_file_identity(path: Path) -> dict[str, Any]:
    identity, digest = capture_regular_file(path, label="VPM CLI")
    return {"identity": identity, "sha256": digest}


def _sealed_vpm_project_state(
    project_path: Path,
    package_id: str,
    detect_package: Callable[[Path | None, list[str]], dict[str, Any]],
) -> dict[str, Any]:
    manifest = project_path / "Packages" / "manifest.json"
    vpm_manifest = project_path / "Packages" / "vpm-manifest.json"
    lock = project_path / "Packages" / "packages-lock.json"
    if not manifest.is_file():
        raise RuntimeError(
            "Unity project Packages/manifest.json is required for package install."
        )
    embedded_root = project_path / "Packages" / package_id
    embedded_manifest = embedded_root / "package.json"
    embedded = None
    if embedded_root.exists() or embedded_root.is_symlink():
        embedded = {
            "directory": capture_directory(
                embedded_root,
                label="Embedded VPM package",
            ),
            "packageJson": _sealed_vpm_file_identity(embedded_manifest),
        }
    return {
        "project": capture_directory(project_path, label="Unity project"),
        "assets": capture_directory(project_path / "Assets", label="Unity Assets"),
        "packages": capture_directory(
            project_path / "Packages",
            label="Unity Packages",
        ),
        "manifest": _sealed_vpm_file_identity(manifest),
        "vpmManifest": (
            _sealed_vpm_file_identity(vpm_manifest)
            if vpm_manifest.is_file()
            else None
        ),
        "lock": _sealed_vpm_file_identity(lock) if lock.is_file() else None,
        "embedded": embedded,
        "packageState": detect_package(project_path, [package_id]),
    }


def _vpm_process_env(
    cli_path: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Minimal inherited environment; CLI-local config owns authentication."""

    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    )
    env = {key: environment[key] for key in allowed if environment.get(key)}
    env["PATH"] = str(cli_path.parent)
    return env


def _run_vpm_process(
    runner: RunBoundedProcessPort,
    process_environment: Callable[[], Mapping[str, str]],
    creationflags: int,
    argv: list[str],
    *,
    cwd: str,
    timeout_seconds: int,
) -> BoundedProcessResult:
    """Run one explicitly owned child with fixed handles, limits and auth scope."""

    return runner(
        argv,
        cwd=cwd,
        env=_vpm_process_env(Path(argv[0]), process_environment()),
        timeout_seconds=timeout_seconds,
        max_output_bytes=_VPM_PROCESS_MAX_OUTPUT_BYTES,
        creationflags=creationflags,
    )


def _semver_precedence(value: str) -> tuple[Any, ...] | None:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        return None
    prerelease = match.group(4)
    prerelease_identifiers = prerelease.split(".") if prerelease else []
    if any(
        identifier.isdigit()
        and len(identifier) > 1
        and identifier.startswith("0")
        for identifier in prerelease_identifiers
    ):
        return None
    prerelease_key = tuple(
        (0, int(identifier), "") if identifier.isdigit() else (1, 0, identifier)
        for identifier in prerelease_identifiers
    )
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        prerelease_key,
    )


def select_sealed_vpm_version(
    info: dict[str, Any],
    requested: str,
    include_prerelease: bool,
) -> str:
    versions = info.get("versions") or info.get("Versions") or []
    candidates: list[tuple[tuple[Any, ...], str]] = []
    for item in versions:
        if isinstance(item, str):
            value, yanked = item.strip(), False
        elif isinstance(item, dict):
            value = str(item.get("version") or item.get("Version") or "").strip()
            yanked = bool(item.get("yanked") or item.get("isYanked"))
        else:
            continue
        precedence = _semver_precedence(value)
        if (
            not value
            or yanked
            or precedence is None
            or (not include_prerelease and "-" in value)
        ):
            continue
        candidates.append((precedence, value))
    if requested:
        if requested not in [value for _key, value in candidates]:
            raise RuntimeError(
                "Requested packageVersion is unavailable, yanked, or excluded by "
                "prerelease policy."
            )
        return requested
    if not candidates:
        raise RuntimeError(
            "No non-yanked package version is available through the selected "
            "existing repository configuration."
        )
    return max(candidates)[1]


def _vpm_install_argv(
    *,
    cli_name: str,
    cli_path: str,
    project_path: str,
    package_id: str,
    package_version: str,
    include_prerelease: bool,
) -> list[str]:
    if cli_name == "vrc-get":
        return [cli_path, "install", "-p", project_path, "-y"] + (
            ["--prerelease"] if include_prerelease else []
        ) + [package_id, package_version]
    raise RuntimeError("Prepared VPM CLI name is invalid.")


def _read_bounded_vpm_json(path: Path, label: str) -> dict[str, Any]:
    identity = _sealed_vpm_file_identity(path)
    if (
        int(_ensure_dict(identity.get("identity")).get("size") or 0)
        > _VPM_JSON_READBACK_MAX_BYTES
    ):
        raise RuntimeError(f"{label} exceeds the fixed readback size limit.")
    raw = path.read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != identity.get("sha256")
        or _sealed_vpm_file_identity(path) != identity
    ):
        raise RuntimeError(f"{label} drifted during readback.")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object.")
    return payload


def _package_entry_version(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(
            entry.get("version") or entry.get("hash") or entry.get("url") or ""
        ).strip()
    return str(entry or "").strip()


def _read_vpm_install_readback(
    project_path: Path,
    package_id: str,
    expected_version: str,
) -> dict[str, Any]:
    vpm_manifest_path = project_path / "Packages" / "vpm-manifest.json"
    manifest = _read_bounded_vpm_json(
        vpm_manifest_path,
        "VPM manifest readback",
    )
    locked_entry = _ensure_dict(manifest.get("locked")).get(package_id)
    locked_version = _package_entry_version(locked_entry)
    if locked_version != expected_version:
        raise RuntimeError(
            "VPM manifest locked readback does not prove the exact approved package "
            "version."
        )
    dependency_entry = _ensure_dict(manifest.get("dependencies")).get(package_id)
    dependency_version = _package_entry_version(dependency_entry)
    if dependency_entry is not None and dependency_version != expected_version:
        raise RuntimeError(
            "VPM manifest dependency readback does not match the exact approved "
            "package version."
        )
    embedded_readback = None
    embedded_root = project_path / "Packages" / package_id
    if embedded_root.exists() or embedded_root.is_symlink():
        embedded_identity = capture_directory(
            embedded_root,
            label="Installed embedded VPM package",
        )
        embedded_manifest = _read_bounded_vpm_json(
            embedded_root / "package.json",
            "Embedded VPM package readback",
        )
        if (
            str(embedded_manifest.get("name") or "") != package_id
            or str(embedded_manifest.get("version") or "") != expected_version
        ):
            raise RuntimeError(
                "Embedded VPM package readback does not match the exact approved "
                "package/version."
            )
        embedded_readback = {
            "directory": embedded_identity,
            "name": package_id,
            "version": expected_version,
        }
    return {
        "packageId": package_id,
        "version": expected_version,
        "source": "vpm-manifest.locked",
        "embeddedPackage": embedded_readback,
    }


class VpmPackageInstallPreparer:
    """Freeze exact CLI/project/version evidence before approval.

    The only processes available here are two bounded read-only probes against
    the already discovered CLI. No install process or project writer is held.
    """

    def __init__(self, ports: VpmPackageInstallPreparationPorts) -> None:
        self._ports = ports

    def _cli_version_and_package_info(
        self,
        cli_path: str,
        package_id: str,
    ) -> tuple[str, dict[str, Any]]:
        cwd = str(Path(cli_path).parent)
        version = _run_vpm_process(
            self._ports.run_probe_process,
            self._ports.process_environment,
            self._ports.creationflags,
            [cli_path, "--version"],
            cwd=cwd,
            timeout_seconds=10,
        )
        if version.returncode != 0:
            raise RuntimeError("Selected VPM CLI version probe failed.")
        info = _run_vpm_process(
            self._ports.run_probe_process,
            self._ports.process_environment,
            self._ports.creationflags,
            [cli_path, "info", "package", "--no-update", package_id],
            cwd=cwd,
            timeout_seconds=30,
        )
        if info.returncode != 0:
            raise RuntimeError(
                "Package is not visible through the selected existing CLI "
                "repository configuration."
            )
        if version.stdout_truncated or version.stderr_truncated or info.stdout_truncated:
            raise RuntimeError(
                "Selected VPM CLI probe output exceeded its fixed capture limit."
            )
        try:
            payload = json.loads(info.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Selected VPM CLI package-info output is not JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Selected VPM CLI package-info output is invalid.")
        return (version.stdout or version.stderr or "").strip()[:256], payload

    def prepare(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]:
        del preview
        if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
            raise RuntimeError(
                "Caller may not provide the reserved prepared Unity execution key."
            )
        package_id = str(
            arguments.get("packageId") or arguments.get("package_id") or ""
        ).strip().lower()
        if not VPM_PACKAGE_ID_RE.match(package_id):
            raise RuntimeError("packageId is invalid.")
        project_path = Path(
            os.path.abspath(
                Path(self._ports.resolve_project_path(arguments)).expanduser()
            )
        )
        if not _is_unity_project_root(project_path):
            raise RuntimeError("A valid Unity projectPath is required.")
        repository = str(
            arguments.get("repository") or arguments.get("vpmRepository") or ""
        ).strip()
        if repository:
            raise RuntimeError(
                "Repository changes are not allowed in the sealed package-install "
                "lane; configure existing repositories separately."
            )
        managers = self._ports.locate_managers()
        strategy = self._ports.select_strategy(arguments, managers)
        cli = (
            strategy.get("commandInstaller")
            if isinstance(strategy.get("commandInstaller"), dict)
            else None
        )
        if not cli or str(cli.get("name")) not in KNOWN_VPM_CLI_NAMES:
            raise RuntimeError(
                "Only a discovered fixed vpm or vrc-get command installer may be "
                "approved."
            )
        binary = _sealed_vpm_file_identity(Path(str(cli.get("path") or "")))
        cli_version, package_info = self._cli_version_and_package_info(
            str(binary["identity"]["path"]),
            package_id,
        )
        prerelease = bool(
            arguments.get("includePrerelease")
            or arguments.get("include_prerelease")
            or arguments.get("prerelease")
        )
        selected_version = select_sealed_vpm_version(
            package_info,
            str(
                arguments.get("packageVersion")
                or arguments.get("package_version")
                or ""
            ).strip(),
            prerelease,
        )
        state = _sealed_vpm_project_state(
            project_path,
            package_id,
            self._ports.detect_package,
        )
        argv = _vpm_install_argv(
            cli_name=str(cli["name"]),
            cli_path=str(binary["identity"]["path"]),
            project_path=str(state["project"]["path"]),
            package_id=package_id,
            package_version=selected_version,
            include_prerelease=prerelease,
        )
        approval_arguments = dict(arguments)
        approval_arguments["packageVersion"] = selected_version
        prepared = install_prepared_calls(
            approval_arguments,
            [
                (
                    "external.vpm.install",
                    {
                        "argv": argv,
                        "cwd": str(state["project"]["path"]),
                        "timeoutSeconds": _VPM_INSTALL_TIMEOUT_SECONDS,
                    },
                )
            ],
            {
                "binary": binary,
                "cliName": cli["name"],
                "cliVersion": cli_version,
                "project": state,
                "packageId": package_id,
                "packageVersion": selected_version,
                "includePrerelease": prerelease,
                "repository": "",
            },
        )
        return prepared, {
            "ok": True,
            "targetTool": "vrcforge_install_vpm_package",
            "projectPath": str(state["project"]["path"]),
            "packageId": package_id,
            "packageVersion": selected_version,
            "command": argv,
            "processPolicy": {
                "scope": "one synchronous child owned by this approved request",
                "timeoutSeconds": _VPM_INSTALL_TIMEOUT_SECONDS,
                "maxOutputBytesPerStream": _VPM_PROCESS_MAX_OUTPUT_BYTES,
                "shell": False,
                "projectOnly": True,
                "authentication": "CLI existing local user configuration only",
            },
        }


class VpmPackageInstallExecutor:
    """Execute exactly one approval-bound install child and verify readback."""

    def __init__(self, ports: VpmPackageInstallExecutionPorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        process_started = False
        try:
            evidence = prepared_evidence(params)
            if not isinstance(evidence, dict):
                raise RuntimeError("Prepared VPM install evidence is invalid.")
            tool_name, call = prepared_call(params)
            if tool_name != "external.vpm.install" or not isinstance(call, dict):
                raise RuntimeError("Prepared VPM install command is invalid.")
            argv = call.get("argv")
            cwd = call.get("cwd")
            timeout = call.get("timeoutSeconds")
            if (
                not isinstance(argv, list)
                or not all(isinstance(item, str) for item in argv)
                or not isinstance(cwd, str)
                or timeout != _VPM_INSTALL_TIMEOUT_SECONDS
            ):
                raise RuntimeError("Prepared VPM install command is invalid.")
            binary_evidence = _ensure_dict(evidence.get("binary"))
            binary_path = verify_regular_file(
                _ensure_dict(binary_evidence.get("identity")),
                str(binary_evidence.get("sha256") or ""),
                label="VPM CLI",
            )
            package_id = str(evidence.get("packageId") or "")
            package_version = str(evidence.get("packageVersion") or "")
            project_evidence = _ensure_dict(evidence.get("project"))
            project_path = verify_directory(
                project_evidence.get("project") or {},
                label="Unity project",
            )
            verify_directory(
                project_evidence.get("assets") or {},
                label="Unity Assets",
            )
            verify_directory(
                project_evidence.get("packages") or {},
                label="Unity Packages",
            )
            expected_argv = _vpm_install_argv(
                cli_name=str(evidence.get("cliName") or ""),
                cli_path=str(binary_path),
                project_path=str(project_path),
                package_id=package_id,
                package_version=package_version,
                include_prerelease=bool(evidence.get("includePrerelease")),
            )
            if argv != expected_argv or cwd != str(project_path):
                raise RuntimeError(
                    "Prepared VPM install argv/cwd drifted from the approved semantic "
                    "command."
                )
            if (
                _sealed_vpm_project_state(
                    project_path,
                    package_id,
                    self._ports.detect_package,
                )
                != evidence.get("project")
            ):
                raise RuntimeError(
                    "Prepared VPM project manifest state drifted after approval."
                )
            process_started = True
            proc = _run_vpm_process(
                self._ports.run_install_process,
                self._ports.process_environment,
                self._ports.creationflags,
                argv,
                cwd=str(project_path),
                timeout_seconds=_VPM_INSTALL_TIMEOUT_SECONDS,
            )
            result = {
                "ok": proc.returncode == 0,
                "exitCode": proc.returncode,
                "stdoutSummary": (proc.stdout or "")[-1500:],
                "stderrSummary": (proc.stderr or "")[-1500:],
                "stdoutTruncated": proc.stdout_truncated,
                "stderrTruncated": proc.stderr_truncated,
                "projectPath": str(project_path),
                "packageId": package_id,
                "packageVersion": package_version,
                "command": argv,
                "unityRefreshRequired": True,
                "recovery": {
                    "checkpointMustBeRestoredOnlyIfUserChooses": proc.returncode != 0,
                    "committed": True,
                    "commitState": (
                        "committed" if proc.returncode == 0 else "unknown"
                    ),
                },
            }
            if proc.returncode != 0:
                return result
            readback = _read_vpm_install_readback(
                project_path,
                package_id,
                package_version,
            )
            return {**result, "vpmManifestReadback": readback}
        except (
            RuntimeError,
            OSError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "unityRefreshRequired": True,
                "recovery": {
                    "checkpointMustBeRestoredOnlyIfUserChooses": process_started,
                    "committed": process_started,
                    "commitState": "unknown" if process_started else "not_started",
                },
            }
