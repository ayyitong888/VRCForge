"""Verify the exact frozen bridge runtime tree before connector import."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


BRIDGE_TARGET_MANIFEST_NAME = "bridge-target-manifest.json"
BRIDGE_TARGET_RUNTIME_TREE_NAME = "bridge_target"
BRIDGE_TARGET_RUNTIME_HOME_NAME = "_internal"
BRIDGE_TARGET_EXECUTABLE_NAME = "vrcforge_bridge_target.exe"
MANIFEST_SCHEMA = "vrcforge.bridge_target_tree_manifest.v1"
TREE_DIGEST_DOMAIN = b"vrcforge.bridge_target_tree_manifest.tree.v1\0"
HASH_ALGORITHM = "sha256"
MAX_ENTRY_COUNT = 200_000
MAX_TREE_BYTES = 16 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "schema",
    "algorithm",
    "directoryCount",
    "directories",
    "entryCount",
    "byteCount",
    "files",
    "treeDigest",
}
_FILE_KEYS = {"path", "length", "sha256"}
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    "CONIN$",
    "CONOUT$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
    "COM\u00b9",
    "COM\u00b2",
    "COM\u00b3",
    "LPT\u00b9",
    "LPT\u00b2",
    "LPT\u00b3",
}


class BridgeTargetRuntimeVerificationError(RuntimeError):
    """Raised when the frozen dependency tree is not exact and stable."""


class _DuplicateJsonKey(ValueError):
    pass


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest data is not canonical JSON"
        ) from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _normalize_relative_path(raw_path: object) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest path is invalid"
        )
    if (
        "\x00" in raw_path
        or "\\" in raw_path
        or raw_path.startswith(("/", "//"))
        or _DRIVE_PATH_RE.match(raw_path)
        or unicodedata.normalize("NFC", raw_path) != raw_path
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest path is not canonical"
        )
    parts = raw_path.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest path is unsafe"
        )
    for part in parts:
        if (
            any(ord(character) < 32 or ord(character) == 127 for character in part)
            or any(unicodedata.category(character) == "Cs" for character in part)
            or ":" in part
            or any(character in '<>"|?*' for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target manifest path is unsafe"
            )
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != raw_path:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest path is not canonical"
        )
    return normalized


def _register_path_claim(
    relative_path: str,
    leaf_kind: str,
    claims: dict[str, tuple[str, str]],
) -> None:
    parts = relative_path.split("/")
    for index in range(1, len(parts) + 1):
        spelling = "/".join(parts[:index])
        kind = leaf_kind if index == len(parts) else "directory"
        key = unicodedata.normalize("NFC", spelling).casefold()
        previous = claims.get(key)
        if previous is None:
            claims[key] = (spelling, kind)
            continue
        previous_spelling, previous_kind = previous
        if previous_spelling != spelling or previous_kind != kind or kind != "directory":
            raise BridgeTargetRuntimeVerificationError(
                "bridge target manifest path claims collide"
            )


def _compute_tree_digest(
    files: Sequence[Mapping[str, Any]], directories: Sequence[str]
) -> str:
    projection = [
        {
            "path": item["path"],
            "length": item["length"],
            "sha256": item["sha256"],
        }
        for item in files
    ]
    digest = hashlib.sha256()
    digest.update(TREE_DIGEST_DOMAIN)
    digest.update(
        _canonical_json_bytes(
            {
                "schema": MANIFEST_SCHEMA,
                "directories": list(directories),
                "files": projection,
            }
        )
    )
    return digest.hexdigest()


def _validate_manifest_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest shape is invalid"
        )
    if value.get("schema") != MANIFEST_SCHEMA or value.get("algorithm") != HASH_ALGORITHM:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest format is invalid"
        )
    directory_count = value.get("directoryCount")
    entry_count = value.get("entryCount")
    byte_count = value.get("byteCount")
    if (
        isinstance(directory_count, bool)
        or not isinstance(directory_count, int)
        or directory_count < 0
        or directory_count > MAX_ENTRY_COUNT
        or isinstance(entry_count, bool)
        or not isinstance(entry_count, int)
        or entry_count < 0
        or entry_count > MAX_ENTRY_COUNT
        or directory_count + entry_count > MAX_ENTRY_COUNT
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count < 0
        or byte_count > MAX_TREE_BYTES
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest counts are invalid"
        )

    raw_directories = value.get("directories")
    if not isinstance(raw_directories, list) or len(raw_directories) != directory_count:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest directories are invalid"
        )
    directories = [_normalize_relative_path(path) for path in raw_directories]
    if directories != sorted(directories) or len(set(directories)) != len(directories):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest directories are not canonical"
        )

    claims: dict[str, tuple[str, str]] = {}
    for path in directories:
        _register_path_claim(path, "directory", claims)
    directory_set = set(directories)
    if any(
        (parent := path.rpartition("/")[0]) and parent not in directory_set
        for path in directories
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest directory tree is incomplete"
        )

    raw_files = value.get("files")
    if not isinstance(raw_files, list) or len(raw_files) != entry_count:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest files are invalid"
        )
    files: list[dict[str, Any]] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != _FILE_KEYS:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target manifest file record is invalid"
            )
        path = _normalize_relative_path(raw_file.get("path"))
        _register_path_claim(path, "file", claims)
        parent = path.rpartition("/")[0]
        if parent and parent not in directory_set:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target manifest directory tree is incomplete"
            )
        length = raw_file.get("length")
        sha256 = raw_file.get("sha256")
        if (
            isinstance(length, bool)
            or not isinstance(length, int)
            or length < 0
            or not isinstance(sha256, str)
            or not _LOWER_SHA256_RE.fullmatch(sha256)
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target manifest file record is invalid"
            )
        files.append({"path": path, "length": length, "sha256": sha256})

    if [item["path"] for item in files] != sorted(item["path"] for item in files):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest files are not canonical"
        )
    if sum(item["length"] for item in files) != byte_count:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest byte count is invalid"
        )
    tree_digest = value.get("treeDigest")
    if (
        not isinstance(tree_digest, str)
        or not _LOWER_SHA256_RE.fullmatch(tree_digest)
        or tree_digest != _compute_tree_digest(files, directories)
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest tree digest is invalid"
        )
    return {
        "schema": MANIFEST_SCHEMA,
        "algorithm": HASH_ALGORITHM,
        "directoryCount": directory_count,
        "directories": directories,
        "entryCount": entry_count,
        "byteCount": byte_count,
        "files": files,
        "treeDigest": tree_digest,
    }


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


def _lstat(path: Path) -> os.stat_result:
    return os.lstat(path)


def _is_link_or_reparse(path: Path, metadata: Any | None = None) -> bool:
    value = metadata if metadata is not None else _lstat(path)
    if stat.S_ISLNK(int(getattr(value, "st_mode", 0) or 0)):
        return True
    if int(getattr(value, "st_file_attributes", 0) or 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction(path))
    except OSError:
        return True


def _has_alternate_data_stream(path: Path) -> bool:
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    class FindStreamData(ctypes.Structure):
        _fields_ = [
            ("stream_size", ctypes.c_longlong),
            ("stream_name", ctypes.c_wchar * 296),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(FindStreamData),
        wintypes.DWORD,
    ]
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(FindStreamData)]
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL

    data = FindStreamData()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in {2, 38}:
            return False
        raise BridgeTargetRuntimeVerificationError(
            "bridge target alternate data streams could not be inspected"
        )
    names: list[str] = []
    try:
        names.append(str(data.stream_name))
        while find_next(handle, ctypes.byref(data)):
            names.append(str(data.stream_name))
        if ctypes.get_last_error() != 38:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target alternate data streams could not be inspected"
            )
    finally:
        find_close(handle)
    return any(name != "::$DATA" for name in names)


def _validate_directory(path: Path) -> os.stat_result:
    try:
        metadata = _lstat(path)
    except OSError as exc:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime directory is unavailable"
        ) from exc
    if (
        _is_link_or_reparse(path, metadata)
        or not stat.S_ISDIR(int(getattr(metadata, "st_mode", 0) or 0))
        or _has_alternate_data_stream(path)
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime directory is unsafe"
        )
    return metadata


def _validate_regular_file(path: Path, metadata: Any) -> None:
    if (
        _is_link_or_reparse(path, metadata)
        or not stat.S_ISREG(int(getattr(metadata, "st_mode", 0) or 0))
        or int(getattr(metadata, "st_nlink", 0) or 0) != 1
        or _has_alternate_data_stream(path)
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime leaf is unsafe"
        )


def _open_flags() -> int:
    return (
        os.O_RDONLY
        | int(getattr(os, "O_BINARY", 0) or 0)
        | int(getattr(os, "O_NOINHERIT", 0) or 0)
        | int(getattr(os, "O_NOFOLLOW", 0) or 0)
    )


@dataclass(slots=True)
class _HeldFile:
    path: Path
    descriptor: int
    identity: tuple[int, ...]
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def open(cls, path: Path) -> _HeldFile:
        descriptor: int | None = None
        try:
            before = _lstat(path)
            _validate_regular_file(path, before)
            descriptor = os.open(path, _open_flags())
            opened = os.fstat(descriptor)
            _validate_regular_file(path, opened)
            identity = _metadata_identity(opened)
            if _metadata_identity(before) != identity:
                raise BridgeTargetRuntimeVerificationError(
                    "bridge target runtime leaf changed while opening"
                )
            return cls(path=path, descriptor=descriptor, identity=identity)
        except BridgeTargetRuntimeVerificationError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf could not be opened"
            ) from exc

    def _verify_path_identity(self) -> os.stat_result:
        if self._closed:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime proof is closed"
            )
        try:
            opened = os.fstat(self.descriptor)
            current = _lstat(self.path)
            _validate_regular_file(self.path, opened)
            _validate_regular_file(self.path, current)
        except BridgeTargetRuntimeVerificationError:
            raise
        except OSError as exc:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf changed"
            ) from exc
        if (
            _metadata_identity(opened) != self.identity
            or _metadata_identity(current) != self.identity
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf identity drifted"
            )
        return opened

    def read(self, maximum_bytes: int) -> bytes:
        opened = self._verify_path_identity()
        if int(getattr(opened, "st_size", -1)) > maximum_bytes:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf exceeds its safety bound"
            )
        chunks: list[bytes] = []
        total = 0
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            while True:
                chunk = os.read(
                    self.descriptor,
                    min(HASH_CHUNK_BYTES, maximum_bytes + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    raise BridgeTargetRuntimeVerificationError(
                        "bridge target runtime leaf exceeds its safety bound"
                    )
        except BridgeTargetRuntimeVerificationError:
            raise
        except OSError as exc:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf could not be read"
            ) from exc
        after = self._verify_path_identity()
        if total != int(getattr(after, "st_size", -1)):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf changed while reading"
            )
        return b"".join(chunks)

    def hash(self) -> tuple[int, bytes]:
        opened = self._verify_path_identity()
        if int(getattr(opened, "st_size", -1)) > MAX_TREE_BYTES:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf exceeds its safety bound"
            )
        digest = hashlib.sha256()
        total = 0
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            while chunk := os.read(self.descriptor, HASH_CHUNK_BYTES):
                digest.update(chunk)
                total += len(chunk)
        except OSError as exc:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf could not be hashed"
            ) from exc
        after = self._verify_path_identity()
        if (
            total != int(getattr(opened, "st_size", -1))
            or total != int(getattr(after, "st_size", -1))
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime leaf changed while hashing"
            )
        return total, digest.digest()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.descriptor)


def _hash_regular_file(path: Path) -> tuple[int, str]:
    held = _HeldFile.open(path)
    try:
        length, digest = held.hash()
        return length, digest.hex()
    finally:
        held.close()


def _scan_tree(root: Path) -> tuple[list[str], list[dict[str, Any]]]:
    directories: list[str] = []
    files: list[dict[str, Any]] = []
    claims: dict[str, tuple[str, str]] = {}
    total_bytes = 0

    def visit(directory: Path, relative_parts: tuple[str, ...]) -> None:
        nonlocal total_bytes
        before = _validate_directory(directory)
        try:
            with os.scandir(directory) as iterator:
                children = sorted(list(iterator), key=lambda entry: entry.name)
        except OSError as exc:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime directory could not be enumerated"
            ) from exc
        for child in children:
            child_parts = (*relative_parts, child.name)
            relative_path = _normalize_relative_path("/".join(child_parts))
            child_path = directory / child.name
            try:
                metadata = _lstat(child_path)
            except OSError as exc:
                raise BridgeTargetRuntimeVerificationError(
                    "bridge target runtime entry became unavailable"
                ) from exc
            if _is_link_or_reparse(child_path, metadata):
                raise BridgeTargetRuntimeVerificationError(
                    "bridge target runtime entry is a link or reparse point"
                )
            mode = int(getattr(metadata, "st_mode", 0) or 0)
            if stat.S_ISDIR(mode):
                _register_path_claim(relative_path, "directory", claims)
                if len(directories) + len(files) >= MAX_ENTRY_COUNT:
                    raise BridgeTargetRuntimeVerificationError(
                        "bridge target runtime exceeds its safety bound"
                    )
                directories.append(relative_path)
                visit(child_path, child_parts)
                continue
            if not stat.S_ISREG(mode):
                raise BridgeTargetRuntimeVerificationError(
                    "bridge target runtime leaf is not a regular file"
                )
            _register_path_claim(relative_path, "file", claims)
            length, sha256 = _hash_regular_file(child_path)
            total_bytes += length
            if (
                len(directories) + len(files) >= MAX_ENTRY_COUNT
                or total_bytes > MAX_TREE_BYTES
            ):
                raise BridgeTargetRuntimeVerificationError(
                    "bridge target runtime exceeds its safety bound"
                )
            files.append({"path": relative_path, "length": length, "sha256": sha256})
        after = _validate_directory(directory)
        if _metadata_identity(before) != _metadata_identity(after):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime directory changed while scanning"
            )

    visit(root, ())
    directories.sort()
    files.sort(key=lambda item: item["path"])
    return directories, files


def _build_manifest(root: Path) -> dict[str, Any]:
    directories, files = _scan_tree(root)
    return _validate_manifest_document(
        {
            "schema": MANIFEST_SCHEMA,
            "algorithm": HASH_ALGORITHM,
            "directoryCount": len(directories),
            "directories": directories,
            "entryCount": len(files),
            "byteCount": sum(item["length"] for item in files),
            "files": files,
            "treeDigest": _compute_tree_digest(files, directories),
        }
    )


def _parse_canonical_manifest(raw_content: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw_content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except _DuplicateJsonKey as exc:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest contains a duplicate key"
        ) from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest is not valid UTF-8 JSON"
        ) from exc
    document = _validate_manifest_document(parsed)
    if raw_content != _canonical_json_bytes(document) + b"\n":
        raise BridgeTargetRuntimeVerificationError(
            "bridge target manifest is not canonical JSON"
        )
    return document


def _valid_digest(value: object) -> bool:
    return isinstance(value, bytes) and len(value) == 32 and any(value)


def _exact_absolute_path(value: os.PathLike[str] | str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target executable path is not canonical"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target executable path is unavailable"
        ) from exc
    if os.path.normcase(str(candidate)) != os.path.normcase(str(resolved)):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target executable path drifted"
        )
    return resolved


@dataclass(slots=True)
class VerifiedBridgeTargetRuntimeDependencies:
    bridge_manifest_digest: bytes
    bridge_tree_digest: bytes
    adapter_executable_digest: bytes
    tree_root: Path
    runtime_home: Path
    manifest_path: Path
    executable_path: Path
    _manifest_document: dict[str, Any] = field(repr=False)
    _parent_identity: tuple[int, ...] = field(repr=False)
    _tree_identity: tuple[int, ...] = field(repr=False)
    _runtime_home_identity: tuple[int, ...] = field(repr=False)
    _manifest_file: _HeldFile = field(repr=False)
    _executable_file: _HeldFile = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _verification_count: int = field(default=0, init=False, repr=False)

    @property
    def verification_count(self) -> int:
        return self._verification_count

    def verify_unchanged(self) -> None:
        if self._closed:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime proof is closed"
            )
        if (
            _metadata_identity(_validate_directory(self.tree_root.parent))
            != self._parent_identity
            or _metadata_identity(_validate_directory(self.tree_root))
            != self._tree_identity
            or _metadata_identity(_validate_directory(self.runtime_home))
            != self._runtime_home_identity
            or _exact_absolute_path(self.executable_path) != self.executable_path
            or _exact_absolute_path(self.runtime_home) != self.runtime_home
            or self.runtime_home
            != self.tree_root / BRIDGE_TARGET_RUNTIME_HOME_NAME
            or self.manifest_path
            != self.tree_root.parent / BRIDGE_TARGET_MANIFEST_NAME
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime path identity drifted"
            )
        raw_manifest = self._manifest_file.read(MAX_MANIFEST_BYTES)
        manifest_document = _parse_canonical_manifest(raw_manifest)
        executable_length, executable_digest = self._executable_file.hash()
        if (
            hashlib.sha256(raw_manifest).digest() != self.bridge_manifest_digest
            or bytes.fromhex(manifest_document["treeDigest"])
            != self.bridge_tree_digest
            or executable_digest != self.adapter_executable_digest
            or manifest_document != self._manifest_document
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime binding drifted"
            )
        executable_records = [
            item
            for item in manifest_document["files"]
            if item["path"] == BRIDGE_TARGET_EXECUTABLE_NAME
        ]
        if (
            len(executable_records) != 1
            or executable_records[0]["length"] != executable_length
            or executable_records[0]["sha256"] != executable_digest.hex()
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target executable manifest binding is invalid"
            )
        if _build_manifest(self.tree_root) != manifest_document:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime tree does not match its manifest"
            )
        if (
            _metadata_identity(_validate_directory(self.tree_root.parent))
            != self._parent_identity
            or _metadata_identity(_validate_directory(self.tree_root))
            != self._tree_identity
            or _metadata_identity(_validate_directory(self.runtime_home))
            != self._runtime_home_identity
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime path identity drifted"
            )
        self._manifest_file._verify_path_identity()
        self._executable_file._verify_path_identity()
        self._verification_count += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[OSError] = []
        for held in (self._manifest_file, self._executable_file):
            try:
                held.close()
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime proof cleanup failed"
            ) from errors[0]


def preflight_frozen_bridge_target_runtime(
    bridge_manifest_digest: bytes,
    bridge_tree_digest: bytes,
    adapter_executable_digest: bytes,
    *,
    executable_path: os.PathLike[str] | str | None = None,
    runtime_home: os.PathLike[str] | str | None = None,
    frozen: object | None = None,
) -> VerifiedBridgeTargetRuntimeDependencies:
    """Open, bind, and fully verify the current frozen runtime dependency set."""

    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen is not True:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime is not a frozen executable"
        )
    if not all(
        _valid_digest(value)
        for value in (
            bridge_manifest_digest,
            bridge_tree_digest,
            adapter_executable_digest,
        )
    ):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime parent binding is invalid"
        )
    executable = _exact_absolute_path(
        sys.executable if executable_path is None else executable_path
    )
    if executable.name != BRIDGE_TARGET_EXECUTABLE_NAME:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target executable leaf is invalid"
        )
    tree_root = executable.parent
    if tree_root.name != BRIDGE_TARGET_RUNTIME_TREE_NAME:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime tree path is invalid"
        )
    configured_runtime_home = (
        getattr(sys, "_MEIPASS", None) if runtime_home is None else runtime_home
    )
    if not isinstance(configured_runtime_home, (str, os.PathLike)):
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime home is unavailable"
        )
    try:
        resolved_runtime_home = _exact_absolute_path(configured_runtime_home)
    except BridgeTargetRuntimeVerificationError as exc:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime home is invalid"
        ) from exc
    if resolved_runtime_home != tree_root / BRIDGE_TARGET_RUNTIME_HOME_NAME:
        raise BridgeTargetRuntimeVerificationError(
            "bridge target runtime home is not bound to the executable tree"
        )
    manifest_path = tree_root.parent / BRIDGE_TARGET_MANIFEST_NAME
    parent_identity = _metadata_identity(_validate_directory(tree_root.parent))
    tree_identity = _metadata_identity(_validate_directory(tree_root))
    runtime_home_identity = _metadata_identity(
        _validate_directory(resolved_runtime_home)
    )

    manifest_file: _HeldFile | None = None
    executable_file: _HeldFile | None = None
    try:
        manifest_file = _HeldFile.open(manifest_path)
        executable_file = _HeldFile.open(executable)
        raw_manifest = manifest_file.read(MAX_MANIFEST_BYTES)
        manifest_document = _parse_canonical_manifest(raw_manifest)
        executable_length, executable_digest = executable_file.hash()
        executable_records = [
            item
            for item in manifest_document["files"]
            if item["path"] == BRIDGE_TARGET_EXECUTABLE_NAME
        ]
        if (
            hashlib.sha256(raw_manifest).digest() != bridge_manifest_digest
            or bytes.fromhex(manifest_document["treeDigest"]) != bridge_tree_digest
            or executable_digest != adapter_executable_digest
            or len(executable_records) != 1
            or executable_records[0]["length"] != executable_length
            or executable_records[0]["sha256"] != executable_digest.hex()
        ):
            raise BridgeTargetRuntimeVerificationError(
                "bridge target runtime parent binding does not match"
            )
        proof = VerifiedBridgeTargetRuntimeDependencies(
            bridge_manifest_digest=bridge_manifest_digest,
            bridge_tree_digest=bridge_tree_digest,
            adapter_executable_digest=adapter_executable_digest,
            tree_root=tree_root,
            runtime_home=resolved_runtime_home,
            manifest_path=manifest_path,
            executable_path=executable,
            _manifest_document=manifest_document,
            _parent_identity=parent_identity,
            _tree_identity=tree_identity,
            _runtime_home_identity=runtime_home_identity,
            _manifest_file=manifest_file,
            _executable_file=executable_file,
        )
        proof.verify_unchanged()
        manifest_file = None
        executable_file = None
        return proof
    except BaseException:
        if manifest_file is not None:
            manifest_file.close()
        if executable_file is not None:
            executable_file.close()
        raise
