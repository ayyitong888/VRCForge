"""Build and verify a deterministic manifest for one local bridge runtime tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


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


class BridgeTargetManifestError(RuntimeError):
    """Raised when a tree or manifest cannot be proven safe and exact."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical UTF-8 JSON representation used by this format."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return encoded.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BridgeTargetManifestError("Manifest data is not canonical JSON.") from exc


def compute_tree_digest(
    files: Sequence[Mapping[str, Any]],
    directories: Sequence[str] = (),
) -> str:
    """Hash canonical leaf records with an explicit format/domain prefix."""

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
        canonical_json_bytes(
            {
                "schema": MANIFEST_SCHEMA,
                "directories": list(directories),
                "files": projection,
            }
        )
    )
    return digest.hexdigest()


def _lstat(path: Path) -> os.stat_result:
    return os.lstat(path)


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
    """Return whether a Windows file or directory has a named data stream."""

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
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {2, 38}:
            return False
        raise BridgeTargetManifestError("Alternate data streams could not be inspected.")

    stream_names: list[str] = []
    try:
        stream_names.append(str(data.stream_name))
        while find_next(handle, ctypes.byref(data)):
            stream_names.append(str(data.stream_name))
        if ctypes.get_last_error() != 38:
            raise BridgeTargetManifestError("Alternate data streams could not be inspected.")
    finally:
        find_close(handle)
    return any(name != "::$DATA" for name in stream_names)


def _normalize_relative_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise BridgeTargetManifestError("A manifest path must be a non-empty string.")
    if "\x00" in raw_path or "\\" in raw_path:
        raise BridgeTargetManifestError("A manifest path is not canonical.")
    if raw_path.startswith("/") or raw_path.startswith("//") or _DRIVE_PATH_RE.match(raw_path):
        raise BridgeTargetManifestError("An absolute manifest path is forbidden.")
    if unicodedata.normalize("NFC", raw_path) != raw_path:
        raise BridgeTargetManifestError("A manifest path has non-canonical Unicode normalization.")

    raw_parts = raw_path.split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise BridgeTargetManifestError("A manifest path contains an unsafe segment.")
    for part in raw_parts:
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            raise BridgeTargetManifestError("A manifest path contains a control character.")
        if any(unicodedata.category(character) == "Cs" for character in part):
            raise BridgeTargetManifestError("A manifest path contains invalid Unicode.")
        if ":" in part:
            raise BridgeTargetManifestError("A manifest path names an alternate data stream.")
        if any(character in '<>"|?*' for character in part):
            raise BridgeTargetManifestError("A manifest path is not Windows-compatible.")
        if part.endswith((" ", ".")):
            raise BridgeTargetManifestError("A manifest path is not Windows-compatible.")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise BridgeTargetManifestError("A manifest path uses a reserved device name.")

    normalized = PurePosixPath(*raw_parts).as_posix()
    if normalized != raw_path:
        raise BridgeTargetManifestError("A manifest path is not canonical.")
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
            raise BridgeTargetManifestError(
                "A manifest path has a casefold, Unicode, duplicate, or type collision."
            )


def _validate_directory(path: Path) -> Any:
    try:
        metadata = _lstat(path)
    except OSError as exc:
        raise BridgeTargetManifestError("A tree directory is unavailable.") from exc
    if _is_link_or_reparse(path, metadata):
        raise BridgeTargetManifestError("A tree entry is a link or reparse point.")
    if not stat.S_ISDIR(int(getattr(metadata, "st_mode", 0) or 0)):
        raise BridgeTargetManifestError("A tree directory is not a directory.")
    if _has_alternate_data_stream(path):
        raise BridgeTargetManifestError("A tree entry has an alternate data stream.")
    return metadata


def _validate_regular_file(path: Path, metadata: Any) -> None:
    if _is_link_or_reparse(path, metadata):
        raise BridgeTargetManifestError("A tree entry is a link or reparse point.")
    if not stat.S_ISREG(int(getattr(metadata, "st_mode", 0) or 0)):
        raise BridgeTargetManifestError("A tree leaf is not a regular file.")
    if int(getattr(metadata, "st_nlink", 0) or 0) != 1:
        raise BridgeTargetManifestError("A tree leaf must have exactly one link.")
    if _has_alternate_data_stream(path):
        raise BridgeTargetManifestError("A tree entry has an alternate data stream.")


def _hash_regular_file(path: Path) -> tuple[int, str]:
    try:
        before = _lstat(path)
        _validate_regular_file(path, before)
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0) or 0)
        flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
        descriptor = os.open(path, flags)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("A tree leaf could not be opened.") from exc

    digest = hashlib.sha256()
    length = 0
    try:
        opened = os.fstat(descriptor)
        _validate_regular_file(path, opened)
        if _metadata_identity(before) != _metadata_identity(opened):
            raise BridgeTargetManifestError("A tree leaf changed while opening.")
        while True:
            chunk = os.read(descriptor, HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
        after_open = os.fstat(descriptor)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("A tree leaf could not be hashed.") from exc
    finally:
        os.close(descriptor)

    try:
        after_path = _lstat(path)
        _validate_regular_file(path, after_path)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("A tree leaf changed while hashing.") from exc
    if (
        _metadata_identity(before) != _metadata_identity(after_open)
        or _metadata_identity(after_open) != _metadata_identity(after_path)
        or length != int(getattr(after_open, "st_size", -1))
    ):
        raise BridgeTargetManifestError("A tree leaf changed while hashing.")
    return length, digest.hexdigest()


def _resolve_tree_root(tree_root: os.PathLike[str] | str) -> Path:
    candidate = Path(tree_root)
    _validate_directory(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BridgeTargetManifestError("The tree root is unavailable.") from exc
    _validate_directory(resolved)
    return resolved


def _manifest_outside_tree(tree_root: Path, manifest_path: os.PathLike[str] | str) -> Path:
    candidate = Path(manifest_path)
    for index, part in enumerate(candidate.parts):
        if index == 0 and part == candidate.anchor:
            continue
        if ":" in part:
            raise BridgeTargetManifestError("The manifest cannot be an alternate data stream.")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise BridgeTargetManifestError("The manifest location is invalid.") from exc
    try:
        resolved.relative_to(tree_root)
    except ValueError:
        return resolved
    raise BridgeTargetManifestError("The manifest must be outside the hashed tree.")


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
            raise BridgeTargetManifestError("A tree directory could not be enumerated.") from exc

        for child in children:
            child_parts = (*relative_parts, child.name)
            relative_path = _normalize_relative_path("/".join(child_parts))
            child_path = directory / child.name
            try:
                metadata = _lstat(child_path)
            except OSError as exc:
                raise BridgeTargetManifestError("A tree entry became unavailable.") from exc
            if _is_link_or_reparse(child_path, metadata):
                raise BridgeTargetManifestError("A tree entry is a link or reparse point.")
            mode = int(getattr(metadata, "st_mode", 0) or 0)
            if stat.S_ISDIR(mode):
                _register_path_claim(relative_path, "directory", claims)
                if len(directories) + len(files) >= MAX_ENTRY_COUNT:
                    raise BridgeTargetManifestError("The tree exceeds the manifest safety bounds.")
                directories.append(relative_path)
                visit(child_path, child_parts)
                continue
            if not stat.S_ISREG(mode):
                raise BridgeTargetManifestError("A tree leaf is not a regular file.")
            _register_path_claim(relative_path, "file", claims)
            length, sha256 = _hash_regular_file(child_path)
            total_bytes += length
            if len(directories) + len(files) >= MAX_ENTRY_COUNT or total_bytes > MAX_TREE_BYTES:
                raise BridgeTargetManifestError("The tree exceeds the manifest safety bounds.")
            files.append({"path": relative_path, "length": length, "sha256": sha256})

        after = _validate_directory(directory)
        if _metadata_identity(before) != _metadata_identity(after):
            raise BridgeTargetManifestError("A tree directory changed while scanning.")

    visit(root, ())
    directories.sort()
    files.sort(key=lambda item: item["path"])
    return directories, files


def build_manifest(tree_root: os.PathLike[str] | str) -> dict[str, Any]:
    """Read one tree and return its canonical manifest without writing anything."""

    root = _resolve_tree_root(tree_root)
    directories, files = _scan_tree(root)
    document = {
        "schema": MANIFEST_SCHEMA,
        "algorithm": HASH_ALGORITHM,
        "directoryCount": len(directories),
        "directories": directories,
        "entryCount": len(files),
        "byteCount": sum(item["length"] for item in files),
        "files": files,
        "treeDigest": compute_tree_digest(files, directories),
    }
    return validate_manifest_document(document)


def validate_manifest_document(value: Any) -> dict[str, Any]:
    """Validate and copy one parsed document into its exact canonical shape."""

    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise BridgeTargetManifestError("The manifest shape is invalid.")
    if value.get("schema") != MANIFEST_SCHEMA or value.get("algorithm") != HASH_ALGORITHM:
        raise BridgeTargetManifestError("The manifest format is unsupported.")
    entry_count = value.get("entryCount")
    directory_count = value.get("directoryCount")
    byte_count = value.get("byteCount")
    if isinstance(directory_count, bool) or not isinstance(directory_count, int):
        raise BridgeTargetManifestError("The manifest directory count is invalid.")
    if directory_count < 0 or directory_count > MAX_ENTRY_COUNT:
        raise BridgeTargetManifestError("The manifest directory count is invalid.")
    if isinstance(entry_count, bool) or not isinstance(entry_count, int):
        raise BridgeTargetManifestError("The manifest entry count is invalid.")
    if entry_count < 0 or entry_count > MAX_ENTRY_COUNT:
        raise BridgeTargetManifestError("The manifest entry count is invalid.")
    if directory_count + entry_count > MAX_ENTRY_COUNT:
        raise BridgeTargetManifestError("The manifest path count is invalid.")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
        raise BridgeTargetManifestError("The manifest byte count is invalid.")
    if byte_count > MAX_TREE_BYTES:
        raise BridgeTargetManifestError("The manifest byte count is invalid.")

    raw_directories = value.get("directories")
    if not isinstance(raw_directories, list) or len(raw_directories) != directory_count:
        raise BridgeTargetManifestError("The manifest directory list is invalid.")
    if any(not isinstance(path, str) for path in raw_directories):
        raise BridgeTargetManifestError("A manifest directory path is invalid.")
    directories = [_normalize_relative_path(path) for path in raw_directories]
    if directories != sorted(directories):
        raise BridgeTargetManifestError("Manifest directory records must be sorted by path.")
    if len(set(directories)) != len(directories):
        raise BridgeTargetManifestError("A manifest directory path has a duplicate collision.")

    claims: dict[str, tuple[str, str]] = {}
    for path in directories:
        _register_path_claim(path, "directory", claims)
    directory_set = set(directories)
    for path in directories:
        parent = path.rpartition("/")[0]
        if parent and parent not in directory_set:
            raise BridgeTargetManifestError("The manifest directory tree is incomplete.")

    raw_files = value.get("files")
    if not isinstance(raw_files, list) or len(raw_files) != entry_count:
        raise BridgeTargetManifestError("The manifest file list is invalid.")
    files: list[dict[str, Any]] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != _FILE_KEYS:
            raise BridgeTargetManifestError("A manifest file record is invalid.")
        path = _normalize_relative_path(raw_file.get("path"))
        _register_path_claim(path, "file", claims)
        parent = path.rpartition("/")[0]
        if parent and parent not in directory_set:
            raise BridgeTargetManifestError("The manifest directory tree is incomplete.")
        length = raw_file.get("length")
        sha256 = raw_file.get("sha256")
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise BridgeTargetManifestError("A manifest file length is invalid.")
        if not isinstance(sha256, str) or not _LOWER_SHA256_RE.fullmatch(sha256):
            raise BridgeTargetManifestError("A manifest file digest is invalid.")
        files.append({"path": path, "length": length, "sha256": sha256})

    paths = [item["path"] for item in files]
    if paths != sorted(paths):
        raise BridgeTargetManifestError("Manifest file records must be sorted by path.")
    if sum(item["length"] for item in files) != byte_count:
        raise BridgeTargetManifestError("The manifest byte count does not match its file records.")
    tree_digest = value.get("treeDigest")
    if not isinstance(tree_digest, str) or not _LOWER_SHA256_RE.fullmatch(tree_digest):
        raise BridgeTargetManifestError("The manifest tree digest is invalid.")
    if tree_digest != compute_tree_digest(files, directories):
        raise BridgeTargetManifestError("The manifest tree digest does not match its file records.")
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


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _read_manifest_file(path: Path) -> dict[str, Any]:
    try:
        before = _lstat(path)
        _validate_regular_file(path, before)
        if int(getattr(before, "st_size", 0) or 0) > MAX_MANIFEST_BYTES:
            raise BridgeTargetManifestError("The manifest exceeds the read safety bound.")
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0) or 0)
        flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
        descriptor = os.open(path, flags)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("The manifest file is unavailable.") from exc

    chunks: list[bytes] = []
    total = 0
    try:
        opened = os.fstat(descriptor)
        _validate_regular_file(path, opened)
        if _metadata_identity(before) != _metadata_identity(opened):
            raise BridgeTargetManifestError("The manifest changed while opening.")
        while True:
            chunk = os.read(descriptor, min(HASH_CHUNK_BYTES, MAX_MANIFEST_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_MANIFEST_BYTES:
                raise BridgeTargetManifestError("The manifest exceeds the read safety bound.")
        after_open = os.fstat(descriptor)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("The manifest file could not be read.") from exc
    finally:
        os.close(descriptor)

    try:
        after_path = _lstat(path)
        _validate_regular_file(path, after_path)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("The manifest changed while reading.") from exc
    if (
        _metadata_identity(before) != _metadata_identity(after_open)
        or _metadata_identity(after_open) != _metadata_identity(after_path)
        or total != int(getattr(after_open, "st_size", -1))
    ):
        raise BridgeTargetManifestError("The manifest changed while reading.")

    raw_content = b"".join(chunks)
    try:
        parsed = json.loads(
            raw_content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except _DuplicateJsonKey as exc:
        raise BridgeTargetManifestError("The manifest contains a duplicate JSON key.") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeTargetManifestError("The manifest is not valid UTF-8 JSON.") from exc
    document = validate_manifest_document(parsed)
    if raw_content != canonical_json_bytes(document) + b"\n":
        raise BridgeTargetManifestError("The manifest file is not canonical JSON.")
    return document


def _validate_manifest_parent(path: Path) -> None:
    parent = path.parent
    metadata = _validate_directory(parent)
    if _is_link_or_reparse(parent, metadata):
        raise BridgeTargetManifestError("The manifest directory is unsafe.")


def _existing_manifest_identity(path: Path) -> tuple[int, ...] | None:
    try:
        metadata = _lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BridgeTargetManifestError("The manifest target could not be inspected.") from exc
    _validate_regular_file(path, metadata)
    return _metadata_identity(metadata)


def write_manifest(
    tree_root: os.PathLike[str] | str,
    manifest_path: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Explicitly build and atomically write a manifest outside the hashed tree."""

    root = _resolve_tree_root(tree_root)
    target = _manifest_outside_tree(root, manifest_path)
    _validate_manifest_parent(target)
    original_identity = _existing_manifest_identity(target)
    document = build_manifest(root)
    content = canonical_json_bytes(document) + b"\n"
    temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_BINARY", 0) or 0)
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_metadata = _lstat(temporary)
        _validate_regular_file(temporary, temporary_metadata)
        if _existing_manifest_identity(target) != original_identity:
            raise BridgeTargetManifestError("The manifest target changed before replacement.")
        os.replace(temporary, target)
    except BridgeTargetManifestError:
        raise
    except OSError as exc:
        raise BridgeTargetManifestError("The manifest could not be written.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    stored = _read_manifest_file(target)
    if stored != document:
        raise BridgeTargetManifestError("The written manifest failed readback verification.")
    return stored


def verify_manifest(
    tree_root: os.PathLike[str] | str,
    manifest_path: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Verify a manifest and exact current tree without modifying either one."""

    root = _resolve_tree_root(tree_root)
    source = _manifest_outside_tree(root, manifest_path)
    expected = _read_manifest_file(source)
    observed = build_manifest(root)
    if observed != expected:
        raise BridgeTargetManifestError("The current tree does not match the manifest.")
    return expected


def _result(document: Mapping[str, Any], mode: str) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": mode,
        "schema": document["schema"],
        "directoryCount": document["directoryCount"],
        "entryCount": document["entryCount"],
        "byteCount": document["byteCount"],
        "treeDigest": document["treeDigest"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify one local VRCForge bridge runtime tree manifest."
    )
    parser.add_argument("--tree", required=True, help="Local bridge runtime tree.")
    parser.add_argument("--manifest", required=True, help="Manifest path outside the tree.")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Explicitly write the manifest; without this flag verification is read-only.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.build:
            document = write_manifest(arguments.tree, arguments.manifest)
            result = _result(document, "build")
        else:
            document = verify_manifest(arguments.tree, arguments.manifest)
            result = _result(document, "verify")
    except BridgeTargetManifestError as exc:
        failure = {"ok": False, "error": str(exc)}
        print(canonical_json_bytes(failure).decode("utf-8"), file=sys.stderr)
        return 1
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
