"""Create or verify the fixed protected-runtime source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


_PACKAGING_ROOT = Path(__file__).resolve().parent
if str(_PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGING_ROOT))

import bridge_target_manifest
import protected_runtime_dependency_set


SOURCE_MANIFEST_SCHEMA = "vrcforge.protected_runtime_source.v2"
LEGACY_SOURCE_MANIFEST_SCHEMAS = frozenset(
    {"vrcforge.protected_runtime_source.v1"}
)
SOURCE_RECEIPT_SCHEMA = "vrcforge.protected_runtime_source_receipt.v2"
SCENARIO_ID = "model_part_composition"
SCENARIO_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "component_feature_application": (
        "typed_component_list_write",
        "feature_component_write",
    ),
    "parameter_optimization": ("parameter_bit_pack",),
    "cross_avatar_accessory_copy": (
        "duplicate_scene_object",
        "save_scene_object_as_prefab",
    ),
    "model_part_composition": ("non_destructive_part_composition",),
}
SCENARIO_ORDER = tuple(SCENARIO_DEFINITIONS)
BRIDGE_TARGET_RUNTIME_SCHEMA = "vrcforge.bridge_target_runtime.v1"
TREE_SOURCE_SCHEMA = "vrcforge.protected_runtime_tree_source.v1"
FIXTURE_DESCRIPTOR_SCHEMA = "vrcforge.primitive_basis_fixture.v1"
FIXTURE_BASELINE_SCHEMA = "vrcforge.primitive_basis_baseline.v1"
BRIDGE_TARGET_RUNTIME_ROOT = "bridge_target"
BRIDGE_TARGET_EXECUTABLE_NAME = "vrcforge_bridge_target.exe"
BRIDGE_TARGET_EXECUTABLE_PATH = (
    f"{BRIDGE_TARGET_RUNTIME_ROOT}/{BRIDGE_TARGET_EXECUTABLE_NAME}"
)
BRIDGE_TARGET_MANIFEST_NAME = "bridge-target-manifest.json"
FIXTURE_CONTRACT_NAME = "fixture-contract.json"
FIXTURE_BASELINE_NAME = "baseline.json"

MAX_SOURCE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
MAX_POLICY_TREE_BYTES = 16 * 1024 * 1024 * 1024
MAX_POLICY_TREE_ENTRIES = 200_000
MAX_PORTABLE_ARCHIVE_ENTRIES = 200_000
MAX_PORTABLE_ARCHIVE_ENTRY_BYTES = 8 * 1024 * 1024 * 1024
MAX_PORTABLE_ARCHIVE_EXPANDED_BYTES = 16 * 1024 * 1024 * 1024
MAX_PORTABLE_ARCHIVE_COMPRESSION_RATIO = 200
MAX_PORTABLE_ARCHIVE_PATH_BYTES = 1024
HASH_CHUNK_BYTES = 1024 * 1024

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

ROLE_FIELDS = (
    ("authority_service", "authority_service"),
    ("driver", "driver"),
    ("desktop", "desktop"),
    ("backend", "backend"),
    ("unity", "unity"),
    ("bridge_launcher", "bridge_launcher"),
    ("bridge_listener", "bridge_listener"),
)
SOURCE_FIELDS = (
    ("runtime_contract", "runtime_contract"),
    ("fixture_baseline", "fixture_baseline"),
)
RELEASE_ARTIFACT_FIELDS = (
    ("strict_release_manifest", "strictManifest", MAX_SOURCE_BYTES),
    ("portable_archive", "portableArchive", MAX_ARCHIVE_BYTES),
    ("unity_package", "unityPackage", MAX_ARCHIVE_BYTES),
)
PACKAGE_TREE_FIELDS = (
    ("backend_tree", "backend"),
    ("vrcforge_core_tree", "vrcforgeCore"),
    ("server_tree", "server"),
)
PORTABLE_ARCHIVE_TREE_BINDINGS = (
    ("backend", "backend"),
    ("vrcforgeCore", "unity_plugin/Assets/VRCForge"),
    ("bridgeTarget", BRIDGE_TARGET_RUNTIME_ROOT),
)
PORTABLE_ARCHIVE_UNITY_PACKAGE_PATH = "unity_plugin/VRCForge.unitypackage"
FIXTURE_DESCRIPTOR_FIELDS = tuple(
    (f"{scenario_id}_descriptor", scenario_id)
    for scenario_id in SCENARIO_ORDER
)
FIXTURE_ROOT_FIELDS = tuple(
    (f"{scenario_id}_root", scenario_id)
    for scenario_id in SCENARIO_ORDER
)
ROLE_NAMES = tuple(name for _field, name in ROLE_FIELDS)
SOURCE_NAMES = tuple(name for _field, name in SOURCE_FIELDS)

_ROOT_KEYS = {
    "schema",
    "version",
    "sourceCommit",
    "scenarioId",
    "buildPolicy",
    "roles",
    "sources",
    "bridgeTargetRuntime",
    "releaseArtifacts",
    "packageTrees",
    "dependencySet",
    "fixtureSet",
    "modelFixture",
}
_BUILD_POLICY_KEYS = {
    "mode",
    "releaseEligible",
    "evidenceEligible",
    "allowDirty",
    "allowUnpushed",
    "allowVersionMismatch",
}
_ROLE_KEYS = {"role", "sha256", "byteCount"}
_SOURCE_KEYS = {"source", "sha256", "byteCount"}
_FILE_RECORD_KEYS = {"sha256", "byteCount"}
_RELEASE_ARTIFACT_KEYS = {name for _field, name, _maximum in RELEASE_ARTIFACT_FIELDS}
_PACKAGE_TREE_KEYS = {name for _field, name in PACKAGE_TREE_FIELDS}
_TREE_RECORD_KEYS = {
    "schema",
    "treeDigest",
    "bindingDigest",
    "directoryCount",
    "entryCount",
    "byteCount",
}
_DEPENDENCY_SET_KEYS = {
    "descriptorSchema",
    "setDigest",
    "descriptorSha256",
    "byteCount",
    "canonicalJson",
    "bindingDigest",
}
_FIXTURE_SET_KEYS = {
    "descriptorSetDigest",
    "fixtureSetDigest",
    "descriptors",
    "materializedRoots",
}
_FIXTURE_DESCRIPTOR_RECORD_KEYS = {
    "scenarioId",
    "fileSha256",
    "descriptorDigest",
    "byteCount",
}
_FIXTURE_ROOT_RECORD_KEYS = {
    "scenarioId",
    "fixtureDigest",
    "baselineDigest",
    "contentTreeDigest",
    "sourceTree",
}
_MODEL_FIXTURE_KEYS = {"scenarioId", "descriptorDigest", "fixtureDigest"}
_FIXTURE_DESCRIPTOR_INPUT_KEYS = {
    "schema",
    "scenarioId",
    "fixtureRoot",
    "baselineManifest",
    "expectedBaselineDigest",
    "expectedTreeDigest",
    "requiredPrimitives",
}
_FIXTURE_BASELINE_INPUT_KEYS = {"schema", "scenarioId", "files"}
_FIXTURE_BASELINE_FILE_KEYS = {"path", "size", "sha256"}
_BRIDGE_RUNTIME_KEYS = {
    "schema",
    "runtimeRelativeRoot",
    "executableRelativePath",
    "executableSha256",
    "manifestRelativePath",
    "manifestSha256",
    "treeDigest",
    "directoryCount",
    "entryCount",
    "byteCount",
    "candidatePayloadIncluded",
    "strictSourceBound",
    "verifiedAfterBuild",
}
_FIXED_BUILD_POLICY = {
    "mode": "strict-evidence",
    "releaseEligible": False,
    "evidenceEligible": True,
    "allowDirty": False,
    "allowUnpushed": False,
    "allowVersionMismatch": False,
}
_FORBIDDEN_SELF_REFERENCE_KEYS = {
    "authoritygenerationsha256",
    "authorityfinalcommitreceiptsha256",
    "protectedmanifestsha256",
    "installedlayoutsha256",
    "serviceconfigurationsha256",
    "protectedruntime",
    "generation",
    "finalcommit",
    "scm",
}
_ERROR_CODES = frozenset(
    {
        "protected_runtime_source_cli_invalid",
        "protected_runtime_source_input_unavailable",
        "protected_runtime_source_input_invalid",
        "protected_runtime_source_duplicate_identity",
        "protected_runtime_source_bridge_invalid",
        "protected_runtime_source_manifest_invalid",
        "protected_runtime_source_manifest_noncanonical",
        "protected_runtime_source_manifest_mismatch",
        "protected_runtime_source_self_reference_forbidden",
        "protected_runtime_source_target_exists",
        "protected_runtime_source_write_failed",
        "protected_runtime_source_internal_failure",
    }
)


class ProtectedRuntimeSourceManifestError(RuntimeError):
    """Fixed-code failure that never includes a caller-supplied path."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            code = "protected_runtime_source_internal_failure"
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        ) from exc


def _contract_json_bytes(value: Any) -> bytes:
    """Return the canonical JSON representation used by existing evidence digests."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc


def _contract_json_digest(value: Any) -> str:
    return hashlib.sha256(_contract_json_bytes(value)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _metadata_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        stat.S_IFMT(int(value.st_mode)),
        int(value.st_nlink),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(getattr(value, "st_file_attributes", 0) or 0),
    )


def _durable_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        stat.S_IFMT(int(value.st_mode)),
        int(value.st_nlink),
        int(getattr(value, "st_file_attributes", 0) or 0),
    )


def _identity_key(value: os.stat_result) -> tuple[int, int]:
    return int(value.st_dev), int(value.st_ino)


def _is_link_or_reparse(path: Path, metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    if int(getattr(metadata, "st_file_attributes", 0) or 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    junction = getattr(os.path, "isjunction", None)
    if junction is None:
        return False
    try:
        return bool(junction(path))
    except OSError:
        return True


def _has_alternate_data_stream(path: Path) -> bool:
    try:
        return bool(bridge_target_manifest._has_alternate_data_stream(path))
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc


def _validate_regular_file(
    path: Path,
    metadata: os.stat_result,
    maximum_bytes: int,
    *,
    allow_empty: bool = False,
) -> None:
    if (
        _is_link_or_reparse(path, metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or int(metadata.st_nlink) != 1
        or int(metadata.st_size) > maximum_bytes
        or (not allow_empty and int(metadata.st_size) == 0)
        or _has_alternate_data_stream(path)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )


def _validate_directory(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_unavailable"
        ) from exc
    if (
        _is_link_or_reparse(path, metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or _has_alternate_data_stream(path)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return metadata


def _path_has_unsafe_component(path: Path) -> bool:
    for index, part in enumerate(path.parts):
        if index == 0 and part == path.anchor:
            continue
        if not part or part in {".", ".."} or ":" in part:
            return True
    return False


def _resolve_existing_path(
    value: os.PathLike[str] | str,
    *,
    directory: bool,
) -> Path:
    candidate = Path(value)
    if _path_has_unsafe_component(candidate):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    try:
        absolute = candidate.absolute()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_unavailable"
        ) from exc
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    metadata = _validate_directory(resolved) if directory else None
    if directory and metadata is None:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return resolved


def _resolve_output_path(value: os.PathLike[str] | str) -> Path:
    candidate = Path(value)
    if _path_has_unsafe_component(candidate) or not candidate.name:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    try:
        bridge_target_manifest._normalize_relative_path(candidate.name)
        absolute = candidate.absolute()
        parent = candidate.parent.resolve(strict=True)
    except (OSError, bridge_target_manifest.BridgeTargetManifestError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    if os.path.normcase(str(absolute.parent)) != os.path.normcase(str(parent)):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    _validate_directory(parent)
    return parent / candidate.name


def _open_flags(*, writable: bool = False, create_new: bool = False) -> int:
    flags = os.O_RDWR if writable else os.O_RDONLY
    flags |= int(getattr(os, "O_BINARY", 0) or 0)
    flags |= int(getattr(os, "O_NOINHERIT", 0) or 0)
    flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
    if create_new:
        flags |= os.O_CREAT | os.O_EXCL
    return flags


def _open_held_readonly(path: Path) -> int:
    """Open one source file while denying concurrent writes on Windows."""

    if os.name != "nt":
        return os.open(path, _open_flags())

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    handle = create_file(
        str(path),
        generic_read,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, "held source open failed", str(path))
    try:
        return msvcrt.open_osfhandle(
            handle,
            os.O_RDONLY
            | int(getattr(os, "O_BINARY", 0) or 0)
            | int(getattr(os, "O_NOINHERIT", 0) or 0),
        )
    except BaseException:
        close_handle(handle)
        raise


@dataclass(slots=True)
class _HeldFile:
    path: Path
    descriptor: int
    identity: tuple[int, ...]
    maximum_bytes: int
    allow_empty: bool = False
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def open(
        cls,
        value: os.PathLike[str] | str,
        maximum_bytes: int,
        *,
        allow_empty: bool = False,
    ) -> _HeldFile:
        path = _resolve_existing_path(value, directory=False)
        descriptor: int | None = None
        try:
            before = path.lstat()
            _validate_regular_file(
                path,
                before,
                maximum_bytes,
                allow_empty=allow_empty,
            )
            descriptor = _open_held_readonly(path)
            opened = os.fstat(descriptor)
            _validate_regular_file(
                path,
                opened,
                maximum_bytes,
                allow_empty=allow_empty,
            )
            if _metadata_identity(before) != _metadata_identity(opened):
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_input_invalid"
                )
            return cls(
                path=path,
                descriptor=descriptor,
                identity=_metadata_identity(opened),
                maximum_bytes=maximum_bytes,
                allow_empty=allow_empty,
            )
        except ProtectedRuntimeSourceManifestError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_unavailable"
            ) from exc

    @property
    def identity_key(self) -> tuple[int, int]:
        return self.identity[0], self.identity[1]

    def _verify_identity(self) -> os.stat_result:
        if self._closed:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        try:
            opened = os.fstat(self.descriptor)
            current = self.path.lstat()
            _validate_regular_file(
                self.path,
                opened,
                self.maximum_bytes,
                allow_empty=self.allow_empty,
            )
            _validate_regular_file(
                self.path,
                current,
                self.maximum_bytes,
                allow_empty=self.allow_empty,
            )
        except ProtectedRuntimeSourceManifestError:
            raise
        except OSError as exc:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from exc
        if (
            _metadata_identity(opened) != self.identity
            or _metadata_identity(current) != self.identity
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        return opened

    def read(self) -> tuple[bytes, bytes]:
        opened = self._verify_identity()
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            while True:
                chunk = os.read(
                    self.descriptor,
                    min(HASH_CHUNK_BYTES, self.maximum_bytes + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                digest.update(chunk)
                total += len(chunk)
                if total > self.maximum_bytes:
                    raise ProtectedRuntimeSourceManifestError(
                        "protected_runtime_source_input_invalid"
                    )
        except ProtectedRuntimeSourceManifestError:
            raise
        except OSError as exc:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from exc
        after = self._verify_identity()
        if total != int(opened.st_size) or total != int(after.st_size):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        return b"".join(chunks), digest.digest()

    def hash(self) -> tuple[int, bytes]:
        opened = self._verify_identity()
        digest = hashlib.sha256()
        total = 0
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            while chunk := os.read(self.descriptor, HASH_CHUNK_BYTES):
                digest.update(chunk)
                total += len(chunk)
                if total > self.maximum_bytes:
                    raise ProtectedRuntimeSourceManifestError(
                        "protected_runtime_source_input_invalid"
                    )
        except ProtectedRuntimeSourceManifestError:
            raise
        except OSError as exc:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from exc
        after = self._verify_identity()
        if total != int(opened.st_size) or total != int(after.st_size):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        return total, digest.digest()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.descriptor)


@dataclass(frozen=True, slots=True)
class ProtectedRuntimeSourcePaths:
    authority_service: os.PathLike[str] | str
    driver: os.PathLike[str] | str
    desktop: os.PathLike[str] | str
    backend: os.PathLike[str] | str
    unity: os.PathLike[str] | str
    bridge_launcher: os.PathLike[str] | str
    bridge_listener: os.PathLike[str] | str
    runtime_contract: os.PathLike[str] | str
    fixture_baseline: os.PathLike[str] | str
    bridge_tree: os.PathLike[str] | str
    bridge_manifest: os.PathLike[str] | str
    strict_release_manifest: os.PathLike[str] | str
    portable_archive: os.PathLike[str] | str
    unity_package: os.PathLike[str] | str
    backend_tree: os.PathLike[str] | str
    vrcforge_core_tree: os.PathLike[str] | str
    server_tree: os.PathLike[str] | str
    dependency_set_descriptor: os.PathLike[str] | str
    component_feature_application_descriptor: os.PathLike[str] | str
    parameter_optimization_descriptor: os.PathLike[str] | str
    cross_avatar_accessory_copy_descriptor: os.PathLike[str] | str
    model_part_composition_descriptor: os.PathLike[str] | str
    component_feature_application_root: os.PathLike[str] | str
    parameter_optimization_root: os.PathLike[str] | str
    cross_avatar_accessory_copy_root: os.PathLike[str] | str
    model_part_composition_root: os.PathLike[str] | str


@dataclass(slots=True)
class _TreeSnapshot:
    path: Path
    document: dict[str, Any]
    record: dict[str, Any]
    parent_identity: tuple[int, ...]
    root_identity: tuple[int, ...]

    @property
    def identity_key(self) -> tuple[int, int]:
        return self.root_identity[0], self.root_identity[1]

    def verify_unchanged(self) -> None:
        current_parent = _validate_directory(self.path.parent)
        current_root = _validate_directory(self.path)
        if (
            _durable_identity(current_parent) != self.parent_identity
            or _metadata_identity(current_root) != self.root_identity
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_mismatch"
            )
        document = _build_and_validate_source_tree(self.path)
        if document != self.document:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_mismatch"
            )
        final_root = _validate_directory(self.path)
        if _metadata_identity(final_root) != self.root_identity:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_mismatch"
            )


@dataclass(slots=True)
class _SourceSnapshot:
    paths: ProtectedRuntimeSourcePaths
    bridge_tree: Path
    bridge_manifest_path: Path
    role_files: tuple[_HeldFile, ...]
    source_files: tuple[_HeldFile, ...]
    release_files: tuple[_HeldFile, ...]
    dependency_file: _HeldFile
    fixture_descriptor_files: tuple[_HeldFile, ...]
    bridge_manifest_file: _HeldFile
    role_records: tuple[dict[str, Any], ...]
    source_records: tuple[dict[str, Any], ...]
    release_artifacts: dict[str, dict[str, Any]]
    package_trees: dict[str, dict[str, Any]]
    dependency_set: dict[str, Any]
    fixture_set: dict[str, Any]
    model_fixture: dict[str, Any]
    tree_snapshots: tuple[_TreeSnapshot, ...]
    bridge_document: dict[str, Any]
    bridge_runtime: dict[str, Any]
    archive_binding_error_code: str
    tree_parent_identity: tuple[int, ...]
    tree_identity: tuple[int, ...]
    runtime_home_identity: tuple[int, ...]
    _closed: bool = field(default=False, init=False, repr=False)

    def verify_unchanged(self) -> None:
        if self._closed:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        current_parent = _validate_directory(self.bridge_tree.parent)
        current_tree = _validate_directory(self.bridge_tree)
        current_home = _validate_directory(self.bridge_tree / "_internal")
        if (
            _durable_identity(current_parent) != self.tree_parent_identity
            or _metadata_identity(current_tree) != self.tree_identity
            or _metadata_identity(current_home) != self.runtime_home_identity
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )

        for held, record in zip(self.role_files, self.role_records, strict=True):
            length, digest = held.hash()
            if (
                length != record["byteCount"]
                or digest.hex() != record["sha256"]
            ):
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_manifest_mismatch"
                )
        for held, record in zip(self.source_files, self.source_records, strict=True):
            length, digest = held.hash()
            if (
                length != record["byteCount"]
                or digest.hex() != record["sha256"]
            ):
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_manifest_mismatch"
                )
        for held, (_field_name, record_name, _maximum) in zip(
            self.release_files, RELEASE_ARTIFACT_FIELDS, strict=True
        ):
            length, digest = held.hash()
            record = self.release_artifacts[record_name]
            if length != record["byteCount"] or digest.hex() != record["sha256"]:
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_manifest_mismatch"
                )
        dependency_length, dependency_digest = self.dependency_file.hash()
        if (
            dependency_length != self.dependency_set["byteCount"]
            or dependency_digest.hex() != self.dependency_set["descriptorSha256"]
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_mismatch"
            )
        for held, record in zip(
            self.fixture_descriptor_files,
            self.fixture_set["descriptors"],
            strict=True,
        ):
            length, digest = held.hash()
            if length != record["byteCount"] or digest.hex() != record["fileSha256"]:
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_manifest_mismatch"
                )
        for tree in self.tree_snapshots:
            tree.verify_unchanged()
        raw_bridge_manifest, bridge_manifest_digest = self.bridge_manifest_file.read()
        current_bridge_document = _parse_bridge_manifest(raw_bridge_manifest)
        if (
            bridge_manifest_digest.hex() != self.bridge_runtime["manifestSha256"]
            or current_bridge_document != self.bridge_document
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )
        observed = _build_and_validate_bridge_tree(self.bridge_tree)
        if observed != self.bridge_document:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )
        _verify_portable_archive_bindings(
            self.release_files[1],
            self.release_artifacts["portableArchive"],
            self.release_files[2],
            self.bridge_manifest_file,
            tuple(
                tree.document
                for tree in self.tree_snapshots[: len(PACKAGE_TREE_FIELDS)]
            ),
            self.bridge_document,
            binding_error_code=self.archive_binding_error_code,
        )
        for tree in self.tree_snapshots:
            tree.verify_unchanged()
        if _build_and_validate_bridge_tree(self.bridge_tree) != self.bridge_document:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )
        current_tree = _validate_directory(self.bridge_tree)
        current_home = _validate_directory(self.bridge_tree / "_internal")
        if (
            _metadata_identity(current_tree) != self.tree_identity
            or _metadata_identity(current_home) != self.runtime_home_identity
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: OSError | None = None
        for held in (
            *self.role_files,
            *self.source_files,
            *self.release_files,
            self.dependency_file,
            *self.fixture_descriptor_files,
            self.bridge_manifest_file,
        ):
            try:
                held.close()
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from first_error


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first)) == os.path.normcase(str(second))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_version_and_commit(version: str, source_commit: str) -> None:
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 128
        or _VERSION_RE.fullmatch(version) is None
        or not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or set(source_commit) == {"0"}
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )


def _validate_fixed_layout(
    paths: ProtectedRuntimeSourcePaths,
) -> tuple[ProtectedRuntimeSourcePaths, Path, Path]:
    resolved_values: dict[str, Path] = {}
    for field_name, _role_name in ROLE_FIELDS:
        path = _resolve_existing_path(getattr(paths, field_name), directory=False)
        if path.suffix.casefold() != ".exe":
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        resolved_values[field_name] = path
    for field_name, _source_name in SOURCE_FIELDS:
        resolved_values[field_name] = _resolve_existing_path(
            getattr(paths, field_name), directory=False
        )

    for field_name, _record_name, _maximum in RELEASE_ARTIFACT_FIELDS:
        resolved_values[field_name] = _resolve_existing_path(
            getattr(paths, field_name), directory=False
        )
    resolved_values["dependency_set_descriptor"] = _resolve_existing_path(
        paths.dependency_set_descriptor,
        directory=False,
    )
    for field_name, _scenario_id in FIXTURE_DESCRIPTOR_FIELDS:
        resolved_values[field_name] = _resolve_existing_path(
            getattr(paths, field_name), directory=False
        )
    for field_name, _tree_name in (*PACKAGE_TREE_FIELDS, *FIXTURE_ROOT_FIELDS):
        resolved_values[field_name] = _resolve_existing_path(
            getattr(paths, field_name), directory=True
        )

    bridge_tree = _resolve_existing_path(paths.bridge_tree, directory=True)
    bridge_manifest = _resolve_existing_path(paths.bridge_manifest, directory=False)
    if bridge_tree.name != BRIDGE_TARGET_RUNTIME_ROOT:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_bridge_invalid"
        )
    expected_bridge_manifest = bridge_tree.parent / BRIDGE_TARGET_MANIFEST_NAME
    expected_listener = bridge_tree / BRIDGE_TARGET_EXECUTABLE_NAME
    if (
        not _same_path(bridge_manifest, expected_bridge_manifest)
        or not _same_path(resolved_values["bridge_listener"], expected_listener)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_bridge_invalid"
        )
    _validate_directory(bridge_tree / "_internal")

    runtime_contract = resolved_values["runtime_contract"]
    fixture_baseline = resolved_values["fixture_baseline"]
    model_root = resolved_values["model_part_composition_root"]
    if (
        runtime_contract.name != FIXTURE_CONTRACT_NAME
        or fixture_baseline.name != FIXTURE_BASELINE_NAME
        or not _same_path(runtime_contract.parent, fixture_baseline.parent)
        or not _same_path(runtime_contract.parent, model_root)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )

    fixture_roots = [
        resolved_values[field_name]
        for field_name, _scenario_id in FIXTURE_ROOT_FIELDS
    ]
    fixture_parent = fixture_roots[0].parent
    if tuple(fixture_parent.parts[-3:]) != ("Assets", "VRCForge", "PrimitiveBasis"):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    for (field_name, scenario_id), root in zip(
        FIXTURE_ROOT_FIELDS, fixture_roots, strict=True
    ):
        del field_name
        if root.name != scenario_id or not _same_path(root.parent, fixture_parent):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )

    descriptor_paths = [
        resolved_values[field_name]
        for field_name, _scenario_id in FIXTURE_DESCRIPTOR_FIELDS
    ]
    descriptor_parent = descriptor_paths[0].parent
    if descriptor_parent.name != "descriptors":
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    for (_field_name, scenario_id), descriptor in zip(
        FIXTURE_DESCRIPTOR_FIELDS, descriptor_paths, strict=True
    ):
        if (
            descriptor.name != f"{scenario_id}.json"
            or not _same_path(descriptor.parent, descriptor_parent)
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )

    if (
        resolved_values["strict_release_manifest"].name != "release-manifest.json"
        or resolved_values["portable_archive"].suffix.casefold() != ".zip"
        or resolved_values["unity_package"].suffix.casefold() != ".unitypackage"
        or resolved_values["dependency_set_descriptor"].suffix.casefold() != ".json"
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )

    for field_name, _role_name in ROLE_FIELDS[:-1]:
        if _is_within(resolved_values[field_name], bridge_tree):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_duplicate_identity"
            )
    if _is_within(runtime_contract, bridge_tree) or _is_within(
        fixture_baseline, bridge_tree
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_duplicate_identity"
        )

    resolved_values["bridge_tree"] = bridge_tree
    resolved_values["bridge_manifest"] = bridge_manifest
    resolved_paths = ProtectedRuntimeSourcePaths(**resolved_values)
    return resolved_paths, bridge_tree, bridge_manifest


def _register_unique_identity(
    held: _HeldFile,
    identities: set[tuple[int, int]],
) -> None:
    if held.identity_key == (0, 0) or held.identity_key in identities:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_duplicate_identity"
        )
    identities.add(held.identity_key)


def _parse_bridge_manifest(raw_content: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw_content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        document = bridge_target_manifest.validate_manifest_document(parsed)
    except (
        _DuplicateJsonKey,
        UnicodeError,
        json.JSONDecodeError,
        bridge_target_manifest.BridgeTargetManifestError,
    ) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_bridge_invalid"
        ) from exc
    if raw_content != bridge_target_manifest.canonical_json_bytes(document) + b"\n":
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_bridge_invalid"
        )
    return document


def _build_and_validate_bridge_tree(tree_root: Path) -> dict[str, Any]:
    try:
        document = bridge_target_manifest.build_manifest(tree_root)
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_bridge_invalid"
        ) from exc
    if (
        document["directoryCount"] == 0
        or document["entryCount"] == 0
        or document["byteCount"] == 0
        or document["directoryCount"] > MAX_POLICY_TREE_ENTRIES
        or document["entryCount"] > MAX_POLICY_TREE_ENTRIES
        or document["byteCount"] > MAX_POLICY_TREE_BYTES
        or "_internal" not in document["directories"]
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_bridge_invalid"
        )
    return document


def _build_and_validate_source_tree(tree_root: Path) -> dict[str, Any]:
    try:
        document = bridge_target_manifest.build_manifest(tree_root)
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    if (
        document["entryCount"] == 0
        or document["byteCount"] == 0
        or document["directoryCount"] > MAX_POLICY_TREE_ENTRIES
        or document["entryCount"] > MAX_POLICY_TREE_ENTRIES
        or document["byteCount"] > MAX_POLICY_TREE_BYTES
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return document


def _tree_source_record(document: Mapping[str, Any]) -> dict[str, Any]:
    binding_rows = [
        {
            "path": row["path"],
            "size": row["length"],
            "sha256": row["sha256"],
        }
        for row in document["files"]
    ]
    return {
        "schema": TREE_SOURCE_SCHEMA,
        "treeDigest": document["treeDigest"],
        "bindingDigest": _contract_json_digest(binding_rows),
        "directoryCount": document["directoryCount"],
        "entryCount": document["entryCount"],
        "byteCount": document["byteCount"],
    }


def _open_tree_snapshot(path: Path) -> _TreeSnapshot:
    parent_before = _validate_directory(path.parent)
    root_before = _validate_directory(path)
    document = _build_and_validate_source_tree(path)
    parent_after = _validate_directory(path.parent)
    root_after = _validate_directory(path)
    if (
        _durable_identity(parent_before) != _durable_identity(parent_after)
        or _metadata_identity(root_before) != _metadata_identity(root_after)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    snapshot = _TreeSnapshot(
        path=path,
        document=document,
        record=_tree_source_record(document),
        parent_identity=_durable_identity(parent_after),
        root_identity=_metadata_identity(root_after),
    )
    snapshot.verify_unchanged()
    return snapshot


def _normalize_archive_member(
    info: zipfile.ZipInfo,
) -> tuple[str, bool]:
    raw_name = info.filename
    is_directory = info.is_dir()
    try:
        path_bytes = raw_name.encode("utf-8", errors="strict")
    except (AttributeError, UnicodeError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    if (
        not isinstance(raw_name, str)
        or not raw_name
        or len(path_bytes) > MAX_PORTABLE_ARCHIVE_PATH_BYTES
        or (is_directory and not raw_name.endswith("/"))
        or (is_directory and raw_name.endswith("//"))
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    candidate = raw_name[:-1] if is_directory else raw_name
    try:
        normalized = bridge_target_manifest._normalize_relative_path(candidate)
    except (UnicodeError, bridge_target_manifest.BridgeTargetManifestError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc

    if info.create_system not in {0, 3}:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    unix_mode = (int(info.external_attr) >> 16) & 0xFFFF
    unix_kind = stat.S_IFMT(unix_mode)
    dos_attributes = int(info.external_attr) & 0xFFFF
    if (
        dos_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or info.flag_bits & 0x41
        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        or isinstance(info.file_size, bool)
        or isinstance(info.compress_size, bool)
        or not isinstance(info.file_size, int)
        or not isinstance(info.compress_size, int)
        or info.file_size < 0
        or info.compress_size < 0
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    if is_directory:
        if (
            info.file_size != 0
            or unix_kind not in {0, stat.S_IFDIR}
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
    elif (
        unix_kind not in {0, stat.S_IFREG}
        or dos_attributes & 0x10
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return normalized, is_directory


def _index_portable_archive(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, zipfile.ZipInfo], set[str]]:
    try:
        members = archive.infolist()
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    if not members or len(members) > MAX_PORTABLE_ARCHIVE_ENTRIES:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )

    files: dict[str, zipfile.ZipInfo] = {}
    directories: set[str] = set()
    member_keys: set[str] = set()
    path_claims: dict[str, tuple[str, str]] = {}
    expanded_bytes = 0
    for info in members:
        path, is_directory = _normalize_archive_member(info)
        member_key = path.casefold()
        if member_key in member_keys:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        member_keys.add(member_key)
        try:
            bridge_target_manifest._register_path_claim(
                path,
                "directory" if is_directory else "file",
                path_claims,
            )
        except bridge_target_manifest.BridgeTargetManifestError as exc:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from exc

        if is_directory:
            directories.add(path)
            continue
        expanded_bytes += info.file_size
        if (
            info.file_size > MAX_PORTABLE_ARCHIVE_ENTRY_BYTES
            or expanded_bytes > MAX_PORTABLE_ARCHIVE_EXPANDED_BYTES
            or (
                info.file_size > 0
                and (
                    info.compress_size == 0
                    or info.file_size
                    > info.compress_size * MAX_PORTABLE_ARCHIVE_COMPRESSION_RATIO
                )
            )
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        files[path] = info
    return files, directories


def _read_archive_member_record(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> dict[str, Any]:
    digest = hashlib.sha256()
    total = 0
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(
                    min(
                        HASH_CHUNK_BYTES,
                        MAX_PORTABLE_ARCHIVE_ENTRY_BYTES + 1 - total,
                    )
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PORTABLE_ARCHIVE_ENTRY_BYTES:
                    raise ProtectedRuntimeSourceManifestError(
                        "protected_runtime_source_input_invalid"
                    )
                digest.update(chunk)
    except ProtectedRuntimeSourceManifestError:
        raise
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    if total != info.file_size:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return {"sha256": digest.hexdigest(), "byteCount": total}


def _verify_archive_tree_binding(
    archive: zipfile.ZipFile,
    files: Mapping[str, zipfile.ZipInfo],
    directories: set[str],
    *,
    prefix: str,
    tree: Mapping[str, Any],
    binding_error_code: str,
) -> None:
    expected_files = {
        f"{prefix}/{row['path']}": {
            "sha256": row["sha256"],
            "byteCount": row["length"],
        }
        for row in tree["files"]
    }
    expected_directories = {
        prefix,
        *(f"{prefix}/{path}" for path in tree["directories"]),
    }
    folded_prefix = prefix.casefold()

    bound_files: set[str] = set()
    for path in files:
        folded = path.casefold()
        if folded == folded_prefix or folded.startswith(f"{folded_prefix}/"):
            if path != prefix and not path.startswith(f"{prefix}/"):
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_input_invalid"
                )
            bound_files.add(path)
    bound_directories: set[str] = set()
    for path in directories:
        folded = path.casefold()
        if folded == folded_prefix or folded.startswith(f"{folded_prefix}/"):
            if path != prefix and not path.startswith(f"{prefix}/"):
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_input_invalid"
                )
            bound_directories.add(path)
    if bound_files != set(expected_files) or not bound_directories.issubset(
        expected_directories
    ):
        raise ProtectedRuntimeSourceManifestError(binding_error_code)
    for path, expected in expected_files.items():
        if _read_archive_member_record(archive, files[path]) != expected:
            raise ProtectedRuntimeSourceManifestError(binding_error_code)


def _verify_archive_member_matches_held_file(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    held: _HeldFile,
    *,
    binding_error_code: str,
) -> None:
    opened = held._verify_identity()
    if info.file_size != int(opened.st_size):
        raise ProtectedRuntimeSourceManifestError(binding_error_code)
    total = 0
    try:
        os.lseek(held.descriptor, 0, os.SEEK_SET)
        with archive.open(info, "r") as stream:
            while True:
                expected = os.read(held.descriptor, HASH_CHUNK_BYTES)
                if not expected:
                    break
                observed = stream.read(len(expected))
                if observed != expected:
                    raise ProtectedRuntimeSourceManifestError(binding_error_code)
                total += len(expected)
            if stream.read(1):
                raise ProtectedRuntimeSourceManifestError(binding_error_code)
    except ProtectedRuntimeSourceManifestError:
        raise
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    after = held._verify_identity()
    if total != int(opened.st_size) or _metadata_identity(after) != held.identity:
        raise ProtectedRuntimeSourceManifestError(binding_error_code)


def _verify_portable_archive_bindings(
    portable_archive: _HeldFile,
    portable_record: Mapping[str, Any],
    unity_package: _HeldFile,
    bridge_manifest: _HeldFile,
    package_tree_documents: Sequence[Mapping[str, Any]],
    bridge_tree_document: Mapping[str, Any],
    *,
    binding_error_code: str,
) -> None:
    portable_archive._verify_identity()
    duplicate: int | None = None
    try:
        duplicate = os.dup(portable_archive.descriptor)
        with os.fdopen(duplicate, "rb", closefd=True) as stream:
            duplicate = None
            stream.seek(0)
            with zipfile.ZipFile(stream, mode="r", allowZip64=True) as archive:
                files, directories = _index_portable_archive(archive)
                tree_documents = (
                    *package_tree_documents[
                        : len(PORTABLE_ARCHIVE_TREE_BINDINGS) - 1
                    ],
                    bridge_tree_document,
                )
                for (_tree_name, prefix), tree in zip(
                    PORTABLE_ARCHIVE_TREE_BINDINGS,
                    tree_documents,
                    strict=True,
                ):
                    _verify_archive_tree_binding(
                        archive,
                        files,
                        directories,
                        prefix=prefix,
                        tree=tree,
                        binding_error_code=binding_error_code,
                    )

                unity_info = files.get(PORTABLE_ARCHIVE_UNITY_PACKAGE_PATH)
                bridge_manifest_info = files.get(BRIDGE_TARGET_MANIFEST_NAME)
                if unity_info is None or bridge_manifest_info is None:
                    raise ProtectedRuntimeSourceManifestError(binding_error_code)
                _verify_archive_member_matches_held_file(
                    archive,
                    unity_info,
                    unity_package,
                    binding_error_code=binding_error_code,
                )
                _verify_archive_member_matches_held_file(
                    archive,
                    bridge_manifest_info,
                    bridge_manifest,
                    binding_error_code=binding_error_code,
                )
    except ProtectedRuntimeSourceManifestError:
        raise
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    finally:
        if duplicate is not None:
            os.close(duplicate)
    length, digest = portable_archive.hash()
    if (
        length != portable_record["byteCount"]
        or digest.hex() != portable_record["sha256"]
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_mismatch"
        )


def _parse_json_object(raw_content: bytes, *, canonical: bool) -> dict[str, Any]:
    try:
        value = json.loads(
            raw_content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (_DuplicateJsonKey, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        ) from exc
    if not isinstance(value, dict):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    if canonical and raw_content != canonical_json_bytes(value) + b"\n":
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return value


def _file_record(held: _HeldFile) -> dict[str, Any]:
    length, digest = held.hash()
    if length == 0:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return {"sha256": digest.hex(), "byteCount": length}


def _input_nonzero_digest(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _LOWER_SHA256_RE.fullmatch(value) is None
        or set(value) == {"0"}
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    return value


def _validate_release_manifest_source(
    raw_content: bytes,
    *,
    version: str,
    source_commit: str,
    portable_name: str,
    portable_sha256: str,
    unity_package_name: str,
    unity_package_sha256: str,
) -> None:
    value = _parse_json_object(raw_content, canonical=False)
    policy = value.get("buildPolicy")
    if (
        value.get("version") != version
        or value.get("commit") != source_commit
        or not isinstance(policy, dict)
        or policy.get("mode") != "strict-evidence"
        or policy.get("evidenceEligible") is not True
        or policy.get("allowDirty") is not False
        or policy.get("allowUnpushed") is not False
        or policy.get("allowVersionMismatch") is not False
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    indexed: dict[str, str] = {}
    folded: set[str] = set()
    for row in artifacts:
        if not isinstance(row, dict):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        name = row.get("name")
        digest = row.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or _LOWER_SHA256_RE.fullmatch(digest or "") is None
            or set(digest) == {"0"}
            or name.casefold() in folded
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        folded.add(name.casefold())
        indexed[name] = digest
    if (
        indexed.get(portable_name) != portable_sha256
        or indexed.get(unity_package_name) != unity_package_sha256
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )


def _validate_fixture_descriptor(
    raw_content: bytes,
    *,
    scenario_id: str,
) -> tuple[dict[str, Any], str]:
    value = _parse_json_object(raw_content, canonical=False)
    expected_root = f"Assets/VRCForge/PrimitiveBasis/{scenario_id}"
    if (
        set(value) != _FIXTURE_DESCRIPTOR_INPUT_KEYS
        or value.get("schema") != FIXTURE_DESCRIPTOR_SCHEMA
        or value.get("scenarioId") != scenario_id
        or value.get("fixtureRoot") != expected_root
        or value.get("baselineManifest") != FIXTURE_BASELINE_NAME
        or value.get("requiredPrimitives") != list(SCENARIO_DEFINITIONS[scenario_id])
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    _input_nonzero_digest(value.get("expectedBaselineDigest"))
    _input_nonzero_digest(value.get("expectedTreeDigest"))
    return value, _contract_json_digest(value)


def _fixture_materialization_record(
    *,
    scenario_id: str,
    descriptor: Mapping[str, Any],
    descriptor_digest: str,
    tree: _TreeSnapshot,
) -> dict[str, Any]:
    baseline_path = tree.path / FIXTURE_BASELINE_NAME
    baseline_file = _HeldFile.open(baseline_path, MAX_SOURCE_BYTES)
    try:
        baseline_raw, _baseline_file_digest = baseline_file.read()
    finally:
        baseline_file.close()
    baseline = _parse_json_object(baseline_raw, canonical=False)
    if (
        set(baseline) != _FIXTURE_BASELINE_INPUT_KEYS
        or baseline.get("schema") != FIXTURE_BASELINE_SCHEMA
        or baseline.get("scenarioId") != scenario_id
        or not isinstance(baseline.get("files"), list)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )

    declared: list[dict[str, Any]] = []
    previous_path = ""
    for row in baseline["files"]:
        if not isinstance(row, dict) or set(row) != _FIXTURE_BASELINE_FILE_KEYS:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        path = row.get("path")
        size = row.get("size")
        digest = row.get("sha256")
        try:
            normalized_path = bridge_target_manifest._normalize_relative_path(path)
        except bridge_target_manifest.BridgeTargetManifestError as exc:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from exc
        if (
            normalized_path == FIXTURE_BASELINE_NAME
            or (previous_path and normalized_path <= previous_path)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            )
        previous_path = normalized_path
        declared.append(
            {
                "path": normalized_path,
                "size": size,
                "sha256": _input_nonzero_digest(digest),
            }
        )

    if not declared:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )

    actual = [
        {"path": row["path"], "size": row["length"], "sha256": row["sha256"]}
        for row in tree.document["files"]
        if row["path"] != FIXTURE_BASELINE_NAME
    ]
    if actual != declared:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    baseline_digest = _contract_json_digest(baseline)
    content_tree_digest = _contract_json_digest(actual)
    if (
        baseline_digest != descriptor["expectedBaselineDigest"]
        or content_tree_digest != descriptor["expectedTreeDigest"]
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_input_invalid"
        )
    fixture_digest = _contract_json_digest(
        {
            "descriptorDigest": descriptor_digest,
            "baselineDigest": baseline_digest,
            "treeDigest": content_tree_digest,
        }
    )
    return {
        "scenarioId": scenario_id,
        "fixtureDigest": fixture_digest,
        "baselineDigest": baseline_digest,
        "contentTreeDigest": content_tree_digest,
        "sourceTree": dict(tree.record),
    }


def _open_source_snapshot(
    paths: ProtectedRuntimeSourcePaths,
    *,
    version: str,
    source_commit: str,
    archive_binding_error_code: str,
) -> _SourceSnapshot:
    resolved, bridge_tree, bridge_manifest_path = _validate_fixed_layout(paths)
    role_files: list[_HeldFile] = []
    source_files: list[_HeldFile] = []
    release_files: list[_HeldFile] = []
    fixture_descriptor_files: list[_HeldFile] = []
    dependency_file: _HeldFile | None = None
    bridge_manifest_file: _HeldFile | None = None
    identities: set[tuple[int, int]] = set()
    try:
        for field_name, _role_name in ROLE_FIELDS:
            held = _HeldFile.open(getattr(resolved, field_name), MAX_EXECUTABLE_BYTES)
            _register_unique_identity(held, identities)
            role_files.append(held)
        for field_name, _source_name in SOURCE_FIELDS:
            held = _HeldFile.open(getattr(resolved, field_name), MAX_SOURCE_BYTES)
            _register_unique_identity(held, identities)
            source_files.append(held)
        for field_name, _record_name, maximum in RELEASE_ARTIFACT_FIELDS:
            held = _HeldFile.open(getattr(resolved, field_name), maximum)
            _register_unique_identity(held, identities)
            release_files.append(held)
        dependency_file = _HeldFile.open(
            resolved.dependency_set_descriptor,
            MAX_SOURCE_BYTES,
        )
        _register_unique_identity(dependency_file, identities)
        for field_name, _scenario_id in FIXTURE_DESCRIPTOR_FIELDS:
            held = _HeldFile.open(getattr(resolved, field_name), MAX_SOURCE_BYTES)
            _register_unique_identity(held, identities)
            fixture_descriptor_files.append(held)
        bridge_manifest_file = _HeldFile.open(
            bridge_manifest_path,
            bridge_target_manifest.MAX_MANIFEST_BYTES,
        )
        _register_unique_identity(bridge_manifest_file, identities)

        role_records: list[dict[str, Any]] = []
        for (_field_name, role_name), held in zip(ROLE_FIELDS, role_files, strict=True):
            record = _file_record(held)
            role_records.append({"role": role_name, **record})
        source_records: list[dict[str, Any]] = []
        for (_field_name, source_name), held in zip(
            SOURCE_FIELDS, source_files, strict=True
        ):
            record = _file_record(held)
            source_records.append({"source": source_name, **record})

        release_artifacts = {
            record_name: _file_record(held)
            for (_field_name, record_name, _maximum), held in zip(
                RELEASE_ARTIFACT_FIELDS, release_files, strict=True
            )
        }
        dependency_raw, dependency_digest = dependency_file.read()
        raw_dependency_document = _parse_json_object(
            dependency_raw,
            canonical=True,
        )
        try:
            dependency_document = (
                protected_runtime_dependency_set.validate_dependency_set_document(
                    raw_dependency_document
                )
            )
        except protected_runtime_dependency_set.ProtectedRuntimeDependencySetError as exc:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_input_invalid"
            ) from exc
        dependency_set = {
            "descriptorSchema": dependency_document["schema"],
            "setDigest": dependency_document["setDigest"],
            "descriptorSha256": dependency_digest.hex(),
            "byteCount": len(dependency_raw),
            "canonicalJson": True,
        }
        dependency_set["bindingDigest"] = _contract_json_digest(dependency_set)

        fixture_descriptors: list[dict[str, Any]] = []
        fixture_descriptor_documents: list[dict[str, Any]] = []
        for (_field_name, scenario_id), held in zip(
            FIXTURE_DESCRIPTOR_FIELDS,
            fixture_descriptor_files,
            strict=True,
        ):
            raw_descriptor, file_digest = held.read()
            descriptor, descriptor_digest = _validate_fixture_descriptor(
                raw_descriptor,
                scenario_id=scenario_id,
            )
            fixture_descriptor_documents.append(descriptor)
            fixture_descriptors.append(
                {
                    "scenarioId": scenario_id,
                    "fileSha256": file_digest.hex(),
                    "descriptorDigest": descriptor_digest,
                    "byteCount": len(raw_descriptor),
                }
            )

        tree_snapshots: list[_TreeSnapshot] = []
        tree_paths = [
            getattr(resolved, field_name)
            for field_name, _tree_name in (
                *PACKAGE_TREE_FIELDS,
                *FIXTURE_ROOT_FIELDS,
            )
        ]
        for index, first in enumerate(tree_paths):
            for second in tree_paths[index + 1 :]:
                if _is_within(first, second) or _is_within(second, first):
                    raise ProtectedRuntimeSourceManifestError(
                        "protected_runtime_source_duplicate_identity"
                    )
        bridge_identity = _identity_key(_validate_directory(bridge_tree))
        if bridge_identity == (0, 0) or bridge_identity in identities:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_duplicate_identity"
            )
        identities.add(bridge_identity)
        for path in tree_paths:
            tree = _open_tree_snapshot(path)
            if tree.identity_key == (0, 0) or tree.identity_key in identities:
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_duplicate_identity"
                )
            identities.add(tree.identity_key)
            tree_snapshots.append(tree)

        package_tree_snapshots = tree_snapshots[: len(PACKAGE_TREE_FIELDS)]
        fixture_tree_snapshots = tree_snapshots[len(PACKAGE_TREE_FIELDS) :]
        package_trees = {
            record_name: dict(tree.record)
            for (_field_name, record_name), tree in zip(
                PACKAGE_TREE_FIELDS,
                package_tree_snapshots,
                strict=True,
            )
        }
        fixture_roots = [
            _fixture_materialization_record(
                scenario_id=scenario_id,
                descriptor=descriptor,
                descriptor_digest=descriptor_record["descriptorDigest"],
                tree=tree,
            )
            for (_field_name, scenario_id), descriptor, descriptor_record, tree in zip(
                FIXTURE_ROOT_FIELDS,
                fixture_descriptor_documents,
                fixture_descriptors,
                fixture_tree_snapshots,
                strict=True,
            )
        ]
        fixture_set = {
            "descriptorSetDigest": _contract_json_digest(
                [
                    {
                        "scenarioId": record["scenarioId"],
                        "descriptorDigest": record["descriptorDigest"],
                    }
                    for record in fixture_descriptors
                ]
            ),
            "fixtureSetDigest": _contract_json_digest(
                [
                    {
                        "scenarioId": record["scenarioId"],
                        "digest": record["fixtureDigest"],
                    }
                    for record in fixture_roots
                ]
            ),
            "descriptors": fixture_descriptors,
            "materializedRoots": fixture_roots,
        }
        model_descriptor = fixture_descriptors[-1]
        model_root = fixture_roots[-1]
        model_fixture = {
            "scenarioId": SCENARIO_ID,
            "descriptorDigest": model_descriptor["descriptorDigest"],
            "fixtureDigest": model_root["fixtureDigest"],
        }

        model_tree_files = {
            row["path"]: row for row in fixture_tree_snapshots[-1].document["files"]
        }
        for source_record, relative_name in zip(
            source_records,
            (FIXTURE_CONTRACT_NAME, FIXTURE_BASELINE_NAME),
            strict=True,
        ):
            tree_record = model_tree_files.get(relative_name)
            if (
                tree_record is None
                or tree_record["length"] != source_record["byteCount"]
                or tree_record["sha256"] != source_record["sha256"]
            ):
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_input_invalid"
                )

        strict_manifest_raw, _strict_manifest_digest = release_files[0].read()
        _validate_release_manifest_source(
            strict_manifest_raw,
            version=version,
            source_commit=source_commit,
            portable_name=resolved.portable_archive.name,
            portable_sha256=release_artifacts["portableArchive"]["sha256"],
            unity_package_name=resolved.unity_package.name,
            unity_package_sha256=release_artifacts["unityPackage"]["sha256"],
        )

        raw_bridge_manifest, bridge_manifest_digest = bridge_manifest_file.read()
        bridge_document = _parse_bridge_manifest(raw_bridge_manifest)
        observed_bridge = _build_and_validate_bridge_tree(bridge_tree)
        if observed_bridge != bridge_document:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )
        executable_records = [
            row
            for row in bridge_document["files"]
            if row["path"] == BRIDGE_TARGET_EXECUTABLE_NAME
        ]
        bridge_listener_length, bridge_listener_digest = role_files[-1].hash()
        if (
            len(executable_records) != 1
            or executable_records[0]["length"] != bridge_listener_length
            or executable_records[0]["sha256"] != bridge_listener_digest.hex()
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_bridge_invalid"
            )
        bridge_runtime = {
            "schema": BRIDGE_TARGET_RUNTIME_SCHEMA,
            "runtimeRelativeRoot": BRIDGE_TARGET_RUNTIME_ROOT,
            "executableRelativePath": BRIDGE_TARGET_EXECUTABLE_PATH,
            "executableSha256": bridge_listener_digest.hex(),
            "manifestRelativePath": BRIDGE_TARGET_MANIFEST_NAME,
            "manifestSha256": bridge_manifest_digest.hex(),
            "treeDigest": bridge_document["treeDigest"],
            "directoryCount": bridge_document["directoryCount"],
            "entryCount": bridge_document["entryCount"],
            "byteCount": bridge_document["byteCount"],
            "candidatePayloadIncluded": True,
            "strictSourceBound": True,
            "verifiedAfterBuild": True,
        }
        snapshot = _SourceSnapshot(
            paths=resolved,
            bridge_tree=bridge_tree,
            bridge_manifest_path=bridge_manifest_path,
            role_files=tuple(role_files),
            source_files=tuple(source_files),
            release_files=tuple(release_files),
            dependency_file=dependency_file,
            fixture_descriptor_files=tuple(fixture_descriptor_files),
            bridge_manifest_file=bridge_manifest_file,
            role_records=tuple(role_records),
            source_records=tuple(source_records),
            release_artifacts=release_artifacts,
            package_trees=package_trees,
            dependency_set=dependency_set,
            fixture_set=fixture_set,
            model_fixture=model_fixture,
            tree_snapshots=tuple(tree_snapshots),
            bridge_document=bridge_document,
            bridge_runtime=bridge_runtime,
            archive_binding_error_code=archive_binding_error_code,
            tree_parent_identity=_durable_identity(
                _validate_directory(bridge_tree.parent)
            ),
            tree_identity=_metadata_identity(_validate_directory(bridge_tree)),
            runtime_home_identity=_metadata_identity(
                _validate_directory(bridge_tree / "_internal")
            ),
        )
        bridge_manifest_file = None
        dependency_file = None
        role_files = []
        source_files = []
        release_files = []
        fixture_descriptor_files = []
        snapshot.verify_unchanged()
        return snapshot
    except BaseException:
        for held in (
            *role_files,
            *source_files,
            *release_files,
            *fixture_descriptor_files,
        ):
            try:
                held.close()
            except OSError:
                pass
        if bridge_manifest_file is not None:
            try:
                bridge_manifest_file.close()
            except OSError:
                pass
        if dependency_file is not None:
            try:
                dependency_file.close()
            except OSError:
                pass
        raise


def _build_document(
    snapshot: _SourceSnapshot,
    version: str,
    source_commit: str,
) -> dict[str, Any]:
    return {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "version": version,
        "sourceCommit": source_commit,
        "scenarioId": SCENARIO_ID,
        "buildPolicy": dict(_FIXED_BUILD_POLICY),
        "roles": [dict(record) for record in snapshot.role_records],
        "sources": [dict(record) for record in snapshot.source_records],
        "bridgeTargetRuntime": dict(snapshot.bridge_runtime),
        "releaseArtifacts": {
            key: dict(value) for key, value in snapshot.release_artifacts.items()
        },
        "packageTrees": {
            key: dict(value) for key, value in snapshot.package_trees.items()
        },
        "dependencySet": dict(snapshot.dependency_set),
        "fixtureSet": {
            "descriptorSetDigest": snapshot.fixture_set["descriptorSetDigest"],
            "fixtureSetDigest": snapshot.fixture_set["fixtureSetDigest"],
            "descriptors": [
                dict(value) for value in snapshot.fixture_set["descriptors"]
            ],
            "materializedRoots": [
                {
                    **{key: value for key, value in record.items() if key != "sourceTree"},
                    "sourceTree": dict(record["sourceTree"]),
                }
                for record in snapshot.fixture_set["materializedRoots"]
            ],
        },
        "modelFixture": dict(snapshot.model_fixture),
    }


def _normalized_forbidden_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _reject_self_reference_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_forbidden_key(key) in _FORBIDDEN_SELF_REFERENCE_KEYS:
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_self_reference_forbidden"
                )
            _reject_self_reference_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_self_reference_fields(item)


def _required_nonzero_digest(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _LOWER_SHA256_RE.fullmatch(value) is None
        or set(value) == {"0"}
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    return value


def _required_count(value: Any, maximum: int, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    return value


def _validate_file_record(value: Any, maximum: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FILE_RECORD_KEYS:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    return {
        "sha256": _required_nonzero_digest(value.get("sha256")),
        "byteCount": _required_count(value.get("byteCount"), maximum),
    }


def _validate_tree_source_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TREE_RECORD_KEYS:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    normalized = {
        "schema": value.get("schema"),
        "treeDigest": _required_nonzero_digest(value.get("treeDigest")),
        "bindingDigest": _required_nonzero_digest(value.get("bindingDigest")),
        "directoryCount": _required_count(
            value.get("directoryCount"),
            MAX_POLICY_TREE_ENTRIES,
            allow_zero=True,
        ),
        "entryCount": _required_count(
            value.get("entryCount"), MAX_POLICY_TREE_ENTRIES
        ),
        "byteCount": _required_count(value.get("byteCount"), MAX_POLICY_TREE_BYTES),
    }
    if normalized["schema"] != TREE_SOURCE_SCHEMA:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    return normalized


def _validate_source_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    _reject_self_reference_fields(value)
    if set(value) != _ROOT_KEYS:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    schema = value.get("schema")
    if schema in LEGACY_SOURCE_MANIFEST_SCHEMAS:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    version = value.get("version")
    source_commit = value.get("sourceCommit")
    scenario_id = value.get("scenarioId")
    if schema != SOURCE_MANIFEST_SCHEMA or scenario_id != SCENARIO_ID:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    _validate_version_and_commit(version, source_commit)

    build_policy = value.get("buildPolicy")
    if (
        not isinstance(build_policy, dict)
        or set(build_policy) != _BUILD_POLICY_KEYS
        or build_policy != _FIXED_BUILD_POLICY
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )

    raw_roles = value.get("roles")
    if not isinstance(raw_roles, list) or len(raw_roles) != len(ROLE_NAMES):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    roles: list[dict[str, Any]] = []
    for expected_name, raw_role in zip(ROLE_NAMES, raw_roles, strict=True):
        if (
            not isinstance(raw_role, dict)
            or set(raw_role) != _ROLE_KEYS
            or raw_role.get("role") != expected_name
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_invalid"
            )
        roles.append(
            {
                "role": expected_name,
                "sha256": _required_nonzero_digest(raw_role.get("sha256")),
                "byteCount": _required_count(
                    raw_role.get("byteCount"), MAX_EXECUTABLE_BYTES
                ),
            }
        )

    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != len(SOURCE_NAMES):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    sources: list[dict[str, Any]] = []
    for expected_name, raw_source in zip(SOURCE_NAMES, raw_sources, strict=True):
        if (
            not isinstance(raw_source, dict)
            or set(raw_source) != _SOURCE_KEYS
            or raw_source.get("source") != expected_name
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_invalid"
            )
        sources.append(
            {
                "source": expected_name,
                "sha256": _required_nonzero_digest(raw_source.get("sha256")),
                "byteCount": _required_count(
                    raw_source.get("byteCount"), MAX_SOURCE_BYTES
                ),
            }
        )

    runtime = value.get("bridgeTargetRuntime")
    if not isinstance(runtime, dict) or set(runtime) != _BRIDGE_RUNTIME_KEYS:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    normalized_runtime = {
        "schema": runtime.get("schema"),
        "runtimeRelativeRoot": runtime.get("runtimeRelativeRoot"),
        "executableRelativePath": runtime.get("executableRelativePath"),
        "executableSha256": _required_nonzero_digest(runtime.get("executableSha256")),
        "manifestRelativePath": runtime.get("manifestRelativePath"),
        "manifestSha256": _required_nonzero_digest(runtime.get("manifestSha256")),
        "treeDigest": _required_nonzero_digest(runtime.get("treeDigest")),
        "directoryCount": _required_count(
            runtime.get("directoryCount"), MAX_POLICY_TREE_ENTRIES
        ),
        "entryCount": _required_count(
            runtime.get("entryCount"), MAX_POLICY_TREE_ENTRIES
        ),
        "byteCount": _required_count(runtime.get("byteCount"), MAX_POLICY_TREE_BYTES),
        "candidatePayloadIncluded": runtime.get("candidatePayloadIncluded"),
        "strictSourceBound": runtime.get("strictSourceBound"),
        "verifiedAfterBuild": runtime.get("verifiedAfterBuild"),
    }
    if (
        normalized_runtime["schema"] != BRIDGE_TARGET_RUNTIME_SCHEMA
        or normalized_runtime["runtimeRelativeRoot"] != BRIDGE_TARGET_RUNTIME_ROOT
        or normalized_runtime["executableRelativePath"]
        != BRIDGE_TARGET_EXECUTABLE_PATH
        or normalized_runtime["manifestRelativePath"] != BRIDGE_TARGET_MANIFEST_NAME
        or normalized_runtime["candidatePayloadIncluded"] is not True
        or normalized_runtime["strictSourceBound"] is not True
        or normalized_runtime["verifiedAfterBuild"] is not True
        or normalized_runtime["executableSha256"] != roles[-1]["sha256"]
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )

    raw_release_artifacts = value.get("releaseArtifacts")
    if (
        not isinstance(raw_release_artifacts, dict)
        or set(raw_release_artifacts) != _RELEASE_ARTIFACT_KEYS
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    release_artifacts = {
        record_name: _validate_file_record(
            raw_release_artifacts.get(record_name), maximum
        )
        for _field_name, record_name, maximum in RELEASE_ARTIFACT_FIELDS
    }

    raw_package_trees = value.get("packageTrees")
    if (
        not isinstance(raw_package_trees, dict)
        or set(raw_package_trees) != _PACKAGE_TREE_KEYS
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    package_trees = {
        record_name: _validate_tree_source_record(
            raw_package_trees.get(record_name)
        )
        for _field_name, record_name in PACKAGE_TREE_FIELDS
    }

    raw_dependency_set = value.get("dependencySet")
    if (
        not isinstance(raw_dependency_set, dict)
        or set(raw_dependency_set) != _DEPENDENCY_SET_KEYS
        or raw_dependency_set.get("descriptorSchema")
        != protected_runtime_dependency_set.DEPENDENCY_SET_SCHEMA
        or raw_dependency_set.get("canonicalJson") is not True
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    dependency_set = {
        "descriptorSchema": protected_runtime_dependency_set.DEPENDENCY_SET_SCHEMA,
        "setDigest": _required_nonzero_digest(raw_dependency_set.get("setDigest")),
        "descriptorSha256": _required_nonzero_digest(
            raw_dependency_set.get("descriptorSha256")
        ),
        "byteCount": _required_count(
            raw_dependency_set.get("byteCount"), MAX_SOURCE_BYTES
        ),
        "canonicalJson": True,
    }
    if _required_nonzero_digest(raw_dependency_set.get("bindingDigest")) != (
        _contract_json_digest(dependency_set)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    dependency_set["bindingDigest"] = raw_dependency_set["bindingDigest"]

    raw_fixture_set = value.get("fixtureSet")
    if not isinstance(raw_fixture_set, dict) or set(raw_fixture_set) != _FIXTURE_SET_KEYS:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    raw_descriptors = raw_fixture_set.get("descriptors")
    raw_roots = raw_fixture_set.get("materializedRoots")
    if (
        not isinstance(raw_descriptors, list)
        or len(raw_descriptors) != len(SCENARIO_ORDER)
        or not isinstance(raw_roots, list)
        or len(raw_roots) != len(SCENARIO_ORDER)
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    descriptors: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    for scenario, raw_descriptor, raw_root in zip(
        SCENARIO_ORDER, raw_descriptors, raw_roots, strict=True
    ):
        if (
            not isinstance(raw_descriptor, dict)
            or set(raw_descriptor) != _FIXTURE_DESCRIPTOR_RECORD_KEYS
            or raw_descriptor.get("scenarioId") != scenario
            or not isinstance(raw_root, dict)
            or set(raw_root) != _FIXTURE_ROOT_RECORD_KEYS
            or raw_root.get("scenarioId") != scenario
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_invalid"
            )
        descriptor = {
            "scenarioId": scenario,
            "fileSha256": _required_nonzero_digest(
                raw_descriptor.get("fileSha256")
            ),
            "descriptorDigest": _required_nonzero_digest(
                raw_descriptor.get("descriptorDigest")
            ),
            "byteCount": _required_count(
                raw_descriptor.get("byteCount"), MAX_SOURCE_BYTES
            ),
        }
        root = {
            "scenarioId": scenario,
            "fixtureDigest": _required_nonzero_digest(
                raw_root.get("fixtureDigest")
            ),
            "baselineDigest": _required_nonzero_digest(
                raw_root.get("baselineDigest")
            ),
            "contentTreeDigest": _required_nonzero_digest(
                raw_root.get("contentTreeDigest")
            ),
            "sourceTree": _validate_tree_source_record(raw_root.get("sourceTree")),
        }
        expected_fixture_digest = _contract_json_digest(
            {
                "descriptorDigest": descriptor["descriptorDigest"],
                "baselineDigest": root["baselineDigest"],
                "treeDigest": root["contentTreeDigest"],
            }
        )
        if root["fixtureDigest"] != expected_fixture_digest:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_invalid"
            )
        descriptors.append(descriptor)
        roots.append(root)

    descriptor_set_digest = _required_nonzero_digest(
        raw_fixture_set.get("descriptorSetDigest")
    )
    fixture_set_digest = _required_nonzero_digest(
        raw_fixture_set.get("fixtureSetDigest")
    )
    if descriptor_set_digest != _contract_json_digest(
        [
            {
                "scenarioId": record["scenarioId"],
                "descriptorDigest": record["descriptorDigest"],
            }
            for record in descriptors
        ]
    ) or fixture_set_digest != _contract_json_digest(
        [
            {"scenarioId": record["scenarioId"], "digest": record["fixtureDigest"]}
            for record in roots
        ]
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    fixture_set = {
        "descriptorSetDigest": descriptor_set_digest,
        "fixtureSetDigest": fixture_set_digest,
        "descriptors": descriptors,
        "materializedRoots": roots,
    }

    raw_model_fixture = value.get("modelFixture")
    if (
        not isinstance(raw_model_fixture, dict)
        or set(raw_model_fixture) != _MODEL_FIXTURE_KEYS
        or raw_model_fixture.get("scenarioId") != SCENARIO_ID
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    model_fixture = {
        "scenarioId": SCENARIO_ID,
        "descriptorDigest": _required_nonzero_digest(
            raw_model_fixture.get("descriptorDigest")
        ),
        "fixtureDigest": _required_nonzero_digest(
            raw_model_fixture.get("fixtureDigest")
        ),
    }
    if (
        model_fixture["descriptorDigest"] != descriptors[-1]["descriptorDigest"]
        or model_fixture["fixtureDigest"] != roots[-1]["fixtureDigest"]
    ):
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        )
    return {
        "schema": schema,
        "version": version,
        "sourceCommit": source_commit,
        "scenarioId": scenario_id,
        "buildPolicy": dict(build_policy),
        "roles": roles,
        "sources": sources,
        "bridgeTargetRuntime": normalized_runtime,
        "releaseArtifacts": release_artifacts,
        "packageTrees": package_trees,
        "dependencySet": dependency_set,
        "fixtureSet": fixture_set,
        "modelFixture": model_fixture,
    }


def _parse_source_manifest(raw_content: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw_content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (_DuplicateJsonKey, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_invalid"
        ) from exc
    document = _validate_source_document(parsed)
    if raw_content != canonical_json_bytes(document) + b"\n":
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_manifest_noncanonical"
        )
    return document


def _safe_remove_created(path: Path, identity: tuple[int, ...] | None) -> None:
    if identity is None:
        return
    try:
        current = path.lstat()
        if _durable_identity(current) == identity:
            path.unlink()
    except OSError:
        pass


def _write_create_new(path: Path, content: bytes) -> tuple[int, ...]:
    descriptor: int | None = None
    identity: tuple[int, ...] | None = None
    try:
        descriptor = os.open(path, _open_flags(writable=True, create_new=True), 0o600)
    except FileExistsError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_target_exists"
        ) from exc
    except OSError as exc:
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_write_failed"
        ) from exc
    failure: ProtectedRuntimeSourceManifestError | None = None
    try:
        opened = os.fstat(descriptor)
        identity = _durable_identity(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_nlink) != 1
            or int(getattr(opened, "st_file_attributes", 0) or 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
            or int(opened.st_size) != 0
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_write_failed"
            )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0 or written > len(content) - offset:
                raise ProtectedRuntimeSourceManifestError(
                    "protected_runtime_source_write_failed"
                )
            offset += written
        os.fsync(descriptor)
        after_write = os.fstat(descriptor)
        if (
            _durable_identity(after_write) != identity
            or int(after_write.st_size) != len(content)
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_write_failed"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = bytearray()
        while len(readback) < len(content):
            chunk = os.read(descriptor, len(content) - len(readback))
            if not chunk:
                break
            readback.extend(chunk)
        if bytes(readback) != content:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_write_failed"
            )
        current = path.lstat()
        if (
            _durable_identity(current) != identity
            or int(current.st_size) != len(content)
            or _is_link_or_reparse(path, current)
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_write_failed"
            )
    except ProtectedRuntimeSourceManifestError as exc:
        failure = exc
    except OSError as exc:
        failure = ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_write_failed"
        )
        failure.__cause__ = exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                if failure is None:
                    failure = ProtectedRuntimeSourceManifestError(
                        "protected_runtime_source_write_failed"
                    )
                    failure.__cause__ = exc
    if failure is not None:
        _safe_remove_created(path, identity)
        raise failure
    if identity is None:
        _safe_remove_created(path, identity)
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_write_failed"
        )
    return identity


def _receipt(mode: str, content: bytes, document: Mapping[str, Any]) -> dict[str, Any]:
    runtime = document["bridgeTargetRuntime"]
    return {
        "ok": True,
        "schema": SOURCE_RECEIPT_SCHEMA,
        "mode": mode,
        "manifestSha256": hashlib.sha256(content).hexdigest(),
        "roleCount": len(document["roles"]),
        "sourceCount": len(document["sources"]),
        "releaseArtifactCount": len(document["releaseArtifacts"]),
        "packageTreeCount": len(document["packageTrees"]),
        "fixtureDescriptorCount": len(document["fixtureSet"]["descriptors"]),
        "fixtureRootCount": len(document["fixtureSet"]["materializedRoots"]),
        "bridgeManifestSha256": runtime["manifestSha256"],
        "bridgeExecutableSha256": runtime["executableSha256"],
        "bridgeTreeDigest": runtime["treeDigest"],
    }


def create_source_manifest(
    source_manifest: os.PathLike[str] | str,
    *,
    version: str,
    source_commit: str,
    paths: ProtectedRuntimeSourcePaths,
) -> dict[str, Any]:
    """Create one new canonical source manifest without replacing a target."""

    _validate_version_and_commit(version, source_commit)
    target = _resolve_output_path(source_manifest)
    if target.exists() or target.is_symlink():
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_target_exists"
        )
    snapshot = _open_source_snapshot(
        paths,
        version=version,
        source_commit=source_commit,
        archive_binding_error_code="protected_runtime_source_input_invalid",
    )
    created_identity: tuple[int, ...] | None = None
    try:
        if _is_within(target, snapshot.bridge_tree) or any(
            _is_within(target, tree.path) for tree in snapshot.tree_snapshots
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_self_reference_forbidden"
            )
        document = _validate_source_document(
            _build_document(snapshot, version, source_commit)
        )
        content = canonical_json_bytes(document) + b"\n"
        if len(content) > MAX_SOURCE_MANIFEST_BYTES:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_invalid"
            )
        snapshot.verify_unchanged()
        created_identity = _write_create_new(target, content)
        try:
            readback = _HeldFile.open(target, MAX_SOURCE_MANIFEST_BYTES)
            try:
                raw_readback, _digest = readback.read()
                if raw_readback != content or _parse_source_manifest(raw_readback) != document:
                    raise ProtectedRuntimeSourceManifestError(
                        "protected_runtime_source_write_failed"
                    )
            finally:
                readback.close()
            snapshot.verify_unchanged()
        except BaseException:
            _safe_remove_created(target, created_identity)
            raise
        return _receipt("create", content, document)
    finally:
        snapshot.close()


def verify_source_manifest(
    source_manifest: os.PathLike[str] | str,
    *,
    version: str,
    source_commit: str,
    paths: ProtectedRuntimeSourcePaths,
) -> dict[str, Any]:
    """Verify canonical manifest bytes against every fixed live input."""

    _validate_version_and_commit(version, source_commit)
    target = _resolve_existing_path(source_manifest, directory=False)
    snapshot = _open_source_snapshot(
        paths,
        version=version,
        source_commit=source_commit,
        archive_binding_error_code="protected_runtime_source_manifest_mismatch",
    )
    manifest_file: _HeldFile | None = None
    try:
        if _is_within(target, snapshot.bridge_tree) or any(
            _is_within(target, tree.path) for tree in snapshot.tree_snapshots
        ):
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_self_reference_forbidden"
            )
        manifest_file = _HeldFile.open(target, MAX_SOURCE_MANIFEST_BYTES)
        occupied = {
            held.identity_key
            for held in (
                *snapshot.role_files,
                *snapshot.source_files,
                *snapshot.release_files,
                snapshot.dependency_file,
                *snapshot.fixture_descriptor_files,
                snapshot.bridge_manifest_file,
            )
        }
        if manifest_file.identity_key in occupied:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_duplicate_identity"
            )
        raw_content, _digest = manifest_file.read()
        document = _parse_source_manifest(raw_content)
        expected = _validate_source_document(
            _build_document(snapshot, version, source_commit)
        )
        if document != expected:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_mismatch"
            )
        snapshot.verify_unchanged()
        final_content, _final_digest = manifest_file.read()
        if final_content != raw_content:
            raise ProtectedRuntimeSourceManifestError(
                "protected_runtime_source_manifest_mismatch"
            )
        return _receipt("verify", raw_content, document)
    finally:
        if manifest_file is not None:
            manifest_file.close()
        snapshot.close()


class _FixedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ProtectedRuntimeSourceManifestError(
            "protected_runtime_source_cli_invalid"
        )


def _parser() -> argparse.ArgumentParser:
    parser = _FixedArgumentParser(
        description="Create or verify the fixed VRCForge protected-runtime source manifest."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--authority-service", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--desktop", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--unity", required=True)
    parser.add_argument("--bridge-launcher", required=True)
    parser.add_argument("--bridge-listener", required=True)
    parser.add_argument("--runtime-contract", required=True)
    parser.add_argument("--fixture-baseline", required=True)
    parser.add_argument("--bridge-tree", required=True)
    parser.add_argument("--bridge-manifest", required=True)
    parser.add_argument("--strict-release-manifest", required=True)
    parser.add_argument("--portable-archive", required=True)
    parser.add_argument("--unity-package", required=True)
    parser.add_argument("--backend-tree", required=True)
    parser.add_argument("--vrcforge-core-tree", required=True)
    parser.add_argument("--server-tree", required=True)
    parser.add_argument("--dependency-set-descriptor", required=True)
    for _field_name, scenario_id in FIXTURE_DESCRIPTOR_FIELDS:
        parser.add_argument(f"--{scenario_id.replace('_', '-')}-descriptor", required=True)
    for _field_name, scenario_id in FIXTURE_ROOT_FIELDS:
        parser.add_argument(f"--{scenario_id.replace('_', '-')}-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        paths = ProtectedRuntimeSourcePaths(
            authority_service=arguments.authority_service,
            driver=arguments.driver,
            desktop=arguments.desktop,
            backend=arguments.backend,
            unity=arguments.unity,
            bridge_launcher=arguments.bridge_launcher,
            bridge_listener=arguments.bridge_listener,
            runtime_contract=arguments.runtime_contract,
            fixture_baseline=arguments.fixture_baseline,
            bridge_tree=arguments.bridge_tree,
            bridge_manifest=arguments.bridge_manifest,
            strict_release_manifest=arguments.strict_release_manifest,
            portable_archive=arguments.portable_archive,
            unity_package=arguments.unity_package,
            backend_tree=arguments.backend_tree,
            vrcforge_core_tree=arguments.vrcforge_core_tree,
            server_tree=arguments.server_tree,
            dependency_set_descriptor=arguments.dependency_set_descriptor,
            component_feature_application_descriptor=(
                arguments.component_feature_application_descriptor
            ),
            parameter_optimization_descriptor=(
                arguments.parameter_optimization_descriptor
            ),
            cross_avatar_accessory_copy_descriptor=(
                arguments.cross_avatar_accessory_copy_descriptor
            ),
            model_part_composition_descriptor=(
                arguments.model_part_composition_descriptor
            ),
            component_feature_application_root=(
                arguments.component_feature_application_root
            ),
            parameter_optimization_root=arguments.parameter_optimization_root,
            cross_avatar_accessory_copy_root=(
                arguments.cross_avatar_accessory_copy_root
            ),
            model_part_composition_root=arguments.model_part_composition_root,
        )
        operation = create_source_manifest if arguments.create else verify_source_manifest
        result = operation(
            arguments.source_manifest,
            version=arguments.version,
            source_commit=arguments.source_commit,
            paths=paths,
        )
    except ProtectedRuntimeSourceManifestError as exc:
        print(
            canonical_json_bytes({"ok": False, "code": exc.code}).decode("utf-8"),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            canonical_json_bytes(
                {"ok": False, "code": "protected_runtime_source_internal_failure"}
            ).decode("utf-8"),
            file=sys.stderr,
        )
        return 1
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
