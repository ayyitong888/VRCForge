from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from prepared_file_imports import capture_regular_file


PROJECT_LIFECYCLE_RECEIPT_SCHEMA = "vrcforge.project_lifecycle_receipt.v1"
PROJECT_CREATION_MARKER_SCHEMA = "vrcforge.project_creation_marker.v1"
_PREPARED_KEY = "_vrcforgeProjectLifecyclePrepared"
_INVALID_PROJECT_NAME = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ProjectLifecycleError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_unity_project(path: Path) -> bool:
    return bool(
        (path / "Assets").is_dir()
        and (path / "Packages").is_dir()
        and (path / "ProjectSettings").is_dir()
        and (path / "Packages" / "manifest.json").is_file()
        and (path / "ProjectSettings" / "ProjectVersion.txt").is_file()
    )


def _directory_entries(root: Path) -> dict[str, tuple[int, bytes]]:
    if not _is_unity_project(root):
        raise ProjectLifecycleError(f"Unity project/template is incomplete: {root}")
    entries: dict[str, tuple[int, bytes]] = {}
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if candidate.is_symlink():
            raise ProjectLifecycleError(f"Linked project/template paths are not supported: {candidate}")
        if candidate.is_dir():
            continue
        try:
            identity, sha256 = capture_regular_file(candidate, label="Project template")
        except (OSError, ValueError) as exc:
            raise ProjectLifecycleError(str(exc)) from exc
        entries[candidate.relative_to(root).as_posix()] = (identity["size"], bytes.fromhex(sha256))
    return entries


def _directory_digest(entries: dict[str, tuple[int, bytes]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    total_bytes = 0
    for relative, (size, file_digest) in sorted(entries.items(), key=lambda item: item[0].casefold()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest)
        total_bytes += size
    return {"sha256": digest.hexdigest(), "fileCount": len(entries), "bytes": total_bytes}


def _normalize_project_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."}:
        raise ProjectLifecycleError("projectName is required.")
    if len(name) > 120:
        raise ProjectLifecycleError("projectName is too long.")
    if _INVALID_PROJECT_NAME.search(name) or name.endswith((" ", ".")):
        raise ProjectLifecycleError("projectName contains Windows-invalid characters.")
    if name.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ProjectLifecycleError("projectName is a reserved Windows name.")
    return name


class ProjectLifecycleService:
    """Create/register projects without assuming VCC, ALCOM, or Hub ownership.

    Creation consumes a validated local Unity template, writes into a same-parent
    staging directory, and atomically publishes only after full readback.  The
    VRCForge catalogue update and rollback receipt are part of the same service
    transaction.  Hub/VCC/ALCOM catalogues remain explicit handoffs unless a
    supported manager adapter is available; this service never edits their
    private settings files.
    """

    def __init__(
        self,
        *,
        prefs_path: Path,
        receipts_dir: Path,
        template_roots: Iterable[Path] = (),
    ) -> None:
        self.prefs_path = Path(prefs_path)
        self.prefs_lock = threading.RLock()
        self.receipts_dir = Path(receipts_dir)
        self.template_roots = tuple(Path(path) for path in template_roots)

    def status(self, _arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        templates = []
        seen: set[str] = set()
        for root in self.template_roots:
            candidates = [root] if _is_unity_project(root) else []
            if root.is_dir() and not candidates:
                candidates.extend(child for child in root.iterdir() if child.is_dir())
            for candidate in candidates:
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not _is_unity_project(resolved):
                    continue
                key = str(resolved).casefold()
                if key in seen:
                    continue
                seen.add(key)
                templates.append(
                    {
                        "name": resolved.name,
                        "path": str(resolved),
                        "backend": "installed_template",
                    }
                )
        templates.sort(key=lambda item: (str(item["name"]).casefold(), str(item["path"]).casefold()))
        return {
            "ok": True,
            "schema": "vrcforge.project_lifecycle_status.v1",
            "createCapable": bool(templates),
            "createBackends": ["installed_template"] if templates else [],
            "templates": templates,
            "registration": {
                "vrcforge": "automatic",
                "vcc": "handoff_or_vpm_cli",
                "alcom": "handoff",
                "unityHub": "handoff_required",
            },
        }

    def plan_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        prepared, preview = self.prepare_create(arguments, None)
        del prepared
        return {"ok": True, "schema": "vrcforge.project_create_plan.v1", **preview}

    def create_installed_project(
        self, name: str, parent_path: str, template_path: str, *, resolve_packages: Callable[[Path], bool],
    ) -> dict[str, Any]:
        name = _normalize_project_name(name)
        parent = Path(parent_path)
        if not parent.is_absolute() or not parent.is_dir():
            raise ProjectLifecycleError("Choose an existing folder for the new project.")
        if template_path not in {template["path"] for template in self.status()["templates"]}:
            raise ProjectLifecycleError("Choose an installed Unity project template.")
        return self.create_project({
            "projectName": name,
            "projectPath": str(parent / name),
            "templatePath": template_path,
        }, resolve_packages=resolve_packages)

    def prepare_create(
        self,
        arguments: dict[str, Any],
        _preview: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        values = dict(arguments or {})
        if _PREPARED_KEY in values:
            raise ProjectLifecycleError("Caller may not provide prepared project lifecycle evidence.")
        target = self._target_path(values)
        if target.exists() or os.path.lexists(target):
            raise ProjectLifecycleError(f"Target project already exists: {target}")
        if not target.parent.is_dir():
            raise ProjectLifecycleError("Target project parent directory does not exist.")
        template = self._resolve_template(values)
        template_identity = _directory_digest(_directory_entries(template))
        prepared = {
            **values,
            "projectPath": str(target),
            "projectName": target.name,
            "templatePath": str(template),
            _PREPARED_KEY: {
                "schema": "vrcforge.project_create_prepared.v1",
                "targetPath": str(target),
                "templatePath": str(template),
                "templateIdentity": template_identity,
            },
        }
        preview = {
            "projectPath": str(target),
            "projectName": target.name,
            "template": str(values.get("template") or template.name),
            "templatePath": str(template),
            "templateIdentity": template_identity,
            "backend": "installed_template",
            "managerRegistration": {
                "vrcforge": "automatic",
                "vcc": "handoff_or_vpm_cli",
                "alcom": "handoff",
                "unityHub": "handoff_required",
            },
            "mutationStarted": False,
            "committed": False,
            "commitState": "not_started",
        }
        return prepared, preview

    def create_project(
        self, arguments: dict[str, Any], *, resolve_packages: Callable[[Path], bool] | None = None,
    ) -> dict[str, Any]:
        values = dict(arguments or {})
        evidence = values.get(_PREPARED_KEY)
        if not isinstance(evidence, dict):
            values, _preview = self.prepare_create(values, None)
            evidence = values[_PREPARED_KEY]
        target = Path(str(evidence.get("targetPath") or ""))
        template = Path(str(evidence.get("templatePath") or ""))
        if target.exists() or os.path.lexists(target):
            raise ProjectLifecycleError(f"Target project already exists: {target}")
        receipt_id = f"project_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        staging = target.parent / f".{target.name}.vrcforge-create-{receipt_id}"
        if staging.exists() or os.path.lexists(staging):
            raise ProjectLifecycleError("Project creation staging path already exists.")
        registration: dict[str, Any] | None = None
        published = False
        try:
            shutil.copytree(template, staging, symlinks=True, copy_function=shutil.copy2)
            staged_entries = _directory_entries(staging)
            if _directory_digest(staged_entries) != evidence.get("templateIdentity"):
                raise ProjectLifecycleError("Project template changed after preparation.")
            if resolve_packages is not None and resolve_packages(staging):
                staged_entries = _directory_entries(staging)
            marker_path = staging / ".vrcforge" / "project-creation.json"
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write_json(
                marker_path,
                {
                    "schema": PROJECT_CREATION_MARKER_SCHEMA,
                    "receiptId": receipt_id,
                    "createdAt": _utc_now(),
                    "templateIdentity": evidence.get("templateIdentity"),
                },
            )
            marker_identity, marker_digest = capture_regular_file(marker_path, label="Project creation marker")
            staged_entries[marker_path.relative_to(staging).as_posix()] = (marker_identity["size"], bytes.fromhex(marker_digest))
            created_identity = _directory_digest(staged_entries)
            os.replace(staging, target)
            published = True
            if (not _is_unity_project(target)
                    or self._read_json(target / ".vrcforge" / "project-creation.json").get("receiptId") != receipt_id):
                raise ProjectLifecycleError("Published project readback did not match the staged project.")
            registration = self._register_project(target)
            receipt = {
                "schema": PROJECT_LIFECYCLE_RECEIPT_SCHEMA,
                "receiptId": receipt_id,
                "kind": "create_project",
                "status": "active",
                "createdAt": _utc_now(),
                "projectPath": str(target),
                "createdIdentity": created_identity,
                "prefsBefore": registration["prefsBefore"],
                "prefsBeforeSha256": registration["prefsBeforeSha256"],
                "prefsAfterSha256": registration["prefsAfterSha256"],
            }
            self._write_receipt(receipt)
        except Exception as exc:  # noqa: BLE001 - transaction cleanup is part of the contract.
            cleanup_errors: list[str] = []
            if registration and registration.get("changed"):
                try:
                    self._remove_failed_registration(registration["entry"])
                except Exception as cleanup_exc:  # noqa: BLE001
                    cleanup_errors.append(f"catalog restore failed: {cleanup_exc}")
            cleanup_target = target if published else staging
            if cleanup_target.exists() or os.path.lexists(cleanup_target):
                try:
                    shutil.rmtree(cleanup_target)
                except Exception as cleanup_exc:  # noqa: BLE001
                    cleanup_errors.append(f"project cleanup failed: {cleanup_exc}")
            message = str(exc)
            if cleanup_errors:
                message += "; " + "; ".join(cleanup_errors)
            raise ProjectLifecycleError(message) from exc
        return {
            "ok": True,
            "schema": "vrcforge.project_create_result.v1",
            "action": "create_project",
            "backend": "installed_template",
            "projectPath": str(target),
            "projectName": target.name,
            "registeredInVRCForge": True,
            "readbackVerified": True,
            "managerRegistration": {
                "vrcforge": "registered",
                "vcc": "handoff_or_vpm_cli",
                "alcom": "handoff",
                "unityHub": "handoff_required",
            },
            "mutationStarted": True,
            "committed": True,
            "commitState": "complete",
            "rollback": {
                "available": True,
                "receiptId": receipt_id,
                "tool": "vrcforge_rollback_project_lifecycle",
                "requiresUserConfirmation": True,
            },
        }

    def register_project(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = str(arguments.get("projectPath") or arguments.get("projectRoot") or "").strip()
        if not raw:
            raise ProjectLifecycleError("projectPath is required.")
        project = Path(raw).expanduser().resolve(strict=True)
        if not _is_unity_project(project):
            raise ProjectLifecycleError("projectPath is not a valid Unity project.")
        registration = self._register_project(project)
        if not registration["changed"]:
            return {
                "ok": True,
                "schema": "vrcforge.project_register_result.v1",
                "action": "register_project",
                "projectPath": str(project),
                "registeredInVRCForge": True,
                "readbackVerified": True,
                "mutationStarted": False,
                "committed": True,
                "commitState": "complete",
                "rollback": {"available": False, "reason": "already_registered"},
            }
        receipt_id = f"registration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        try:
            self._write_receipt(
                {
                    "schema": PROJECT_LIFECYCLE_RECEIPT_SCHEMA,
                    "receiptId": receipt_id,
                    "kind": "register_project",
                    "status": "active",
                    "createdAt": _utc_now(),
                    "projectPath": str(project),
                    "registrationEntry": registration["entry"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            try:
                self._remove_failed_registration(registration["entry"])
            except Exception as cleanup_exc:  # noqa: BLE001
                raise ProjectLifecycleError(
                    f"Registration receipt failed: {exc}; catalogue compensation failed: {cleanup_exc}"
                ) from exc
            raise ProjectLifecycleError(f"Registration receipt failed: {exc}") from exc
        return {
            "ok": True,
            "schema": "vrcforge.project_register_result.v1",
            "action": "register_project",
            "projectPath": str(project),
            "registeredInVRCForge": True,
            "readbackVerified": True,
            "mutationStarted": True,
            "committed": True,
            "commitState": "complete",
            "rollback": {
                "available": True,
                "receiptId": receipt_id,
                "tool": "vrcforge_rollback_project_lifecycle",
                "requiresUserConfirmation": True,
            },
        }

    def rollback(self, arguments: dict[str, Any]) -> dict[str, Any]:
        receipt_id = str(arguments.get("receiptId") or arguments.get("receipt_id") or "").strip()
        if not receipt_id or not re.fullmatch(r"[A-Za-z0-9_-]{10,160}", receipt_id):
            raise ProjectLifecycleError("A valid receiptId is required.")
        receipt_path = self.receipts_dir / f"{receipt_id}.json"
        with self.prefs_lock:
            receipt = self._read_json(receipt_path)
            if receipt.get("schema") != PROJECT_LIFECYCLE_RECEIPT_SCHEMA or receipt.get("receiptId") != receipt_id:
                raise ProjectLifecycleError("Project lifecycle receipt is invalid.")
            if receipt.get("status") != "active":
                raise ProjectLifecycleError("Project lifecycle receipt is no longer active.")
            kind = str(receipt.get("kind") or "")
            if kind == "register_project":
                return self._rollback_registration(receipt_path, receipt)
        if kind != "create_project":
            raise ProjectLifecycleError("Project lifecycle receipt kind is unsupported.")
        current_prefs = self._load_prefs()
        current_prefs_sha = _sha256_bytes(_canonical_json_bytes(current_prefs))
        if current_prefs_sha != receipt.get("prefsAfterSha256"):
            raise ProjectLifecycleError("Project catalogue changed after this operation; rollback refused.")
        current_prefs_snapshot = self._read_json(self.prefs_path)
        recovery_path = ""
        target: Path | None = None
        recovery: Path | None = None
        if kind == "create_project":
            target = Path(str(receipt.get("projectPath") or ""))
            if not target.is_dir() or _directory_digest(_directory_entries(target)) != receipt.get("createdIdentity"):
                raise ProjectLifecycleError("Created project changed after creation; rollback refused.")
            marker = self._read_json(target / ".vrcforge" / "project-creation.json")
            if marker.get("schema") != PROJECT_CREATION_MARKER_SCHEMA or marker.get("receiptId") != receipt_id:
                raise ProjectLifecycleError("Created project marker does not match the rollback receipt.")
            recovery = target.parent / f"VRCForge_Rollback_{target.name}_{receipt_id[-8:]}"
            if recovery.exists() or os.path.lexists(recovery):
                raise ProjectLifecycleError("Project rollback recovery directory already exists.")
            os.replace(target, recovery)
            recovery_path = str(recovery)
        try:
            self._write_prefs(receipt.get("prefsBefore"))
            if self._read_json(self.prefs_path) != receipt.get("prefsBefore"):
                raise ProjectLifecycleError("Project catalogue rollback readback failed.")
            if target is not None and (
                os.path.lexists(target) or recovery is None
                or self._read_json(recovery / ".vrcforge" / "project-creation.json").get("receiptId") != receipt_id
            ):
                raise ProjectLifecycleError("Project recovery directory readback failed.")
            receipt["status"] = "rolled_back"
            receipt["rolledBackAt"] = _utc_now()
            receipt["recoveryPath"] = recovery_path
            self._atomic_write_json(receipt_path, receipt)
        except Exception as exc:  # noqa: BLE001
            compensation_errors = []
            try:
                self._write_prefs(current_prefs_snapshot)
                if self._read_json(self.prefs_path) != current_prefs_snapshot:
                    raise ProjectLifecycleError("Project catalogue compensation readback failed.")
            except Exception as restore_exc:  # noqa: BLE001
                compensation_errors.append(f"catalogue: {restore_exc}")
            if target is not None and recovery is not None and recovery.exists() and not target.exists():
                try:
                    os.replace(recovery, target)
                except Exception as restore_exc:  # noqa: BLE001
                    compensation_errors.append(f"project retained at {recovery}: {restore_exc}")
            if compensation_errors:
                raise ProjectLifecycleError(
                    f"Project catalogue rollback failed: {exc}; compensation failed: "
                    + "; ".join(compensation_errors)
                ) from exc
            raise ProjectLifecycleError(f"Project catalogue rollback failed: {exc}") from exc
        return {
            "ok": True,
            "schema": "vrcforge.project_lifecycle_rollback_result.v1",
            "action": "rollback_project_lifecycle",
            "receiptId": receipt_id,
            "kind": kind,
            "projectPath": str(receipt.get("projectPath") or ""),
            "recoveryPath": recovery_path,
            "readbackVerified": True,
            "mutationStarted": True,
            "committed": True,
            "commitState": "complete",
        }

    def _rollback_registration(self, receipt_path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
        receipt_id = receipt_path.stem
        project_path = str(receipt.get("projectPath") or "")
        entry = {"path": project_path, "projectType": "unity"}
        # Older receipts recorded a complete catalogue rather than the entry.
        if not project_path or receipt.get("registrationEntry", entry) != entry:
            raise ProjectLifecycleError("Project registration receipt does not identify its catalogue entry.")

        def read_entries() -> tuple[dict[str, Any], list[Any], list[int]]:
            prefs = self._read_json(self.prefs_path)
            projects = prefs.get("customProjects")
            if not isinstance(projects, list):
                raise ProjectLifecycleError("VRCForge project catalogue is invalid.")
            matches = [
                index for index, item in enumerate(projects)
                if isinstance(item, dict) and str(item.get("path") or "").casefold() == project_path.casefold()
            ]
            return prefs, projects, matches

        prefs, projects, matches = read_entries()
        if len(matches) != 1 or projects[matches[0]] != entry:
            raise ProjectLifecycleError("The registered project entry changed after registration.")
        position = matches[0]
        del projects[position]
        try:
            self._write_prefs(prefs)
            if read_entries()[2]:
                raise ProjectLifecycleError("Project registration rollback readback failed.")
            receipt.update(status="rolled_back", rolledBackAt=_utc_now(), recoveryPath="")
            self._atomic_write_json(receipt_path, receipt)
        except Exception as exc:  # noqa: BLE001
            try:
                current, current_projects, current_matches = read_entries()
                if not current_matches:
                    current_projects.insert(position, entry)
                    self._write_prefs(current)
                    _, restored, positions = read_entries()
                    if len(positions) != 1 or restored[positions[0]] != entry:
                        raise ProjectLifecycleError("Project entry compensation readback failed.")
                elif len(current_matches) != 1 or current_projects[current_matches[0]] != entry:
                    raise ProjectLifecycleError("Project entry compensation would overwrite another entry.")
            except Exception as restore_exc:  # noqa: BLE001
                raise ProjectLifecycleError(
                    f"Project registration rollback failed: {exc}; compensation failed: {restore_exc}"
                ) from exc
            raise ProjectLifecycleError(f"Project registration rollback failed: {exc}") from exc

        return {
            "ok": True,
            "schema": "vrcforge.project_lifecycle_rollback_result.v1",
            "action": "rollback_project_lifecycle",
            "receiptId": receipt_id,
            "kind": "register_project",
            "projectPath": project_path,
            "recoveryPath": "",
            "readbackVerified": True,
            "mutationStarted": True,
            "committed": True,
            "commitState": "complete",
        }

    def _target_path(self, values: dict[str, Any]) -> Path:
        raw = str(values.get("projectPath") or values.get("projectRoot") or "").strip()
        if not raw:
            raise ProjectLifecycleError("projectPath is required and must name the exact new project directory.")
        target = Path(raw).expanduser()
        if not target.is_absolute():
            raise ProjectLifecycleError("projectPath must be absolute.")
        target = target.resolve(strict=False)
        requested_name = _normalize_project_name(str(values.get("projectName") or target.name))
        if target.name.casefold() != requested_name.casefold():
            raise ProjectLifecycleError("projectName must match the final projectPath directory name.")
        return target

    def _resolve_template(self, values: dict[str, Any]) -> Path:
        explicit = str(values.get("templatePath") or values.get("template_path") or "").strip()
        candidates: list[Path] = []
        if explicit:
            candidate = Path(explicit).expanduser()
            if not candidate.is_absolute():
                raise ProjectLifecycleError("templatePath must be absolute.")
            candidates.append(candidate)
        else:
            name = str(values.get("template") or "Avatar").strip() or "Avatar"
            for root in self.template_roots:
                candidates.append(root if root.name.casefold() == name.casefold() else root / name)
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if _is_unity_project(resolved):
                return resolved
        raise ProjectLifecycleError("No usable local Unity project template was found.")

    def _load_prefs(self) -> dict[str, Any]:
        if not self.prefs_path.is_file():
            return {"version": 2, "customProjects": [], "hiddenPaths": []}
        payload = self._read_json(self.prefs_path)
        if not isinstance(payload.get("customProjects"), list) or not isinstance(payload.get("hiddenPaths"), list):
            raise ProjectLifecycleError("VRCForge project catalogue is invalid.")
        return {
            "version": 2,
            "customProjects": [dict(item) for item in payload["customProjects"] if isinstance(item, dict)],
            "hiddenPaths": [str(item) for item in payload["hiddenPaths"] if isinstance(item, str)],
        }

    def _register_project(self, project_path: Path) -> dict[str, Any]:
        normalized = str(project_path.resolve(strict=True))
        entry = {"path": normalized, "projectType": "unity"}
        with self.prefs_lock:
            before = self._load_prefs()
            changed = all(
                str(item.get("path") or "").casefold() != normalized.casefold()
                for item in before["customProjects"]
            )
            after = before
            if changed:
                after = {**before, "customProjects": [*before["customProjects"], entry]}
                self._write_prefs(after)
                try:
                    registered = self._load_prefs()["customProjects"]
                except ProjectLifecycleError as exc:
                    try:
                        self._remove_failed_registration(entry)
                    except (OSError, ProjectLifecycleError) as cleanup_exc:
                        raise ProjectLifecycleError(
                            f"Registration readback failed: {exc}; catalogue compensation failed: {cleanup_exc}"
                        ) from exc
                    raise
                if entry not in registered:
                    raise ProjectLifecycleError("Project catalogue registration entry was not saved.")
        return {
            "changed": changed,
            "entry": entry,
            "prefsBefore": before,
            "prefsBeforeSha256": _sha256_bytes(_canonical_json_bytes(before)),
            "prefsAfterSha256": _sha256_bytes(_canonical_json_bytes(after)),
        }

    def _remove_failed_registration(self, entry: dict[str, str]) -> None:
        with self.prefs_lock:
            if not self.prefs_path.exists():
                return
            current = self._read_json(self.prefs_path)
            projects = current.get("customProjects")
            if not isinstance(projects, list):
                raise ProjectLifecycleError("VRCForge project catalogue is invalid.")
            if entry in projects:
                projects.remove(entry)
                self._write_prefs(current)

    def _write_prefs(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ProjectLifecycleError("Project catalogue snapshot is invalid.")
        self._atomic_write_json(self.prefs_path, payload)

    def _write_receipt(self, payload: dict[str, Any]) -> None:
        receipt_id = str(payload.get("receiptId") or "")
        self._atomic_write_json(self.receipts_dir / f"{receipt_id}.json", payload)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectLifecycleError(f"Unable to read JSON state: {path}") from exc
        if not isinstance(payload, dict):
            raise ProjectLifecycleError(f"JSON state root must be an object: {path}")
        return payload

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
