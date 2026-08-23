from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECT_CATALOG_RECEIPT_SCHEMA = "vrcforge.project_catalog_registration_receipt.v1"
_PREPARED_KEY = "_vrcforgeProjectCatalogPrepared"
_SUPPORTED_CATALOGS = {"vcc", "alcom", "unityHub"}


class ProjectCatalogRegistrationError(RuntimeError):
    pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_unity_project(path: Path) -> bool:
    return bool(
        (path / "Assets").is_dir()
        and (path / "Packages" / "manifest.json").is_file()
        and (path / "ProjectSettings" / "ProjectVersion.txt").is_file()
    )


def _read_project_version(project: Path) -> tuple[str, str]:
    text = (project / "ProjectSettings" / "ProjectVersion.txt").read_text(
        encoding="utf-8-sig",
        errors="replace",
    )
    version_match = re.search(r"^m_EditorVersion:\s*(\S+)\s*$", text, re.MULTILINE)
    revision_match = re.search(
        r"^m_EditorVersionWithRevision:\s*\S+\s*\(([^)]+)\)\s*$",
        text,
        re.MULTILINE,
    )
    return (
        version_match.group(1) if version_match else "Unknown",
        revision_match.group(1) if revision_match else "",
    )


class ProjectCatalogRegistrationService:
    """Register one existing Unity project in one explicit manager catalogue."""

    def __init__(
        self,
        *,
        receipts_dir: Path,
        catalog_paths: Mapping[str, Iterable[Path]],
    ) -> None:
        self.receipts_dir = Path(receipts_dir)
        self.catalog_paths = {
            str(name): tuple(Path(path) for path in paths)
            for name, paths in catalog_paths.items()
        }

    def status(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        values = dict(arguments or {})
        project: Path | None = None
        raw_project = str(values.get("projectPath") or values.get("projectRoot") or "").strip()
        if raw_project:
            project = self._project_path(raw_project)
        catalogs: list[dict[str, Any]] = []
        for catalog in ("vcc", "alcom", "unityHub"):
            target = self._first_existing_path(catalog)
            item: dict[str, Any] = {
                "catalog": catalog,
                "installed": target is not None,
                "supported": False,
                "registered": False if project is not None else None,
                "reloadRequiredAfterWrite": True,
            }
            if target is None:
                item["reason"] = "catalog_not_found"
            else:
                item["settingsPath"] = str(target)
                try:
                    payload = self._read_payload(target)
                    self._validate_schema(catalog, payload)
                    item["supported"] = True
                    item["reason"] = "ready"
                    if project is not None:
                        item["registered"] = self._contains_project(catalog, payload, project)
                except ProjectCatalogRegistrationError as exc:
                    item["reason"] = str(exc)
            catalogs.append(item)
        return {
            "ok": True,
            "schema": "vrcforge.project_catalog_registration_status.v1",
            "projectPath": str(project) if project is not None else "",
            "catalogs": catalogs,
        }

    def prepare_register(
        self,
        arguments: dict[str, Any],
        _preview: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        values = dict(arguments or {})
        if _PREPARED_KEY in values:
            raise ProjectCatalogRegistrationError("Caller may not provide prepared catalogue evidence.")
        catalog = self._catalog_name(values.get("catalog"))
        project = self._project_path(
            str(values.get("projectPath") or values.get("projectRoot") or "").strip()
        )
        target = self._required_catalog_path(catalog)
        before_bytes = self._read_bytes(target)
        payload = self._decode_payload(target, before_bytes)
        self._validate_schema(catalog, payload)
        already_registered = self._contains_project(catalog, payload, project)
        prepared = {
            **values,
            "catalog": catalog,
            "projectPath": str(project),
            _PREPARED_KEY: {
                "schema": "vrcforge.project_catalog_registration_prepared.v1",
                "catalog": catalog,
                "projectPath": str(project),
                "settingsPath": str(target),
                "settingsBeforeSha256": _sha256(before_bytes),
            },
        }
        preview = {
            "catalog": catalog,
            "projectPath": str(project),
            "settingsPath": str(target),
            "alreadyRegistered": already_registered,
            "reloadRequiredAfterWrite": not already_registered,
            "mutationStarted": False,
            "committed": False,
            "commitState": "not_started",
        }
        return prepared, preview

    def register(self, arguments: dict[str, Any]) -> dict[str, Any]:
        values = dict(arguments or {})
        evidence = values.get(_PREPARED_KEY)
        if not isinstance(evidence, dict):
            values, _preview = self.prepare_register(values, None)
            evidence = values[_PREPARED_KEY]
        catalog = self._catalog_name(evidence.get("catalog"))
        project = self._project_path(str(evidence.get("projectPath") or ""))
        target = Path(str(evidence.get("settingsPath") or ""))
        if target != self._required_catalog_path(catalog):
            raise ProjectCatalogRegistrationError("Catalogue path changed after preparation.")
        before_bytes = self._read_bytes(target)
        before_sha = _sha256(before_bytes)
        if before_sha != evidence.get("settingsBeforeSha256"):
            raise ProjectCatalogRegistrationError("Catalogue changed after preparation; registration refused.")
        payload = self._decode_payload(target, before_bytes)
        self._validate_schema(catalog, payload)
        if self._contains_project(catalog, payload, project):
            return {
                "ok": True,
                "schema": "vrcforge.project_catalog_registration_result.v1",
                "action": "register_project_catalog",
                "catalog": catalog,
                "projectPath": str(project),
                "mutationStarted": False,
                "committed": True,
                "commitState": "complete",
                "reloadRequired": False,
                "rollback": {"available": False, "reason": "already_registered"},
            }
        after_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        self._append_project(catalog, after_payload, project)
        after_bytes = self._encode_payload(after_payload)
        receipt_id = (
            f"catalog_{catalog}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        receipt = {
            "schema": PROJECT_CATALOG_RECEIPT_SCHEMA,
            "receiptId": receipt_id,
            "status": "active",
            "createdAt": _utc_now(),
            "catalog": catalog,
            "projectPath": str(project),
            "settingsPath": str(target),
            "settingsBeforeBase64": base64.b64encode(before_bytes).decode("ascii"),
            "settingsBeforeSha256": before_sha,
            "settingsAfterSha256": _sha256(after_bytes),
        }
        try:
            self._atomic_write_bytes(target, after_bytes)
            if self._read_bytes(target) != after_bytes:
                raise ProjectCatalogRegistrationError("Catalogue readback did not match the committed payload.")
            self._write_receipt(receipt)
        except Exception as exc:  # noqa: BLE001 - exact cleanup is part of the atomic contract.
            cleanup_error = ""
            try:
                self._atomic_write_bytes(target, before_bytes)
                if self._read_bytes(target) != before_bytes:
                    raise ProjectCatalogRegistrationError("Catalogue cleanup readback failed.")
            except Exception as cleanup_exc:  # noqa: BLE001
                cleanup_error = f"; exact cleanup failed: {cleanup_exc}"
            raise ProjectCatalogRegistrationError(f"Catalogue registration failed: {exc}{cleanup_error}") from exc
        return {
            "ok": True,
            "schema": "vrcforge.project_catalog_registration_result.v1",
            "action": "register_project_catalog",
            "catalog": catalog,
            "projectPath": str(project),
            "settingsPath": str(target),
            "mutationStarted": True,
            "committed": True,
            "commitState": "complete",
            "reloadRequired": True,
            "rollback": {
                "available": True,
                "receiptId": receipt_id,
                "tool": "vrcforge_rollback_project_catalog_registration",
                "requiresUserConfirmation": True,
            },
        }

    def rollback(self, arguments: dict[str, Any]) -> dict[str, Any]:
        receipt_id = str(arguments.get("receiptId") or arguments.get("receipt_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,180}", receipt_id):
            raise ProjectCatalogRegistrationError("A valid receiptId is required.")
        receipt_path = self.receipts_dir / f"{receipt_id}.json"
        receipt = self._read_payload(receipt_path)
        if (
            receipt.get("schema") != PROJECT_CATALOG_RECEIPT_SCHEMA
            or receipt.get("receiptId") != receipt_id
            or receipt.get("status") != "active"
        ):
            raise ProjectCatalogRegistrationError("Project catalogue receipt is invalid or inactive.")
        catalog = self._catalog_name(receipt.get("catalog"))
        target = Path(str(receipt.get("settingsPath") or ""))
        if target != self._required_catalog_path(catalog):
            raise ProjectCatalogRegistrationError("Catalogue path no longer matches the receipt.")
        current = self._read_bytes(target)
        if _sha256(current) != receipt.get("settingsAfterSha256"):
            raise ProjectCatalogRegistrationError("Catalogue changed after registration; rollback refused.")
        try:
            before = base64.b64decode(str(receipt.get("settingsBeforeBase64") or ""), validate=True)
        except ValueError as exc:
            raise ProjectCatalogRegistrationError("Catalogue receipt snapshot is invalid.") from exc
        if _sha256(before) != receipt.get("settingsBeforeSha256"):
            raise ProjectCatalogRegistrationError("Catalogue receipt snapshot digest is invalid.")
        self._atomic_write_bytes(target, before)
        if self._read_bytes(target) != before:
            raise ProjectCatalogRegistrationError("Catalogue rollback readback failed.")
        receipt["status"] = "rolled_back"
        receipt["rolledBackAt"] = _utc_now()
        self._atomic_write_bytes(receipt_path, self._encode_payload(receipt))
        return {
            "ok": True,
            "schema": "vrcforge.project_catalog_registration_rollback_result.v1",
            "action": "rollback_project_catalog_registration",
            "receiptId": receipt_id,
            "catalog": catalog,
            "projectPath": str(receipt.get("projectPath") or ""),
            "mutationStarted": True,
            "committed": True,
            "commitState": "complete",
            "reloadRequired": True,
        }

    def _first_existing_path(self, catalog: str) -> Path | None:
        for candidate in self.catalog_paths.get(catalog, ()):
            if candidate.is_file():
                return candidate.resolve(strict=True)
        return None

    def _required_catalog_path(self, catalog: str) -> Path:
        target = self._first_existing_path(catalog)
        if target is None:
            raise ProjectCatalogRegistrationError(f"{catalog} catalogue is not installed or has no settings file.")
        return target

    @staticmethod
    def _catalog_name(value: Any) -> str:
        raw = str(value or "").strip()
        aliases = {"unityhub": "unityHub", "unity_hub": "unityHub", "hub": "unityHub"}
        catalog = aliases.get(raw.casefold(), raw.casefold())
        if catalog not in _SUPPORTED_CATALOGS:
            raise ProjectCatalogRegistrationError("catalog must be one of: vcc, alcom, unityHub.")
        return catalog

    @staticmethod
    def _project_path(raw: str) -> Path:
        if not raw:
            raise ProjectCatalogRegistrationError("projectPath is required.")
        project = Path(raw).expanduser().resolve(strict=True)
        if not _is_unity_project(project):
            raise ProjectCatalogRegistrationError("projectPath is not a valid Unity project.")
        return project

    @staticmethod
    def _validate_schema(catalog: str, payload: dict[str, Any]) -> None:
        if catalog == "vcc":
            if not isinstance(payload.get("userProjects"), list) or not all(
                isinstance(item, str) for item in payload["userProjects"]
            ):
                raise ProjectCatalogRegistrationError("Unsupported VCC settings schema: userProjects is missing.")
        elif catalog == "alcom":
            if not isinstance(payload.get("projects"), list):
                raise ProjectCatalogRegistrationError("Unsupported ALCOM/vrc-get settings schema: projects is missing.")
        elif (
            payload.get("schema_version") != "v1"
            or not isinstance(payload.get("data"), dict)
        ):
            raise ProjectCatalogRegistrationError("Unsupported Unity Hub projects-v1 schema.")

    @staticmethod
    def _contains_project(catalog: str, payload: dict[str, Any], project: Path) -> bool:
        expected = str(project).casefold()
        if catalog == "vcc":
            paths = payload["userProjects"]
        elif catalog == "alcom":
            paths = [item if isinstance(item, str) else item.get("path", "") for item in payload["projects"] if isinstance(item, (str, dict))]
        else:
            paths = [value.get("path") or key for key, value in payload["data"].items() if isinstance(value, dict)]
        return any(str(path or "").casefold() == expected for path in paths)

    @staticmethod
    def _append_project(catalog: str, payload: dict[str, Any], project: Path) -> None:
        path = str(project)
        if catalog == "vcc":
            payload["userProjects"].append(path)
        elif catalog == "alcom":
            payload["projects"].append({"path": path})
        else:
            version, changeset = _read_project_version(project)
            payload["data"][path] = {
                "title": project.name,
                "lastModified": int(project.stat().st_mtime * 1000),
                "isCustomEditor": False,
                "path": path,
                "containingFolderPath": str(project.parent),
                "version": version,
                "architecture": "x86_64",
                "changeset": changeset,
                "isFavorite": False,
                "hasCustomDisplayName": False,
                "sizeCalculatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            }

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ProjectCatalogRegistrationError(f"Unable to read catalogue: {path}") from exc

    @classmethod
    def _read_payload(cls, path: Path) -> dict[str, Any]:
        return cls._decode_payload(path, cls._read_bytes(path))

    @staticmethod
    def _decode_payload(path: Path, value: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(value.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectCatalogRegistrationError(f"Unable to parse catalogue JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise ProjectCatalogRegistrationError(f"Catalogue JSON root must be an object: {path}")
        return payload

    @staticmethod
    def _encode_payload(payload: Any) -> bytes:
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    def _write_receipt(self, payload: dict[str, Any]) -> None:
        self._atomic_write_bytes(
            self.receipts_dir / f"{payload['receiptId']}.json",
            self._encode_payload(payload),
        )

    @staticmethod
    def _atomic_write_bytes(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
