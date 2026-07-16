from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cmp_to_key
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


LOCK_NAME = "skill.lock.json"
SIGNATURE_NAME = "skill.sig"
PUBLIC_KEY_NAME = "author.pub"
MANIFEST_NAME = "manifest.json"
RESERVED_PACKAGE_FILES = {LOCK_NAME, SIGNATURE_NAME, PUBLIC_KEY_NAME}

LOCK_SCHEMA = "vrcforge.skill-lock.v1"
REGISTRY_SCHEMA = "vrcforge.skill-registry.v1"
GOVERNANCE_SCHEMA = "vrcforge.skill-package-governance.v1"
GOVERNANCE_DECISION_SCHEMA = "vrcforge.skill-package-governance-decision.v1"
DRY_RUN_SCHEMA = "vrcforge.skill-package-dry-run.v1"

DEFAULT_MAX_FILE_COUNT = 2_048
DEFAULT_MAX_FILE_SIZE = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE = 64 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200.0
DEFAULT_AUDIT_LIMIT = 200

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
SKILL_ID_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)+$"
)
TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,80}$")
DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")

LOW_PERMISSIONS = {
    "read_project",
    "read_assets",
    "read_package",
    "analyze_logs",
    "build_index",
}
MEDIUM_PERMISSIONS = {
    "unity_scan_scene",
    "unity_modify_materials",
    "unity_modify_prefab",
    "unity_modify_components",
    "unity_run_validation",
}
HIGH_PERMISSIONS = {
    "write_project_files",
    "delete_files",
    "execute_shell",
    "run_editor_script",
    "network_access",
    "read_env",
    "write_outside_project",
}

EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "booth",
    "paid-assets",
    "paid_assets",
    "private-assets",
    "private_assets",
}
EXCLUDED_EXACT_NAMES = {
    ".env",
    ".ds_store",
    "thumbs.db",
    "cookies",
    "cookies.json",
    "cookies.txt",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
    "secrets",
    "secrets.json",
}
EXCLUDED_SUFFIXES = {
    # Private keys, credentials, and encrypted key stores.
    ".cer",
    ".crt",
    ".der",
    ".key",
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
    # Unity/avatar source assets and paid binaries do not belong in a skill.
    ".anim",
    ".asset",
    ".assetbundle",
    ".blend",
    ".controller",
    ".fbx",
    ".mat",
    ".mesh",
    ".obj",
    ".prefab",
    ".unity",
    ".unity3d",
    ".unitypackage",
    ".vrca",
    ".vrm",
    ".vsk",
    # Textures and opaque executable/binary payloads.
    ".bmp",
    ".dds",
    ".dll",
    ".dylib",
    ".exe",
    ".exr",
    ".gif",
    ".hdr",
    ".jpeg",
    ".jpg",
    ".ktx",
    ".ktx2",
    ".png",
    ".psd",
    ".so",
    ".tga",
    ".webp",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class SkillPackageError(ValueError):
    """Base exception for invalid or unsafe skill packages."""


class ManifestValidationError(SkillPackageError):
    pass


class PackageSecurityError(SkillPackageError):
    pass


class PackageIntegrityError(SkillPackageError):
    pass


class PackageSignatureError(SkillPackageError):
    pass


class PackageCompatibilityError(SkillPackageError):
    pass


class PackageUpdateError(SkillPackageError):
    pass


@dataclass(frozen=True)
class ExportResult:
    package_path: Path
    manifest: dict[str, Any]
    signature_status: str
    signer_fingerprint: str | None
    lock_sha256: str
    file_count: int
    total_size: int
    excluded_files: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_path": str(self.package_path),
            "manifest": dict(self.manifest),
            "signature_status": self.signature_status,
            "signer_fingerprint": self.signer_fingerprint,
            "lock_sha256": self.lock_sha256,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "excluded_files": list(self.excluded_files),
        }


@dataclass(frozen=True)
class ImportPreview:
    package_path: Path
    package_sha256: str
    manifest: dict[str, Any]
    signature_status: str
    signer_fingerprint: str | None
    lock_sha256: str
    permissions: tuple[str, ...]
    permission_tiers: dict[str, tuple[str, ...]]
    risk_level: str
    file_count: int
    total_size: int
    update_action: str = "new"
    governance: dict[str, Any] | None = None
    dry_run: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_path": str(self.package_path),
            "package_sha256": self.package_sha256,
            "manifest": dict(self.manifest),
            "signature_status": self.signature_status,
            "signer_fingerprint": self.signer_fingerprint,
            "lock_sha256": self.lock_sha256,
            "permissions": list(self.permissions),
            "permission_tiers": {key: list(value) for key, value in self.permission_tiers.items()},
            "risk_level": self.risk_level,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "update_action": self.update_action,
            "governance": dict(self.governance or {}),
            "dryRun": dict(self.dry_run or {}),
        }


@dataclass(frozen=True)
class InstallResult:
    preview: ImportPreview
    installed_path: Path
    registry_entry: dict[str, Any]
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "preview": self.preview.as_dict(),
            "installed_path": str(self.installed_path),
            "registry_entry": dict(self.registry_entry),
            "changed": self.changed,
        }


@dataclass(frozen=True)
class PackageStateResult:
    skill_id: str
    registry_entry: dict[str, Any]
    manifest: dict[str, Any]
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "registry_entry": dict(self.registry_entry),
            "manifest": dict(self.manifest),
            "changed": self.changed,
        }


@dataclass(frozen=True)
class UninstallResult:
    skill_id: str
    registry_entry: dict[str, Any]
    manifest: dict[str, Any]
    removed_path: Path
    removed_versions: tuple[str, ...]
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "registry_entry": dict(self.registry_entry),
            "manifest": dict(self.manifest),
            "removed_path": str(self.removed_path),
            "removed_versions": list(self.removed_versions),
            "changed": self.changed,
        }


@dataclass(frozen=True)
class SigningKeyPair:
    private_key_pem: bytes
    public_key: bytes
    fingerprint: str


@dataclass(frozen=True)
class _ValidatedPackage:
    temp_dir: Path
    preview: ImportPreview


def canonical_json_bytes(value: Any) -> bytes:
    """Return the single byte representation used for locks and signatures."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_regular_file_bounded(path: Path, max_bytes: int, *, label: str) -> bytes:
    if _is_symlink_like(path):
        raise PackageSecurityError(f"{label} must be a regular non-link file.")
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PackageIntegrityError(f"{label} metadata is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PackageSecurityError(f"{label} must be a regular file.")
    if metadata.st_size > max_bytes:
        raise PackageIntegrityError(f"{label} exceeds the {max_bytes}-byte limit.")
    try:
        with path.open("rb") as stream:
            value = stream.read(max_bytes + 1)
    except OSError as exc:
        raise PackageIntegrityError(f"{label} cannot be read.") from exc
    if len(value) > max_bytes:
        raise PackageIntegrityError(f"{label} exceeds the {max_bytes}-byte limit.")
    return value


def _sha256_regular_file_bounded(path: Path, max_bytes: int, *, label: str) -> str:
    if _is_symlink_like(path):
        raise PackageSecurityError(f"{label} must be a regular non-link file.")
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PackageIntegrityError(f"{label} metadata is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PackageSecurityError(f"{label} must be a regular file.")
    if metadata.st_size > max_bytes:
        raise PackageIntegrityError(f"{label} exceeds the {max_bytes}-byte limit.")
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PackageIntegrityError(f"{label} exceeds the {max_bytes}-byte limit.")
                digest.update(chunk)
    except OSError as exc:
        raise PackageIntegrityError(f"{label} cannot be read.") from exc
    return digest.hexdigest()


def _json_object_no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackageIntegrityError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_bytes(value: bytes, label: str) -> Any:
    try:
        return json.loads(value.decode("utf-8"), object_pairs_hook=_json_object_no_duplicates)
    except UnicodeDecodeError as exc:
        raise PackageIntegrityError(f"{label} must be UTF-8 JSON.") from exc
    except json.JSONDecodeError as exc:
        raise PackageIntegrityError(f"{label} is invalid JSON: {exc.msg}.") from exc


def _parse_semver(value: str, field: str = "version") -> tuple[int, int, int, tuple[str, ...], str]:
    match = SEMVER_RE.fullmatch(str(value or ""))
    if not match:
        raise ManifestValidationError(f"{field} must be a valid semantic version.")
    prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease, match.group(5) or ""


def _compare_semver(left: str, right: str) -> int:
    left_version = _parse_semver(left)
    right_version = _parse_semver(right)
    left_core = left_version[:3]
    right_core = right_version[:3]
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    left_pre = left_version[3]
    right_pre = right_version[3]
    if not left_pre and not right_pre:
        return 0
    if not left_pre:
        return 1
    if not right_pre:
        return -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_item) < int(right_item) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_item < right_item else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def _safe_relative_path(raw_path: str, *, label: str = "path") -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise PackageSecurityError(f"{label} must be a non-empty relative path.")
    if "\x00" in raw_path or "\\" in raw_path:
        raise PackageSecurityError(f"Unsafe {label}: {raw_path!r}.")
    if raw_path.startswith("/") or raw_path.startswith("//") or DRIVE_PATH_RE.match(raw_path):
        raise PackageSecurityError(f"Absolute {label} is not allowed: {raw_path!r}.")
    normalized_unicode = unicodedata.normalize("NFC", raw_path)
    if normalized_unicode != raw_path:
        raise PackageSecurityError(f"{label} must use canonical Unicode normalization: {raw_path!r}.")
    path = PurePosixPath(raw_path)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise PackageSecurityError(f"Traversal is not allowed in {label}: {raw_path!r}.")
    for part in parts:
        if ":" in part or part.endswith((" ", ".")):
            raise PackageSecurityError(f"Unsafe Windows-compatible {label}: {raw_path!r}.")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise PackageSecurityError(f"Reserved device name in {label}: {raw_path!r}.")
    normalized = path.as_posix()
    if normalized != raw_path:
        raise PackageSecurityError(f"Non-canonical {label}: {raw_path!r}.")
    return normalized


def _path_collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _is_symlink_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction(path))
    except OSError:
        return False


def _path_contains_symlink_like(path: Path, root: Path) -> bool:
    current = path
    while True:
        if _is_symlink_like(current):
            return True
        if current == root:
            return False
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _decode_fixed_base64(value: bytes, expected_size: int, label: str) -> bytes:
    try:
        text = value.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise PackageSignatureError(f"{label} must be ASCII base64.") from exc
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PackageSignatureError(f"{label} is not valid base64.") from exc
    if len(decoded) != expected_size:
        raise PackageSignatureError(f"{label} must decode to {expected_size} bytes.")
    return decoded


class SkillPackageService:
    """Build, inspect, and install signed offline VRCForge skill packages."""

    def __init__(
        self,
        skill_store: str | os.PathLike[str] | None = None,
        *,
        vrcforge_version: str | None = None,
        max_file_count: int = DEFAULT_MAX_FILE_COUNT,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
        max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    ) -> None:
        if skill_store is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
            skill_store = base / "VRCForge" / "skills"
        self.skill_store = Path(skill_store).expanduser().resolve()
        self.registry_path = self.skill_store / "registry.json"
        self.max_file_count = int(max_file_count)
        self.max_file_size = int(max_file_size)
        self.max_total_size = int(max_total_size)
        self.max_compression_ratio = float(max_compression_ratio)
        if min(self.max_file_count, self.max_file_size, self.max_total_size) <= 0:
            raise ValueError("Package limits must be positive.")
        if self.max_compression_ratio <= 1:
            raise ValueError("max_compression_ratio must be greater than one.")
        self.vrcforge_version = vrcforge_version or self._read_local_version()
        _parse_semver(self.vrcforge_version, "vrcforge_version")

    @staticmethod
    def generate_signing_keypair() -> SigningKeyPair:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return SigningKeyPair(private_pem, public_key, sha256_bytes(public_key))

    @staticmethod
    def save_signing_keypair(
        key_pair: SigningKeyPair,
        private_key_path: str | os.PathLike[str],
        public_key_path: str | os.PathLike[str] | None = None,
    ) -> None:
        private_path = Path(private_key_path)
        private_path.parent.mkdir(parents=True, exist_ok=True)
        SkillPackageService._atomic_write_bytes(private_path, key_pair.private_key_pem, mode=0o600)
        if public_key_path is not None:
            public_path = Path(public_key_path)
            public_path.parent.mkdir(parents=True, exist_ok=True)
            SkillPackageService._atomic_write_bytes(
                public_path,
                base64.b64encode(key_pair.public_key),
                mode=0o644,
            )

    def validate_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        package_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(manifest, Mapping):
            raise ManifestValidationError("manifest.json must contain an object.")
        required = {
            "id",
            "name",
            "version",
            "author",
            "description",
            "min_vrcforge_version",
            "permissions",
            "entrypoints",
        }
        missing = sorted(required - set(manifest))
        if missing:
            raise ManifestValidationError(f"manifest.json is missing required fields: {', '.join(missing)}.")

        try:
            normalized = json.loads(json.dumps(dict(manifest), ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ManifestValidationError("manifest.json must contain portable JSON values.") from exc
        skill_id = normalized.get("id")
        if not isinstance(skill_id, str) or len(skill_id) > 128 or not SKILL_ID_RE.fullmatch(skill_id):
            raise ManifestValidationError("id must be a lowercase reverse-domain style identifier.")
        for field, maximum in (("name", 160), ("author", 160), ("description", 4_000)):
            value = normalized.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ManifestValidationError(f"{field} must be a non-empty string of at most {maximum} characters.")
        _parse_semver(normalized.get("version"), "version")
        _parse_semver(normalized.get("min_vrcforge_version"), "min_vrcforge_version")

        permissions = normalized.get("permissions")
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            raise ManifestValidationError("permissions must be an array of permission names.")
        if len(permissions) != len(set(permissions)):
            raise ManifestValidationError("permissions must not contain duplicates.")
        for permission in permissions:
            if not TOKEN_RE.fullmatch(permission):
                raise ManifestValidationError(f"Invalid permission name: {permission!r}.")

        entrypoints = normalized.get("entrypoints")
        if not isinstance(entrypoints, dict) or not entrypoints:
            raise ManifestValidationError("entrypoints must be a non-empty object.")
        normalized_entrypoints: dict[str, str] = {}
        for name, value in entrypoints.items():
            if not isinstance(name, str) or not TOKEN_RE.fullmatch(name):
                raise ManifestValidationError(f"Invalid entrypoint name: {name!r}.")
            if not isinstance(value, str):
                raise ManifestValidationError(f"Entrypoint {name!r} must be a path string.")
            normalized_entrypoints[name] = _safe_relative_path(value, label=f"entrypoint {name}")
            if normalized_entrypoints[name] in RESERVED_PACKAGE_FILES:
                raise ManifestValidationError(f"Entrypoint cannot target package metadata: {value}.")
        normalized["entrypoints"] = normalized_entrypoints

        dependencies = normalized.get("skill_dependencies")
        if dependencies is not None:
            if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
                raise ManifestValidationError("skill_dependencies must be an array of skill ids.")
            if len(dependencies) != len(set(dependencies)):
                raise ManifestValidationError("skill_dependencies must not contain duplicates.")
            for dependency in dependencies:
                if len(dependency) > 128 or not SKILL_ID_RE.fullmatch(dependency):
                    raise ManifestValidationError(f"Invalid skill dependency id: {dependency!r}.")

        targets = normalized.get("targets")
        if targets is not None and not isinstance(targets, dict):
            raise ManifestValidationError("targets must be an object when present.")
        agent = normalized.get("agent")
        if agent is not None and not isinstance(agent, dict):
            raise ManifestValidationError("agent must be an object when present.")

        if package_root is not None:
            root = Path(package_root).resolve()
            for name, relative in normalized_entrypoints.items():
                candidate = root / relative
                entrypoint = candidate.resolve()
                try:
                    entrypoint.relative_to(root)
                except ValueError as exc:
                    raise ManifestValidationError(f"Entrypoint escapes the package: {name}.") from exc
                if _path_contains_symlink_like(candidate, root):
                    raise ManifestValidationError(f"Entrypoint cannot use symlinks or junctions: {relative}.")
                if not candidate.is_file():
                    raise ManifestValidationError(f"Entrypoint does not exist as a regular file: {relative}.")
        return normalized

    def export_dev(
        self,
        source_dir: str | os.PathLike[str],
        output_path: str | os.PathLike[str],
        *,
        overwrite: bool = True,
    ) -> ExportResult:
        return self._export(source_dir, output_path, package_mode="dev", private_key=None, overwrite=overwrite)

    def export_release(
        self,
        source_dir: str | os.PathLike[str],
        output_path: str | os.PathLike[str],
        private_key: Ed25519PrivateKey | bytes | str | os.PathLike[str],
        *,
        overwrite: bool = True,
    ) -> ExportResult:
        return self._export(
            source_dir,
            output_path,
            package_mode="release",
            private_key=private_key,
            overwrite=overwrite,
        )

    def inspect_package(self, package_path: str | os.PathLike[str]) -> ImportPreview:
        with self._validated_package(package_path) as validated:
            return validated.preview

    def preflight_import(
        self,
        package_path: str | os.PathLike[str],
        *,
        allow_downgrade: bool = False,
        dev_mode: bool = False,
    ) -> ImportPreview:
        with self._validated_package(package_path) as validated:
            registry = self.load_registry()
            action = self._check_update_policy(
                validated.preview,
                self._find_installed_entry(validated.preview.manifest["id"], registry),
                allow_downgrade=allow_downgrade,
                dev_mode=dev_mode,
            )
            governance = self._evaluate_preview_governance(validated.preview, registry)
            return self._preview_with_action(validated.preview, action, governance=governance)

    def install(
        self,
        package_path: str | os.PathLike[str],
        *,
        source: str | None = None,
        allow_downgrade: bool = False,
        dev_mode: bool = False,
    ) -> InstallResult:
        with self.install_transaction(
            package_path,
            source=source,
            allow_downgrade=allow_downgrade,
            dev_mode=dev_mode,
        ) as result:
            return result

    @contextmanager
    def install_transaction(
        self,
        package_path: str | os.PathLike[str],
        *,
        source: str | None = None,
        allow_downgrade: bool = False,
        dev_mode: bool = False,
    ) -> Iterator[InstallResult]:
        """Keep package metadata reversible until its external projection succeeds."""

        with self._validated_package(package_path) as validated:
            self.skill_store.mkdir(parents=True, exist_ok=True)
            registry = self.load_registry()
            skill_id = validated.preview.manifest["id"]
            existing = self._find_installed_entry(skill_id, registry)
            action = self._check_update_policy(
                validated.preview,
                existing,
                allow_downgrade=allow_downgrade,
                dev_mode=dev_mode,
            )
            governance = self._evaluate_preview_governance(validated.preview, registry)
            preview = self._preview_with_action(validated.preview, action, governance=governance)
            if not governance.get("importAllowed", False):
                self._write_registry_audit(
                    registry,
                    {
                        "event": "skill_package_import_blocked",
                        "skill_id": skill_id,
                        "package_sha256": preview.package_sha256,
                        "signer_fingerprint": preview.signer_fingerprint,
                        "reasons": list(governance.get("blockingReasons") or []),
                    },
                )
                raise PackageSecurityError(self._format_governance_block("import", governance))
            snapshot = self._capture_install_state(skill_id, str(preview.manifest["version"]))
            result = self._install_validated(validated.temp_dir, preview, registry, source=source)
            try:
                yield result
            except Exception:
                try:
                    self._restore_install_state(snapshot)
                except Exception as restore_exc:
                    raise PackageIntegrityError(
                        f"Skill package projection failed and install state could not be restored: {skill_id}."
                    ) from restore_exc
                raise

    def _capture_install_state(self, skill_id: str, version: str) -> dict[str, Any]:
        skill_root = self.skill_store / skill_id
        versions_root = skill_root / "versions"
        version_root = versions_root / version
        installed_path = skill_root / "installed.json"
        registry_exists = self.registry_path.is_file()
        installed_exists = installed_path.is_file()
        return {
            "skill_id": skill_id,
            "version": version,
            "skill_root_existed": skill_root.exists(),
            "versions_root_existed": versions_root.exists(),
            "version_root_existed": version_root.exists(),
            "registry_existed": registry_exists,
            "registry_bytes": self.registry_path.read_bytes() if registry_exists else None,
            "installed_existed": installed_exists,
            "installed_bytes": installed_path.read_bytes() if installed_exists else None,
        }

    def _restore_install_state(self, snapshot: Mapping[str, Any]) -> None:
        skill_id = str(snapshot["skill_id"])
        version = str(snapshot["version"])
        skill_root = self.skill_store / skill_id
        versions_root = skill_root / "versions"
        version_root = versions_root / version
        installed_path = skill_root / "installed.json"

        if not bool(snapshot["version_root_existed"]) and version_root.exists():
            if _is_symlink_like(version_root):
                raise PackageSecurityError(f"Refusing to roll back a linked skill version directory: {skill_id}.")
            shutil.rmtree(version_root)
        if bool(snapshot["installed_existed"]):
            self._atomic_write_bytes(installed_path, bytes(snapshot["installed_bytes"]))
        else:
            installed_path.unlink(missing_ok=True)
        if bool(snapshot["registry_existed"]):
            self._atomic_write_bytes(self.registry_path, bytes(snapshot["registry_bytes"]))
        else:
            self.registry_path.unlink(missing_ok=True)

        if not bool(snapshot["versions_root_existed"]) and versions_root.exists():
            try:
                versions_root.rmdir()
            except OSError:
                pass
        if not bool(snapshot["skill_root_existed"]) and skill_root.exists():
            try:
                skill_root.rmdir()
            except OSError:
                pass

    def import_package(self, *args: Any, **kwargs: Any) -> InstallResult:
        """Alias for callers that use import terminology."""

        return self.install(*args, **kwargs)

    def set_enabled(self, skill_id: str, enabled: bool) -> PackageStateResult:
        normalized_id = self._normalize_installed_skill_id(skill_id)
        registry = self.load_registry()
        entry = self._find_installed_entry(normalized_id, registry)
        if entry is None:
            raise SkillPackageError(f"Skill package is not installed: {normalized_id}.")
        governance = self._evaluate_installed_governance(entry, registry)
        if bool(enabled) and not governance.get("enableAllowed", False):
            self._write_registry_audit(
                registry,
                {
                    "event": "skill_package_enable_blocked",
                    "skill_id": normalized_id,
                    "signer_fingerprint": entry.get("signer_fingerprint"),
                    "reasons": list(governance.get("blockingReasons") or []),
                },
            )
            raise PackageSecurityError(self._format_governance_block("enable", governance))
        registry_entry = dict(entry)
        registry_entry.pop("versions", None)
        changed = bool(registry_entry.get("enabled", True)) != bool(enabled)
        registry_entry["enabled"] = bool(enabled)
        self._write_installed_registry_entry(
            normalized_id,
            registry_entry,
            registry,
            audit_event={
                "event": "skill_package_enabled" if bool(enabled) else "skill_package_disabled",
                "skill_id": normalized_id,
                "changed": changed,
            },
        )
        manifest = self._read_current_manifest(normalized_id, str(registry_entry.get("version") or ""))
        return PackageStateResult(normalized_id, registry_entry, manifest, changed)

    @contextmanager
    def state_transaction(self, skill_ids: Sequence[str] | None = None) -> Iterator[None]:
        """Restore registry and installed metadata if a coordinated host write fails."""

        snapshot = self._capture_state_transaction(skill_ids)
        try:
            yield
        except Exception:
            try:
                self._restore_state_transaction(snapshot)
            except Exception as restore_exc:
                raise PackageIntegrityError(
                    "Skill package state update failed and registry metadata could not be restored."
                ) from restore_exc
            raise

    def _capture_state_transaction(self, skill_ids: Sequence[str] | None) -> dict[str, Any]:
        registry = self.load_registry()
        if skill_ids is None:
            normalized_ids = sorted(registry["skills"])
        else:
            normalized_ids = sorted({self._normalize_installed_skill_id(skill_id) for skill_id in skill_ids})
        registry_existed = self.registry_path.is_file()
        installed: dict[str, bytes | None] = {}
        for skill_id in normalized_ids:
            installed_path = self.skill_store / skill_id / "installed.json"
            if _is_symlink_like(installed_path):
                raise PackageSecurityError(f"Installed metadata must not be linked: {skill_id}.")
            if installed_path.exists() and not installed_path.is_file():
                raise PackageSecurityError(f"Installed metadata must be a regular file: {skill_id}.")
            installed[skill_id] = installed_path.read_bytes() if installed_path.is_file() else None
        return {
            "registry_existed": registry_existed,
            "registry_bytes": self.registry_path.read_bytes() if registry_existed else None,
            "installed": installed,
        }

    def _restore_state_transaction(self, snapshot: Mapping[str, Any]) -> None:
        installed = snapshot.get("installed")
        if not isinstance(installed, Mapping):
            raise PackageIntegrityError("Skill package state transaction snapshot is invalid.")
        for raw_skill_id, raw_bytes in installed.items():
            skill_id = self._normalize_installed_skill_id(str(raw_skill_id))
            installed_path = self.skill_store / skill_id / "installed.json"
            if _is_symlink_like(installed_path):
                raise PackageSecurityError(f"Refusing to restore linked installed metadata: {skill_id}.")
            if raw_bytes is None:
                installed_path.unlink(missing_ok=True)
            else:
                self._atomic_write_bytes(installed_path, bytes(raw_bytes))
        if bool(snapshot.get("registry_existed")):
            self._atomic_write_bytes(self.registry_path, bytes(snapshot.get("registry_bytes") or b""))
        else:
            self.registry_path.unlink(missing_ok=True)

    def uninstall(self, skill_id: str) -> UninstallResult:
        with self.uninstall_transaction(skill_id) as result:
            return result

    @contextmanager
    def uninstall_transaction(self, skill_id: str) -> Iterator[UninstallResult]:
        """Keep an uninstalled package tree recoverable until host projection removal succeeds."""

        normalized_id = self._normalize_installed_skill_id(skill_id)
        registry = self.load_registry()
        entry = self._find_installed_entry(normalized_id, registry)
        if entry is None:
            raise SkillPackageError(f"Skill package is not installed: {normalized_id}.")
        registry_entry = dict(entry)
        registry_entry.pop("versions", None)
        skill_root = self.skill_store / normalized_id
        if skill_root.is_symlink():
            raise PackageSecurityError(f"Refusing to uninstall through a symlinked skill directory: {skill_root}.")
        if skill_root.exists() and not skill_root.is_dir():
            raise PackageSecurityError(f"Installed skill path is not a directory: {skill_root}.")
        manifest = self._read_current_manifest(normalized_id, str(registry_entry.get("version") or ""))
        removed_versions = tuple(self._installed_versions(normalized_id))
        registry_bytes = self.registry_path.read_bytes()
        next_registry = self._registry_document(
            registry,
            skills=dict(registry["skills"]),
            audit_event={"event": "skill_package_uninstalled", "skill_id": normalized_id},
        )
        next_registry["skills"].pop(normalized_id, None)
        self.skill_store.mkdir(parents=True, exist_ok=True)
        staging_root = self.skill_store / ".uninstall-staging"
        if _is_symlink_like(staging_root) or (staging_root.exists() and not staging_root.is_dir()):
            raise PackageSecurityError(f"Refusing to use unsafe uninstall staging directory: {staging_root}.")
        staging_root.mkdir(parents=True, exist_ok=True)
        isolated_root = staging_root / f"{normalized_id}.{uuid.uuid4().hex}"
        moved = False
        committed = False
        try:
            if skill_root.exists():
                os.replace(skill_root, isolated_root)
                moved = True
            self._atomic_write_json(self.registry_path, next_registry)
            result = UninstallResult(
                normalized_id,
                registry_entry,
                manifest,
                skill_root,
                removed_versions,
                moved or normalized_id in registry["skills"],
            )
            yield result
            committed = True
        except Exception:
            try:
                if moved and isolated_root.exists():
                    if skill_root.exists():
                        raise PackageIntegrityError(
                            f"Cannot restore uninstalled package because its original path was recreated: {normalized_id}."
                        )
                    os.replace(isolated_root, skill_root)
                    moved = False
                self._atomic_write_bytes(self.registry_path, registry_bytes)
            except Exception as restore_exc:
                raise PackageIntegrityError(
                    f"Skill package uninstall failed and prior package state could not be restored: {normalized_id}."
                ) from restore_exc
            raise
        finally:
            if committed and isolated_root.exists():
                shutil.rmtree(isolated_root, ignore_errors=True)
            if staging_root.exists():
                try:
                    staging_root.rmdir()
                except OSError:
                    pass

    def load_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return self._registry_document({"skills": {}})
        if self.registry_path.is_symlink() or not self.registry_path.is_file():
            raise PackageSecurityError("registry.json must be a regular file.")
        value = _load_json_bytes(self.registry_path.read_bytes(), "registry.json")
        if not isinstance(value, dict) or value.get("schema") != REGISTRY_SCHEMA:
            raise PackageIntegrityError("registry.json has an unsupported schema.")
        skills = value.get("skills")
        if not isinstance(skills, dict):
            raise PackageIntegrityError("registry.json skills must be an object.")
        normalized_skills: dict[str, dict[str, Any]] = {}
        for skill_id, entry in skills.items():
            normalized_skills[skill_id] = self._validate_registry_entry(
                skill_id,
                entry,
                label=f"registry entry {skill_id!r}",
            )
        return self._registry_document(
            value,
            skills=normalized_skills,
            governance=self._normalize_governance(value.get("governance")),
            audit=self._normalize_audit(value.get("audit")),
        )

    def list_installed(self) -> list[dict[str, Any]]:
        registry = self.load_registry()
        return [
            self._decorate_installed_entry(dict(registry["skills"][key]), registry)
            for key in sorted(registry["skills"])
        ]

    def runtime_audit_context(
        self,
        projected_skill_name: str,
        projected_skill_path: str | os.PathLike[str],
        projected_support_files: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Return verified package identity for an unchanged projected skill.

        Installed manifests, complete lock contents, and release signatures are
        revalidated before the signer is attributed to a runtime execution. An
        edited projection or ambiguous projected name deliberately returns no
        package context so callers can preserve their legacy audit shape.
        """

        target_name = str(projected_skill_name or "").strip().lower()
        projected_input = Path(projected_skill_path).expanduser()
        if not target_name or _is_symlink_like(projected_input) or not projected_input.is_file():
            return {}
        try:
            projected = projected_input.resolve(strict=True)
            registry = self.load_registry()
        except (OSError, SkillPackageError):
            return {}

        matches: list[dict[str, Any]] = []
        for skill_id in sorted(registry["skills"]):
            try:
                entry = self._find_installed_entry(skill_id, registry)
                if entry is None:
                    continue
                version = str(entry.get("version") or "")
                package_sha256 = str(entry.get("package_sha256") or "")
                version_root = self.skill_store / skill_id / "versions" / version
                if (
                    not version
                    or not package_sha256
                    or _is_symlink_like(version_root)
                    or not version_root.is_dir()
                ):
                    continue
                resolved_root = version_root.resolve(strict=True)
                resolved_root.relative_to(self.skill_store)
                manifest_path = resolved_root / MANIFEST_NAME
                lock_path = resolved_root / LOCK_NAME
                if (
                    not manifest_path.is_file()
                    or _is_symlink_like(manifest_path)
                    or not lock_path.is_file()
                    or _is_symlink_like(lock_path)
                ):
                    continue
                manifest_bytes = _read_regular_file_bounded(
                    manifest_path,
                    self.max_file_size,
                    label=f"{skill_id}/{version}/{MANIFEST_NAME}",
                )
                manifest_value = _load_json_bytes(manifest_bytes, f"{skill_id}/{version}/{MANIFEST_NAME}")
                manifest = self.validate_manifest(manifest_value, package_root=resolved_root)
                projected_name = re.sub(
                    r"[^a-z0-9_.-]+",
                    "-",
                    str(manifest.get("skill_name") or manifest.get("skillName") or skill_id).lower(),
                ).strip("-._")
                if projected_name != target_name:
                    continue
                skill_entrypoint = str(manifest.get("entrypoints", {}).get("skill") or "").strip()
                if not skill_entrypoint:
                    continue
                installed_skill = (resolved_root / skill_entrypoint).resolve(strict=True)
                installed_skill.relative_to(resolved_root)
                if not installed_skill.is_file() or _is_symlink_like(installed_skill):
                    continue

                lock_bytes = _read_regular_file_bounded(
                    lock_path,
                    self.max_file_size,
                    label=f"{skill_id}/{version}/{LOCK_NAME}",
                )
                lock_value = _load_json_bytes(lock_bytes, f"{skill_id}/{version}/{LOCK_NAME}")
                locked_files = lock_value.get("files") if isinstance(lock_value, dict) else None
                if (
                    not isinstance(lock_value, dict)
                    or lock_value.get("schema") != LOCK_SCHEMA
                    or lock_value.get("algorithm") != "sha256"
                    or canonical_json_bytes(lock_value) != lock_bytes
                    or not isinstance(locked_files, dict)
                ):
                    continue
                expected_files: dict[str, str] = {}
                lock_valid = True
                for raw_relative, digest in locked_files.items():
                    try:
                        relative = _safe_relative_path(raw_relative, label="runtime audit lock path")
                    except SkillPackageError:
                        lock_valid = False
                        break
                    if (
                        relative in RESERVED_PACKAGE_FILES
                        or not isinstance(digest, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or relative in expected_files
                    ):
                        lock_valid = False
                        break
                    expected_files[relative] = digest
                if not lock_valid or not expected_files:
                    continue

                actual_files: set[str] = set()
                installed_tree_valid = True
                installed_total_size = 0
                for path in resolved_root.rglob("*"):
                    if _is_symlink_like(path):
                        installed_tree_valid = False
                        break
                    if path.is_dir():
                        continue
                    if not path.is_file():
                        installed_tree_valid = False
                        break
                    relative = path.relative_to(resolved_root).as_posix()
                    if relative not in RESERVED_PACKAGE_FILES:
                        metadata = path.stat(follow_symlinks=False)
                        if (
                            not stat.S_ISREG(metadata.st_mode)
                            or metadata.st_size > self.max_file_size
                        ):
                            installed_tree_valid = False
                            break
                        installed_total_size += metadata.st_size
                        if installed_total_size > self.max_total_size:
                            installed_tree_valid = False
                            break
                        actual_files.add(relative)
                        if len(actual_files) > self.max_file_count:
                            installed_tree_valid = False
                            break
                if not installed_tree_valid or actual_files != set(expected_files):
                    continue
                if any(
                    _sha256_regular_file_bounded(
                        resolved_root / relative,
                        self.max_file_size,
                        label=f"{skill_id}/{version}/{relative}",
                    )
                    != digest
                    for relative, digest in expected_files.items()
                ):
                    continue

                expected_manifest_sha = expected_files.get(MANIFEST_NAME)
                expected_skill_sha = expected_files.get(skill_entrypoint)
                if not expected_manifest_sha or not expected_skill_sha:
                    continue
                if sha256_bytes(manifest_bytes) != expected_manifest_sha:
                    continue
                if (
                    _sha256_regular_file_bounded(
                        installed_skill,
                        self.max_file_size,
                        label=f"{skill_id}/{version}/{skill_entrypoint}",
                    )
                    != expected_skill_sha
                ):
                    continue
                if (
                    _sha256_regular_file_bounded(
                        projected,
                        self.max_file_size,
                        label=f"projected skill {target_name}",
                    )
                    != expected_skill_sha
                ):
                    continue

                projected_root = projected.parent
                projection_valid = True
                projected_relatives = set(manifest.get("entrypoints", {}).values())
                for raw_relative in projected_support_files or ():
                    try:
                        projected_relatives.add(
                            _safe_relative_path(raw_relative, label="projected runtime support path")
                        )
                    except SkillPackageError:
                        projection_valid = False
                        break
                if not projection_valid:
                    continue
                for raw_relative in projected_relatives:
                    relative = str(raw_relative or "").strip()
                    if not relative or relative == skill_entrypoint:
                        continue
                    expected_digest = expected_files.get(relative)
                    if not expected_digest:
                        projection_valid = False
                        break
                    relative_path = PurePosixPath(relative)
                    projected_support_input = projected_root.joinpath(*relative_path.parts)
                    try:
                        projected_support = projected_support_input.resolve(strict=True)
                        projected_support.relative_to(projected_root)
                    except (OSError, ValueError):
                        projection_valid = False
                        break
                    if (
                        _path_contains_symlink_like(projected_support_input, projected_root)
                        or not projected_support.is_file()
                        or _sha256_regular_file_bounded(
                            projected_support,
                            self.max_file_size,
                            label=f"projected support {relative}",
                        )
                        != expected_digest
                    ):
                        projection_valid = False
                        break
                if not projection_valid:
                    continue

                signature_status = str(entry.get("signature_status") or "")
                signer_fingerprint = entry.get("signer_fingerprint")
                package_mode = lock_value.get("package_mode")
                public_key_path = resolved_root / PUBLIC_KEY_NAME
                signature_path = resolved_root / SIGNATURE_NAME
                public_key_present = public_key_path.exists() or _is_symlink_like(public_key_path)
                signature_present = signature_path.exists() or _is_symlink_like(signature_path)
                has_public_key = public_key_present and public_key_path.is_file() and not _is_symlink_like(public_key_path)
                has_signature = signature_present and signature_path.is_file() and not _is_symlink_like(signature_path)
                if signature_status == "signed":
                    if package_mode != "release" or not has_public_key or not has_signature:
                        continue
                    public_key_bytes = _decode_fixed_base64(
                        _read_regular_file_bounded(public_key_path, 4 * 1024, label=PUBLIC_KEY_NAME),
                        32,
                        PUBLIC_KEY_NAME,
                    )
                    signature_bytes = _decode_fixed_base64(
                        _read_regular_file_bounded(signature_path, 4 * 1024, label=SIGNATURE_NAME),
                        64,
                        SIGNATURE_NAME,
                    )
                    try:
                        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature_bytes, lock_bytes)
                    except InvalidSignature:
                        continue
                    verified_signer = sha256_bytes(public_key_bytes)
                    if verified_signer != signer_fingerprint:
                        continue
                elif signature_status == "dev":
                    if package_mode != "dev" or public_key_present or signature_present or signer_fingerprint is not None:
                        continue
                    verified_signer = None
                else:
                    continue

                tiers = self._permission_tiers(manifest["permissions"])
                risk_level = "high" if tiers["high"] else "medium" if tiers["medium"] else "low"
                lock_sha256 = sha256_bytes(lock_bytes)
                if (
                    manifest.get("id") != skill_id
                    or manifest.get("version") != version
                    or (
                        entry.get("author") is not None
                        and manifest.get("author") != entry.get("author")
                    )
                    or lock_sha256 != entry.get("lock_sha256")
                    or risk_level != entry.get("risk_level")
                    or list(manifest["permissions"]) != list(entry.get("permissions") or [])
                ):
                    continue
                decorated = self._decorate_installed_entry(dict(entry), registry)
                matches.append(
                    {
                        "packageId": skill_id,
                        "authorId": manifest["author"],
                        "packageVersion": version,
                        "packageSha256": package_sha256,
                        "lockSha256": lock_sha256,
                        "signatureStatus": signature_status,
                        "signerFingerprint": verified_signer,
                        "signerTrustStatus": decorated.get("signer_trust_status"),
                    }
                )
            except (OSError, ValueError, SkillPackageError):
                continue
        return matches[0] if len(matches) == 1 else {}

    def set_safe_mode(self, enabled: bool, *, reason: str | None = None) -> dict[str, Any]:
        registry = self.load_registry()
        governance = self._normalize_governance(registry.get("governance"))
        previous = bool(governance["safe_mode"].get("enabled", False))
        governance["safe_mode"]["enabled"] = bool(enabled)
        skills = dict(registry["skills"])
        disabled = self._disable_governance_blocked_skills(skills, governance) if enabled else []
        document = self._registry_document(
            registry,
            skills=skills,
            governance=governance,
            audit_event={
                "event": "skill_package_safe_mode_updated",
                "enabled": bool(enabled),
                "reason": self._normalize_reason(reason),
                "disabled_skill_ids": disabled,
            },
        )
        self._atomic_write_json(self.registry_path, document)
        self._sync_installed_metadata(document, disabled)
        return {
            "ok": True,
            "changed": previous != bool(enabled) or bool(disabled),
            "governance": document["governance"],
            "disabledSkillIds": disabled,
        }

    def trust_signer(self, signer_fingerprint: str, *, reason: str | None = None) -> dict[str, Any]:
        fingerprint = self._normalize_signer_fingerprint(signer_fingerprint)
        registry = self.load_registry()
        governance = self._normalize_governance(registry.get("governance"))
        if fingerprint in governance["revoked_signers"]:
            raise PackageSecurityError("A revoked signer cannot be trusted until the revocation list is edited.")
        previous = fingerprint in governance["trusted_signers"]
        governance["trusted_signers"][fingerprint] = {
            "trusted_at": self._utc_timestamp(),
            "reason": self._normalize_reason(reason),
        }
        document = self._registry_document(
            registry,
            governance=governance,
            audit_event={
                "event": "skill_package_signer_trusted",
                "signer_fingerprint": fingerprint,
                "reason": self._normalize_reason(reason),
            },
        )
        self._atomic_write_json(self.registry_path, document)
        return {"ok": True, "changed": not previous, "governance": document["governance"]}

    def revoke_signer(self, signer_fingerprint: str, *, reason: str | None = None) -> dict[str, Any]:
        fingerprint = self._normalize_signer_fingerprint(signer_fingerprint)
        registry = self.load_registry()
        governance = self._normalize_governance(registry.get("governance"))
        previous = fingerprint in governance["revoked_signers"]
        governance["trusted_signers"].pop(fingerprint, None)
        governance["revoked_signers"][fingerprint] = {
            "revoked_at": self._utc_timestamp(),
            "reason": self._normalize_reason(reason),
        }
        skills = dict(registry["skills"])
        disabled = self._disable_matching_skills(
            skills,
            lambda entry: entry.get("signer_fingerprint") == fingerprint,
            disabled_reason="revoked_signer",
        )
        document = self._registry_document(
            registry,
            skills=skills,
            governance=governance,
            audit_event={
                "event": "skill_package_signer_revoked",
                "signer_fingerprint": fingerprint,
                "reason": self._normalize_reason(reason),
                "disabled_skill_ids": disabled,
            },
        )
        self._atomic_write_json(self.registry_path, document)
        self._sync_installed_metadata(document, disabled)
        return {
            "ok": True,
            "changed": not previous or bool(disabled),
            "governance": document["governance"],
            "disabledSkillIds": disabled,
        }

    def block_package(
        self,
        *,
        package_id: str | None = None,
        package_sha256: str | None = None,
        lock_sha256: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_optional_skill_id(package_id)
        package_hash = self._normalize_optional_sha256(package_sha256, "package_sha256")
        lock_hash = self._normalize_optional_sha256(lock_sha256, "lock_sha256")
        if not any((normalized_id, package_hash, lock_hash)):
            raise PackageSecurityError("Blocklist needs a package id, package SHA-256, or lock SHA-256.")
        registry = self.load_registry()
        governance = self._normalize_governance(registry.get("governance"))
        blocked = governance["blocked_packages"]
        timestamp = self._utc_timestamp()
        reason_text = self._normalize_reason(reason)
        previous = False
        if normalized_id:
            previous = normalized_id in blocked["ids"] or previous
            blocked["ids"][normalized_id] = {"blocked_at": timestamp, "reason": reason_text}
        if package_hash:
            previous = package_hash in blocked["package_sha256"] or previous
            blocked["package_sha256"][package_hash] = {"blocked_at": timestamp, "reason": reason_text}
        if lock_hash:
            previous = lock_hash in blocked["lock_sha256"] or previous
            blocked["lock_sha256"][lock_hash] = {"blocked_at": timestamp, "reason": reason_text}
        skills = dict(registry["skills"])
        disabled = self._disable_matching_skills(
            skills,
            lambda entry: (
                (normalized_id is not None and entry.get("id") == normalized_id)
                or (package_hash is not None and entry.get("package_sha256") == package_hash)
                or (lock_hash is not None and entry.get("lock_sha256") == lock_hash)
            ),
            disabled_reason="blocked_package",
        )
        document = self._registry_document(
            registry,
            skills=skills,
            governance=governance,
            audit_event={
                "event": "skill_package_blocked",
                "package_id": normalized_id,
                "package_sha256": package_hash,
                "lock_sha256": lock_hash,
                "reason": reason_text,
                "disabled_skill_ids": disabled,
            },
        )
        self._atomic_write_json(self.registry_path, document)
        self._sync_installed_metadata(document, disabled)
        return {
            "ok": True,
            "changed": not previous or bool(disabled),
            "governance": document["governance"],
            "disabledSkillIds": disabled,
        }

    @staticmethod
    def _normalize_installed_skill_id(skill_id: str) -> str:
        normalized = str(skill_id or "").strip().lower()
        if not normalized or len(normalized) > 128 or not SKILL_ID_RE.fullmatch(normalized):
            raise SkillPackageError("Skill package id must be a lowercase reverse-domain id.")
        return normalized

    @staticmethod
    def _validate_registry_entry(
        skill_id: str,
        entry: Any,
        *,
        label: str,
        allow_versions: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(skill_id, str) or len(skill_id) > 128 or not SKILL_ID_RE.fullmatch(skill_id):
            raise PackageIntegrityError(f"{label} has an invalid skill id.")
        if not isinstance(entry, dict):
            raise PackageIntegrityError(f"{label} must be an object.")
        if entry.get("id") != skill_id:
            raise PackageIntegrityError(f"{label} id does not match its registry key.")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise PackageIntegrityError(f"{label} has an invalid name.")
        author = entry.get("author")
        if author is not None and (
            not isinstance(author, str) or not author.strip() or len(author) > 160
        ):
            raise PackageIntegrityError(f"{label} has an invalid author identity.")
        try:
            _parse_semver(entry.get("version"), f"{label} version")
        except ManifestValidationError as exc:
            raise PackageIntegrityError(f"{label} has an invalid version.") from exc
        signature_status = entry.get("signature_status")
        if signature_status not in {"dev", "signed"}:
            raise PackageIntegrityError(f"{label} has an invalid signature status.")
        signer = entry.get("signer_fingerprint")
        if signature_status == "signed":
            if not isinstance(signer, str) or not re.fullmatch(r"[0-9a-f]{64}", signer):
                raise PackageIntegrityError(f"{label} has an invalid signer fingerprint.")
        elif signer is not None:
            raise PackageIntegrityError(f"{label} must not pin a signer for dev packages.")
        lock_sha = entry.get("lock_sha256")
        if not isinstance(lock_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", lock_sha):
            raise PackageIntegrityError(f"{label} has an invalid lock SHA-256.")
        permissions = entry.get("permissions")
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            raise PackageIntegrityError(f"{label} permissions must be an array.")
        if len(permissions) != len(set(permissions)) or any(not TOKEN_RE.fullmatch(item) for item in permissions):
            raise PackageIntegrityError(f"{label} has invalid permissions.")
        risk_level = entry.get("risk_level")
        if risk_level not in {"low", "medium", "high"}:
            raise PackageIntegrityError(f"{label} has an invalid risk level.")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PackageIntegrityError(f"{label} enabled must be a boolean.")
        source = entry.get("source")
        if source is not None and not isinstance(source, str):
            raise PackageIntegrityError(f"{label} source must be a string.")
        package_sha = entry.get("package_sha256")
        if package_sha is not None and (
            not isinstance(package_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", package_sha)
        ):
            raise PackageIntegrityError(f"{label} has an invalid package SHA-256.")
        governance = entry.get("governance")
        if governance is not None and not isinstance(governance, dict):
            raise PackageIntegrityError(f"{label} governance must be an object.")
        if allow_versions:
            versions = entry.get("versions", [])
            if not isinstance(versions, list) or any(not isinstance(item, str) for item in versions):
                raise PackageIntegrityError(f"{label} versions must be an array.")
            for installed_version in versions:
                try:
                    _parse_semver(installed_version, f"{label} installed version")
                except ManifestValidationError as exc:
                    raise PackageIntegrityError(f"{label} has an invalid installed version.") from exc
        return dict(entry)

    def _registry_document(
        self,
        registry: Mapping[str, Any],
        *,
        skills: Mapping[str, dict[str, Any]] | None = None,
        governance: Mapping[str, Any] | None = None,
        audit: Sequence[Mapping[str, Any]] | None = None,
        audit_event: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        audit_entries = self._normalize_audit(audit if audit is not None else registry.get("audit"))
        if audit_event is not None:
            audit_entries.append(self._audit_entry(audit_event))
            audit_entries = audit_entries[-DEFAULT_AUDIT_LIMIT:]
        return {
            "schema": REGISTRY_SCHEMA,
            "skills": dict(skills if skills is not None else registry.get("skills") or {}),
            "governance": self._normalize_governance(
                governance if governance is not None else registry.get("governance")
            ),
            "audit": audit_entries,
        }

    @staticmethod
    def _default_governance() -> dict[str, Any]:
        return {
            "schema": GOVERNANCE_SCHEMA,
            "safe_mode": {
                "enabled": False,
                "disable_risk_levels": ["medium", "high"],
                "block_enable": True,
            },
            "trusted_signers": {},
            "revoked_signers": {},
            "blocked_packages": {
                "ids": {},
                "package_sha256": {},
                "lock_sha256": {},
            },
        }

    @classmethod
    def _normalize_governance(cls, value: Any) -> dict[str, Any]:
        default = cls._default_governance()
        if value is None:
            return default
        if not isinstance(value, Mapping):
            raise PackageIntegrityError("registry governance must be an object.")
        if value.get("schema", GOVERNANCE_SCHEMA) != GOVERNANCE_SCHEMA:
            raise PackageIntegrityError("registry governance has an unsupported schema.")

        safe_mode = value.get("safe_mode")
        if safe_mode is not None and not isinstance(safe_mode, Mapping):
            raise PackageIntegrityError("registry safe_mode must be an object.")
        safe_mode = dict(safe_mode or {})
        disable_levels = safe_mode.get("disable_risk_levels", default["safe_mode"]["disable_risk_levels"])
        if not isinstance(disable_levels, list) or any(item not in {"low", "medium", "high"} for item in disable_levels):
            raise PackageIntegrityError("registry safe_mode.disable_risk_levels is invalid.")

        blocked = value.get("blocked_packages")
        if blocked is not None and not isinstance(blocked, Mapping):
            raise PackageIntegrityError("registry blocked_packages must be an object.")
        blocked = dict(blocked or {})
        return {
            "schema": GOVERNANCE_SCHEMA,
            "safe_mode": {
                "enabled": bool(safe_mode.get("enabled", default["safe_mode"]["enabled"])),
                "disable_risk_levels": list(dict.fromkeys(disable_levels)),
                "block_enable": bool(safe_mode.get("block_enable", default["safe_mode"]["block_enable"])),
            },
            "trusted_signers": cls._normalize_signer_map(value.get("trusted_signers"), timestamp_key="trusted_at"),
            "revoked_signers": cls._normalize_signer_map(value.get("revoked_signers"), timestamp_key="revoked_at"),
            "blocked_packages": {
                "ids": cls._normalize_blocked_id_map(blocked.get("ids")),
                "package_sha256": cls._normalize_blocked_hash_map(blocked.get("package_sha256"), "package_sha256"),
                "lock_sha256": cls._normalize_blocked_hash_map(blocked.get("lock_sha256"), "lock_sha256"),
            },
        }

    @classmethod
    def _normalize_signer_map(cls, value: Any, *, timestamp_key: str) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise PackageIntegrityError("registry signer governance maps must be objects.")
        normalized: dict[str, dict[str, str]] = {}
        for raw_fingerprint, raw_meta in value.items():
            fingerprint = cls._normalize_signer_fingerprint(str(raw_fingerprint))
            meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            normalized[fingerprint] = {
                timestamp_key: str(meta.get(timestamp_key) or ""),
                "reason": str(meta.get("reason") or "")[:500],
            }
        return normalized

    @classmethod
    def _normalize_blocked_id_map(cls, value: Any) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise PackageIntegrityError("registry blocked package ids must be an object.")
        normalized: dict[str, dict[str, str]] = {}
        for raw_id, raw_meta in value.items():
            skill_id = cls._normalize_optional_skill_id(str(raw_id))
            if skill_id is None:
                raise PackageIntegrityError("registry blocked package id is invalid.")
            meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            normalized[skill_id] = {
                "blocked_at": str(meta.get("blocked_at") or ""),
                "reason": str(meta.get("reason") or "")[:500],
            }
        return normalized

    @classmethod
    def _normalize_blocked_hash_map(cls, value: Any, label: str) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise PackageIntegrityError(f"registry blocked {label} values must be an object.")
        normalized: dict[str, dict[str, str]] = {}
        for raw_hash, raw_meta in value.items():
            digest = cls._normalize_optional_sha256(str(raw_hash), label)
            if digest is None:
                raise PackageIntegrityError(f"registry blocked {label} value is invalid.")
            meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            normalized[digest] = {
                "blocked_at": str(meta.get("blocked_at") or ""),
                "reason": str(meta.get("reason") or "")[:500],
            }
        return normalized

    @staticmethod
    def _normalize_audit(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise PackageIntegrityError("registry audit must be an array.")
        entries: list[dict[str, Any]] = []
        for item in value[-DEFAULT_AUDIT_LIMIT:]:
            if isinstance(item, Mapping):
                entries.append(dict(item))
        return entries

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _audit_entry(cls, event: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema": "vrcforge.skill-package-audit.v1",
            "timestamp": cls._utc_timestamp(),
            **dict(event),
        }

    def _write_registry_audit(self, registry: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        self.skill_store.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self.registry_path, self._registry_document(registry, audit_event=event))

    @classmethod
    def _normalize_signer_fingerprint(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise PackageSecurityError("Signer fingerprint must be a 64-character lowercase SHA-256 hex string.")
        return normalized

    @classmethod
    def _normalize_optional_skill_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if len(normalized) > 128 or not SKILL_ID_RE.fullmatch(normalized):
            raise PackageSecurityError("Package id must be a lowercase reverse-domain id.")
        return normalized

    @classmethod
    def _normalize_optional_sha256(cls, value: str | None, label: str) -> str | None:
        if value is None:
            return None
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise PackageSecurityError(f"{label} must be a 64-character SHA-256 hex string.")
        return normalized

    @staticmethod
    def _normalize_reason(value: str | None) -> str:
        return str(value or "").strip()[:500]

    def _evaluate_preview_governance(
        self,
        preview: ImportPreview,
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._build_governance_decision(
            registry=registry,
            skill_id=str(preview.manifest["id"]),
            signature_status=preview.signature_status,
            signer_fingerprint=preview.signer_fingerprint,
            risk_level=preview.risk_level,
            package_sha256=preview.package_sha256,
            lock_sha256=preview.lock_sha256,
        )

    def _evaluate_installed_governance(
        self,
        entry: Mapping[str, Any],
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._build_governance_decision(
            registry=registry,
            skill_id=str(entry.get("id") or ""),
            signature_status=str(entry.get("signature_status") or ""),
            signer_fingerprint=entry.get("signer_fingerprint") if isinstance(entry.get("signer_fingerprint"), str) else None,
            risk_level=str(entry.get("risk_level") or "high"),
            package_sha256=entry.get("package_sha256") if isinstance(entry.get("package_sha256"), str) else None,
            lock_sha256=entry.get("lock_sha256") if isinstance(entry.get("lock_sha256"), str) else None,
        )

    def _build_governance_decision(
        self,
        *,
        registry: Mapping[str, Any],
        skill_id: str,
        signature_status: str,
        signer_fingerprint: str | None,
        risk_level: str,
        package_sha256: str | None,
        lock_sha256: str | None,
    ) -> dict[str, Any]:
        governance = self._normalize_governance(registry.get("governance"))
        blocking_reasons: list[str] = []
        warnings: list[str] = []
        signature_verified = signature_status == "signed"
        signer_status = "unsigned_dev"
        if signature_verified:
            signer = self._normalize_signer_fingerprint(str(signer_fingerprint or ""))
            if signer in governance["revoked_signers"]:
                signer_status = "revoked"
                reason = governance["revoked_signers"][signer].get("reason") or "signer revoked"
                blocking_reasons.append(f"signer revoked: {reason}")
            elif signer in governance["trusted_signers"]:
                signer_status = "trusted"
            else:
                signer_status = "untrusted"
                warnings.append("signature is valid, but the signer is not trusted or verified")
        else:
            warnings.append("dev package has no signature; this is never verified")

        blocked = governance["blocked_packages"]
        if skill_id in blocked["ids"]:
            reason = blocked["ids"][skill_id].get("reason") or "package id is blocklisted"
            blocking_reasons.append(f"package id blocklisted: {reason}")
        if package_sha256 and package_sha256 in blocked["package_sha256"]:
            reason = blocked["package_sha256"][package_sha256].get("reason") or "package SHA-256 is blocklisted"
            blocking_reasons.append(f"package SHA-256 blocklisted: {reason}")
        if lock_sha256 and lock_sha256 in blocked["lock_sha256"]:
            reason = blocked["lock_sha256"][lock_sha256].get("reason") or "lock SHA-256 is blocklisted"
            blocking_reasons.append(f"lock SHA-256 blocklisted: {reason}")

        safe_mode = governance["safe_mode"]
        safe_mode_disables = bool(safe_mode.get("enabled")) and risk_level in set(safe_mode.get("disable_risk_levels") or [])
        if safe_mode_disables:
            warnings.append(f"safe mode disables {risk_level}-risk imported skills by default")

        import_allowed = not blocking_reasons
        default_enabled = bool(import_allowed and not safe_mode_disables and signer_status == "trusted")
        if import_allowed and not safe_mode_disables and not default_enabled:
            if signer_status == "unsigned_dev":
                warnings.append("dev package imports disabled by default until explicitly enabled")
            elif signer_status == "untrusted":
                warnings.append("signed package imports disabled by default until signer is trusted or explicitly enabled")
        enable_allowed = import_allowed and not (safe_mode_disables and bool(safe_mode.get("block_enable", True)))
        return {
            "schema": GOVERNANCE_DECISION_SCHEMA,
            "signatureVerified": signature_verified,
            "verified": False,
            "verifiedLabel": "not_verified",
            "signerTrustStatus": signer_status,
            "safeMode": {
                "enabled": bool(safe_mode.get("enabled")),
                "defaultEnabled": default_enabled,
                "disablesRiskLevel": bool(safe_mode_disables),
                "blockEnable": bool(safe_mode.get("block_enable", True)),
            },
            "importAllowed": bool(import_allowed),
            "enableAllowed": bool(enable_allowed),
            "blockingReasons": blocking_reasons,
            "warnings": warnings,
        }

    def _build_dry_run_standard(self, preview: ImportPreview, governance: Mapping[str, Any]) -> dict[str, Any]:
        skill_id = str(preview.manifest["id"])
        version = str(preview.manifest["version"])
        return {
            "schema": DRY_RUN_SCHEMA,
            "supported": True,
            "mode": "package-preflight",
            "willWrite": False,
            "wouldImport": bool(governance.get("importAllowed", False)),
            "wouldEnable": bool(governance.get("safeMode", {}).get("defaultEnabled", False)),
            "requiresApprovalForApply": True,
            "writes": [
                {
                    "target": "skill-package-store",
                    "path": f"{skill_id}/versions/{version}",
                    "blocked": not bool(governance.get("importAllowed", False)),
                },
                {
                    "target": "skill-package-registry",
                    "path": "registry.json",
                    "blocked": not bool(governance.get("importAllowed", False)),
                },
                {
                    "target": "projected-user-skill",
                    "path": str(preview.manifest.get("skill_name") or preview.manifest.get("skillName") or skill_id),
                    "blocked": not bool(governance.get("importAllowed", False)),
                },
            ],
        }

    @staticmethod
    def _format_governance_block(action: str, governance: Mapping[str, Any]) -> str:
        reasons = [str(item) for item in governance.get("blockingReasons") or [] if str(item).strip()]
        if not reasons and not governance.get("enableAllowed", True):
            reasons = [str(item) for item in governance.get("warnings") or [] if str(item).strip()]
        suffix = "; ".join(reasons) if reasons else "blocked by skill package governance"
        return f"Skill package {action} is blocked: {suffix}."

    def _decorate_installed_entry(self, entry: dict[str, Any], registry: Mapping[str, Any]) -> dict[str, Any]:
        governance = self._evaluate_installed_governance(entry, registry)
        entry["governance"] = governance
        entry["verified"] = False
        entry["signer_trust_status"] = governance["signerTrustStatus"]
        entry["safe_mode_disabled"] = bool(governance.get("safeMode", {}).get("disablesRiskLevel"))
        return entry

    def _disable_matching_skills(
        self,
        skills: dict[str, dict[str, Any]],
        predicate: Any,
        *,
        disabled_reason: str,
    ) -> list[str]:
        disabled: list[str] = []
        timestamp = self._utc_timestamp()
        for skill_id, raw_entry in list(skills.items()):
            entry = dict(raw_entry)
            if not predicate(entry):
                continue
            if not bool(entry.get("enabled", True)):
                continue
            entry["enabled"] = False
            entry_governance = dict(entry.get("governance") or {})
            entry_governance["disabled_by"] = disabled_reason
            entry_governance["disabled_at"] = timestamp
            entry["governance"] = entry_governance
            skills[skill_id] = entry
            disabled.append(skill_id)
        return disabled

    def _disable_governance_blocked_skills(
        self,
        skills: dict[str, dict[str, Any]],
        governance: Mapping[str, Any],
    ) -> list[str]:
        registry = self._registry_document({"skills": skills}, skills=skills, governance=governance)
        return self._disable_matching_skills(
            skills,
            lambda entry: not self._evaluate_installed_governance(entry, registry).get("enableAllowed", False),
            disabled_reason="safe_mode",
        )

    def _sync_installed_metadata(self, registry: Mapping[str, Any], skill_ids: Sequence[str]) -> None:
        for skill_id in skill_ids:
            entry = registry.get("skills", {}).get(skill_id)
            if not isinstance(entry, Mapping):
                continue
            skill_root = self.skill_store / skill_id
            if not skill_root.exists():
                continue
            installed_entry = dict(entry)
            installed_entry["versions"] = self._installed_versions(skill_id)
            self._atomic_write_json(skill_root / "installed.json", installed_entry)

    def _export(
        self,
        source_dir: str | os.PathLike[str],
        output_path: str | os.PathLike[str],
        *,
        package_mode: str,
        private_key: Ed25519PrivateKey | bytes | str | os.PathLike[str] | None,
        overwrite: bool,
    ) -> ExportResult:
        source_input = Path(source_dir).expanduser()
        if _is_symlink_like(source_input):
            raise SkillPackageError("Skill source must be a regular directory, not a symlink or junction.")
        source = source_input.resolve()
        if not source.is_dir():
            raise SkillPackageError("Skill source must be a regular directory.")
        manifest_path = source / MANIFEST_NAME
        if not manifest_path.is_file() or _is_symlink_like(manifest_path):
            raise ManifestValidationError("Skill source must contain a regular manifest.json file.")
        manifest_value = _load_json_bytes(manifest_path.read_bytes(), MANIFEST_NAME)
        if not isinstance(manifest_value, dict):
            raise ManifestValidationError("manifest.json must contain an object.")
        manifest = self.validate_manifest(manifest_value)
        manifest_bytes = canonical_json_bytes(manifest)
        if self._contains_sensitive_content(manifest_bytes):
            raise PackageSecurityError("manifest.json contains secret or binary material and cannot be exported.")

        payload, excluded = self._collect_source_files(source)
        payload[MANIFEST_NAME] = manifest_bytes
        self._validate_payload_limits(payload)
        for entrypoint in manifest["entrypoints"].values():
            if entrypoint not in payload:
                raise ManifestValidationError(
                    f"Entrypoint is missing or was excluded from export: {entrypoint}."
                )

        lock = {
            "algorithm": "sha256",
            "files": {path: sha256_bytes(value) for path, value in sorted(payload.items())},
            "package_mode": package_mode,
            "schema": LOCK_SCHEMA,
        }
        lock_bytes = canonical_json_bytes(lock)
        archive_files = dict(payload)
        archive_files[LOCK_NAME] = lock_bytes
        signer_fingerprint: str | None = None
        if package_mode == "release":
            signing_key = self._load_private_key(private_key)
            public_key = signing_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            signature = signing_key.sign(lock_bytes)
            archive_files[SIGNATURE_NAME] = base64.b64encode(signature)
            archive_files[PUBLIC_KEY_NAME] = base64.b64encode(public_key)
            signer_fingerprint = sha256_bytes(public_key)
        elif package_mode != "dev":
            raise ValueError(f"Unsupported package mode: {package_mode}.")

        output = Path(output_path).expanduser()
        if output.suffix.lower() != ".vsk":
            output = output.with_name(output.name + ".vsk")
        output = output.resolve() if overwrite else output.parent.resolve() / output.name
        output.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite and os.path.lexists(output):
            raise SkillPackageError(f"Output package already exists and overwrite was not authorized: {output}.")
        temp_output = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
        try:
            self._write_archive(temp_output, archive_files)
            self._validate_zip_metadata(temp_output)
            if overwrite:
                os.replace(temp_output, output)
            else:
                try:
                    os.link(temp_output, output)
                except FileExistsError as exc:
                    raise SkillPackageError(
                        f"Output package already exists and overwrite was not authorized: {output}."
                    ) from exc
        finally:
            temp_output.unlink(missing_ok=True)

        return ExportResult(
            package_path=output,
            manifest=manifest,
            signature_status="signed" if package_mode == "release" else "dev",
            signer_fingerprint=signer_fingerprint,
            lock_sha256=sha256_bytes(lock_bytes),
            file_count=len(payload),
            total_size=sum(len(value) for value in payload.values()),
            excluded_files=tuple(sorted(excluded)),
        )

    def _collect_source_files(self, source: Path) -> tuple[dict[str, bytes], list[str]]:
        payload: dict[str, bytes] = {}
        excluded: list[str] = []
        collision_keys: set[str] = set()
        for current_root, directories, files in os.walk(source, followlinks=False):
            current = Path(current_root)
            kept_directories: list[str] = []
            for directory_name in directories:
                directory = current / directory_name
                relative = directory.relative_to(source).as_posix()
                if _is_symlink_like(directory):
                    raise PackageSecurityError(f"Symlink directories cannot be exported: {relative}.")
                if directory_name.casefold() in EXCLUDED_DIRECTORY_NAMES:
                    excluded.append(relative + "/")
                else:
                    kept_directories.append(directory_name)
            directories[:] = kept_directories

            for file_name in files:
                path = current / file_name
                relative = _safe_relative_path(path.relative_to(source).as_posix(), label="source path")
                if _is_symlink_like(path):
                    raise PackageSecurityError(f"Symlink files cannot be exported: {relative}.")
                if relative in RESERVED_PACKAGE_FILES:
                    excluded.append(relative)
                    continue
                if self._source_file_exclusion_reason(path, relative):
                    excluded.append(relative)
                    continue
                collision_key = _path_collision_key(relative)
                if collision_key in collision_keys:
                    raise PackageSecurityError(f"Case-insensitive duplicate source path: {relative}.")
                collision_keys.add(collision_key)
                data = path.read_bytes()
                if len(data) > self.max_file_size:
                    excluded.append(relative)
                    continue
                if self._contains_sensitive_content(data):
                    excluded.append(relative)
                    continue
                payload[relative] = data
                if len(payload) > self.max_file_count:
                    raise PackageSecurityError("Skill source exceeds the package file-count limit.")
                if sum(len(value) for value in payload.values()) > self.max_total_size:
                    raise PackageSecurityError("Skill source exceeds the package total-size limit.")
        return payload, excluded

    def _source_file_exclusion_reason(self, path: Path, relative: str) -> str | None:
        name = path.name.casefold()
        if name in EXCLUDED_EXACT_NAMES or name.startswith(".env."):
            return "sensitive-name"
        if any(token in name for token in ("cookie", "credential", "private_key", "secret")):
            return "sensitive-name"
        if path.suffix.casefold() in EXCLUDED_SUFFIXES:
            return "asset-or-binary"
        if any(part.casefold() in EXCLUDED_DIRECTORY_NAMES for part in PurePosixPath(relative).parts[:-1]):
            return "private-directory"
        return None

    @staticmethod
    def _contains_sensitive_content(data: bytes) -> bool:
        sample = data[:64 * 1024]
        if b"\x00" in sample:
            return True
        if any(
            marker in sample
            for marker in (
                b"-----BEGIN PRIVATE KEY-----",
                b"-----BEGIN OPENSSH PRIVATE KEY-----",
                b"-----BEGIN RSA PRIVATE KEY-----",
                b"-----BEGIN EC PRIVATE KEY-----",
            )
        ):
            return True
        try:
            text = sample.decode("utf-8")
        except UnicodeDecodeError:
            return True
        if re.search(r"(?im)^\s*(?:cookie|set-cookie)\s*:", text):
            return True
        if re.search(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", text):
            return True
        if re.search(r"\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{16,}\b", text):
            return True
        assignment = re.compile(
            r"(?im)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|auth[_-]?token|password)\b\s*[\"']?\s*[:=]\s*[\"']?([^\s\"',}\]]{8,})"
        )
        for match in assignment.finditer(text):
            candidate = match.group(1).casefold()
            if not any(
                marker in candidate
                for marker in ("${", "{{", "<", "changeme", "example", "placeholder", "your_")
            ):
                return True
        return False

    def _validate_payload_limits(self, payload: Mapping[str, bytes]) -> None:
        if len(payload) > self.max_file_count:
            raise PackageSecurityError("Package exceeds the file-count limit.")
        total = 0
        for path, value in payload.items():
            _safe_relative_path(path, label="payload path")
            if len(value) > self.max_file_size:
                raise PackageSecurityError(f"Package file exceeds the size limit: {path}.")
            total += len(value)
        if total > self.max_total_size:
            raise PackageSecurityError("Package exceeds the total uncompressed-size limit.")

    def _write_archive(self, destination: Path, files: Mapping[str, bytes]) -> None:
        with zipfile.ZipFile(destination, "w", allowZip64=False) as archive:
            for relative, data in sorted(files.items()):
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                compressed = zlib.compress(data, level=9)
                ratio = len(data) / max(len(compressed), 1)
                compression = zipfile.ZIP_STORED if ratio > self.max_compression_ratio else zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compress_type=compression, compresslevel=9)

    def _read_package_bytes(self, package_path: Path) -> bytes:
        if not package_path.is_file() or package_path.is_symlink():
            raise PackageSecurityError("Package must be a regular .vsk file.")
        max_archive_size = self.max_total_size + (self.max_file_count * 2_048) + (1024 * 1024)
        if package_path.stat().st_size > max_archive_size:
            raise PackageSecurityError("Compressed package exceeds the archive-size limit.")
        return package_path.read_bytes()

    def _validate_zip_metadata(self, package_path: Path) -> tuple[list[zipfile.ZipInfo], int]:
        return self._validate_zip_bytes(self._read_package_bytes(package_path))

    def _validate_zip_bytes(self, package_bytes: bytes) -> tuple[list[zipfile.ZipInfo], int]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise PackageSecurityError("Package is not a valid zip archive.") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > (self.max_file_count * 2) + 16:
                raise PackageSecurityError("Archive contains too many directory or metadata entries.")
            files: list[zipfile.ZipInfo] = []
            seen: set[str] = set()
            file_names: set[str] = set()
            file_name_keys: dict[str, str] = {}
            payload_count = 0
            total_size = 0
            for info in infos:
                raw_name = info.filename[:-1] if info.is_dir() else info.filename
                normalized = _safe_relative_path(raw_name, label="archive member")
                collision_key = _path_collision_key(normalized)
                if collision_key in seen:
                    raise PackageSecurityError(f"Duplicate archive member: {info.filename!r}.")
                seen.add(collision_key)
                if info.flag_bits & 0x1:
                    raise PackageSecurityError(f"Encrypted archive members are not supported: {info.filename}.")
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if file_type == stat.S_IFLNK:
                    raise PackageSecurityError(f"Symlink archive member is not allowed: {info.filename}.")
                if info.is_dir():
                    if file_type not in {0, stat.S_IFDIR}:
                        raise PackageSecurityError(f"Non-directory archive member uses a directory name: {info.filename}.")
                    continue
                if file_type == stat.S_IFDIR:
                    raise PackageSecurityError(f"Directory archive member must end with '/': {info.filename}.")
                if file_type not in {0, stat.S_IFREG}:
                    raise PackageSecurityError(f"Non-regular archive member is not allowed: {info.filename}.")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PackageSecurityError(f"Unsupported compression method: {info.filename}.")
                if info.file_size > self.max_file_size:
                    raise PackageSecurityError(f"Archive member exceeds the size limit: {info.filename}.")
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > self.max_compression_ratio:
                    raise PackageSecurityError(f"Archive member exceeds the compression-ratio limit: {info.filename}.")
                total_size += info.file_size
                if total_size > self.max_total_size:
                    raise PackageSecurityError("Archive exceeds the total uncompressed-size limit.")
                files.append(info)
                file_names.add(normalized)
                file_name_keys[_path_collision_key(normalized)] = normalized
                if normalized not in RESERVED_PACKAGE_FILES:
                    payload_count += 1
                if payload_count > self.max_file_count:
                    raise PackageSecurityError("Archive exceeds the file-count limit.")
            for name in file_names:
                for parent in PurePosixPath(name).parents:
                    parent_name = parent.as_posix()
                    if parent_name == ".":
                        break
                    parent_collision = file_name_keys.get(_path_collision_key(parent_name))
                    if parent_collision is not None:
                        raise PackageSecurityError(
                            f"Archive file conflicts with a child path: {parent_collision!r}."
                        )
            return files, total_size

    @contextmanager
    def _validated_package(self, package_path: str | os.PathLike[str]) -> Iterator[_ValidatedPackage]:
        package = Path(package_path).expanduser().absolute()
        package_bytes = self._read_package_bytes(package)
        files, total_size = self._validate_zip_bytes(package_bytes)
        with tempfile.TemporaryDirectory(prefix="vrcforge-vsk-") as temp_name:
            temp_dir = Path(temp_name)
            try:
                with zipfile.ZipFile(io.BytesIO(package_bytes), "r") as archive:
                    for info in files:
                        relative = _safe_relative_path(info.filename, label="archive member")
                        target = temp_dir / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        written = 0
                        with archive.open(info.filename, "r") as source, target.open("xb") as destination:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > info.file_size or written > self.max_file_size:
                                    raise PackageSecurityError(
                                        f"Archive member expanded beyond its declared size: {relative}."
                                    )
                                destination.write(chunk)
                        if written != info.file_size:
                            raise PackageIntegrityError(f"Archive member size mismatch: {relative}.")
            except (zipfile.BadZipFile, EOFError, zlib.error) as exc:
                raise PackageIntegrityError("Package data or CRC is corrupt.") from exc
            preview = self._inspect_extracted(
                package,
                temp_dir,
                total_size,
                package_sha256=sha256_bytes(package_bytes),
            )
            yield _ValidatedPackage(temp_dir=temp_dir, preview=preview)

    def _inspect_extracted(
        self,
        package: Path,
        root: Path,
        total_size: int,
        *,
        package_sha256: str,
    ) -> ImportPreview:
        manifest_path = root / MANIFEST_NAME
        lock_path = root / LOCK_NAME
        if not manifest_path.is_file() or not lock_path.is_file():
            raise PackageIntegrityError("Package must contain manifest.json and skill.lock.json.")
        manifest_bytes = manifest_path.read_bytes()
        if self._contains_sensitive_content(manifest_bytes):
            raise PackageSecurityError("manifest.json contains secret or binary material.")
        manifest_value = _load_json_bytes(manifest_bytes, MANIFEST_NAME)
        if not isinstance(manifest_value, dict):
            raise ManifestValidationError("manifest.json must contain an object.")
        manifest = self.validate_manifest(manifest_value, package_root=root)
        if _compare_semver(self.vrcforge_version, manifest["min_vrcforge_version"]) < 0:
            raise PackageCompatibilityError(
                f"Skill requires VRCForge {manifest['min_vrcforge_version']} or newer."
            )

        lock_bytes = lock_path.read_bytes()
        lock_value = _load_json_bytes(lock_bytes, LOCK_NAME)
        if not isinstance(lock_value, dict) or lock_value.get("schema") != LOCK_SCHEMA:
            raise PackageIntegrityError("skill.lock.json has an unsupported schema.")
        if lock_value.get("algorithm") != "sha256":
            raise PackageIntegrityError("skill.lock.json must use sha256.")
        if canonical_json_bytes(lock_value) != lock_bytes:
            raise PackageIntegrityError("skill.lock.json is not canonical JSON.")
        locked_files = lock_value.get("files")
        if not isinstance(locked_files, dict) or not locked_files:
            raise PackageIntegrityError("skill.lock.json files must be a non-empty object.")

        expected: dict[str, str] = {}
        for relative, digest in locked_files.items():
            normalized = _safe_relative_path(relative, label="lock path")
            if normalized in RESERVED_PACKAGE_FILES:
                raise PackageIntegrityError(f"Reserved metadata cannot be declared in the lock: {normalized}.")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise PackageIntegrityError(f"Invalid SHA-256 for lock entry: {normalized}.")
            expected[normalized] = digest

        actual_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.relative_to(root).as_posix() not in RESERVED_PACKAGE_FILES
        }
        expected_files = set(expected)
        undeclared = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        if undeclared:
            raise PackageIntegrityError(f"Package contains undeclared files: {', '.join(undeclared)}.")
        if missing:
            raise PackageIntegrityError(f"Package is missing locked files: {', '.join(missing)}.")
        for relative, digest in expected.items():
            actual_digest = sha256_bytes((root / relative).read_bytes())
            if actual_digest != digest:
                raise PackageIntegrityError(f"SHA-256 mismatch for {relative}.")

        has_signature = (root / SIGNATURE_NAME).is_file()
        has_public_key = (root / PUBLIC_KEY_NAME).is_file()
        if has_signature != has_public_key:
            raise PackageSignatureError("skill.sig and author.pub must either both exist or both be absent.")
        package_mode = lock_value.get("package_mode")
        if package_mode not in {"dev", "release"}:
            raise PackageIntegrityError("skill.lock.json package_mode must be dev or release.")
        signer_fingerprint: str | None = None
        if package_mode == "release":
            if not has_signature:
                raise PackageSignatureError("Release packages must be signed.")
            public_key_bytes = _decode_fixed_base64((root / PUBLIC_KEY_NAME).read_bytes(), 32, PUBLIC_KEY_NAME)
            signature_bytes = _decode_fixed_base64((root / SIGNATURE_NAME).read_bytes(), 64, SIGNATURE_NAME)
            try:
                Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature_bytes, lock_bytes)
            except InvalidSignature as exc:
                raise PackageSignatureError("Ed25519 signature verification failed.") from exc
            signer_fingerprint = sha256_bytes(public_key_bytes)
            signature_status = "signed"
        else:
            if has_signature:
                raise PackageSignatureError("Dev packages must not contain a signature.")
            signature_status = "dev"

        permissions = tuple(manifest["permissions"])
        tiers = self._permission_tiers(permissions)
        risk_level = "high" if tiers["high"] else "medium" if tiers["medium"] else "low"
        return ImportPreview(
            package_path=package,
            package_sha256=package_sha256,
            manifest=manifest,
            signature_status=signature_status,
            signer_fingerprint=signer_fingerprint,
            lock_sha256=sha256_bytes(lock_bytes),
            permissions=permissions,
            permission_tiers=tiers,
            risk_level=risk_level,
            file_count=len(actual_files),
            total_size=total_size,
        )

    @staticmethod
    def _permission_tiers(permissions: Sequence[str]) -> dict[str, tuple[str, ...]]:
        low: list[str] = []
        medium: list[str] = []
        high: list[str] = []
        for permission in permissions:
            if permission in LOW_PERMISSIONS:
                low.append(permission)
            elif permission in MEDIUM_PERMISSIONS:
                medium.append(permission)
            else:
                # Unknown permissions never become implicitly low-risk.
                high.append(permission)
        return {"low": tuple(low), "medium": tuple(medium), "high": tuple(high)}

    def _find_installed_entry(self, skill_id: str, registry: dict[str, Any]) -> dict[str, Any] | None:
        registry_entry = registry["skills"].get(skill_id)
        installed_path = self.skill_store / skill_id / "installed.json"
        installed_entry: dict[str, Any] | None = None
        if installed_path.exists():
            if installed_path.is_symlink() or not installed_path.is_file():
                raise PackageSecurityError(f"Installed metadata is not a regular file: {skill_id}.")
            value = _load_json_bytes(installed_path.read_bytes(), f"{skill_id}/installed.json")
            installed_entry = self._validate_registry_entry(
                skill_id,
                value,
                label=f"installed metadata {skill_id!r}",
                allow_versions=True,
            )
        versions_dir = self.skill_store / skill_id / "versions"
        if registry_entry is None and installed_entry is None and versions_dir.exists():
            raise PackageIntegrityError(f"Installed version directory has no trust metadata: {skill_id}.")
        if registry_entry is not None and installed_entry is not None:
            for field in (
                "version",
                "package_sha256",
                "signature_status",
                "signer_fingerprint",
                "lock_sha256",
                "risk_level",
                "permissions",
            ):
                if registry_entry.get(field) != installed_entry.get(field):
                    raise PackageIntegrityError(f"Registry and installed metadata disagree for {skill_id}: {field}.")
            registry_author = registry_entry.get("author")
            installed_author = installed_entry.get("author")
            if registry_author is not None and installed_author is not None and registry_author != installed_author:
                raise PackageIntegrityError(f"Registry and installed metadata disagree for {skill_id}: author.")
        entry = registry_entry or installed_entry
        return dict(entry) if entry is not None else None

    def _installed_versions(self, skill_id: str) -> list[str]:
        versions_root = self.skill_store / skill_id / "versions"
        if versions_root.is_symlink():
            raise PackageSecurityError(f"Refusing to read symlinked versions directory: {skill_id}.")
        if not versions_root.exists():
            return []
        if not versions_root.is_dir():
            raise PackageSecurityError(f"Versions path is not a directory: {skill_id}.")
        versions = [path.name for path in versions_root.iterdir() if path.is_dir()]
        return sorted(versions, key=cmp_to_key(_compare_semver))

    def _read_current_manifest(self, skill_id: str, version: str) -> dict[str, Any]:
        if not version:
            return {}
        manifest_path = self.skill_store / skill_id / "versions" / version / MANIFEST_NAME
        if not manifest_path.exists():
            return {}
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PackageSecurityError(f"Installed manifest is not a regular file: {skill_id}.")
        value = _load_json_bytes(manifest_path.read_bytes(), f"{skill_id}/{version}/{MANIFEST_NAME}")
        if not isinstance(value, dict):
            raise PackageIntegrityError(f"Installed manifest is invalid: {skill_id}.")
        return dict(value)

    def _write_installed_registry_entry(
        self,
        skill_id: str,
        registry_entry: dict[str, Any],
        registry: dict[str, Any],
        *,
        audit_event: Mapping[str, Any] | None = None,
    ) -> None:
        self.skill_store.mkdir(parents=True, exist_ok=True)
        skill_root = self.skill_store / skill_id
        if skill_root.is_symlink():
            raise PackageSecurityError(f"Refusing to write through symlinked skill directory: {skill_id}.")
        if skill_root.exists() and not skill_root.is_dir():
            raise PackageSecurityError(f"Installed skill path is not a directory: {skill_id}.")
        installed_path = skill_root / "installed.json"
        if skill_root.exists():
            installed_entry = dict(registry_entry)
            installed_entry["versions"] = self._installed_versions(skill_id)
            self._atomic_write_json(installed_path, installed_entry)
        next_registry = self._registry_document(
            registry,
            skills=dict(registry["skills"]),
            audit_event=audit_event,
        )
        next_registry["skills"][skill_id] = dict(registry_entry)
        self._atomic_write_json(self.registry_path, next_registry)

    def _check_update_policy(
        self,
        incoming: ImportPreview,
        existing: dict[str, Any] | None,
        *,
        allow_downgrade: bool,
        dev_mode: bool,
    ) -> str:
        if existing is None:
            return "new"
        incoming_signed = incoming.signature_status == "signed"
        existing_signed = existing.get("signature_status") == "signed"
        incoming_fingerprint = incoming.signer_fingerprint
        existing_fingerprint = existing.get("signer_fingerprint")

        incoming_author = str(incoming.manifest.get("author") or "").strip()
        metadata_author = str(existing.get("author") or "").strip()
        installed_manifest = self._read_current_manifest(
            str(incoming.manifest["id"]),
            str(existing.get("version") or ""),
        )
        manifest_author = str(installed_manifest.get("author") or "").strip()
        if metadata_author and manifest_author and metadata_author != manifest_author:
            raise PackageIntegrityError("Installed author identity metadata does not match its manifest.")
        existing_author = manifest_author or metadata_author
        if existing_author and incoming_author != existing_author:
            raise PackageUpdateError("Author identity does not match the installed skill identity.")

        if existing_signed:
            if not incoming_signed:
                raise PackageUpdateError("An unsigned/dev package cannot overwrite a signed installation.")
            if incoming_fingerprint != existing_fingerprint:
                raise PackageUpdateError("Signer fingerprint does not match the installed skill identity.")
        elif incoming_signed:
            # The first signed update pins the previously unsigned id to this signer.
            pass

        comparison = _compare_semver(incoming.manifest["version"], str(existing.get("version") or ""))
        if comparison < 0:
            same_signer = incoming_signed and existing_signed and incoming_fingerprint == existing_fingerprint
            if not (dev_mode and allow_downgrade and same_signer):
                raise PackageUpdateError("Skill downgrade is blocked; dev override requires the same signer.")
            return "downgrade"
        if comparison == 0:
            if incoming.lock_sha256 != existing.get("lock_sha256"):
                raise PackageUpdateError("A published skill version is immutable; bump the semantic version.")
            return "reinstall"
        return "update"

    def _preview_with_action(
        self,
        preview: ImportPreview,
        action: str,
        *,
        governance: Mapping[str, Any] | None = None,
    ) -> ImportPreview:
        decision = dict(governance or preview.governance or {})
        return ImportPreview(
            package_path=preview.package_path,
            package_sha256=preview.package_sha256,
            manifest=preview.manifest,
            signature_status=preview.signature_status,
            signer_fingerprint=preview.signer_fingerprint,
            lock_sha256=preview.lock_sha256,
            permissions=preview.permissions,
            permission_tiers=preview.permission_tiers,
            risk_level=preview.risk_level,
            file_count=preview.file_count,
            total_size=preview.total_size,
            update_action=action,
            governance=decision,
            dry_run=self._build_dry_run_standard(preview, decision),
        )

    def _install_validated(
        self,
        extracted_root: Path,
        preview: ImportPreview,
        registry: dict[str, Any],
        *,
        source: str | None,
    ) -> InstallResult:
        skill_id = preview.manifest["id"]
        version = preview.manifest["version"]
        skill_root = self.skill_store / skill_id
        versions_root = skill_root / "versions"
        version_root = versions_root / version
        for path, label in (
            (skill_root, "skill directory"),
            (versions_root, "versions directory"),
            (version_root, "version directory"),
        ):
            if path.is_symlink():
                raise PackageSecurityError(f"Refusing to install through a symlinked {label}: {path}.")
        versions_root.mkdir(parents=True, exist_ok=True)
        staging_root = self.skill_store / ".staging"
        if staging_root.is_symlink():
            raise PackageSecurityError(f"Refusing to install through a symlinked staging directory: {staging_root}.")
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / uuid.uuid4().hex
        changed = False
        installed_path = skill_root / "installed.json"
        old_installed = installed_path.read_bytes() if installed_path.is_file() else None
        try:
            if version_root.exists():
                existing_lock_path = version_root / LOCK_NAME
                if not existing_lock_path.is_file() or sha256_bytes(existing_lock_path.read_bytes()) != preview.lock_sha256:
                    raise PackageUpdateError("Installed version directory differs from the incoming immutable version.")
            else:
                shutil.copytree(extracted_root, staging, symlinks=False)
                os.replace(staging, version_root)
                changed = True

            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            existing_entry = registry["skills"].get(skill_id) or {}
            governance = preview.governance or self._evaluate_preview_governance(preview, registry)
            safe_mode_default_enabled = bool(governance.get("safeMode", {}).get("defaultEnabled", True))
            enabled = bool(existing_entry.get("enabled", True)) and safe_mode_default_enabled
            registry_entry = {
                "id": skill_id,
                "name": preview.manifest["name"],
                "author": preview.manifest["author"],
                "version": version,
                "enabled": enabled,
                "signer_fingerprint": preview.signer_fingerprint,
                "signature_status": preview.signature_status,
                "source": source or str(preview.package_path),
                "installed_at": timestamp,
                "package_sha256": preview.package_sha256,
                "lock_sha256": preview.lock_sha256,
                "risk_level": preview.risk_level,
                "permissions": list(preview.permissions),
                "governance": {
                    "signer_trust_status": governance.get("signerTrustStatus"),
                    "verified": False,
                    "safe_mode_disabled": not enabled and bool(governance.get("safeMode", {}).get("disablesRiskLevel")),
                },
            }
            versions = sorted(
                {path.name for path in versions_root.iterdir() if path.is_dir()},
                key=cmp_to_key(_compare_semver),
            )
            installed_entry = dict(registry_entry)
            installed_entry["versions"] = versions
            self._atomic_write_json(installed_path, installed_entry)
            next_registry = self._registry_document(
                registry,
                skills=dict(registry["skills"]),
                audit_event={
                    "event": "skill_package_imported",
                    "skill_id": skill_id,
                    "author_id": preview.manifest["author"],
                    "version": version,
                    "source": source or str(preview.package_path),
                    "signature_status": preview.signature_status,
                    "signer_fingerprint": preview.signer_fingerprint,
                    "risk_level": preview.risk_level,
                    "enabled": enabled,
                },
            )
            next_registry["skills"][skill_id] = registry_entry
            self._atomic_write_json(self.registry_path, next_registry)
            return InstallResult(preview, version_root, registry_entry, changed)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if changed and version_root.exists():
                shutil.rmtree(version_root, ignore_errors=True)
            if old_installed is None:
                installed_path.unlink(missing_ok=True)
            else:
                self._atomic_write_bytes(installed_path, old_installed)
            raise

    @staticmethod
    def _load_private_key(
        value: Ed25519PrivateKey | bytes | str | os.PathLike[str] | None,
    ) -> Ed25519PrivateKey:
        if isinstance(value, Ed25519PrivateKey):
            return value
        if value is None:
            raise PackageSignatureError("Release export requires an Ed25519 private key.")
        if isinstance(value, os.PathLike):
            data = Path(value).read_bytes()
        elif isinstance(value, str):
            if "-----BEGIN" in value:
                data = value.encode("ascii")
            else:
                candidate = Path(value)
                try:
                    is_file = candidate.is_file()
                except OSError:
                    is_file = False
                data = candidate.read_bytes() if is_file else value.encode("ascii")
        elif isinstance(value, bytes):
            data = value
        else:
            raise PackageSignatureError("Unsupported Ed25519 private-key value.")
        if b"-----BEGIN" in data:
            try:
                key = serialization.load_pem_private_key(data, password=None)
            except (TypeError, ValueError) as exc:
                raise PackageSignatureError("Invalid unencrypted Ed25519 PEM private key.") from exc
            if not isinstance(key, Ed25519PrivateKey):
                raise PackageSignatureError("Private key must use Ed25519.")
            return key
        if len(data) == 32:
            return Ed25519PrivateKey.from_private_bytes(data)
        stripped = data.strip()
        try:
            raw = base64.b64decode(stripped, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PackageSignatureError("Private key must be raw, PEM, or base64 Ed25519 material.") from exc
        if len(raw) != 32:
            raise PackageSignatureError("Ed25519 private key must contain 32 raw bytes.")
        return Ed25519PrivateKey.from_private_bytes(raw)

    def _read_local_version(self) -> str:
        version_path = Path(__file__).resolve().with_name("VERSION")
        if version_path.is_file():
            value = version_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        return "0.0.0"

    @staticmethod
    def _atomic_write_json(path: Path, value: Any) -> None:
        SkillPackageService._atomic_write_bytes(path, canonical_json_bytes(value))

    @staticmethod
    def _atomic_write_bytes(path: Path, value: bytes, *, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = [
    "LOCK_NAME",
    "MANIFEST_NAME",
    "PUBLIC_KEY_NAME",
    "SIGNATURE_NAME",
    "ExportResult",
    "ImportPreview",
    "InstallResult",
    "ManifestValidationError",
    "PackageCompatibilityError",
    "PackageIntegrityError",
    "PackageSecurityError",
    "PackageSignatureError",
    "PackageUpdateError",
    "SigningKeyPair",
    "SkillPackageError",
    "SkillPackageService",
    "canonical_json_bytes",
]
