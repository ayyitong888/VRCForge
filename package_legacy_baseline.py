"""Read-only verification of an imported legacy UnityPackage tree."""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from prepared_file_imports import capture_regular_file

_MAX_MEMBERS = 5000
_MAX_UNCOMPRESSED = 64 * 1024 * 1024
_SNAPSHOT_SCHEMA = "vrcforge.legacy_package_snapshot.v1"


def _safe_name(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ValueError(f"Unsafe UnityPackage pathname: {value}")
    name = raw.strip("/")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe UnityPackage pathname: {value}")
    return path.as_posix()


def _digest_rows(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _archive_identity(path: Path) -> dict[str, Any]:
    identity, digest = capture_regular_file(path, label="Legacy baseline archive")
    return {"identity": identity, "sha256": digest}


def verify_legacy_baseline(archive_path: str | Path, assets_root: str | Path) -> dict[str, Any]:
    """Compare every current file under an Assets legacy root with package bytes."""
    archive_input = Path(archive_path).expanduser()
    root_input = Path(assets_root).expanduser()
    if archive_input.is_symlink() or root_input.is_symlink():
        raise ValueError("Legacy baseline archive/root may not be links.")
    archive = archive_input.resolve()
    root = root_input.resolve()
    if not archive.is_file():
        raise ValueError("Legacy baseline archive must be a regular file.")
    if not root.is_dir():
        raise ValueError("Legacy baseline Assets root must be a regular directory.")
    prefix = f"Assets/{root.name}/"
    expected: dict[str, str] = {}
    archive_id = _archive_identity(archive)
    with tarfile.open(archive, mode="r:*") as bundle:
        if len(bundle.getmembers()) > _MAX_MEMBERS:
            raise ValueError("Legacy baseline archive contains too many members.")
        members = {member.name: member for member in bundle.getmembers()}
        total_size = 0
        for member in bundle.getmembers():
            if not member.isfile() or not member.name.endswith("/pathname"):
                continue
            guid = member.name.rsplit("/", 1)[0]
            raw_name = bundle.extractfile(member).read().decode("utf-8-sig").strip()
            name = _safe_name(raw_name)
            is_root_dir = name == f"Assets/{root.name}"
            if is_root_dir:
                relative = f"../{root.name}.meta"
            elif name.startswith(prefix):
                relative = name[len(prefix):]
            else:
                continue
            if not relative and not is_root_dir:
                continue
            suffixes = ("/asset.meta",) if is_root_dir else ("/asset", "/asset.meta")
            for suffix in suffixes:
                payload = members.get(f"{guid}{suffix}")
                if payload is None:
                    if suffix == "/asset":
                        continue
                    raise ValueError(f"Baseline entry missing {suffix}: {name}")
                if not payload.isfile():
                    raise ValueError(f"Baseline entry is not a file: {name}")
                content = bundle.extractfile(payload).read()
                total_size += len(content)
                if total_size > _MAX_UNCOMPRESSED:
                    raise ValueError("Legacy baseline archive content exceeds the fixed limit.")
                target = (f"../{root.name}.meta" if is_root_dir else relative + ".meta") if suffix == "/asset.meta" else relative
                if target in expected:
                    raise ValueError(f"Duplicate baseline pathname: {target}")
                expected[target] = hashlib.sha256(content).hexdigest()
    rows = [{"path": path, "sha256": expected[path]} for path in sorted(expected)]
    actual: dict[str, str] = {}
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"Legacy Assets root contains a link: {candidate}")
        if candidate.is_file():
            relative = candidate.relative_to(root).as_posix()
            actual[relative] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    root_meta = root.with_name(root.name + ".meta")
    if root_meta.is_file() and not root_meta.is_symlink():
        actual[f"../{root.name}.meta"] = hashlib.sha256(root_meta.read_bytes()).hexdigest()
    missing = sorted(set(expected) - set(actual))
    unknown = sorted(set(actual) - set(expected))
    modified = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
    return {
        "ok": not (missing or unknown or modified),
        "archive": archive_id,
        "assetsRoot": str(root).replace("\\", "/"),
        "entries": rows,
        "treeDigest": _digest_rows([{"path": path, "sha256": actual[path]} for path in sorted(actual)]),
        "baselineDigest": _digest_rows(rows),
        "missing": missing,
        "unknown": unknown,
        "modified": modified,
    }


def _snapshot_source_rows(root: Path) -> tuple[list[dict[str, Any]], int]:
    """Read a bounded, link-free tree manifest and its byte total."""
    rows: list[dict[str, Any]] = []
    total = 0
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"Legacy Assets root contains a link: {candidate}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ValueError(f"Legacy Assets root contains a non-file entry: {candidate}")
        relative = candidate.relative_to(root).as_posix()
        identity, digest = capture_regular_file(candidate, label="Legacy Assets file")
        total += int(identity["size"])
        if len(rows) >= _MAX_MEMBERS or total > _MAX_UNCOMPRESSED:
            raise ValueError("Legacy Assets tree exceeds the fixed snapshot limit.")
        rows.append({"path": relative, "sha256": digest, "size": int(identity["size"])})
    root_meta = root.with_name(root.name + ".meta")
    if root_meta.exists():
        if root_meta.is_symlink() or not root_meta.is_file():
            raise ValueError("Legacy Assets root meta must be a regular file.")
        identity, digest = capture_regular_file(root_meta, label="Legacy Assets root meta")
        total += int(identity["size"])
        if len(rows) >= _MAX_MEMBERS or total > _MAX_UNCOMPRESSED:
            raise ValueError("Legacy Assets tree exceeds the fixed snapshot limit.")
        rows.append({"path": f"../{root.name}.meta", "sha256": digest, "size": int(identity["size"])})
    rows.sort(key=lambda row: row["path"])
    return rows, total


def create_legacy_preservation_snapshot(
    assets_root: str | Path,
    snapshot_path: str | Path,
) -> dict[str, Any]:
    """Atomically preserve a legacy Assets tree outside Assets before migration.

    This is an explicit opt-in operation for an approved upgrade.  The target
    must not already exist; the source is never removed or changed.  The
    returned manifest is independently verifiable with
    :func:`verify_legacy_preservation_snapshot`.
    """
    source_input = Path(assets_root).expanduser()
    destination_input = Path(snapshot_path).expanduser()
    if source_input.is_symlink() or destination_input.exists() or destination_input.is_symlink():
        raise ValueError("Legacy snapshot source may not be a link and destination must be new.")
    source = source_input.resolve()
    destination = destination_input.resolve()
    if not source.is_dir():
        raise ValueError("Legacy Assets root must be a regular directory.")
    if destination == source or source in destination.parents:
        raise ValueError("Legacy snapshot must be outside the Assets root.")
    rows, total = _snapshot_source_rows(source)
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("Legacy snapshot parent must be an existing regular directory.")
    staging_fd, staging_name = tempfile.mkstemp(prefix=f".{destination.name}.staging-", dir=str(parent))
    os.close(staging_fd)
    staging = Path(staging_name)
    try:
        manifest = {
            "schema": _SNAPSHOT_SCHEMA,
            "sourceAssetsRoot": str(source).replace("\\", "/"),
            "sourceRootName": source.name,
            "entries": rows,
            "bytes": total,
            "treeDigest": _digest_rows([{"path": r["path"], "sha256": r["sha256"]} for r in rows]),
        }
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, indent=2) + "\n")
            for row in rows:
                relative = PurePosixPath(row["path"])
                source_file = source.with_name(source.name + ".meta") if str(relative).startswith("../") else source / Path(*relative.parts)
                _, digest_before = capture_regular_file(source_file, label="Legacy Assets file")
                archive_name = f"tree/{source.name}.meta" if str(relative).startswith("../") else f"tree/{source.name}/{relative.as_posix()}"
                bundle.write(source_file, archive_name)
                _, digest_after = capture_regular_file(source_file, label="Legacy Assets file")
                if digest_before != row["sha256"] or digest_after != row["sha256"]:
                    raise ValueError(f"Legacy Assets file changed while it was copied: {row['path']}")
        rows_after, total_after = _snapshot_source_rows(source)
        if rows_after != rows or total_after != total:
            raise ValueError("Legacy Assets tree changed while it was copied.")
        # Hard-link publication is exclusive on the same volume: a concurrent
        # creator cannot cause an approved backup to overwrite its archive.
        os.link(staging, destination)
        staging.unlink()
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    result = verify_legacy_preservation_snapshot(destination)
    if not result["ok"]:
        raise ValueError("Legacy preservation snapshot failed its post-publish verification.")
    _, archive_digest = capture_regular_file(destination, label="Legacy preservation snapshot")
    result["archivePath"] = str(destination).replace("\\", "/")
    result["archiveSha256"] = archive_digest
    return result


def verify_legacy_preservation_snapshot(snapshot_path: str | Path) -> dict[str, Any]:
    """Verify the durable snapshot manifest and every preserved file."""
    snapshot_input = Path(snapshot_path).expanduser()
    if snapshot_input.is_symlink():
        raise ValueError("Legacy snapshot may not be a link.")
    snapshot = snapshot_input.resolve()
    if not snapshot.is_file():
        raise ValueError("Legacy snapshot must be a regular archive file.")
    try:
        with zipfile.ZipFile(snapshot) as bundle:
            payloads = {name: bundle.read(name) for name in bundle.namelist()}
            manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError("Legacy snapshot manifest is unreadable.") from exc
    if manifest.get("schema") != _SNAPSHOT_SCHEMA:
        raise ValueError("Unsupported legacy snapshot schema.")
    root_name = _safe_name(str(manifest.get("sourceRootName") or ""))
    rows = manifest.get("entries")
    if not isinstance(rows, list) or len(rows) > _MAX_MEMBERS:
        raise ValueError("Legacy snapshot manifest is invalid.")
    actual: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        relative = _safe_name(str(row.get("path") or "")) if not str(row.get("path") or "").startswith("../") else str(row["path"])
        if relative.startswith("../") and relative != f"../{root_name}.meta":
            raise ValueError("Legacy snapshot contains an unsafe path.")
        archive_name = f"tree/{root_name}.meta" if relative.startswith("../") else f"tree/{root_name}/{PurePosixPath(relative).as_posix()}"
        if archive_name not in payloads:
            return {"ok": False, "snapshot": str(snapshot).replace("\\", "/"), "reason": "missing_entry", "path": relative}
        content = payloads[archive_name]
        digest = hashlib.sha256(content).hexdigest()
        total += len(content)
        actual.append({"path": relative, "sha256": digest})
    expected = [{"path": str(r.get("path")), "sha256": str(r.get("sha256"))} for r in rows]
    digest = _digest_rows(actual)
    expected_names = {"manifest.json"} | {
        f"tree/{root_name}.meta" if str(r.get("path", "")).startswith("../") else f"tree/{root_name}/{PurePosixPath(str(r.get('path'))).as_posix()}"
        for r in rows
    }
    ok = (
        actual == expected
        and set(payloads) == expected_names
        and digest == str(manifest.get("treeDigest"))
        and total == int(manifest.get("bytes", -1))
    )
    _, archive_digest = capture_regular_file(snapshot, label="Legacy preservation snapshot")
    return {
        "ok": ok,
        "snapshot": str(snapshot).replace("\\", "/"),
        "archiveSha256": archive_digest,
        "treeDigest": digest,
        "entries": len(actual),
        "bytes": total,
    }
