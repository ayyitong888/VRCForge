"""Read-only verification for the fixed bridge runtime inside a release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping, Sequence


_PACKAGING_ROOT = Path(__file__).resolve().parent
if str(_PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGING_ROOT))

import bridge_target_manifest


VERIFICATION_SCHEMA = "vrcforge.bridge_target_release_verification.v1"
RUNTIME_SCHEMA = "vrcforge.bridge_target_runtime.v1"
PAYLOAD_INTEGRITY_SCHEMA = "vrcforge.payload-integrity.v1"
RUNTIME_ROOT = "bridge_target"
EXECUTABLE_PATH = "bridge_target/vrcforge_bridge_target.exe"
EXECUTABLE_TREE_PATH = "vrcforge_bridge_target.exe"
TREE_MANIFEST_PATH = "bridge-target-manifest.json"
PAYLOAD_INTEGRITY_PATH = "payload-integrity.json"
MAX_RELEASE_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_INTEGRITY_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_ENTRY_COUNT = bridge_target_manifest.MAX_ENTRY_COUNT + 100_000
MAX_PAYLOAD_ARCHIVE_BYTES = 32 * 1024 * 1024 * 1024

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_KEYS = {
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


class BridgeTargetReleaseVerificationError(RuntimeError):
    """Raised when archive bytes do not satisfy the strict release binding."""


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(int(metadata.st_mode)),
        int(metadata.st_nlink),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(getattr(metadata, "st_file_attributes", 0) or 0),
    )


def _validate_regular_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or int(getattr(metadata, "st_file_attributes", 0) or 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise BridgeTargetReleaseVerificationError(
            "A release verification input is not a regular single-link file."
        )


def _read_stable_file(path: Path, maximum_bytes: int) -> bytes:
    try:
        before_path = path.lstat()
        _validate_regular_metadata(before_path)
        if before_path.st_size > maximum_bytes:
            raise BridgeTargetReleaseVerificationError(
                "A release verification input exceeds its safety bound."
            )
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0) or 0)
        flags |= int(getattr(os, "O_NOINHERIT", 0) or 0)
        flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
        descriptor = os.open(path, flags)
    except BridgeTargetReleaseVerificationError:
        raise
    except OSError as exc:
        raise BridgeTargetReleaseVerificationError(
            "A release verification input is unavailable."
        ) from exc

    chunks: list[bytes] = []
    total = 0
    try:
        before_open = os.fstat(descriptor)
        _validate_regular_metadata(before_open)
        if _identity(before_path) != _identity(before_open):
            raise BridgeTargetReleaseVerificationError(
                "A release verification input changed while opening."
            )
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise BridgeTargetReleaseVerificationError(
                    "A release verification input exceeds its safety bound."
                )
        after_open = os.fstat(descriptor)
    except BridgeTargetReleaseVerificationError:
        raise
    except OSError as exc:
        raise BridgeTargetReleaseVerificationError(
            "A release verification input could not be read."
        ) from exc
    finally:
        os.close(descriptor)

    try:
        after_path = path.lstat()
        _validate_regular_metadata(after_path)
    except (OSError, BridgeTargetReleaseVerificationError) as exc:
        raise BridgeTargetReleaseVerificationError(
            "A release verification input changed while reading."
        ) from exc
    if (
        _identity(before_path) != _identity(after_open)
        or _identity(after_open) != _identity(after_path)
        or total != after_open.st_size
    ):
        raise BridgeTargetReleaseVerificationError(
            "A release verification input changed while reading."
        )
    return b"".join(chunks)


@contextmanager
def _open_stable_archive(
    path: Path,
) -> Iterator[tuple[zipfile.ZipFile, str]]:
    try:
        before_path = path.lstat()
        _validate_regular_metadata(before_path)
        if before_path.st_size > MAX_PAYLOAD_ARCHIVE_BYTES:
            raise BridgeTargetReleaseVerificationError(
                "The payload archive exceeds its safety bound."
            )
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0) or 0)
        flags |= int(getattr(os, "O_NOINHERIT", 0) or 0)
        flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
        descriptor = os.open(path, flags)
    except BridgeTargetReleaseVerificationError:
        raise
    except OSError as exc:
        raise BridgeTargetReleaseVerificationError(
            "The payload archive is unavailable."
        ) from exc

    handle: BinaryIO | None = None
    archive: zipfile.ZipFile | None = None
    after_open: os.stat_result | None = None
    try:
        before_open = os.fstat(descriptor)
        _validate_regular_metadata(before_open)
        if _identity(before_path) != _identity(before_open):
            raise BridgeTargetReleaseVerificationError(
                "The payload archive changed while opening."
            )
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        digest = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        payload_digest = digest.hexdigest()
        handle.seek(0)
        archive = zipfile.ZipFile(handle, "r")
        yield archive, payload_digest
        archive.close()
        archive = None
        handle.seek(0)
        readback_digest = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            readback_digest.update(chunk)
        if readback_digest.hexdigest() != payload_digest:
            raise BridgeTargetReleaseVerificationError(
                "The payload archive changed during verification."
            )
        after_open = os.fstat(handle.fileno())
    except BridgeTargetReleaseVerificationError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise BridgeTargetReleaseVerificationError(
            "The payload archive is not a valid readable ZIP."
        ) from exc
    finally:
        if archive is not None:
            archive.close()
        if handle is not None:
            handle.close()
        elif descriptor >= 0:
            os.close(descriptor)

    if after_open is None:
        raise BridgeTargetReleaseVerificationError(
            "The payload archive did not complete verification."
        )
    try:
        after_path = path.lstat()
        _validate_regular_metadata(after_path)
    except (OSError, BridgeTargetReleaseVerificationError) as exc:
        raise BridgeTargetReleaseVerificationError(
            "The payload archive changed while reading."
        ) from exc
    if (
        _identity(before_path) != _identity(after_open)
        or _identity(after_open) != _identity(after_path)
    ):
        raise BridgeTargetReleaseVerificationError(
            "The payload archive changed while reading."
        )


def _parse_json(raw: bytes, *, label: str, allow_bom: bool = False) -> Mapping[str, Any]:
    encoding = "utf-8-sig" if allow_bom else "utf-8"
    try:
        value = json.loads(
            raw.decode(encoding, errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (_DuplicateJsonKey, UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeTargetReleaseVerificationError(f"{label} is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise BridgeTargetReleaseVerificationError(f"{label} has an invalid shape.")
    return value


def _validate_strict_build_policy(release_manifest: Mapping[str, Any]) -> None:
    policy = release_manifest.get("buildPolicy")
    if not isinstance(policy, Mapping):
        raise BridgeTargetReleaseVerificationError(
            "The release manifest is not a strict source build."
        )
    mode = policy.get("mode")
    release_mode = mode == "strict" and policy.get("releaseEligible") is True
    evidence_mode = (
        mode == "strict-evidence"
        and policy.get("releaseEligible") is False
        and policy.get("evidenceEligible") is True
    )
    if (
        not (release_mode or evidence_mode)
        or policy.get("allowDirty") is not False
        or policy.get("allowUnpushed") is not False
        or policy.get("allowVersionMismatch") is not False
    ):
        raise BridgeTargetReleaseVerificationError(
            "The release manifest is not a strict source build."
        )


def _validate_runtime_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RUNTIME_KEYS:
        raise BridgeTargetReleaseVerificationError(
            "The bridge runtime binding has an invalid shape."
        )
    if (
        value.get("schema") != RUNTIME_SCHEMA
        or value.get("runtimeRelativeRoot") != RUNTIME_ROOT
        or value.get("executableRelativePath") != EXECUTABLE_PATH
        or value.get("manifestRelativePath") != TREE_MANIFEST_PATH
        or value.get("candidatePayloadIncluded") is not True
        or value.get("strictSourceBound") is not True
        or value.get("verifiedAfterBuild") is not True
    ):
        raise BridgeTargetReleaseVerificationError(
            "The bridge runtime binding is inconsistent."
        )
    for key in ("executableSha256", "manifestSha256", "treeDigest"):
        if not isinstance(value.get(key), str) or not _LOWER_SHA256_RE.fullmatch(value[key]):
            raise BridgeTargetReleaseVerificationError(
                "The bridge runtime binding digest is invalid."
            )
    for key in ("directoryCount", "entryCount", "byteCount"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise BridgeTargetReleaseVerificationError(
                "The bridge runtime binding count is invalid."
            )
    if (
        value["directoryCount"] + value["entryCount"]
        > bridge_target_manifest.MAX_ENTRY_COUNT
        or value["byteCount"] > bridge_target_manifest.MAX_TREE_BYTES
    ):
        raise BridgeTargetReleaseVerificationError(
            "The bridge runtime binding exceeds its safety bound."
        )
    return dict(value)


def _normalize_member_name(raw_name: str) -> tuple[str, bool]:
    if not isinstance(raw_name, str) or not raw_name:
        raise BridgeTargetReleaseVerificationError("An archive member path is unsafe.")
    is_directory = raw_name.endswith("/")
    candidate = raw_name[:-1] if is_directory else raw_name
    if not candidate or candidate.endswith("/"):
        raise BridgeTargetReleaseVerificationError("An archive member path is unsafe.")
    try:
        normalized = bridge_target_manifest._normalize_relative_path(candidate)
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise BridgeTargetReleaseVerificationError(
            "An archive member path is unsafe."
        ) from exc
    if normalized != candidate:
        raise BridgeTargetReleaseVerificationError("An archive member path is unsafe.")
    return normalized, is_directory


def _validate_archive_entries(
    archive: zipfile.ZipFile,
) -> dict[str, tuple[zipfile.ZipInfo, str]]:
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRY_COUNT:
        raise BridgeTargetReleaseVerificationError(
            "The payload archive exceeds its entry safety bound."
        )
    members: dict[str, tuple[zipfile.ZipInfo, str]] = {}
    path_claims: dict[str, tuple[str, str]] = {}
    for info in entries:
        normalized, is_directory = _normalize_member_name(info.filename)
        if normalized in members:
            raise BridgeTargetReleaseVerificationError(
                "An archive member has a duplicate collision."
            )
        kind = "directory" if is_directory else "file"
        try:
            bridge_target_manifest._register_path_claim(
                normalized,
                kind,
                path_claims,
            )
        except bridge_target_manifest.BridgeTargetManifestError as exc:
            raise BridgeTargetReleaseVerificationError(
                "An archive member has a casefold or type collision."
            ) from exc

        unix_mode = (int(info.external_attr) >> 16) & 0xFFFF
        unix_kind = stat.S_IFMT(unix_mode)
        dos_attributes = int(info.external_attr) & 0xFFFF
        if info.flag_bits & 0x1:
            raise BridgeTargetReleaseVerificationError(
                "An encrypted archive member is forbidden."
            )
        if (
            stat.S_ISLNK(unix_mode)
            or dos_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or (is_directory and unix_kind not in {0, stat.S_IFDIR})
            or (not is_directory and unix_kind not in {0, stat.S_IFREG})
        ):
            raise BridgeTargetReleaseVerificationError(
                "An archive member is a link or non-regular entry."
            )
        if is_directory and int(info.file_size) != 0:
            raise BridgeTargetReleaseVerificationError(
                "An archive directory member contains unexpected data."
            )
        if not is_directory and int(info.file_size) < 0:
            raise BridgeTargetReleaseVerificationError(
                "An archive member length is invalid."
            )
        members[normalized] = (info, kind)
    return members


def _read_archive_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    maximum_bytes: int,
    *,
    capture: bool,
) -> tuple[int, str, bytes | None]:
    if info.is_dir() or info.file_size > maximum_bytes:
        raise BridgeTargetReleaseVerificationError(
            "An archive member exceeds its read safety bound."
        )
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info, "r") as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, maximum_bytes + 1 - total))
                if not chunk:
                    break
                digest.update(chunk)
                if capture:
                    chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    raise BridgeTargetReleaseVerificationError(
                        "An archive member exceeds its read safety bound."
                    )
    except BridgeTargetReleaseVerificationError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BridgeTargetReleaseVerificationError(
            "An archive member could not be read."
        ) from exc
    if total != info.file_size:
        raise BridgeTargetReleaseVerificationError(
            "An archive member length changed while reading."
        )
    return total, digest.hexdigest(), b"".join(chunks) if capture else None


def _required_file(
    members: Mapping[str, tuple[zipfile.ZipInfo, str]],
    path: str,
) -> zipfile.ZipInfo:
    value = members.get(path)
    if value is None or value[1] != "file":
        raise BridgeTargetReleaseVerificationError(
            "A required release archive member is missing."
        )
    return value[0]


def _runtime_tree_from_archive(
    archive: zipfile.ZipFile,
    members: Mapping[str, tuple[zipfile.ZipInfo, str]],
) -> dict[str, Any]:
    root_member = members.get(RUNTIME_ROOT)
    if root_member is not None and root_member[1] != "directory":
        raise BridgeTargetReleaseVerificationError(
            "The archived bridge runtime root is invalid."
        )
    prefix = f"{RUNTIME_ROOT}/"
    directories: set[str] = set()
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for member_path, (info, kind) in members.items():
        if not member_path.startswith(prefix):
            continue
        relative_path = member_path[len(prefix) :]
        try:
            normalized_relative = bridge_target_manifest._normalize_relative_path(
                relative_path
            )
        except bridge_target_manifest.BridgeTargetManifestError as exc:
            raise BridgeTargetReleaseVerificationError(
                "An archived bridge runtime path is invalid."
            ) from exc
        parts = normalized_relative.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
        if kind == "directory":
            directories.add(normalized_relative)
            continue
        length, digest, _ = _read_archive_member(
            archive,
            info,
            bridge_target_manifest.MAX_TREE_BYTES - total_bytes,
            capture=False,
        )
        total_bytes += length
        if total_bytes > bridge_target_manifest.MAX_TREE_BYTES:
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge runtime exceeds its byte safety bound."
            )
        files.append(
            {"path": normalized_relative, "length": length, "sha256": digest}
        )

    sorted_directories = sorted(directories)
    files.sort(key=lambda row: row["path"])
    document = {
        "schema": bridge_target_manifest.MANIFEST_SCHEMA,
        "algorithm": bridge_target_manifest.HASH_ALGORITHM,
        "directoryCount": len(sorted_directories),
        "directories": sorted_directories,
        "entryCount": len(files),
        "byteCount": total_bytes,
        "files": files,
        "treeDigest": bridge_target_manifest.compute_tree_digest(
            files,
            sorted_directories,
        ),
    }
    try:
        return bridge_target_manifest.validate_manifest_document(document)
    except bridge_target_manifest.BridgeTargetManifestError as exc:
        raise BridgeTargetReleaseVerificationError(
            "The archived bridge runtime tree is invalid."
        ) from exc


def _artifact_payload_digest(
    release_manifest: Mapping[str, Any],
    payload_name: str,
) -> str:
    artifacts = release_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise BridgeTargetReleaseVerificationError(
            "The release manifest artifact list is invalid."
        )
    matches = [
        item
        for item in artifacts
        if isinstance(item, Mapping) and item.get("name") == payload_name
    ]
    if len(matches) != 1:
        raise BridgeTargetReleaseVerificationError(
            "The release manifest does not uniquely bind the payload archive."
        )
    digest = matches[0].get("sha256")
    if not isinstance(digest, str) or not _LOWER_SHA256_RE.fullmatch(digest):
        raise BridgeTargetReleaseVerificationError(
            "The release manifest payload digest is invalid."
        )
    return digest


def verify_release_bridge_target(
    release_manifest_path: os.PathLike[str] | str,
    payload_zip_path: os.PathLike[str] | str,
) -> dict[str, Any]:
    release_path = Path(release_manifest_path)
    payload_path = Path(payload_zip_path)
    release_manifest = _parse_json(
        _read_stable_file(release_path, MAX_RELEASE_MANIFEST_BYTES),
        label="The release manifest",
        allow_bom=True,
    )
    _validate_strict_build_policy(release_manifest)
    release_version = release_manifest.get("version")
    if not isinstance(release_version, str) or not release_version:
        raise BridgeTargetReleaseVerificationError(
            "The release manifest version is invalid."
        )
    release_binding = _validate_runtime_binding(
        release_manifest.get("bridgeTargetRuntime")
    )
    expected_payload_digest = _artifact_payload_digest(
        release_manifest,
        payload_path.name,
    )

    with _open_stable_archive(payload_path) as (archive, payload_digest):
        if payload_digest != expected_payload_digest:
            raise BridgeTargetReleaseVerificationError(
                "The payload digest does not match the release manifest."
            )
        members = _validate_archive_entries(archive)

        payload_info = _required_file(members, PAYLOAD_INTEGRITY_PATH)
        _, _, payload_bytes = _read_archive_member(
            archive,
            payload_info,
            MAX_PAYLOAD_INTEGRITY_BYTES,
            capture=True,
        )
        if payload_bytes is None:
            raise BridgeTargetReleaseVerificationError(
                "The payload integrity document could not be read."
            )
        payload_integrity = _parse_json(
            payload_bytes,
            label="The payload integrity document",
            allow_bom=True,
        )
        if (
            payload_integrity.get("schema") != PAYLOAD_INTEGRITY_SCHEMA
            or payload_integrity.get("version") != release_version
        ):
            raise BridgeTargetReleaseVerificationError(
                "The payload integrity document is inconsistent."
            )
        payload_binding = _validate_runtime_binding(
            payload_integrity.get("bridgeTargetRuntime")
        )
        if payload_binding != release_binding:
            raise BridgeTargetReleaseVerificationError(
                "The bridge runtime binding differs across release records."
            )

        tree_manifest_info = _required_file(members, TREE_MANIFEST_PATH)
        _, manifest_sha256, tree_manifest_bytes = _read_archive_member(
            archive,
            tree_manifest_info,
            bridge_target_manifest.MAX_MANIFEST_BYTES,
            capture=True,
        )
        if tree_manifest_bytes is None:
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge tree manifest could not be read."
            )
        if manifest_sha256 != release_binding["manifestSha256"]:
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge tree manifest digest is inconsistent."
            )
        parsed_tree = _parse_json(
            tree_manifest_bytes,
            label="The archived bridge tree manifest",
        )
        try:
            expected_tree = bridge_target_manifest.validate_manifest_document(
                parsed_tree
            )
        except bridge_target_manifest.BridgeTargetManifestError as exc:
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge tree manifest is invalid."
            ) from exc
        if (
            tree_manifest_bytes
            != bridge_target_manifest.canonical_json_bytes(expected_tree) + b"\n"
        ):
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge tree manifest is not canonical."
            )

        observed_tree = _runtime_tree_from_archive(archive, members)
        if observed_tree != expected_tree:
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge runtime tree does not match its manifest."
            )
        executable_member = members.get(EXECUTABLE_PATH)
        if executable_member is None or executable_member[1] != "file":
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge runtime executable is missing."
            )
        executable_records = [
            row
            for row in observed_tree["files"]
            if row.get("path") == EXECUTABLE_TREE_PATH
        ]
        if (
            len(executable_records) != 1
            or executable_records[0].get("sha256")
            != release_binding["executableSha256"]
        ):
            raise BridgeTargetReleaseVerificationError(
                "The archived bridge runtime executable digest is inconsistent."
            )
        executable_sha256 = executable_records[0]["sha256"]
        if (
            release_binding["treeDigest"] != observed_tree["treeDigest"]
            or release_binding["directoryCount"] != observed_tree["directoryCount"]
            or release_binding["entryCount"] != observed_tree["entryCount"]
            or release_binding["byteCount"] != observed_tree["byteCount"]
        ):
            raise BridgeTargetReleaseVerificationError(
                "The bridge runtime binding does not match the archived tree."
            )

    return {
        "ok": True,
        "schema": VERIFICATION_SCHEMA,
        "payloadSha256": payload_digest,
        "manifestSha256": manifest_sha256,
        "executableSha256": executable_sha256,
        "treeDigest": observed_tree["treeDigest"],
        "directoryCount": observed_tree["directoryCount"],
        "entryCount": observed_tree["entryCount"],
        "byteCount": observed_tree["byteCount"],
        "verifiedFromArchive": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only verification of the fixed bridge runtime in a release ZIP."
    )
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--payload-zip", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_release_bridge_target(
            arguments.release_manifest,
            arguments.payload_zip,
        )
    except BridgeTargetReleaseVerificationError as exc:
        failure = {"ok": False, "error": str(exc)}
        print(
            bridge_target_manifest.canonical_json_bytes(failure).decode("utf-8"),
            file=sys.stderr,
        )
        return 1
    except Exception:
        failure = {"ok": False, "error": "Release archive verification failed closed."}
        print(
            bridge_target_manifest.canonical_json_bytes(failure).decode("utf-8"),
            file=sys.stderr,
        )
        return 1
    print(bridge_target_manifest.canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
