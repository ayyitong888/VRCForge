"""Create or verify the fixed protected-runtime dependency-set descriptor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


_PACKAGING_ROOT = Path(__file__).resolve().parent
if str(_PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGING_ROOT))

import bridge_target_manifest


DEPENDENCY_SET_SCHEMA = "vrcforge.protected_runtime_dependency_set.v2"
DEPENDENCY_SET_RECEIPT_SCHEMA = (
    "vrcforge.protected_runtime_dependency_set_receipt.v2"
)
TREE_SOURCE_SCHEMA = "vrcforge.protected_runtime_tree_source.v1"
FIXTURE_DESCRIPTOR_SCHEMA = "vrcforge.primitive_basis_fixture.v1"
EXPECTED_UNITY_VERSION = "2022.3.22f1"
EXPECTED_UNITY_REVISION = "887be4894c44"

CONTRACT_DIGEST_DOMAIN = (
    b"vrcforge.protected_runtime_dependency_set.contract.v2\0"
)
SET_DIGEST_DOMAIN = b"vrcforge.protected_runtime_dependency_set.set.v2\0"

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 64 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_GENERATED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".vs",
        "__generated",
        "library",
        "logs",
        "obj",
        "temp",
        "usersettings",
    }
)

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

DIRECT_PACKAGE_VERSIONS: dict[str, str] = {
    "com.vrchat.avatars": "3.10.3",
    "com.vrchat.base": "3.10.3",
    "com.vrcfury.vrcfury": "1.1334.0",
    "nadena.dev.modular-avatar": "1.17.1",
    "nadena.dev.ndmf": "1.13.1",
}
_SHARED_DIRECT_PACKAGES = (
    "com.vrchat.avatars",
    "com.vrchat.base",
)
SCENARIO_PACKAGE_IDS: dict[str, tuple[str, ...]] = {
    "component_feature_application": (
        *_SHARED_DIRECT_PACKAGES,
        "com.vrcfury.vrcfury",
    ),
    "parameter_optimization": (
        *_SHARED_DIRECT_PACKAGES,
        "com.vrcfury.vrcfury",
        "nadena.dev.ndmf",
    ),
    "cross_avatar_accessory_copy": _SHARED_DIRECT_PACKAGES,
    "model_part_composition": (
        *_SHARED_DIRECT_PACKAGES,
        "nadena.dev.modular-avatar",
        "nadena.dev.ndmf",
    ),
}

# These digests bind the checked-in model fixture's exact manifest and complete
# lock graph semantically. A production input is the same fixed graph with the
# one feature package below added as a direct embedded package. This permits no
# undeclared package or dependency-edge drift.
_BASE_MANIFEST_SEMANTIC_DIGEST = (
    "efae25b9d4cdab91119b8677747fe71f5d8e77f0aa1da72aca280602fd86f709"
)
_BASE_LOCK_SEMANTIC_DIGEST = (
    "b4ab746524de7503159583c1726804c9e4cf8b16278f2509668e6a24066d66c4"
)
_FEATURE_PACKAGE_ID = "com.vrcfury.vrcfury"
_FEATURE_LOCK_ROW = {
    "version": f"file:{_FEATURE_PACKAGE_ID}",
    "depth": 0,
    "source": "embedded",
    "dependencies": {},
}
_BASE_PACKAGE_IDS = frozenset(
    {
        "com.unity.burst",
        "com.unity.collections",
        "com.unity.mathematics",
        "com.unity.nuget.mono-cecil",
        "com.unity.nuget.newtonsoft-json",
        "com.unity.postprocessing",
        "com.unity.timeline",
        "com.unity.ugui",
        "com.unity.xr.legacyinputhelpers",
        "com.unity.xr.management",
        "com.unity.xr.oculus",
        "com.vrchat.avatars",
        "com.vrchat.base",
        "nadena.dev.modular-avatar",
        "nadena.dev.ndmf",
        "com.unity.modules.ai",
        "com.unity.modules.androidjni",
        "com.unity.modules.animation",
        "com.unity.modules.assetbundle",
        "com.unity.modules.audio",
        "com.unity.modules.cloth",
        "com.unity.modules.director",
        "com.unity.modules.imageconversion",
        "com.unity.modules.imgui",
        "com.unity.modules.jsonserialize",
        "com.unity.modules.particlesystem",
        "com.unity.modules.physics",
        "com.unity.modules.physics2d",
        "com.unity.modules.screencapture",
        "com.unity.modules.subsystems",
        "com.unity.modules.terrain",
        "com.unity.modules.terrainphysics",
        "com.unity.modules.tilemap",
        "com.unity.modules.ui",
        "com.unity.modules.uielements",
        "com.unity.modules.umbra",
        "com.unity.modules.unityanalytics",
        "com.unity.modules.unitywebrequest",
        "com.unity.modules.unitywebrequestassetbundle",
        "com.unity.modules.unitywebrequestaudio",
        "com.unity.modules.unitywebrequesttexture",
        "com.unity.modules.unitywebrequestwww",
        "com.unity.modules.vehicles",
        "com.unity.modules.video",
        "com.unity.modules.vr",
        "com.unity.modules.wind",
        "com.unity.modules.xr",
    }
)
EXPECTED_PACKAGE_IDS = _BASE_PACKAGE_IDS | {_FEATURE_PACKAGE_ID}
_EXPECTED_CLOSURE_SEMANTIC_DIGEST = (
    "82edb338d73efd50b00d48f9639cd3fb5e15a556d731ce89f291a918f44f4d08"
)

_ROOT_KEYS = {
    "schema",
    "unity",
    "inputs",
    "packages",
    "scenarioRequirements",
    "editorBuiltins",
    "setDigest",
}
_UNITY_KEYS = {"version", "revision"}
_INPUT_KEYS = {"manifest", "packagesLock", "projectVersion"}
_FILE_RECORD_KEYS = {"sha256", "byteCount"}
_PACKAGE_KEYS = {
    "id",
    "version",
    "lockVersion",
    "source",
    "depth",
    "relativeRoot",
    "packageJsonSha256",
    "dependencies",
    "tree",
}
_DEPENDENCY_KEYS = {"id", "requestedVersion"}
_SCENARIO_KEYS = {
    "scenarioId",
    "descriptorSha256",
    "requiredPrimitives",
    "requiredPackages",
}
_REQUIRED_PACKAGE_KEYS = {"id", "version"}
_EDITOR_BUILTINS_KEYS = {"relativeRoot", "tree"}
_TREE_KEYS = {
    "schema",
    "treeDigest",
    "bindingDigest",
    "directoryCount",
    "entryCount",
    "byteCount",
}

_ERROR_CODES = frozenset(
    {
        "protected_runtime_dependency_cli_invalid",
        "protected_runtime_dependency_input_unavailable",
        "protected_runtime_dependency_input_invalid",
        "protected_runtime_dependency_duplicate_identity",
        "protected_runtime_dependency_descriptor_invalid",
        "protected_runtime_dependency_descriptor_noncanonical",
        "protected_runtime_dependency_descriptor_mismatch",
        "protected_runtime_dependency_target_exists",
        "protected_runtime_dependency_write_failed",
        "protected_runtime_dependency_internal_failure",
    }
)


class ProtectedRuntimeDependencySetError(RuntimeError):
    """Fixed-code failure that never includes caller-supplied paths."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            code = "protected_runtime_dependency_internal_failure"
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
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_internal_failure"
        ) from exc


def _contract_digest(value: Any) -> str:
    digest = hashlib.sha256()
    digest.update(CONTRACT_DIGEST_DOMAIN)
    digest.update(canonical_json_bytes(value))
    return digest.hexdigest()


def _set_digest(value: Mapping[str, Any]) -> str:
    projection = {key: item for key, item in value.items() if key != "setDigest"}
    digest = hashlib.sha256()
    digest.update(SET_DIGEST_DOMAIN)
    digest.update(canonical_json_bytes(projection))
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (_DuplicateJsonKey, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        ) from exc


def _metadata_identity(value: Any) -> tuple[int, ...]:
    return (
        int(getattr(value, "st_dev", 0) or 0),
        int(getattr(value, "st_ino", 0) or 0),
        stat.S_IFMT(int(getattr(value, "st_mode", 0) or 0)),
        int(getattr(value, "st_nlink", 0) or 0),
        int(getattr(value, "st_size", 0) or 0),
        int(getattr(value, "st_mtime_ns", 0) or 0),
        int(getattr(value, "st_file_attributes", 0) or 0),
    )


def _durable_identity(value: Any) -> tuple[int, int]:
    return (
        int(getattr(value, "st_dev", 0) or 0),
        int(getattr(value, "st_ino", 0) or 0),
    )


def _is_link_or_reparse(path: Path, metadata: Any) -> bool:
    if stat.S_ISLNK(int(getattr(metadata, "st_mode", 0) or 0)):
        return True
    if (
        int(getattr(metadata, "st_file_attributes", 0) or 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction(path))
    except OSError:
        return True


def _has_alternate_data_stream(path: Path) -> bool:
    try:
        return bool(bridge_target_manifest._has_alternate_data_stream(path))
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        ) from exc


def _validate_directory(path: Path) -> Any:
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_unavailable"
        ) from exc
    if (
        _is_link_or_reparse(path, value)
        or not stat.S_ISDIR(int(getattr(value, "st_mode", 0) or 0))
        or _has_alternate_data_stream(path)
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    return value


def _validate_regular_file(path: Path, value: Any) -> None:
    if (
        _is_link_or_reparse(path, value)
        or not stat.S_ISREG(int(getattr(value, "st_mode", 0) or 0))
        or int(getattr(value, "st_nlink", 0) or 0) != 1
        or _has_alternate_data_stream(path)
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )


def _resolve_directory(path: os.PathLike[str] | str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    _validate_directory(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_unavailable"
        ) from exc
    _validate_directory(resolved)
    if not resolved.is_absolute():
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    return resolved


@dataclass
class _HeldFile:
    path: Path
    descriptor: int
    identity: tuple[int, ...]
    maximum_bytes: int

    @classmethod
    def open(cls, path: Path, maximum_bytes: int = MAX_INPUT_BYTES) -> "_HeldFile":
        try:
            before = os.lstat(path)
            _validate_regular_file(path, before)
            if int(getattr(before, "st_size", -1)) > maximum_bytes:
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_input_invalid"
                )
            flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0) or 0)
            flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
            descriptor = os.open(path, flags)
        except ProtectedRuntimeDependencySetError:
            raise
        except OSError as exc:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_unavailable"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            _validate_regular_file(path, opened)
            if _metadata_identity(before) != _metadata_identity(opened):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_input_invalid"
                )
            return cls(
                path=path,
                descriptor=descriptor,
                identity=_metadata_identity(opened),
                maximum_bytes=maximum_bytes,
            )
        except BaseException:
            os.close(descriptor)
            raise

    @property
    def identity_key(self) -> tuple[int, int]:
        return self.identity[0], self.identity[1]

    def read(self) -> tuple[bytes, str]:
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            total = 0
            digest = hashlib.sha256()
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
                    raise ProtectedRuntimeDependencySetError(
                        "protected_runtime_dependency_input_invalid"
                    )
            self.verify_unchanged()
        except ProtectedRuntimeDependencySetError:
            raise
        except OSError as exc:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_unavailable"
            ) from exc
        if total != self.identity[4]:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        return b"".join(chunks), digest.hexdigest()

    def verify_unchanged(self) -> None:
        try:
            opened = os.fstat(self.descriptor)
            current = os.lstat(self.path)
            _validate_regular_file(self.path, opened)
            _validate_regular_file(self.path, current)
        except ProtectedRuntimeDependencySetError:
            raise
        except OSError as exc:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_unavailable"
            ) from exc
        if (
            _metadata_identity(opened) != self.identity
            or _metadata_identity(current) != self.identity
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_unavailable"
            ) from exc


@dataclass
class _TreeSnapshot:
    path: Path
    document: dict[str, Any]
    record: dict[str, Any]
    parent_identity: tuple[int, int]
    root_identity: tuple[int, ...]

    @property
    def identity_key(self) -> tuple[int, int]:
        return self.root_identity[0], self.root_identity[1]

    def verify_unchanged(self) -> None:
        parent = _validate_directory(self.path.parent)
        root = _validate_directory(self.path)
        if (
            _durable_identity(parent) != self.parent_identity
            or _metadata_identity(root) != self.root_identity
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        if _build_tree_document(self.path) != self.document:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        final_parent = _validate_directory(self.path.parent)
        final_root = _validate_directory(self.path)
        if (
            _durable_identity(final_parent) != self.parent_identity
            or _metadata_identity(final_root) != self.root_identity
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )


@dataclass(frozen=True)
class DependencySetPaths:
    project_root: Path
    descriptors_root: Path
    editor_builtins_root: Path
    package_roots: Mapping[str, Path]
    output: Path


def _reject_generated_paths(document: Mapping[str, Any]) -> None:
    paths = [*document["directories"], *(row["path"] for row in document["files"])]
    for path in paths:
        if any(part.casefold() in _GENERATED_DIRECTORY_NAMES for part in path.split("/")):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )


def _build_tree_document(path: Path) -> dict[str, Any]:
    try:
        document = bridge_target_manifest.build_manifest(path)
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        ) from exc
    if document["entryCount"] < 1 or document["byteCount"] < 1:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    _reject_generated_paths(document)
    return document


def _tree_record(document: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        {
            "path": row["path"],
            "size": row["length"],
            "sha256": row["sha256"],
        }
        for row in document["files"]
    ]
    try:
        binding_bytes = json.dumps(
            rows,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_internal_failure"
        ) from exc
    return {
        "schema": TREE_SOURCE_SCHEMA,
        "treeDigest": document["treeDigest"],
        "bindingDigest": hashlib.sha256(binding_bytes).hexdigest(),
        "directoryCount": document["directoryCount"],
        "entryCount": document["entryCount"],
        "byteCount": document["byteCount"],
    }


def _open_tree_snapshot(path: Path) -> _TreeSnapshot:
    parent_before = _validate_directory(path.parent)
    root_before = _validate_directory(path)
    document = _build_tree_document(path)
    parent_after = _validate_directory(path.parent)
    root_after = _validate_directory(path)
    if (
        _durable_identity(parent_before) != _durable_identity(parent_after)
        or _metadata_identity(root_before) != _metadata_identity(root_after)
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    snapshot = _TreeSnapshot(
        path=path,
        document=document,
        record=_tree_record(document),
        parent_identity=_durable_identity(parent_after),
        root_identity=_metadata_identity(root_after),
    )
    snapshot.verify_unchanged()
    return snapshot


def _normalize_relative_root(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    raw_parts = value.split("/")
    if (
        value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} or ":" in part for part in raw_parts)
        or PurePosixPath(*raw_parts).as_posix() != value
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    return value


def _logical_package_root(package_id: str, source: str, version: str) -> str:
    if source == "embedded":
        return f"Packages/{package_id}"
    if source == "registry":
        return f"PackageCache/{package_id}@{version}"
    if source == "builtin":
        return f"EditorBuiltins/{package_id}"
    raise ProtectedRuntimeDependencySetError(
        "protected_runtime_dependency_input_invalid"
    )


def _register_identity(
    identity: tuple[int, int],
    claims: set[tuple[int, int]],
) -> None:
    if identity == (0, 0) or identity in claims:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_duplicate_identity"
        )
    claims.add(identity)


def _validate_unity_project_version(raw: bytes) -> tuple[str, str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        ) from exc
    match = re.fullmatch(
        r"m_EditorVersion: ([^\r\n]+)\r?\n"
        r"m_EditorVersionWithRevision: ([^\s()]+) \(([0-9a-f]{12})\)\r?\n?",
        text,
    )
    if (
        match is None
        or match.group(1) != match.group(2)
        or match.group(1) != EXPECTED_UNITY_VERSION
        or match.group(3) != EXPECTED_UNITY_REVISION
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    return match.group(1), match.group(3)


def _require_string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    normalized: dict[str, str] = {}
    folded: set[str] = set()
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or _PACKAGE_ID_RE.fullmatch(key) is None
            or key.casefold() in folded
            or not isinstance(item, str)
            or not item
            or len(item) > 256
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        folded.add(key.casefold())
        normalized[key] = item
    return normalized


def _validate_manifest_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"dependencies"}:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    dependencies = _require_string_map(value.get("dependencies"))
    if set(DIRECT_PACKAGE_VERSIONS) - set(dependencies):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    for package_id in DIRECT_PACKAGE_VERSIONS:
        if dependencies.get(package_id) != f"file:{package_id}":
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
    baseline = {
        "dependencies": {
            key: item
            for key, item in dependencies.items()
            if key not in DIRECT_PACKAGE_VERSIONS
        }
    }
    if _contract_digest(baseline) != _BASE_MANIFEST_SEMANTIC_DIGEST:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    return {"dependencies": dict(sorted(dependencies.items()))}


def _validate_lock_row(package_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    source = value.get("source")
    expected_keys = {"version", "depth", "source", "dependencies"}
    if source == "registry":
        expected_keys.add("url")
    if set(value) != expected_keys or source not in {"embedded", "registry", "builtin"}:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    version = value.get("version")
    depth = value.get("depth")
    dependencies = _require_string_map(value.get("dependencies"))
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 256
        or isinstance(depth, bool)
        or not isinstance(depth, int)
        or depth < 0
        or depth > 32
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    if source == "embedded":
        if version != f"file:{package_id}":
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
    elif _VERSION_RE.fullmatch(version) is None:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    if source == "registry":
        url = value.get("url")
        if url != "https://packages.unity.com":
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
    return {
        "version": version,
        "depth": depth,
        "source": source,
        "dependencies": dict(sorted(dependencies.items())),
        **({"url": value["url"]} if source == "registry" else {}),
    }


def _validate_lock_document(
    value: Any,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"dependencies"}:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    raw_dependencies = value.get("dependencies")
    if not isinstance(raw_dependencies, dict):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    rows: dict[str, dict[str, Any]] = {}
    folded: set[str] = set()
    for package_id, row in raw_dependencies.items():
        if (
            not isinstance(package_id, str)
            or _PACKAGE_ID_RE.fullmatch(package_id) is None
            or package_id.casefold() in folded
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        folded.add(package_id.casefold())
        rows[package_id] = _validate_lock_row(package_id, row)
    if rows.get(_FEATURE_PACKAGE_ID) != _FEATURE_LOCK_ROW:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    baseline_rows = dict(rows)
    del baseline_rows[_FEATURE_PACKAGE_ID]
    if _contract_digest({"dependencies": baseline_rows}) != _BASE_LOCK_SEMANTIC_DIGEST:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )

    manifest_dependencies = manifest["dependencies"]
    depth_zero = {package_id for package_id, row in rows.items() if row["depth"] == 0}
    if depth_zero != set(manifest_dependencies):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )

    pending = list(sorted(depth_zero))
    reached: set[str] = set()
    calculated_depth = {package_id: 0 for package_id in depth_zero}
    while pending:
        package_id = pending.pop(0)
        reached.add(package_id)
        parent_depth = calculated_depth[package_id]
        for dependency_id in rows[package_id]["dependencies"]:
            if dependency_id not in rows:
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_input_invalid"
                )
            candidate_depth = parent_depth + 1
            previous = calculated_depth.get(dependency_id)
            if previous is None or candidate_depth < previous:
                calculated_depth[dependency_id] = candidate_depth
                pending.append(dependency_id)
    if reached != set(rows):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    if any(rows[package_id]["depth"] != calculated_depth[package_id] for package_id in rows):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    return {"dependencies": dict(sorted(rows.items()))}


def _validate_fixture_descriptor(raw: bytes, scenario_id: str) -> dict[str, Any]:
    value = _parse_json(raw)
    expected_keys = {
        "schema",
        "scenarioId",
        "fixtureRoot",
        "baselineManifest",
        "expectedBaselineDigest",
        "expectedTreeDigest",
        "requiredPrimitives",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema") != FIXTURE_DESCRIPTOR_SCHEMA
        or value.get("scenarioId") != scenario_id
        or value.get("fixtureRoot")
        != f"Assets/VRCForge/PrimitiveBasis/{scenario_id}"
        or value.get("baselineManifest") != "baseline.json"
        or value.get("requiredPrimitives") != list(SCENARIO_DEFINITIONS[scenario_id])
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    for key in ("expectedBaselineDigest", "expectedTreeDigest"):
        digest = value.get(key)
        if not isinstance(digest, str) or (
            digest and _LOWER_SHA256_RE.fullmatch(digest) is None
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
    return value


def _validate_package_json(
    raw: bytes,
    *,
    package_id: str,
    lock_row: Mapping[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    value = _parse_json(raw)
    if not isinstance(value, dict):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    version = value.get("version")
    if (
        value.get("name") != package_id
        or not isinstance(version, str)
        or _VERSION_RE.fullmatch(version) is None
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    if lock_row["source"] == "embedded":
        if DIRECT_PACKAGE_VERSIONS.get(package_id) != version:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
    elif lock_row["version"] != version:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    package_dependencies = _require_string_map(value.get("dependencies", {}))
    if package_dependencies != lock_row["dependencies"]:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    return version, [
        {"id": dependency_id, "requestedVersion": requested_version}
        for dependency_id, requested_version in sorted(package_dependencies.items())
    ]


def _validate_editor_builtin_layout(
    snapshot: _TreeSnapshot,
    builtin_ids: set[str],
) -> None:
    top_level_directories = {
        path.split("/", 1)[0] for path in snapshot.document["directories"]
    }
    if top_level_directories != builtin_ids:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_input_invalid"
        )
    for row in snapshot.document["files"]:
        if "/" not in row["path"] or row["path"].split("/", 1)[0] not in builtin_ids:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )


def _resolve_inputs(paths: DependencySetPaths) -> DependencySetPaths:
    project_root = _resolve_directory(paths.project_root)
    descriptors_root = _resolve_directory(paths.descriptors_root)
    editor_builtins_root = _resolve_directory(paths.editor_builtins_root)
    package_roots: dict[str, Path] = {}
    folded: set[str] = set()
    for package_id, raw_root in paths.package_roots.items():
        if (
            not isinstance(package_id, str)
            or _PACKAGE_ID_RE.fullmatch(package_id) is None
            or package_id.casefold() in folded
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        folded.add(package_id.casefold())
        package_roots[package_id] = _resolve_directory(raw_root)
    output = _resolve_output_target(paths.output)
    return DependencySetPaths(
        project_root=project_root,
        descriptors_root=descriptors_root,
        editor_builtins_root=editor_builtins_root,
        package_roots=package_roots,
        output=output,
    )


def _resolve_output_target(path: os.PathLike[str] | str) -> Path:
    candidate = Path(path)
    if candidate.name in {"", ".", ".."} or candidate.suffix.casefold() != ".json":
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_cli_invalid"
        )
    for index, part in enumerate(candidate.parts):
        if index == 0 and part == candidate.anchor:
            continue
        if ":" in part:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_cli_invalid"
            )
    parent = _resolve_directory(candidate.parent)
    target = parent / candidate.name
    if _has_alternate_data_stream(target):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_cli_invalid"
        )
    return target


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class _DependencySnapshot:
    document: dict[str, Any]
    held_records: list[tuple[_HeldFile, int, str]]
    tree_snapshots: list[_TreeSnapshot]

    def verify_unchanged(self) -> None:
        for held, expected_length, expected_digest in self.held_records:
            raw, digest = held.read()
            if len(raw) != expected_length or digest != expected_digest:
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_input_invalid"
                )
        for tree in self.tree_snapshots:
            tree.verify_unchanged()

    def close(self) -> None:
        first_error: ProtectedRuntimeDependencySetError | None = None
        for held, _expected_length, _expected_digest in self.held_records:
            try:
                held.close()
            except ProtectedRuntimeDependencySetError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def _open_dependency_snapshot(paths: DependencySetPaths) -> _DependencySnapshot:
    resolved = _resolve_inputs(paths)
    if _is_within(resolved.output, resolved.project_root) or _is_within(
        resolved.output, resolved.descriptors_root
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_cli_invalid"
        )

    held_records: list[tuple[_HeldFile, int, str]] = []
    tree_snapshots: list[_TreeSnapshot] = []
    held_identities: set[tuple[int, int]] = set()
    tree_identities: set[tuple[int, int]] = set()

    def open_source(path: Path) -> tuple[bytes, str]:
        held = _HeldFile.open(path)
        try:
            _register_identity(held.identity_key, held_identities)
            raw, digest = held.read()
            held_records.append((held, len(raw), digest))
            return raw, digest
        except BaseException:
            held.close()
            raise

    try:
        manifest_raw, manifest_digest = open_source(
            resolved.project_root / "Packages" / "manifest.json"
        )
        lock_raw, lock_digest = open_source(
            resolved.project_root / "Packages" / "packages-lock.json"
        )
        project_version_raw, project_version_digest = open_source(
            resolved.project_root / "ProjectSettings" / "ProjectVersion.txt"
        )
        manifest = _validate_manifest_document(_parse_json(manifest_raw))
        lock = _validate_lock_document(_parse_json(lock_raw), manifest)
        unity_version, unity_revision = _validate_unity_project_version(
            project_version_raw
        )

        scenario_requirements: list[dict[str, Any]] = []
        for scenario_id in SCENARIO_ORDER:
            descriptor_raw, descriptor_digest = open_source(
                resolved.descriptors_root / f"{scenario_id}.json"
            )
            _validate_fixture_descriptor(descriptor_raw, scenario_id)
            scenario_requirements.append(
                {
                    "scenarioId": scenario_id,
                    "descriptorSha256": descriptor_digest,
                    "requiredPrimitives": list(SCENARIO_DEFINITIONS[scenario_id]),
                    "requiredPackages": [
                        {
                            "id": package_id,
                            "version": DIRECT_PACKAGE_VERSIONS[package_id],
                        }
                        for package_id in SCENARIO_PACKAGE_IDS[scenario_id]
                    ],
                }
            )

        lock_rows = lock["dependencies"]
        if set(lock_rows) != EXPECTED_PACKAGE_IDS:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )
        builtin_ids = {
            package_id
            for package_id, row in lock_rows.items()
            if row["source"] == "builtin"
        }
        non_builtin_ids = set(lock_rows) - builtin_ids
        if set(resolved.package_roots) != non_builtin_ids:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_input_invalid"
            )

        editor_snapshot = _open_tree_snapshot(resolved.editor_builtins_root)
        _register_identity(editor_snapshot.identity_key, tree_identities)
        _validate_editor_builtin_layout(editor_snapshot, builtin_ids)
        tree_snapshots.append(editor_snapshot)
        if _is_within(resolved.output, resolved.editor_builtins_root):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_cli_invalid"
            )

        package_records: list[dict[str, Any]] = []
        package_root_paths: list[Path] = []
        for package_id, lock_row in lock_rows.items():
            source = lock_row["source"]
            package_root = (
                resolved.editor_builtins_root / package_id
                if source == "builtin"
                else resolved.package_roots[package_id]
            )
            package_root = _resolve_directory(package_root)
            if source != "builtin":
                if any(
                    _is_within(package_root, previous)
                    or _is_within(previous, package_root)
                    for previous in package_root_paths
                ):
                    raise ProtectedRuntimeDependencySetError(
                        "protected_runtime_dependency_duplicate_identity"
                    )
                package_root_paths.append(package_root)
            if _is_within(resolved.output, package_root):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_cli_invalid"
                )
            snapshot = _open_tree_snapshot(package_root)
            _register_identity(snapshot.identity_key, tree_identities)
            tree_snapshots.append(snapshot)

            package_json_raw, package_json_digest = open_source(
                package_root / "package.json"
            )
            package_version, dependency_records = _validate_package_json(
                package_json_raw,
                package_id=package_id,
                lock_row=lock_row,
            )
            package_json_rows = [
                row for row in snapshot.document["files"] if row["path"] == "package.json"
            ]
            if (
                len(package_json_rows) != 1
                or package_json_rows[0]["length"] != len(package_json_raw)
                or package_json_rows[0]["sha256"] != package_json_digest
            ):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_input_invalid"
                )
            package_records.append(
                {
                    "id": package_id,
                    "version": package_version,
                    "lockVersion": lock_row["version"],
                    "source": source,
                    "depth": lock_row["depth"],
                    "relativeRoot": _logical_package_root(
                        package_id, source, package_version
                    ),
                    "packageJsonSha256": package_json_digest,
                    "dependencies": dependency_records,
                    "tree": dict(snapshot.record),
                }
            )

        package_records.sort(key=lambda row: row["id"])
        document: dict[str, Any] = {
            "schema": DEPENDENCY_SET_SCHEMA,
            "unity": {
                "version": unity_version,
                "revision": unity_revision,
            },
            "inputs": {
                "manifest": {
                    "sha256": manifest_digest,
                    "byteCount": len(manifest_raw),
                },
                "packagesLock": {
                    "sha256": lock_digest,
                    "byteCount": len(lock_raw),
                },
                "projectVersion": {
                    "sha256": project_version_digest,
                    "byteCount": len(project_version_raw),
                },
            },
            "packages": package_records,
            "scenarioRequirements": scenario_requirements,
            "editorBuiltins": {
                "relativeRoot": "EditorBuiltins",
                "tree": dict(editor_snapshot.record),
            },
        }
        document["setDigest"] = _set_digest(document)
        document = validate_dependency_set_document(document)
        snapshot = _DependencySnapshot(
            document=document,
            held_records=held_records,
            tree_snapshots=tree_snapshots,
        )
        snapshot.verify_unchanged()
        return snapshot
    except BaseException:
        first_error: BaseException | None = None
        for held, _length, _digest in held_records:
            try:
                held.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None and sys.exc_info()[0] is None:
            raise first_error
        raise


def _require_digest(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _LOWER_SHA256_RE.fullmatch(value) is None
        or set(value) == {"0"}
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    return value


def _require_count(value: Any, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    return value


def _validate_file_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FILE_RECORD_KEYS:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    normalized = {
        "sha256": _require_digest(value.get("sha256")),
        "byteCount": _require_count(value.get("byteCount"), MAX_INPUT_BYTES),
    }
    if normalized["byteCount"] < 1:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    return normalized


def _validate_tree_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TREE_KEYS:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    normalized = {
        "schema": value.get("schema"),
        "treeDigest": _require_digest(value.get("treeDigest")),
        "bindingDigest": _require_digest(value.get("bindingDigest")),
        "directoryCount": _require_count(
            value.get("directoryCount"), bridge_target_manifest.MAX_ENTRY_COUNT
        ),
        "entryCount": _require_count(
            value.get("entryCount"), bridge_target_manifest.MAX_ENTRY_COUNT
        ),
        "byteCount": _require_count(
            value.get("byteCount"), bridge_target_manifest.MAX_TREE_BYTES
        ),
    }
    if (
        normalized["schema"] != TREE_SOURCE_SCHEMA
        or normalized["entryCount"] < 1
        or normalized["byteCount"] < 1
        or normalized["directoryCount"] + normalized["entryCount"]
        > bridge_target_manifest.MAX_ENTRY_COUNT
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    return normalized


def validate_dependency_set_document(value: Any) -> dict[str, Any]:
    """Validate and copy one dependency-set descriptor into its exact shape."""

    if not isinstance(value, dict) or set(value) != _ROOT_KEYS:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    unity = value.get("unity")
    if (
        value.get("schema") != DEPENDENCY_SET_SCHEMA
        or not isinstance(unity, dict)
        or set(unity) != _UNITY_KEYS
        or unity.get("version") != EXPECTED_UNITY_VERSION
        or unity.get("revision") != EXPECTED_UNITY_REVISION
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    inputs = value.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != _INPUT_KEYS:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    normalized_inputs = {
        key: _validate_file_record(inputs.get(key))
        for key in ("manifest", "packagesLock", "projectVersion")
    }

    raw_packages = value.get("packages")
    if not isinstance(raw_packages, list) or len(raw_packages) != len(
        EXPECTED_PACKAGE_IDS
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    packages: list[dict[str, Any]] = []
    previous_id = ""
    observed_ids: set[str] = set()
    for raw_package in raw_packages:
        if not isinstance(raw_package, dict) or set(raw_package) != _PACKAGE_KEYS:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        package_id = raw_package.get("id")
        version = raw_package.get("version")
        lock_version = raw_package.get("lockVersion")
        source = raw_package.get("source")
        depth = raw_package.get("depth")
        if (
            not isinstance(package_id, str)
            or _PACKAGE_ID_RE.fullmatch(package_id) is None
            or (previous_id and package_id <= previous_id)
            or not isinstance(version, str)
            or _VERSION_RE.fullmatch(version) is None
            or not isinstance(lock_version, str)
            or not lock_version
            or source not in {"embedded", "registry", "builtin"}
            or isinstance(depth, bool)
            or not isinstance(depth, int)
            or depth < 0
            or depth > 32
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        previous_id = package_id
        observed_ids.add(package_id)
        if (
            (source == "embedded" and lock_version != f"file:{package_id}")
            or (source != "embedded" and lock_version != version)
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        relative_root = _normalize_relative_root(raw_package.get("relativeRoot"))
        if relative_root != _logical_package_root(package_id, source, version):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        raw_dependencies = raw_package.get("dependencies")
        if not isinstance(raw_dependencies, list):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        dependencies: list[dict[str, str]] = []
        previous_dependency_id = ""
        for raw_dependency in raw_dependencies:
            if (
                not isinstance(raw_dependency, dict)
                or set(raw_dependency) != _DEPENDENCY_KEYS
            ):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_descriptor_invalid"
                )
            dependency_id = raw_dependency.get("id")
            requested_version = raw_dependency.get("requestedVersion")
            if (
                not isinstance(dependency_id, str)
                or _PACKAGE_ID_RE.fullmatch(dependency_id) is None
                or (previous_dependency_id and dependency_id <= previous_dependency_id)
                or not isinstance(requested_version, str)
                or not requested_version
                or len(requested_version) > 256
            ):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_descriptor_invalid"
                )
            previous_dependency_id = dependency_id
            dependencies.append(
                {"id": dependency_id, "requestedVersion": requested_version}
            )
        packages.append(
            {
                "id": package_id,
                "version": version,
                "lockVersion": lock_version,
                "source": source,
                "depth": depth,
                "relativeRoot": relative_root,
                "packageJsonSha256": _require_digest(
                    raw_package.get("packageJsonSha256")
                ),
                "dependencies": dependencies,
                "tree": _validate_tree_record(raw_package.get("tree")),
            }
        )
    if observed_ids != EXPECTED_PACKAGE_IDS:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    for package in packages:
        if any(dependency["id"] not in observed_ids for dependency in package["dependencies"]):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
    indexed_packages = {package["id"]: package for package in packages}
    closure_projection = [
        {
            "id": package["id"],
            "lockVersion": package["lockVersion"],
            "source": package["source"],
            "depth": package["depth"],
            "dependencies": package["dependencies"],
        }
        for package in packages
    ]
    if _contract_digest(closure_projection) != _EXPECTED_CLOSURE_SEMANTIC_DIGEST:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    for package_id, version in DIRECT_PACKAGE_VERSIONS.items():
        package = indexed_packages[package_id]
        if package["version"] != version or package["source"] != "embedded":
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )

    raw_scenarios = value.get("scenarioRequirements")
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) != len(
        SCENARIO_ORDER
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    scenarios: list[dict[str, Any]] = []
    for scenario_id, raw_scenario in zip(
        SCENARIO_ORDER, raw_scenarios, strict=True
    ):
        if (
            not isinstance(raw_scenario, dict)
            or set(raw_scenario) != _SCENARIO_KEYS
            or raw_scenario.get("scenarioId") != scenario_id
            or raw_scenario.get("requiredPrimitives")
            != list(SCENARIO_DEFINITIONS[scenario_id])
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        raw_required = raw_scenario.get("requiredPackages")
        expected_ids = SCENARIO_PACKAGE_IDS[scenario_id]
        if not isinstance(raw_required, list) or len(raw_required) != len(expected_ids):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_invalid"
            )
        required: list[dict[str, str]] = []
        for package_id, raw_package in zip(expected_ids, raw_required, strict=True):
            if (
                not isinstance(raw_package, dict)
                or set(raw_package) != _REQUIRED_PACKAGE_KEYS
                or raw_package.get("id") != package_id
                or raw_package.get("version") != DIRECT_PACKAGE_VERSIONS[package_id]
            ):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_descriptor_invalid"
                )
            required.append(
                {"id": package_id, "version": DIRECT_PACKAGE_VERSIONS[package_id]}
            )
        scenarios.append(
            {
                "scenarioId": scenario_id,
                "descriptorSha256": _require_digest(
                    raw_scenario.get("descriptorSha256")
                ),
                "requiredPrimitives": list(SCENARIO_DEFINITIONS[scenario_id]),
                "requiredPackages": required,
            }
        )

    editor_builtins = value.get("editorBuiltins")
    if (
        not isinstance(editor_builtins, dict)
        or set(editor_builtins) != _EDITOR_BUILTINS_KEYS
        or editor_builtins.get("relativeRoot") != "EditorBuiltins"
    ):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    normalized: dict[str, Any] = {
        "schema": DEPENDENCY_SET_SCHEMA,
        "unity": {
            "version": EXPECTED_UNITY_VERSION,
            "revision": EXPECTED_UNITY_REVISION,
        },
        "inputs": normalized_inputs,
        "packages": packages,
        "scenarioRequirements": scenarios,
        "editorBuiltins": {
            "relativeRoot": "EditorBuiltins",
            "tree": _validate_tree_record(editor_builtins.get("tree")),
        },
        "setDigest": _require_digest(value.get("setDigest")),
    }
    if normalized["setDigest"] != _set_digest(normalized):
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        )
    return normalized


def _parse_descriptor(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (_DuplicateJsonKey, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_invalid"
        ) from exc
    document = validate_dependency_set_document(parsed)
    if raw != canonical_json_bytes(document) + b"\n":
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_descriptor_noncanonical"
        )
    return document


def _safe_remove_created(path: Path, identity: tuple[int, ...] | None) -> None:
    if identity is None:
        return
    try:
        current = os.lstat(path)
        if _metadata_identity(current) == identity:
            path.unlink()
    except OSError:
        pass


def _write_create_new(path: Path, content: bytes) -> tuple[int, ...]:
    descriptor: int | None = None
    identity: tuple[int, ...] | None = None
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | int(getattr(os, "O_BINARY", 0) or 0)
        )
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_target_exists"
        ) from exc
    except OSError as exc:
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_write_failed"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        identity = _metadata_identity(opened)
        if (
            not stat.S_ISREG(int(getattr(opened, "st_mode", 0) or 0))
            or int(getattr(opened, "st_nlink", 0) or 0) != 1
            or int(getattr(opened, "st_size", -1)) != 0
            or int(getattr(opened, "st_file_attributes", 0) or 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_write_failed"
            )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_write_failed"
                )
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            _durable_identity(after) != _durable_identity(opened)
            or int(getattr(after, "st_size", -1)) != len(content)
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_write_failed"
            )
        os.close(descriptor)
        descriptor = None
        current = os.lstat(path)
        _validate_regular_file(path, current)
        if _metadata_identity(current) != _metadata_identity(after):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_write_failed"
            )
        return _metadata_identity(current)
    except ProtectedRuntimeDependencySetError:
        _safe_remove_created(path, identity)
        raise
    except OSError as exc:
        _safe_remove_created(path, identity)
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_write_failed"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _receipt(document: Mapping[str, Any], raw: bytes, mode: str) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": mode,
        "schema": DEPENDENCY_SET_RECEIPT_SCHEMA,
        "descriptorSchema": document["schema"],
        "descriptorSha256": hashlib.sha256(raw).hexdigest(),
        "setDigest": document["setDigest"],
        "packageCount": len(document["packages"]),
        "scenarioCount": len(document["scenarioRequirements"]),
    }


def create_dependency_set(paths: DependencySetPaths) -> dict[str, Any]:
    """Create one canonical descriptor with CreateNew and live-input readback."""

    resolved_output = _resolve_output_target(paths.output)
    snapshot_paths = DependencySetPaths(
        project_root=paths.project_root,
        descriptors_root=paths.descriptors_root,
        editor_builtins_root=paths.editor_builtins_root,
        package_roots=paths.package_roots,
        output=resolved_output,
    )
    snapshot = _open_dependency_snapshot(snapshot_paths)
    created_identity: tuple[int, ...] | None = None
    try:
        raw = canonical_json_bytes(snapshot.document) + b"\n"
        created_identity = _write_create_new(resolved_output, raw)
        stored = _HeldFile.open(resolved_output, MAX_DESCRIPTOR_BYTES)
        try:
            stored_raw, stored_digest = stored.read()
            document = _parse_descriptor(stored_raw)
            if (
                stored_raw != raw
                or stored_digest != hashlib.sha256(raw).hexdigest()
                or document != snapshot.document
            ):
                raise ProtectedRuntimeDependencySetError(
                    "protected_runtime_dependency_write_failed"
                )
            snapshot.verify_unchanged()
            receipt = _receipt(document, stored_raw, "create")
        finally:
            stored.close()
        snapshot.close()
        snapshot = None
        return receipt
    except BaseException:
        _safe_remove_created(resolved_output, created_identity)
        if snapshot is not None:
            try:
                snapshot.close()
            except ProtectedRuntimeDependencySetError:
                pass
        raise


def verify_dependency_set(paths: DependencySetPaths) -> dict[str, Any]:
    """Verify a canonical descriptor against the current held dependency roots."""

    resolved_output = _resolve_output_target(paths.output)
    descriptor = _HeldFile.open(resolved_output, MAX_DESCRIPTOR_BYTES)
    snapshot: _DependencySnapshot | None = None
    try:
        raw, digest = descriptor.read()
        document = _parse_descriptor(raw)
        snapshot = _open_dependency_snapshot(paths)
        if document != snapshot.document:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_mismatch"
            )
        snapshot.verify_unchanged()
        reread, reread_digest = descriptor.read()
        if reread != raw or reread_digest != digest or _parse_descriptor(reread) != document:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_descriptor_mismatch"
            )
        return _receipt(document, raw, "verify")
    finally:
        if snapshot is not None:
            snapshot.close()
        descriptor.close()


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ProtectedRuntimeDependencySetError(
            "protected_runtime_dependency_cli_invalid"
        )


def _parse_package_roots(values: Sequence[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    folded: set[str] = set()
    for value in values:
        if not isinstance(value, str) or "=" not in value:
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_cli_invalid"
            )
        package_id, raw_path = value.split("=", 1)
        path = Path(raw_path)
        if (
            _PACKAGE_ID_RE.fullmatch(package_id) is None
            or package_id.casefold() in folded
            or not raw_path
            or not path.is_absolute()
        ):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_cli_invalid"
            )
        folded.add(package_id.casefold())
        roots[package_id] = path
    return roots


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(add_help=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--descriptors-root", required=True)
    parser.add_argument("--editor-builtins-root", required=True)
    parser.add_argument("--package-root", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        raw_paths = (
            arguments.output,
            arguments.project_root,
            arguments.descriptors_root,
            arguments.editor_builtins_root,
        )
        if any(not Path(path).is_absolute() for path in raw_paths):
            raise ProtectedRuntimeDependencySetError(
                "protected_runtime_dependency_cli_invalid"
            )
        paths = DependencySetPaths(
            project_root=Path(arguments.project_root),
            descriptors_root=Path(arguments.descriptors_root),
            editor_builtins_root=Path(arguments.editor_builtins_root),
            package_roots=_parse_package_roots(arguments.package_root),
            output=Path(arguments.output),
        )
        receipt = (
            create_dependency_set(paths)
            if arguments.create
            else verify_dependency_set(paths)
        )
        sys.stdout.buffer.write(canonical_json_bytes(receipt) + b"\n")
        return 0
    except ProtectedRuntimeDependencySetError as exc:
        failure = {"ok": False, "error": exc.code}
        sys.stderr.buffer.write(canonical_json_bytes(failure) + b"\n")
        return 2
    except BaseException:
        failure = {
            "ok": False,
            "error": "protected_runtime_dependency_internal_failure",
        }
        sys.stderr.buffer.write(canonical_json_bytes(failure) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
