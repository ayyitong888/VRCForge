from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping, Protocol

from agent_gateway import AgentGatewayError
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
from package_legacy_baseline import verify_legacy_baseline, create_legacy_preservation_snapshot
from uuid import uuid4


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
_RUNTIME_COMPATIBILITY_ERROR_MAX_CHARS = 2000
_VPM_REPOSITORY_INPUT_REJECTION = (
    "Repository changes cannot be made by an install request. No installation was started. "
    "Omit repository and vpmRepository and retry with the same package ID and version using the configured sources. "
    "A new source requires a separate registration step if the package cannot be resolved."
)
_UPM_RESOLUTION_GUIDANCE = (
    "Open the selected project with its matching Unity Editor and let Unity Package Manager resolve "
    "the declared dependency. If resolution fails, inspect the Editor's package error."
)


def _requests_vpm_repository(arguments: dict[str, Any]) -> bool:
    return any(str(arguments.get(field) or "").strip() for field in ("repository", "vpmRepository"))


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
        started: Event | None = None,
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
            # The VCC `vpm` executable is discovered for diagnostics only.  The
            # prepared executor has no sealed argv adapter for it, so claiming
            # command-install support here would lie to external Agents.
            "vpm": ("VCC vpm CLI (discovery only)", False),
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
            info["installed"] = None
            info["warning"] = "No Unity project selected; package detection skipped."
            return info

        def record_read_error(path: Path, error: Exception, *, directory: bool = False) -> None:
            info.setdefault("readErrors", []).append({"path": str(path), "error": str(error), "isDirectory": directory})
            info["warning"] = "Some package state could not be read; do not infer package absence from this result."
            info["installed"] = None

        def read_object(path: Path) -> dict[str, Any] | None:
            try:
                data = json.loads(self._ports.read_utf8_sig_text(path))
                if not isinstance(data, dict):
                    raise ValueError("Package JSON root must be an object.")
                return data
            except (OSError, ValueError, UnicodeError) as exc:
                record_read_error(path, exc)
                return None

        packages_dir = project_path / "Packages"
        upm_declaration = None
        vpm_manifest = packages_dir / "vpm-manifest.json"
        vpm = read_object(vpm_manifest) if self._ports.path_exists(vpm_manifest) else None
        # Unity identifies embedded packages by their manifest name; folder names may differ.
        # Older VPM packages can also keep a package manifest one level below Assets.
        for root, source in ((packages_dir, "embedded"), (project_path / "Assets", "assets")):
            try:
                candidates = root.iterdir() if root.is_dir() else ()
                for directory in candidates:
                    manifest_path = directory / "package.json"
                    if not self._ports.path_exists(manifest_path):
                        continue
                    manifest = read_object(manifest_path)
                    if manifest is None:
                        continue
                    package_id = str(manifest.get("name") or "").strip().lower()
                    if source == "embedded" and directory.name in package_ids and package_id != directory.name:
                        record_read_error(manifest_path, ValueError(f"Expected {directory.name}; package manifest names {package_id!r}."))
                    if package_id in package_ids:
                        installed_version = str(manifest.get("version") or "")
                        result = {**info, "installed": True, "packageId": package_id, "version": installed_version,
                                  "source": source, "path": manifest_path.as_posix()}
                        locked_version = _package_entry_version(_ensure_dict((vpm or {}).get("locked")).get(package_id))
                        if locked_version and locked_version != installed_version:
                            result.update(resolution="version_mismatch", lockedVersion=locked_version,
                                          warning=f"{package_id} is installed at {installed_version}, but VPM locked {locked_version}.")
                        return result
                    dependencies = manifest.get("dependencies")
                    if source == "embedded" and isinstance(dependencies, dict):
                        match = next((name for name in package_ids if name in dependencies), None)
                        if match:
                            upm_declaration = (match, dependencies[match], manifest_path)
            except OSError as exc:
                record_read_error(root, exc, directory=True)

        vpm_lock = None
        readable_manifest = False
        lock_enabled = True
        for manifest_name, source in (
            ("vpm-manifest.json", "vpm"),
            ("manifest.json", "upm"),
            ("packages-lock.json", "packages-lock"),
        ):
            manifest_path = packages_dir / manifest_name
            if source == "packages-lock" and not lock_enabled:
                continue
            if not self._ports.path_exists(manifest_path):
                continue
            manifest = vpm if manifest_path == vpm_manifest else read_object(manifest_path)
            if manifest is None:
                continue
            if source == "upm":
                lock_enabled = manifest.get("enableLockFile") is not False
            sections = ("locked", "dependencies") if source == "vpm" else ("dependencies",)
            for section_name in sections:
                section = manifest.get(section_name)
                if not isinstance(section, dict):
                    if section is not None:
                        record_read_error(manifest_path, ValueError("Package dependency section must be an object."))
                    continue
                readable_manifest = True
                for package_id in package_ids:
                    entry = section.get(package_id)
                    if entry is None:
                        continue
                    if source == "upm":
                        upm_declaration = (package_id, entry, manifest_path)
                        continue
                    if source == "vpm":
                        if section_name == "locked":
                            vpm_lock = (package_id, entry)
                        continue
                    if vpm_lock or isinstance(entry, dict) and entry.get("source") == "embedded":
                        # The embedded package the lock refers to has no matching files on disk.
                        continue
                    info.update(
                        {
                            "installed": True,
                            "packageId": package_id,
                            "version": _package_entry_version(entry),
                            "source": source,
                        }
                    )
                    return info
        if vpm_lock is not None:
            package_id, entry = vpm_lock
            info.update(packageId=package_id, lockedVersion=_package_entry_version(entry), source="vpm")
            if info["installed"] is False:
                info["resolution"] = "restore_required"
            return info
        if upm_declaration is not None:
            package_id, requested, declaring_file = upm_declaration
            info.update({
                "installed": None, "resolution": "pending", "packageId": package_id,
                "source": "upm", "requestedVersion": _package_entry_version(requested),
                "declaredBy": declaring_file.relative_to(project_path).as_posix(),
            })
        elif not readable_manifest and not info.get("readErrors"):
            info.update(installed=None, warning="No readable project package manifest or lock is available.")
        elif not lock_enabled and info["installed"] is False:
            info.update(installed=None, warning="This project disables the UPM lock. Use the running Editor's package state to confirm resolution.")
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
    sdk_package_ids: tuple[str, ...] | list[str] = ()


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
        if preferred in {"vcc", "alcom"}:
            if selected_handoff and selected_handoff["name"] == preferred:
                strategy = "ui_handoff"
                message = f"The user selected {preferred}. Use that manager for this package."
            else:
                strategy = "agent_download" if allow_agent else "manual_handoff"
                message = f"The selected {preferred} manager was not detected. Check host tools for authorized setup of that manager."
        elif selected_cli:
            strategy = "command"
            message = "Use vrc-get through the current permission policy and checkpoint flow. Package availability is checked while preparing the request."
        elif allow_agent:
            strategy = "agent_download"
            message = "Check for an authorized host tool to install vrc-get from its official distribution. After installing it, repeat this package plan."
        elif selected_handoff:
            strategy = "ui_handoff"
            message = f"No supported CLI was detected. Use an available desktop tool to operate {selected_handoff['name']}. Ask for a user action only if the host cannot perform it."
        else:
            strategy = "manual_handoff"
            message = "No supported package manager was detected. Check for an authorized host installation tool before requesting manual setup."
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
            "executionStrategy": strategy,
            "preferredManager": selected_cli if strategy == "command" else selected_handoff if strategy == "ui_handoff" else None,
            "commandInstaller": selected_cli,
            "uiHandoff": selected_handoff,
            "managers": managers,
            "allowAgentManagedDownload": allow_agent,
            "directManifestEditing": False,
            "requiresApproval": True,
            "requiresCheckpoint": True,
            "message": message,
            "agentManagedDownload": {
                "available": None if strategy == "agent_download" else False,
                "requiresCallerHostTool": strategy == "agent_download",
                "afterSetupTool": "vrcforge_package_install_plan",
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
        package_id = str(normalized.get("packageId") or normalized.get("package_id") or "").strip().lower()
        if package_id and not VPM_PACKAGE_ID_RE.fullmatch(package_id):
            return {"ok": False, "error": "packageId must be a valid VPM package id, for example nadena.dev.modular-avatar."}
        for known_id in dict.fromkeys([*self._ports.sdk_package_ids, *([package_id] if package_id else [])]):
            existing = next((packages[name] for name, spec in self._ports.addon_frameworks.items()
                             if spec["packageIds"] == [known_id] or packages[name].get("packageId") == known_id), None)
            packages[known_id] = existing if existing is not None else self._ports.detect_package(project_path, [known_id])
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
            "canRequestInstall": bool(usable) and bool(project_value),
            "packages": packages,
            "knownOptimizationPackages": self._optimizer_package_catalog(),
            "installPolicy": {
                "managerPriority": [
                    "vrc-get CLI for user-requested installs under the current App permission policy",
                    "agent-managed CLI setup with an authorized host tool if the CLI is missing",
                    "selected graphical manager through available desktop tools, or a necessary user interaction",
                ],
                "directManifestEditing": False,
                "requiresApprovalCheckpoint": True,
            },
            "hint": (
                "For a user-requested package installation, read vrcforge_package_install_plan for the exact "
                "project/package using configured sources (omit repository), with preferredManager set to preferredCommandInstaller.name when a command "
                "installer is available, then use vrcforge_package_install_request with the same preference "
                "when its command-install path is ready. "
                "The request applies the current App permission policy and checkpoint flow; external MCP Gateway "
                "enablement is not required for the built-in Agent. A previously rejected request is historical "
                "evidence: use the current plan/receipt to decide whether installation is blocked. "
                "If the CLI is missing, check host installation or desktop tools and continue with any available authorized route. "
                "Manager detection alone is neither installation permission nor proof that a package was installed."
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
        explicit_repository = _requests_vpm_repository(normalized)
        package_state = None
        if project_value and Path(project_value).is_dir():
            package_state = self._ports.detect_package(Path(project_value), [package_id])
        installed = bool(
            isinstance(package_state, dict) and package_state.get("installed")
        )
        unreadable_state = bool(package_state is not None and package_state.get("installed") is None and not package_state.get("resolution"))
        installed_version = str((package_state or {}).get("version") or "")
        requested_version = str(
            normalized.get("packageVersion")
            or normalized.get("package_version")
            or ""
        ).strip()
        upgrade_requested = bool(
            normalized.get("upgrade") or normalized.get("upgradePackage")
            or normalized.get("upgrade_package")
        )
        restore_version = _missing_vpm_locked_version(package_state or {}, requested_version)
        installed_key = _semver_precedence(installed_version)
        requested_key = _semver_precedence(requested_version)
        upgrade_available = bool(
            installed and upgrade_requested and requested_key
            and (not installed_key or requested_key > installed_key)
        )
        assets_migration_required = bool(
            upgrade_available and str((package_state or {}).get("source") or "") == "assets"
        )
        baseline_ok = False
        baseline_report: dict[str, Any] | None = None
        if assets_migration_required:
            archive = str(normalized.get("legacyBaselineArchive") or normalized.get("legacy_baseline_archive") or "").strip()
            root = str(normalized.get("legacyBaselineAssetsRoot") or normalized.get("legacy_baseline_assets_root") or "").strip()
            detected_root = str(Path(str((package_state or {}).get("path") or "")).parent)
            if archive and root and detected_root and Path(root).expanduser().resolve() == Path(detected_root).expanduser().resolve():
                try:
                    baseline = verify_legacy_baseline(archive, root)
                    baseline_ok = bool(baseline.get("ok"))
                    baseline_report = {
                        key: baseline[key]
                        for key in ("ok", "treeDigest", "baselineDigest", "missing", "unknown", "modified")
                    }
                except (OSError, tarfile.TarError, ValueError) as exc:
                    baseline_ok = False
                    baseline_report = {"ok": False, "error": str(exc)}
        preserve_legacy = normalized.get("preserveLegacyFiles") is True
        preservation_ready = bool(preserve_legacy and baseline_report and "treeDigest" in baseline_report)
        assets_migration_required = assets_migration_required and not (baseline_ok or preservation_ready)
        blocker = None
        upm_pending = (package_state or {}).get("resolution") == "pending"
        upm_upgrade = upgrade_requested and (package_state or {}).get("source") == "packages-lock"
        version_conflict = (package_state or {}).get("resolution") == "version_mismatch"
        if version_conflict and not (upgrade_requested and requested_version):
            action = "inspect_version_mismatch"
            blocker = {"code": "vpm_version_mismatch", "message": (
                f"Installed {installed_version}; VPM locked {package_state['lockedVersion']}. "
                "Determine the intended version before changing this package."
            )}
        elif upm_pending or upm_upgrade:
            blocker = {"code": "unity_package_manager_required", "message": _UPM_RESOLUTION_GUIDANCE}
            strategy.update(strategy="unity_package_manager", executionStrategy="unity_package_manager", preferredManager=None)
            action = "resolve_in_unity"
        elif unreadable_state:
            action = "inspect_package_state"
        elif restore_version:
            action = "restore_locked"
        elif assets_migration_required:
            action = "migration_required"
        elif upgrade_available:
            action = "upgrade"
        else:
            action = "use_installed" if installed else "install_missing"
        if action == "use_installed":
            strategy["message"] = f"Use the installed {package_id} {installed_version}."
        if explicit_repository:
            blocker = {
                "code": "vpm_repository_input_unsupported",
                "message": _VPM_REPOSITORY_INPUT_REJECTION,
            }
        return {
            "ok": True,
            **strategy,
            "readOnly": True,
            "planOnly": True,
            "projectPath": project_value,
            "packageState": package_state,
            "packageVersion": requested_version,
            "installedVersion": installed_version,
            "upgradeRequested": upgrade_requested,
            "legacyBaselineVerification": baseline_report,
            "legacyPreservationRequired": preservation_ready,
            "compatibilityAction": action,
            "canExecuteCommandInstall": strategy["executionStrategy"] == "command"
            and bool(project_value) and action in {"restore_locked", "upgrade", "install_missing"} and not blocker,
            "canCreateInstallRequest": strategy["executionStrategy"] == "command" and bool(project_value) and (not installed or upgrade_available)
            and not assets_migration_required and not unreadable_state and not blocker,
            "canPrepareUpgradeRequest": upgrade_available and not assets_migration_required
            and not unreadable_state and not blocker,
            **({"commandInstallBlocker": blocker, "message": blocker["message"]} if blocker else {}),
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
        original_error = safe_text.strip()[:_RUNTIME_COMPATIBILITY_ERROR_MAX_CHARS]
        original_error_sha256 = (
            hashlib.sha256(original_error.encode("utf-8")).hexdigest()
            if original_error
            else ""
        )
        warnings: list[str] = []
        try:
            package_status = self.package_manager_status({**normalized, "projectPath": project_value})
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
        package_state = (package_status.get("packages") or {}).get(package_id)
        read_errors = []
        for state in (package_status.get("packages") or {}).values():
            for error in state.get("readErrors") or []:
                if error not in read_errors:
                    read_errors.append(error)
        if read_errors:
            symptoms = [item for item in symptoms if item["code"] != "unknown"]
            symptoms.insert(0, {
                "code": "package_state_unreadable",
                "title": "Project package state could not be read completely",
                "suggestion": "Inspect the reported file errors before deciding whether a package needs installing.",
                "readErrors": read_errors,
            })
        if (package_id and project_value and Path(project_value).is_dir()
                and isinstance(package_state, dict) and package_state.get("installed") is False
                and not package_state.get("warning")):
            symptoms = [item for item in symptoms if item["code"] != "unknown"]
            symptoms.insert(0, {
                "code": "missing_package",
                "packageId": package_id,
                "title": f"Requested package {package_id} was not found in the project package state",
                "suggestion": "Plan installation of this package through the existing package manager; package absence alone does not establish the cause of a Core connection failure.",
            })
        if (package_state or {}).get("resolution") == "pending":
            symptoms = [item for item in symptoms if item["code"] not in {"unknown", "no_vpm_cli"}]
            symptoms.insert(0, {
                "code": "upm_resolution_pending", "title": f"Unity has not confirmed resolution of {package_id}",
                "suggestion": _UPM_RESOLUTION_GUIDANCE,
            })
        if compile_errors.get("ok") is False:
            symptoms = [item for item in symptoms if item["code"] != "unknown"]
            symptoms.append({
                "code": "compile_reader_unavailable",
                "title": "Unity compile diagnostics could not be read",
                "suggestion": "Use the returned host-side package facts and App diagnostics or Unity Console > Open Editor Log. Do not repeat the unavailable Core reader or treat this as zero compile errors.",
            })
        suggestions = self._build_fix_suggestions(symptoms, package_status, package_id)
        return {
            "ok": True,
            "schema": "vrcforge.package_install_diagnostics.v1",
            "readOnly": True,
            "projectPath": project_value,
            "packageId": package_id,
            "originalError": original_error,
            "originalErrorSha256": original_error_sha256,
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
                        "Check available host tools for installing vrc-get or operating the detected graphical manager."
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
        symptoms: list[dict[str, Any]],
        package_status: dict[str, Any],
        package_id: str,
    ) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        codes = {item.get("code") for item in symptoms}
        if "package_state_unreadable" in codes:
            for item in symptoms:
                for error in item.get("readErrors") or []:
                    suggestions.append({
                        "id": "inspect_package_state_file", "risk": "read_only",
                        "tool": "vrcforge_list_directory" if error.get("isDirectory") else "vrcforge_read_text_file",
                        "arguments": {"projectPath": package_status.get("projectPath", ""), "path": error["path"]},
                        "summary": error["error"],
                    })
        if "missing_package" in codes:
            suggestions.append({
                "id": "missing_package_install_plan", "risk": "plan_only",
                "tool": "vrcforge_package_install_plan",
                "arguments": {"projectPath": package_status.get("projectPath", ""), "packageId": package_id},
                "summary": f"Inspect the existing supervised installation plan for missing package {package_id}; do not install unrelated optional packages.",
            })
        if "compile" in codes and "compile_reader_unavailable" not in codes:
            suggestions.append({"id": "explain_compile_errors", "risk": "read_only", "tool": "vrcforge_get_compile_errors", "summary": "Read Unity compile errors and create a separate fix plan."})
        if {"manifest", "dependency"} & codes:
            suggestions.append({"id": "dependency_repair_plan", "risk": "plan_only", "tool": "vrcforge_package_manager_status", "summary": "Compare package manager status with manifest/lock state before any repair."})
        if "unitypackage" in codes:
            suggestions.append({"id": "unitypackage_import_plan", "risk": "plan_only", "tool": "vrcforge_plan_outfit_import", "summary": "Inspect the package and build a supervised import plan with rollback proof."})
        if "upm_resolution_pending" in codes:
            suggestions.append({"id": "resolve_in_unity", "risk": "plan_only", "summary": _UPM_RESOLUTION_GUIDANCE})
        if (package_id and package_status.get("preferredCli")
                and (package_status.get("packages") or {}).get(package_id, {}).get("installed") is False
                and not {"missing_package", "package_state_unreadable"} & codes):
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
        blocker = plan.get("commandInstallBlocker")
        if blocker:
            return {
                "ok": False, "status": "blocked", "errorCode": blocker["code"],
                "error": blocker["message"], "installPlan": plan,
            }
        package_state = (
            plan.get("packageState")
            if isinstance(plan.get("packageState"), dict)
            else {}
        )
        if plan.get("compatibilityAction") == "inspect_package_state":
            return {"ok": False, "status": "blocked", "error": "Package state could not be determined. Inspect project package metadata.", "installPlan": plan}
        installed = bool(package_state.get("installed"))
        upgrade_requested = bool(
            normalized.get("upgrade") or normalized.get("upgradePackage")
            or normalized.get("upgrade_package")
        )
        if installed and not upgrade_requested:
            return {
                "ok": True,
                "status": "use_installed",
                "approvalCreated": False,
                "packageState": package_state,
                "message": (
                    "The installed package version must be tried first. An upgrade "
                    "is not created from caller-supplied evidence. If the installed "
                    "version fails, preserve that runtime error and check newer "
                    "versions through the supervised package manager flow."
                ),
                "installPlan": plan,
            }
        if installed and not plan.get("canPrepareUpgradeRequest"):
            return {
                "ok": False,
                "status": "blocked",
                "error": (
                    "Assets-imported package upgrade requires an explicit migration plan; "
                    "the sealed CLI upgrade lane is fail-closed."
                    if str(package_state.get("source") or "") == "assets"
                    else "Upgrade requires upgrade=true and a newer valid packageVersion."
                ),
                "migrationRequired": str(package_state.get("source") or "") == "assets",
                "installPlan": plan,
            }
        if not plan.get("canExecuteCommandInstall"):
            blocked: dict[str, Any] = {
                "ok": False,
                "status": "blocked",
                "error": plan["message"],
                "installPlan": plan,
            }
            return blocked

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

        approval_arguments = {
            "projectPath": plan.get("projectPath"),
            "packageId": plan.get("packageId"),
            # The plan may expose a repository discovered from optimizer
            # metadata, but that is diagnostic/source context, not a request
            # to mutate the sealed package-manager configuration. Preserve
            # only a repository explicitly supplied by the caller so the
            # existing sealed-lane rejection remains authoritative.
            "repository": str(
                normalized.get("repository")
                or normalized.get("vpmRepository")
                or ""
            ).strip(),
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
        }
        if upgrade_requested:
            approval_arguments["upgrade"] = True
            if normalized.get("preserveLegacyFiles") is True:
                approval_arguments["preserveLegacyFiles"] = True
            if normalized.get("legacyBaselineArchive") or normalized.get("legacy_baseline_archive"):
                approval_arguments["legacyBaselineArchive"] = str(normalized.get("legacyBaselineArchive") or normalized.get("legacy_baseline_archive") or "").strip()
            if normalized.get("legacyBaselineAssetsRoot") or normalized.get("legacy_baseline_assets_root"):
                approval_arguments["legacyBaselineAssetsRoot"] = str(normalized.get("legacyBaselineAssetsRoot") or normalized.get("legacy_baseline_assets_root") or "").strip()
        return self._ports.create_apply_request(
            {
                "target_tool": "vrcforge_install_vpm_package",
                "arguments": approval_arguments,
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
    started: Event | None = None,
) -> BoundedProcessResult:
    """Run one explicitly owned child with fixed handles, limits and auth scope."""

    return runner(
        argv,
        cwd=cwd,
        env=_vpm_process_env(Path(argv[0]), process_environment()),
        timeout_seconds=timeout_seconds,
        max_output_bytes=_VPM_PROCESS_MAX_OUTPUT_BYTES,
        creationflags=creationflags,
        **({"started": started} if started is not None else {}),
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
    excluded_prereleases: set[str] = set()
    for item in versions:
        if isinstance(item, str):
            value, yanked = item.strip(), False
        elif isinstance(item, dict):
            value = str(item.get("version") or item.get("Version") or "").strip()
            yanked = bool(item.get("yanked") or item.get("isYanked"))
        else:
            continue
        precedence = _semver_precedence(value)
        if yanked or precedence is None:
            continue
        if not include_prerelease and not precedence[3]:
            excluded_prereleases.add(value)
            continue
        candidates.append((precedence, value))
    if requested in excluded_prereleases or (not requested and not candidates and excluded_prereleases):
        raise RuntimeError(
            "The package version is unavailable under the current stable-only policy. "
            "A non-yanked prerelease was found. To select a prerelease intentionally, "
            "retry with includePrerelease=true."
        )
    if requested:
        if requested not in [value for _key, value in candidates]:
            raise RuntimeError(
                "Requested packageVersion is unavailable, yanked, or excluded by "
                "prerelease policy."
            )
        return requested
    if not candidates:
        raise RuntimeError(
            "The configured sources returned no eligible non-yanked semantic version. "
            "Check the package ID and the configured source's version listing."
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
    restore_locked: bool = False,
) -> list[str]:
    if cli_name == "vrc-get":
        if restore_locked:
            return [cli_path, "reinstall", "-p", project_path, "-y", package_id]
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


def _missing_vpm_locked_version(state: dict[str, Any], requested_version: str) -> str:
    version = state.get("lockedVersion") if state.get("resolution") == "restore_required" else ""
    return version if version and requested_version in {"", version} else ""


def _read_vpm_install_readback(
    project_path: Path,
    package_id: str,
    expected_version: str,
    packages_identity: dict[str, Any],
) -> dict[str, Any]:
    # Observe each side independently: a changed lock with no embedded package
    # is real evidence even though the install target has not been reached.
    packages = project_path / "Packages"
    observed: dict[str, Any] = {}
    errors = []
    try:
        verify_directory(packages_identity, label="Unity Packages")
    except (OSError, ValueError) as exc:
        return {"targetSatisfied": None, "error": str(exc)}
    paths = {
        "lockedVersion": packages / "vpm-manifest.json",
        "embeddedPackage": packages / package_id / "package.json",
    }
    for field, path in paths.items():
        try:
            directory = None
            if field == "embeddedPackage":
                path.parent.lstat()
                directory = capture_directory(path.parent, label="Installed embedded VPM package")
            path.lstat()
            data = _read_bounded_vpm_json(path, "VPM package readback")
            if field == "lockedVersion":
                locked_entry = _ensure_dict(data.get("locked")).get(package_id)
                observed[field] = _package_entry_version(locked_entry) or None
            else:
                observed[field] = {
                    "directory": directory, "name": data.get("name"), "version": data.get("version"),
                }
        except FileNotFoundError:
            observed[field] = None
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(str(exc))

    embedded = _ensure_dict(observed.get("embeddedPackage"))
    mismatch = (
        "lockedVersion" in observed and observed["lockedVersion"] != expected_version
        or "embeddedPackage" in observed
        and (embedded.get("name") != package_id or embedded.get("version") != expected_version)
    )
    if mismatch:
        observed["targetSatisfied"] = False
    elif errors:
        observed["targetSatisfied"] = None
    else:
        observed["targetSatisfied"] = True
    if errors:
        observed["error"] = "; ".join(errors)
    return observed


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
        if _requests_vpm_repository(arguments):
            raise AgentGatewayError(
                _VPM_REPOSITORY_INPUT_REJECTION,
                status_code=403,
                cause_code="vpm_repository_input_unsupported",
                failure_layer="argument_validation",
                failure_phase="preparation",
                operation_kind="write",
                tool="vrcforge_install_vpm_package",
                mutation_started=False,
                committed=False,
            )
        state = _sealed_vpm_project_state(
            project_path,
            package_id,
            self._ports.detect_package,
        )
        package_state = _ensure_dict(state.get("packageState"))
        baseline = None
        installed = bool(package_state.get("installed"))
        upgrade_requested = bool(
            arguments.get("upgrade") or arguments.get("upgradePackage")
            or arguments.get("upgrade_package")
        )
        if package_state.get("resolution") == "pending" or (upgrade_requested and package_state.get("source") == "packages-lock"):
            raise AgentGatewayError(_UPM_RESOLUTION_GUIDANCE, status_code=409)
        if package_state.get("installed") is None:
            raise AgentGatewayError("Cannot confirm package state from the project package metadata.", status_code=409)
        requested_version = str(
            arguments.get("packageVersion")
            or arguments.get("package_version")
            or ""
        ).strip()
        restore_version = _missing_vpm_locked_version(package_state, requested_version)
        if package_state.get("resolution") == "version_mismatch" and not (upgrade_requested and requested_version):
            raise AgentGatewayError("Installed and VPM-locked versions differ; determine the intended version before changing it.", status_code=409)
        if installed and not upgrade_requested:
            raise AgentGatewayError(
                "The installed package version must be tried first; this install "
                "lane does not accept caller-supplied upgrade evidence.",
                status_code=409,
            )
        if installed and upgrade_requested:
            if str(package_state.get("source") or "") == "assets":
                baseline_archive = str(arguments.get("legacyBaselineArchive") or arguments.get("legacy_baseline_archive") or "").strip()
                baseline_root = str(arguments.get("legacyBaselineAssetsRoot") or arguments.get("legacy_baseline_assets_root") or "").strip()
                if not baseline_archive or not baseline_root:
                    raise AgentGatewayError("Assets-imported package upgrade requires legacyBaselineArchive and legacyBaselineAssetsRoot.", status_code=409)
                expected_assets_root = str(Path(str(package_state.get("path") or "")).parent)
                if not expected_assets_root or Path(baseline_root).expanduser().resolve() != Path(expected_assets_root).expanduser().resolve():
                    raise AgentGatewayError("legacyBaselineAssetsRoot must match the detected package Assets root.", status_code=409)
                try:
                    baseline = verify_legacy_baseline(baseline_archive, baseline_root)
                except (OSError, tarfile.TarError, ValueError) as exc:
                    raise AgentGatewayError(f"Legacy baseline verification failed: {exc}", status_code=409) from exc
                if not baseline.get("ok") and arguments.get("preserveLegacyFiles") is not True:
                    raise AgentGatewayError(f"Legacy baseline mismatch: {json.dumps({k: baseline[k] for k in ('missing','unknown','modified')}, ensure_ascii=False)}", status_code=409)
            installed_key = _semver_precedence(str(package_state.get("version") or ""))
            requested_key = _semver_precedence(requested_version)
            if requested_key is None or (installed_key and requested_key <= installed_key):
                raise AgentGatewayError(
                    "Upgrade packageVersion must be a newer valid semantic version.",
                    status_code=409,
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
        selected_version = select_sealed_vpm_version(package_info, restore_version or requested_version, prerelease)
        argv = _vpm_install_argv(
            cli_name=str(cli["name"]),
            cli_path=str(binary["identity"]["path"]),
            project_path=str(state["project"]["path"]),
            package_id=package_id,
            package_version=selected_version,
            include_prerelease=prerelease,
            restore_locked=bool(restore_version),
        )
        approval_arguments = dict(arguments)
        approval_arguments["packageVersion"] = selected_version
        prepared_evidence_payload: dict[str, Any] = {
            "binary": binary,
            "cliName": cli["name"],
            "cliVersion": cli_version,
            "project": state,
            "packageId": package_id,
            "packageVersion": selected_version,
            "upgrade": upgrade_requested,
            "restoreLocked": bool(restore_version),
            "includePrerelease": prerelease,
            "repository": "",
        }
        if baseline is not None:
            prepared_evidence_payload["legacyBaseline"] = baseline
            prepared_evidence_payload["preserveLegacyFiles"] = arguments.get("preserveLegacyFiles") is True
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
            prepared_evidence_payload,
        )
        prepared_preview: dict[str, Any] = {
            "ok": True,
            "targetTool": "vrcforge_install_vpm_package",
            "projectPath": str(state["project"]["path"]),
            "packageId": package_id,
            "packageVersion": selected_version,
            "compatibilityAction": "restore_locked" if restore_version else "upgrade" if upgrade_requested else "install_missing",
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
        return prepared, prepared_preview


class VpmPackageInstallExecutor:
    """Execute exactly one approval-bound install child and verify readback."""

    def __init__(self, ports: VpmPackageInstallExecutionPorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        child_started = Event()
        preservation = None
        result: dict[str, Any] = {"schema": "vrcforge.vpm_package_install.v1"}

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
                require_digest=True,
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
                restore_locked=bool(evidence.get("restoreLocked")),
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
            legacy_baseline = evidence.get("legacyBaseline")
            if isinstance(legacy_baseline, dict):
                archive_evidence = _ensure_dict(legacy_baseline.get("archive"))
                archive_path = str(_ensure_dict(archive_evidence.get("identity")).get("path") or "")
                archive_now = verify_legacy_baseline(archive_path, str(legacy_baseline.get("assetsRoot") or ""))
                if archive_now.get("archive", {}).get("sha256") != archive_evidence.get("sha256") or archive_now.get("archive", {}).get("identity") != archive_evidence.get("identity"):
                    raise RuntimeError("Legacy baseline archive drifted after approval.")
                preserve = evidence.get("preserveLegacyFiles") is True
                if (not archive_now.get("ok") and not preserve) or archive_now.get("treeDigest") != legacy_baseline.get("treeDigest") or archive_now.get("baselineDigest") != legacy_baseline.get("baselineDigest"):
                    raise RuntimeError("Legacy Assets baseline drifted after approval.")
                if preserve:
                    backup_dir = project_path / ".vrcforge" / "package-backups"
                    for parent in (backup_dir.parent, backup_dir):
                        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                            raise RuntimeError("Legacy backup directory must not be a link or file.")
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    preservation = create_legacy_preservation_snapshot(
                        legacy_baseline["assetsRoot"], backup_dir / f"{uuid4().hex}.zip"
                    )
                    if not preservation.get("ok") or preservation.get("treeDigest") != legacy_baseline.get("treeDigest"):
                        raise RuntimeError("Legacy preservation snapshot differs from the approved tree.")
            result.update(
                projectPath=str(project_path), packageId=package_id,
                packageVersion=package_version, command=argv,
            )
            before = _read_vpm_install_readback(
                project_path, package_id, package_version, project_evidence["packages"],
            )
            proc = _run_vpm_process(
                self._ports.run_install_process,
                self._ports.process_environment,
                self._ports.creationflags,
                argv,
                cwd=str(project_path),
                timeout_seconds=_VPM_INSTALL_TIMEOUT_SECONDS,
                started=child_started,
            )
            child_started.set()  # An exit status also proves that the runner spawned the child.
            result.update({
                "exitCode": proc.returncode,
                "stdoutSummary": (proc.stdout or "")[-1500:],
                "stderrSummary": (proc.stderr or "")[-1500:],
                "stdoutTruncated": proc.stdout_truncated,
                "stderrTruncated": proc.stderr_truncated,
            })
            if proc.returncode != 0:
                result["error"] = f"VPM install process exited with code {proc.returncode}."
        except (subprocess.TimeoutExpired, RuntimeError, OSError, ValueError) as exc:
            result["error"] = str(exc)

        process_started = child_started.is_set()
        changed = []
        satisfied = None
        if process_started:
            after = _read_vpm_install_readback(
                project_path, package_id, package_version, project_evidence["packages"],
            )
            satisfied = after["targetSatisfied"]
            changed = [key for key in ("lockedVersion", "embeddedPackage")
                       if key in before and key in after and before[key] != after[key]]
            readback = {
                "verified": satisfied is True, "scope": "package_manager_files",
                "before": before, "after": after, "changed": changed,
            }
            result["readback"] = readback
            if satisfied is not True:
                readback["error"] = after.get("error") or (
                    "VPM lock and embedded package do not both match the approved package/version."
                )
                result.setdefault("error", readback["error"])
                result["nextAction"] = {
                    "tool": "vrcforge_package_install_plan",
                    "arguments": {
                        "projectPath": str(project_path), "packageId": package_id,
                        "packageVersion": package_version,
                    },
                }
            else:
                exact = {
                    "packageId": package_id, "version": package_version,
                    "source": "vpm-manifest.locked", "embeddedPackage": after["embeddedPackage"],
                }
                readback.update(exact)
                result["vpmManifestReadback"] = exact

        if changed:
            commit_state, committed = "committed" if satisfied else "partial", True
        elif process_started:
            commit_state, committed = "unknown", None
        else:
            commit_state, committed = "not_started", False
        return {
            **result,
            "ok": satisfied is True and "error" not in result,
            "legacyPreservation": preservation,
            "processStarted": process_started,
            "mutationStarted": committed,
            "mutationApplied": committed,
            "committed": committed,
            "commitState": commit_state,
            "targetSatisfied": satisfied,
            "verified": satisfied is True,
            "unityRefreshRequired": committed,
            "recovery": {
                "checkpointMustBeRestoredOnlyIfUserChooses": process_started and "error" in result,
                "committed": committed,
                "commitState": commit_state,
            },
        }
