from __future__ import annotations

import gzip
import json
import re
import sqlite3
import time
import threading
import tarfile
import zipfile
from collections import Counter
from collections.abc import Generator, Iterable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


INSPECTION_SCHEMA = "vrcforge.outfit_package_inspection.v1"
LOOSE_PREFAB_EXTENSIONS = {".prefab"}
ASSEMBLY_DEFINITION_EXTENSIONS = {".asmdef"}
TEXTURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr"}
MATERIAL_EXTENSIONS = {".mat"}
MODEL_EXTENSIONS = {".fbx", ".blend", ".obj"}
MAX_UNITYPACKAGE_SCAN_INPUT_BYTES = 1024 * 1024 * 1024
MAX_UNITYPACKAGE_MEMBERS = 200_000
ASSET_META_GUID = re.compile(rb"(?m)^guid:[ \t]*([0-9a-f]{32})[ \t]*\r?$", re.IGNORECASE)
UNITY_GUID = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)


def unitypackage_member_parts(member_name: str) -> tuple[str, str] | None:
    """Return the GUID and leaf name for a UnityPackage file member.

    Unity's exporter prefixes tar members with ``./``. Remove only that one
    leading current-directory component; retain and reject any remaining dot,
    parent, empty, or nested components instead of normalizing traversal.
    """
    normalized = str(member_name).replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts[0], parts[1]


def is_single_fbx_folder(source_type: str, models: list[dict[str, Any]], *, model_count: int, truncated: bool) -> bool:
    """Shared inspection/planning fact; no prefab or model choice is invented."""
    return (
        source_type == "folder" and not truncated
        and len(models) == 1 and model_count == 1
        and str(models[0].get("path") or "").lower().endswith(".fbx")
    )


class _BoundedArchiveReader:
    def __init__(self, reader: Any, limit: int) -> None:
        self._reader = reader
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self._limit - self.bytes_read + 1
        data = self._reader.read(size)
        self.bytes_read += len(data)
        if self.bytes_read > self._limit:
            raise tarfile.ReadError("UnityPackage scan input byte limit exceeded")
        return data

    def tell(self) -> int:
        return self.bytes_read


ARCHIVE_CURSOR_PATTERN = r"archive_[0-9a-f]{32}_[1-9][0-9]*"
_ARCHIVE_INDEX_PAGE_SIZE = 50_000
_RESUMABLE_ARCHIVE_REASONS = {"archive_entries", "pathname_entries"}
_ARCHIVE_INDEX_MAX_BYTES = 64 * 1024 * 1024


def _source_read(processed, total, reason, *, remaining_at_least=0, unit="archive_entries_and_pathnames"):
    return {
        "status": "complete" if total == processed and reason is None else (
            "partial" if remaining_at_least or reason in {"archive_members", "input_bytes"} else "unknown"
        ),
        "countUnit": unit,
        "processedCount": processed,
        "totalCount": total,
        "remainingCount": total - processed if total is not None and processed is not None else None,
        **({"remainingAtLeast": remaining_at_least} if total is None and remaining_at_least else {}),
        **({"reason": reason} if reason else {}),
    }


def _source_identity(path: Path):
    stat = path.stat()
    return path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class ArchiveInspectionStreams:
    """Short-lived sequential readers; SQLite owns the durable cursors and finished pages."""

    def __init__(self, capacity=8, idle_seconds=60):
        self._entries = {}
        self._lock = threading.Lock()
        self._capacity = capacity
        self._idle_seconds = idle_seconds

    def take(self, key):
        with self._lock:
            entry = self._entries.pop(key, None)
        if entry:
            stream, timer = entry
            timer.cancel()
            return stream

    def park(self, key, stream):
        # Each issued offset is unique. Evicted/expired readers release the source
        # handle; the version-checked cursor can be reconstructed after a restart.
        def expire():
            with self._lock:
                if self._entries.get(key) != (stream, timer):
                    return
                del self._entries[key]
            stream.close()

        timer = threading.Timer(self._idle_seconds, expire)
        timer.daemon = True
        with self._lock:
            old = self._entries.pop(key, None)
            if not old and len(self._entries) >= self._capacity:
                old = self._entries.pop(next(iter(self._entries)))
            self._entries[key] = stream, timer
        if old:
            old[1].cancel()
            old[0].close()
        timer.start()


class ArchiveInspectionIndex:
    """Cache pages only as requested in the private profile, scoped to the host-supplied owner."""

    def __init__(self, database: Path, owner: str, *, streams=None, planner_owner=None):
        self._database = database
        self._owner = owner
        self._streams = streams if streams is not None else ArchiveInspectionStreams()
        self._planner_owner = planner_owner

    def _open(self):
        self._database.parent.mkdir(parents=True, exist_ok=True)
        return closing(sqlite3.connect(self._database, timeout=5))

    def _key(self, token, offset):
        return self._database, self._owner, token, offset

    @staticmethod
    def _reader(path, max_entries):
        generator = _page_zip if path.suffix.lower() == ".zip" else _page_unitypackage
        return generator(path, max_entries, page_size=_ARCHIVE_INDEX_PAGE_SIZE)

    @staticmethod
    def _continuation(token, offset):
        return {"tool": "vrcforge_inspect_outfit_package", "arguments": {"archiveCursor": f"archive_{token}_{offset}"}}

    def inspect(self, path: Path, max_entries: int):
        identity = json.dumps(_source_identity(path)[1:])
        token = uuid4().hex
        pages = self._reader(path, max_entries)
        try:
            result, coverage = next(pages)
            result["sourceRead"] = coverage
            resumable = coverage.get("reason") in _RESUMABLE_ARCHIVE_REASONS
            if not resumable and coverage["status"] != "complete":
                return result
            if identity != json.dumps(_source_identity(path)[1:]):
                result["ok"] = False
                result["error"] = "Archive changed while it was being inspected. Inspect its current version."
                result["sourceRead"] = _source_read(None, None, "archive_changed_during_indexing")
                return result
            try:
                with self._open() as database, database:
                    database.execute("PRAGMA foreign_keys=ON")
                    database.execute(
                        "CREATE TABLE IF NOT EXISTS inspections (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                        "path TEXT NOT NULL, identity TEXT NOT NULL, created REAL NOT NULL, size INTEGER NOT NULL, total INTEGER)"
                    )
                    database.execute(
                        "CREATE TABLE IF NOT EXISTS pages (id TEXT REFERENCES inspections(id) ON DELETE CASCADE, "
                        "offset INTEGER NOT NULL, payload BLOB NOT NULL, PRIMARY KEY (id, offset))"
                    )
                    database.execute("CREATE TABLE IF NOT EXISTS planning_readers ("
                                     "id TEXT PRIMARY KEY REFERENCES inspections(id) ON DELETE CASCADE, owner TEXT NOT NULL)")
                    database.execute("DELETE FROM inspections WHERE created < ?", (time.time() - 7 * 24 * 3600,))
                    # Page zero stores just the restart parameters. Charge a minimum per
                    # cursor so abandoned first pages cannot grow the database unbounded.
                    payload = str(max_entries)
                    if not resumable:
                        result.update(inspectedAt=utc_now(), privacy=privacy_policy())
                        payload = gzip.compress(json.dumps(result, ensure_ascii=False).encode("utf8"), compresslevel=1)
                        if len(payload) + 1024 > _ARCHIVE_INDEX_MAX_BYTES:
                            return result
                    database.execute("INSERT INTO inspections VALUES (?, ?, ?, ?, ?, ?, ?)",
                                     (token, self._owner, str(path), identity, time.time(), len(payload) + 1024,
                                      None if resumable else coverage["totalCount"]))
                    database.execute("INSERT INTO pages VALUES (?, ?, ?)", (token, 0 if resumable else 1, payload))
                    if self._planner_owner:
                        database.execute("INSERT INTO planning_readers VALUES (?,?)", (token, self._planner_owner))
                    self._evict(database)
                if resumable:
                    self._streams.park(self._key(token, 1), pages)
                    pages = None
                    coverage["nextRequest"] = self._continuation(token, 1)
            except (OSError, sqlite3.Error):
                if resumable:
                    coverage["reason"] = "archive_index_unavailable"
            return result
        finally:
            if pages is not None:
                pages.close()

    @staticmethod
    def _evict(database):
        database.execute(
            "DELETE FROM inspections WHERE id IN ("
            "SELECT id FROM (SELECT id, sum(size) OVER (ORDER BY created DESC, id DESC) AS used "
            "FROM inspections) WHERE used > ?)", (_ARCHIVE_INDEX_MAX_BYTES,),
        )

    def completed(self, path):
        if not self._database.is_file() or not path.is_file():
            return None
        try:
            with self._open() as database:
                # Older indices already contain complete owner-private pages.
                has_sharing = database.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='planning_readers'"
                ).fetchone()
                scope = "inspections.owner=:owner"
                if has_sharing:
                    scope += " OR id IN (SELECT id FROM planning_readers WHERE owner=:owner)"
                row = database.execute(
                    f"SELECT payload FROM inspections JOIN pages USING(id) WHERE ({scope}) AND path=:path "
                    "AND identity=:identity AND total IS NOT NULL AND created>=:cutoff "
                    "ORDER BY created DESC, offset DESC LIMIT 1",
                    {"owner": self._owner, "path": str(path), "identity": json.dumps(_source_identity(path)[1:]),
                     "cutoff": time.time() - 7 * 24 * 3600},
                ).fetchone()
            if row:
                page = json.loads(gzip.decompress(row[0]))
                if page["sourceRead"]["status"] == "complete":
                    return page
        except (OSError, EOFError, ValueError, sqlite3.Error):
            pass
        return None

    def resume(self, cursor: str, path: Path | None):
        if not re.fullmatch(ARCHIVE_CURSOR_PATTERN, cursor) or not self._database.is_file():
            return _archive_cursor_error()
        _, token, ordinal = cursor.split("_")
        pages = None
        try:
            offset = int(ordinal)
            with self._open() as database, database:
                database.execute("PRAGMA foreign_keys=ON")
                row = database.execute(
                    "SELECT path, identity, size FROM inspections WHERE id=? AND owner=? AND created>=?",
                    (token, self._owner, time.time() - 7 * 24 * 3600),
                ).fetchone()
                if row is None:
                    return _archive_cursor_error()
                source = Path(row[0])
                if (path is not None and source != path) or json.dumps(_source_identity(source)[1:]) != row[1]:
                    return _archive_cursor_error()
                cached = database.execute("SELECT payload FROM pages WHERE id=? AND offset=?", (token, offset)).fetchone()
                if cached:
                    return json.loads(gzip.decompress(cached[0]))
                latest = database.execute("SELECT offset, payload FROM pages WHERE id=? ORDER BY offset DESC LIMIT 1",
                                          (token,)).fetchone()
                if not latest or latest[0] + 1 != offset:
                    return _archive_cursor_error()
                if latest[0] and not json.loads(gzip.decompress(latest[1]))["sourceRead"].get("nextRequest"):
                    return _archive_cursor_error()
                pages = self._streams.take(self._key(token, offset))
                if pages is None:
                    initial = database.execute("SELECT payload FROM pages WHERE id=? AND offset=0", (token,)).fetchone()
                    if not initial:
                        return _archive_cursor_error()
                    pages = self._reader(source, int(initial[0]))
                    for _ in range(offset):
                        next(pages)
                result, coverage = next(pages)
                result.update(sourceRead=coverage, inspectedAt=utc_now(), privacy=privacy_policy())
                resumable = coverage.get("reason") in _RESUMABLE_ARCHIVE_REASONS
                if resumable:
                    coverage["nextRequest"] = self._continuation(token, offset + 1)
                if json.dumps(_source_identity(source)[1:]) != row[1]:
                    return _archive_cursor_error()
                payload = gzip.compress(json.dumps(result, ensure_ascii=False).encode("utf8"), compresslevel=1)
                if row[2] + len(payload) > _ARCHIVE_INDEX_MAX_BYTES:
                    coverage.pop("nextRequest", None)
                    if resumable:
                        coverage["reason"] = "archive_index_capacity"
                    return result
                # Concurrent retries may reconstruct the same page. Only the winner
                # publishes it; all consumers get identical evidence.
                inserted = database.execute("INSERT OR IGNORE INTO pages VALUES (?, ?, ?)", (token, offset, payload))
                if not inserted.rowcount:
                    saved = database.execute("SELECT payload FROM pages WHERE id=? AND offset=?", (token, offset)).fetchone()
                    return json.loads(gzip.decompress(saved[0]))
                database.execute("UPDATE inspections SET size=size+?, total=?, created=? WHERE id=?",
                                 (len(payload), coverage["totalCount"] if coverage["status"] == "complete" else None,
                                  time.time(), token))
                self._evict(database)
            if resumable:
                self._streams.park(self._key(token, offset + 1), pages)
                pages = None
            return result
        except (OSError, EOFError, ValueError, OverflowError, StopIteration, sqlite3.Error, tarfile.TarError, zipfile.BadZipFile):
            return _archive_cursor_error()
        finally:
            if pages is not None:
                pages.close()


def _archive_cursor_error():
    return {
        "ok": False, "schema": INSPECTION_SCHEMA,
        "error": "Archive cursor is unavailable for this owner or source.",
        "sourceRead": _source_read(None, None, "archive_cursor_unavailable"),
    }


def _page_unitypackage(path, max_entries, *, page_size=None):
    with path.open("rb") as package_file:
        bounded = _BoundedArchiveReader(package_file, MAX_UNITYPACKAGE_SCAN_INPUT_BYTES)
        with tarfile.open(fileobj=bounded, mode="r|*") as archive:
            with closing(_page_unitypackage_members(archive, archive, max_entries, path.name, page_size=page_size)) as pages:
                for parsed in pages:
                    reason = parsed["scanLimit"]
                    processed = parsed["processedPathnameCount"]
                    reconciled = processed == (
                        len(parsed["pathnamesEvidence"]) + parsed["folderPathnameCount"] + parsed["unsafePathnameCount"]
                    )
                    if not reason and not reconciled:
                        reason = "incomplete_pathname_inventory"
                    yield _build_unitypackage_result(path, parsed), _source_read(
                        processed, processed if not reason else None, reason,
                        remaining_at_least=int(reason in {"pathname_entries", "archive_members"}),
                    )


def inspect_outfit_package(
    path_value: str | Path | None, max_entries: int = 5000, *,
    archive_index: ArchiveInspectionIndex | None = None, archive_cursor: str | None = None,
) -> dict[str, Any]:
    source_path = Path(path_value).expanduser().resolve() if path_value else None
    if archive_cursor:
        return archive_index.resume(archive_cursor, source_path) if archive_index else _archive_cursor_error()
    if source_path is None or not source_path.exists():
        return {
            "ok": False,
            "schema": INSPECTION_SCHEMA,
            "error": "Package path does not exist.",
            "source": source_summary(source_path, "missing") if source_path else None,
            "privacy": privacy_policy(),
        }
    try:
        if archive_index and source_path.is_file() and source_path.suffix.lower() in {".zip", ".unitypackage"}:
            result = archive_index.inspect(source_path, max_entries)
        elif source_path.is_dir():
            result = inspect_folder(source_path, max_entries=max_entries)
        elif source_path.suffix.lower() == ".zip":
            result = inspect_zip(source_path, max_entries=max_entries)
        elif source_path.suffix.lower() == ".unitypackage":
            result = inspect_unitypackage(source_path, max_entries=max_entries)
        else:
            result = inspect_single_file(source_path)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return {
            "ok": False,
            "schema": INSPECTION_SCHEMA,
            "error": str(exc),
            "source": source_summary(source_path, "unknown"),
            "privacy": privacy_policy(),
        }
    if archive_index and "sourceRead" not in result:
        summary = result.get("summary", {})
        count = summary.get("entryCount")
        if isinstance(count, int):
            count += sum(row.get("processedPathnameCount", 0) for row in result.get("unityPackages", []))
        incomplete = (not result.get("ok") or count is None or summary.get("truncated")
                      or summary.get("nestedPathnamesTruncated") or summary.get("archiveCensusComplete") is False)
        result["sourceRead"] = _source_read(
            count, count if not incomplete else None, "source_coverage_unknown" if incomplete else None,
            unit="inventory_entries",
        )
    result.setdefault("inspectedAt", utc_now())
    result.setdefault("privacy", privacy_policy())
    return result


def inspect_single_file(path: Path) -> dict[str, Any]:
    category = classify_asset_name(path.name)
    return {
        "ok": category != "unsupported",
        "schema": INSPECTION_SCHEMA,
        "source": source_summary(path, category),
        "summary": {
            "unityPackageCount": 1 if category == "unitypackage" else 0,
            "prefabCandidateCount": 1 if category == "prefab" else 0,
            "textureCount": 1 if category == "texture" else 0,
            "materialCount": 1 if category == "material" else 0,
            "modelCount": 1 if category == "model" else 0,
            "entryCount": 1,
            "truncated": False,
            "importPlanKind": "manual_loose_asset" if category != "unsupported" else "unsupported",
        },
        "unityPackages": [file_entry(path.name, path.stat().st_size)] if category == "unitypackage" else [],
        "prefabCandidates": [file_entry(path.name, path.stat().st_size)] if category == "prefab" else [],
        "textures": [file_entry(path.name, path.stat().st_size)] if category == "texture" else [],
        "materials": [file_entry(path.name, path.stat().st_size)] if category == "material" else [],
        "models": [file_entry(path.name, path.stat().st_size)] if category == "model" else [],
        "warnings": [] if category != "unsupported" else ["Unsupported package input. Expected .unitypackage, Booth .zip, folder, prefab, or texture/material assets."],
    }


def inspect_folder(path: Path, max_entries: int) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    truncated = False
    skipped_symlinks = 0
    nested_pathnames: list[dict[str, Any]] = []
    nested_package_results: list[dict[str, Any]] = []
    nested_warnings: list[str] = []
    parsed_package_count = 0
    archives: list[dict[str, Any]] = []
    archive_entries_remaining = max_entries
    for file_path in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: str(item).lower()):
        if len(entries) >= max_entries:
            truncated = True
            break
        if file_path.is_symlink():
            skipped_symlinks += 1
            continue
        try:
            relative = file_path.relative_to(path).as_posix()
            size = file_path.stat().st_size
            entries.append(file_entry(relative, size))
        except OSError:
            continue
        if file_path.suffix.lower() in {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}:
            archive, scanned = _census_folder_archive(file_path, relative, archive_entries_remaining)
            archives.append(archive)
            archive_entries_remaining -= scanned
        if not relative.lower().endswith(".unitypackage"):
            continue
        remaining = max(0, max_entries - len(nested_pathnames))
        try:
            parsed = inspect_unitypackage(file_path, max_entries=remaining)
        except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as exc:
            nested_warnings.append(f"Could not inspect UnityPackage {relative}: {exc}")
            nested_package_results.append({
                "path": relative,
                "size": size,
                "parsedPathnameCount": 0,
                "pathnamesEvidenceComplete": False,
                "inspectionSkipped": "invalid_unitypackage",
            })
            continue
        package_records = parsed.get("unityPackages")
        package_record = package_records[0] if package_records else None
        if not isinstance(package_record, dict):
            nested_warnings.append(f"Could not inspect UnityPackage {relative}: no pathname inventory was returned.")
            nested_package_results.append({
                "path": relative,
                "size": size,
                "parsedPathnameCount": 0,
                "pathnamesEvidenceComplete": False,
                "inspectionSkipped": "missing_unitypackage_inventory",
            })
            continue
        package_record = dict(package_record)
        package_record["path"] = relative
        pathnames = [
            {**item, "packagePath": relative}
            for item in package_record.get("pathnamesEvidence", [])
            if isinstance(item, dict)
        ]
        package_record["pathnamesEvidence"] = pathnames
        package_record["pathnames"] = pathnames[:200]
        nested_pathnames.extend(pathnames)
        if package_record.get("pathnamesEvidenceComplete") is True:
            parsed_package_count += 1
        else:
            nested_warnings.extend(parsed.get("warnings") or [])
        nested_package_results.append(package_record)

    result = build_container_result(
        path,
        "folder",
        entries,
        truncated,
        can_parse_unitypackage=parsed_package_count > 0,
        archive_inventory_pending=bool(archives),
        extra_warnings=[f"Skipped {skipped_symlinks} symlinked file(s)." if skipped_symlinks else ""],
    )
    nested_entries = [
        file_entry(f"{item['packagePath']}!/{item['path']}", 0)
        for item in nested_pathnames
    ]
    for entry in nested_entries:
        target = {
            "prefab": "prefabCandidates",
            "texture": "textures",
            "material": "materials",
            "model": "models",
            "assembly_definition": "assemblyDefinitions",
        }.get(entry["category"])
        if target:
            result[target].append(entry)
    counts = Counter(item["category"] for item in nested_pathnames)
    result["summary"]["outerEntryCount"] = len(entries)
    result["summary"]["nestedUnityPackageCount"] = parsed_package_count
    result["summary"]["nestedPathnameCount"] = len(nested_pathnames)
    result["summary"]["nestedPathnamesTruncated"] = any(
        item.get("pathnamesEvidenceComplete") is not True
        for item in nested_package_results
    )
    result["summary"]["unsafePathnameCount"] = sum(
        int(item.get("unsafePathnameCount") or 0) for item in nested_package_results
    )
    result["summary"]["prefabCandidateCount"] += counts["prefab"]
    result["summary"]["textureCount"] += counts["texture"]
    result["summary"]["materialCount"] += counts["material"]
    result["summary"]["modelCount"] += counts["model"]
    result["summary"]["assemblyDefinitionCount"] += counts["assembly_definition"]
    result["summary"]["canParseUnityPackagePathnames"] = parsed_package_count > 0
    result["unityPackages"] = nested_package_results or result["unityPackages"]
    result["prefabCandidates"] = result["prefabCandidates"][:200]
    result["textures"] = result["textures"][:200]
    result["materials"] = result["materials"][:200]
    result["models"] = result["models"][:200]
    result["assemblyDefinitions"] = result["assemblyDefinitions"][:200]
    if nested_warnings:
        result["warnings"].extend(nested_warnings)
    if nested_package_results:
        result["summary"]["assetCountScope"] = "folder assets plus parsed UnityPackage pathnames"
        result["summary"]["entryCountScope"] = "direct folder entries; nested pathnames counted separately"
    if archives:
        result["archives"] = archives
        result["summary"].update(
            archiveCount=len(archives),
            archiveUnityPackageCount=sum(row.get("unityPackageCount", 0) for row in archives),
            archiveCensusComplete=not truncated and all(row["status"] == "listed" for row in archives),
            unityPackageCountScope="UnityPackage files in the folder tree; archives are counted separately",
            assetCountScope="Folder assets and parsed loose UnityPackages; archive contents are not included",
        )
        result["warnings"].append(
            "Archive inventory comes from ZIP entry names. Inspect a selected ZIP using its path relative to this folder "
            "joined to the original packagePath; use the same read tool to inspect its UnityPackages. "
            "Unsupported or unopened archives may contain additional packages."
        )
    return result


def _census_folder_archive(path: Path, relative: str, remaining: int) -> tuple[dict[str, Any], int]:
    """Read ZIP central-directory metadata without inflating the members."""
    if path.suffix.lower() != ".zip":
        return {"path": relative, "status": "unsupported"}, 0
    if remaining <= 0:
        return {"path": relative, "status": "unopened", "reason": "archive_entry_limit"}, 0
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile):
        return {"path": relative, "status": "unopened", "reason": "invalid_or_unreadable_zip"}, 0
    packages: dict[str, str] = {}
    exclusions = len(entries) > remaining
    for item in entries[:remaining]:
        if item.is_dir():
            continue
        name = normalize_archive_name(item.filename)
        if item.filename.startswith(("/", "\\")) or not is_safe_archive_path(name):
            exclusions = True
        elif name.lower().endswith(".unitypackage"):
            packages.setdefault(name.casefold(), name)
        elif name.lower().endswith((".zip", ".rar", ".7z", ".tar", ".gz", ".tgz")):
            exclusions = True
    return {"path": relative, "status": "listed_partial" if exclusions else "listed",
            "unityPackageCount": len(packages), "unityPackagePaths": list(packages.values())}, min(len(entries), remaining)


def inspect_zip(path: Path, max_entries: int) -> dict[str, Any]:
    with closing(_page_zip(path, max_entries)) as pages:
        return next(pages)[0]


def _page_zip(path: Path, max_entries: int, *, page_size: int | None = None) -> Generator[tuple[dict[str, Any], dict[str, Any]], None, None]:
    entries: list[dict[str, Any]] = []
    unsafe_entry_count = 0
    duplicate_entry_count = 0
    seen_paths: set[str] = set()
    nested_package_results: list[dict[str, Any]] = []
    nested_warnings: list[str] = []

    def snapshot(index: int, total: int, reason: str | None = None, has_unread_pathnames: bool = False):
        result = _build_zip_result(
            path, entries, index < total, unsafe_entry_count, duplicate_entry_count,
            list(nested_package_results), list(nested_warnings),
        )
        processed = index + sum(row.get("processedPathnameCount", 0) for row in nested_package_results)
        missing_inventory = result["summary"]["nestedPathnamesTruncated"]
        remaining = total - index
        reconciled_outer = index == len(entries) + unsafe_entry_count + duplicate_entry_count + directory_count
        reconciled_nested = all(
            row.get("processedPathnameCount") == (
                row.get("pathnamesEvidenceCount", 0) + row.get("folderPathnameCount", 0)
                + row.get("unsafePathnameCount", 0)
            ) for row in nested_package_results
        )
        total_known = not missing_inventory and reconciled_outer and reconciled_nested and not has_unread_pathnames
        if remaining:
            total_known &= not any(
                row.filename.lower().endswith(".unitypackage") for row in outer_entries[index:]
            )
        final_stop = next((row["scanLimit"] for row in nested_package_results if row.get("scanLimit")), None)
        return result, _source_read(
            processed, processed + remaining if total_known else None,
            reason or final_stop or ("incomplete_nested_inventory" if not total_known else None),
            remaining_at_least=remaining + int(has_unread_pathnames),
        )

    with zipfile.ZipFile(path) as archive:
        outer_entries = sorted(archive.infolist(), key=lambda item: item.filename.lower())
        total_outer_entries = len(outer_entries)
        next_outer_stop = max_entries
        next_pathname_stop = max_entries
        pathname_page_size = page_size if page_size is not None else max_entries
        directory_count = 0
        for index, info in enumerate(outer_entries):
            if page_size is None and info.is_dir():
                directory_count += 1
                continue
            if index >= next_outer_stop:
                yield snapshot(index, total_outer_entries, "archive_entries")
                next_outer_stop += max(1, page_size if page_size is not None else max_entries)
            if info.is_dir():
                directory_count += 1
                continue
            normalized = normalize_archive_name(info.filename)
            if not is_safe_archive_path(normalized):
                unsafe_entry_count += 1
                continue
            lower = normalized.lower()
            if lower in seen_paths:
                duplicate_entry_count += 1
                continue
            seen_paths.add(lower)
            entries.append(file_entry(normalized, int(info.file_size)))
            if not lower.endswith(".unitypackage"):
                continue
            budget_field = "pathnamesEvidenceCount" if page_size is None else "processedPathnameCount"
            remaining = max(0, next_pathname_stop - sum(
                row.get(budget_field, 0) for row in nested_package_results
            ))
            try:
                with archive.open(info, "r") as nested_file:
                    bounded = _BoundedArchiveReader(nested_file, MAX_UNITYPACKAGE_SCAN_INPUT_BYTES)
                    with tarfile.open(fileobj=bounded, mode="r|*") as nested_archive:
                        with closing(_page_unitypackage_members(
                            nested_archive, nested_archive, remaining, normalized, page_size=pathname_page_size,
                        )) as nested_pages:
                            # The same row is replaced on resumption, so totals and
                            # entries are cumulative even when a GUID crosses pages.
                            nested_package_results.append({})
                            for parsed in nested_pages:
                                nested_package_results[-1] = _unitypackage_record(normalized, info.file_size, parsed)
                                if parsed["scanLimit"] == "pathname_entries":
                                    receipt, coverage = snapshot(index + 1, total_outer_entries, "pathname_entries", True)
                                    receipt["warnings"].extend(parsed["warnings"])
                                    yield receipt, coverage
                                    next_pathname_stop += max(1, pathname_page_size)
                            nested_warnings.extend(parsed["warnings"])
            except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as exc:
                nested_warnings.append(f"Could not inspect nested UnityPackage {normalized}: {exc}")
                skipped = {
                    "path": normalized,
                    "size": int(info.file_size),
                    "parsedPathnameCount": 0,
                    "pathnamesEvidenceComplete": False,
                    "inspectionSkipped": "invalid_nested_package",
                }
                if nested_package_results and nested_package_results[-1].get("path") in {normalized, None}:
                    nested_package_results[-1] = skipped
                else:
                    nested_package_results.append(skipped)
        yield snapshot(total_outer_entries, total_outer_entries)


def _build_zip_result(
    path: Path, entries: list[dict[str, Any]], truncated: bool, unsafe_entry_count: int,
    duplicate_entry_count: int, nested_package_results: list[dict[str, Any]], nested_warnings: list[str],
) -> dict[str, Any]:
    nested_pathnames = [
        item for row in nested_package_results for item in row.get("pathnamesEvidence", [])
    ]
    parsed_nested_count = sum("inspectionSkipped" not in row for row in nested_package_results)
    nested_truncated = any(not row.get("pathnamesEvidenceComplete") for row in nested_package_results)
    result = build_container_result(
        path,
        "zip",
        entries,
        truncated,
        can_parse_unitypackage=parsed_nested_count > 0,
        unsafe_entry_count=unsafe_entry_count,
        duplicate_entry_count=duplicate_entry_count,
    )
    nested_entries = [
        file_entry(f"{item['packagePath']}!/{item['path']}", 0)
        for item in nested_pathnames
    ]
    for entry in nested_entries:
        target = {
            "prefab": "prefabCandidates",
            "texture": "textures",
            "material": "materials",
            "model": "models",
            "assembly_definition": "assemblyDefinitions",
        }.get(entry["category"])
        if target:
            result[target].append(entry)
    counts = Counter(item["category"] for item in nested_pathnames)
    result["summary"]["outerEntryCount"] = len(entries)
    result["summary"]["nestedUnityPackageCount"] = parsed_nested_count
    result["summary"]["nestedPathnameCount"] = len(nested_pathnames)
    result["summary"]["nestedPathnamesTruncated"] = nested_truncated
    result["summary"]["unsafePathnameCount"] = sum(
        int(package.get("unsafePathnameCount") or 0) for package in nested_package_results
    )
    result["summary"]["prefabCandidateCount"] += counts["prefab"]
    result["summary"]["textureCount"] += counts["texture"]
    result["summary"]["materialCount"] += counts["material"]
    result["summary"]["modelCount"] += counts["model"]
    result["summary"]["assemblyDefinitionCount"] += counts["assembly_definition"]
    result["summary"]["canParseUnityPackagePathnames"] = parsed_nested_count > 0
    result["unityPackages"] = nested_package_results or result["unityPackages"]
    result["prefabCandidates"] = result["prefabCandidates"][:200]
    result["textures"] = result["textures"][:200]
    result["materials"] = result["materials"][:200]
    result["models"] = result["models"][:200]
    result["assemblyDefinitions"] = result["assemblyDefinitions"][:200]
    if nested_warnings:
        result["warnings"].extend(nested_warnings)
    if nested_package_results:
        result["summary"]["assetCountScope"] = "outer ZIP assets plus parsed nested UnityPackage pathnames"
        result["summary"]["entryCountScope"] = "outer ZIP direct entries; nested pathnames counted separately"
    return result


def inspect_unitypackage(path: Path, max_entries: int) -> dict[str, Any]:
    with path.open("rb") as package_file:
        parsed = inspect_unitypackage_stream(
            _BoundedArchiveReader(package_file, MAX_UNITYPACKAGE_SCAN_INPUT_BYTES),
            max_entries=max_entries,
            package_path=path.name,
        )
    return _build_unitypackage_result(path, parsed)


def _build_unitypackage_result(path: Path, parsed: dict[str, Any]) -> dict[str, Any]:
    pathnames = parsed["pathnamesEvidence"]
    truncated = parsed["truncated"]
    unsafe_pathname_count = parsed["unsafePathnameCount"]
    entries = [file_entry(item["path"], 0) for item in pathnames]
    result = build_container_result(path, "unitypackage", entries, truncated, can_parse_unitypackage=True)
    result["unityPackages"] = [_unitypackage_record(path.name, path.stat().st_size, parsed)]
    result["summary"]["unityPackageCount"] = 1
    result["summary"]["entryCount"] = len(pathnames)
    result["summary"]["importPlanKind"] = "unitypackage_import"
    result["summary"]["unsafePathnameCount"] = unsafe_pathname_count
    result["warnings"] = parsed["warnings"]
    return result


def _unitypackage_record(package_path: str, size: int, parsed: dict[str, Any]) -> dict[str, Any]:
    pathnames = parsed["pathnamesEvidence"]
    return {
        "path": package_path,
        "size": int(size),
        "parsedPathnameCount": len(pathnames),
        "archiveMemberCount": parsed["archiveMemberCount"],
        "processedPathnameCount": parsed["processedPathnameCount"],
        "scanLimit": parsed["scanLimit"],
        "pathnames": pathnames[:200],
        "pathnamesEvidence": pathnames,
        "pathnamesEvidenceCount": len(pathnames),
        "pathnamesEvidenceComplete": (
            not parsed["truncated"] and parsed["missingAssetPayloadCount"] == 0
        ),
        "pathnamesReturnedCount": min(len(pathnames), 200),
        "pathnamesReturnedTruncated": len(pathnames) > 200,
        "unsafePathnameCount": parsed["unsafePathnameCount"],
        "folderPathnameCount": parsed["folderPathnameCount"],
        "missingAssetPayloadCount": parsed["missingAssetPayloadCount"],
    }


def inspect_unitypackage_stream(package_file: Any, max_entries: int, package_path: str) -> dict[str, Any]:
    with tarfile.open(fileobj=package_file, mode="r|*") as archive:
        return _collect_unitypackage_members(archive, archive, max_entries, package_path)


def inspect_open_unitypackage(
    path: Path, archive: tarfile.TarFile, members: Iterable[tarfile.TarInfo], max_entries: int,
) -> dict[str, Any]:
    parsed = _collect_unitypackage_members(archive, members, max_entries, path.name)
    result = _build_unitypackage_result(path, parsed)
    result["inspectedAt"] = utc_now()
    result["privacy"] = privacy_policy()
    return result


def _collect_unitypackage_members(
    archive: tarfile.TarFile, members: Iterable[tarfile.TarInfo], max_entries: int, package_path: str,
) -> dict[str, Any]:
    with closing(_page_unitypackage_members(archive, members, max_entries, package_path)) as pages:
        return next(pages)


def _page_unitypackage_members(
    archive: tarfile.TarFile, members: Iterable[tarfile.TarInfo], max_entries: int, package_path: str,
    *, page_size: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    records: dict[str, dict[str, Any]] = {}
    member_count = 0
    pathname_count = 0
    page_stop = max_entries
    unsafe_pathname_count = 0
    scan_limit = None
    try:
        for member in members:
            member_count += 1
            if member_count > MAX_UNITYPACKAGE_MEMBERS:
                scan_limit = "archive_members"
                break
            if not member.isfile():
                continue
            parts = unitypackage_member_parts(member.name)
            if parts is None or parts[1] not in {"pathname", "asset", "asset.meta"}:
                continue
            record = records.setdefault(
                parts[0],
                {"path": None, "hasAsset": False, "folderAsset": False, "duplicatePathname": False},
            )
            if parts[1] == "asset":
                record["hasAsset"] = True
                continue
            if parts[1] == "asset.meta":
                extracted = archive.extractfile(member)
                if extracted is not None:
                    metadata = extracted.read(65536)
                    record["folderAsset"] = b"folderAsset: yes" in metadata
                    guids = ASSET_META_GUID.findall(metadata)
                    if len(guids) == 1 and guids[0].decode().casefold() == parts[0].casefold():
                        record["verifiedGuid"] = parts[0].casefold()
                continue
            if pathname_count >= page_stop:
                yield _summarize_unitypackage_members(
                    records, package_path, "pathname_entries", member_count - 1,
                    pathname_count, unsafe_pathname_count,
                )
                page_stop += max(1, page_size if page_size is not None else max_entries)
            pathname_count += 1
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            raw = extracted.read(16385)
            if len(raw) > 16384:
                unsafe_pathname_count += 1
                continue
            name = raw.decode("utf-8", errors="replace").strip().replace("\\", "/")
            if name:
                if not is_safe_unitypackage_pathname(name):
                    unsafe_pathname_count += 1
                    continue
                if record["path"] is not None:
                    record["duplicatePathname"] = True
                    unsafe_pathname_count += 1
                else:
                    record["path"] = name
    except tarfile.ReadError as exc:
        if "scan input byte limit exceeded" not in str(exc):
            raise
        scan_limit = "input_bytes"
    yield _summarize_unitypackage_members(
        records, package_path, scan_limit, member_count, pathname_count, unsafe_pathname_count,
    )


def _summarize_unitypackage_members(
    records: dict[str, dict[str, Any]], package_path: str, scan_limit: str | None,
    member_count: int, pathname_count: int, unsafe_pathname_count: int,
) -> dict[str, Any]:
    truncated = scan_limit is not None
    pathnames: list[dict[str, Any]] = []
    folder_pathname_count = 0
    missing_asset_payload_count = 0
    for group, record in records.items():
        pathname = record["path"]
        if not pathname:
            continue
        if record["hasAsset"]:
            item = {"packagePath": package_path, "path": pathname, "category": classify_asset_name(pathname)}
            if item["category"] == "prefab" and UNITY_GUID.fullmatch(group):
                item["archiveGroup"] = group
            if item["category"] == "material" and record.get("verifiedGuid"):
                item["assetGuid"] = record["verifiedGuid"]
            pathnames.append(item)
        elif record["folderAsset"]:
            folder_pathname_count += 1
        else:
            missing_asset_payload_count += 1

    warnings: list[str] = []
    if unsafe_pathname_count:
        warnings.append(f"Skipped {unsafe_pathname_count} unsafe or oversized UnityPackage pathname(s).")
    if folder_pathname_count:
        warnings.append(f"Excluded {folder_pathname_count} UnityPackage folder pathname(s) from the imported file inventory.")
    if missing_asset_payload_count:
        warnings.append(f"UnityPackage has {missing_asset_payload_count} pathname(s) without an asset payload or folder marker; exact file inventory is incomplete.")
    if truncated:
        warnings.append("UnityPackage pathname inspection reached a configured member, byte, or entry limit.")
    if scan_limit == "input_bytes":
        warnings.append("UnityPackage pathname inspection stopped at the scan input byte limit.")
    return {
        "pathnamesEvidence": pathnames,
        "archiveMemberCount": member_count,
        "processedPathnameCount": pathname_count,
        "unsafePathnameCount": unsafe_pathname_count,
        "folderPathnameCount": folder_pathname_count,
        "missingAssetPayloadCount": missing_asset_payload_count,
        "truncated": truncated,
        "scanLimit": scan_limit,
        "warnings": warnings,
    }


def is_safe_unitypackage_pathname(name: str) -> bool:
    normalized = name.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized or normalized.startswith("/"):
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    return all(part not in {".", ".."} for part in normalized.split("/"))


def build_container_result(
    path: Path,
    source_type: str,
    entries: list[dict[str, Any]],
    truncated: bool,
    can_parse_unitypackage: bool,
    unsafe_entry_count: int = 0,
    duplicate_entry_count: int = 0,
    extra_warnings: list[str] | None = None,
    archive_inventory_pending: bool = False,
) -> dict[str, Any]:
    unity_packages = [entry for entry in entries if entry["category"] == "unitypackage"]
    prefabs = [entry for entry in entries if entry["category"] == "prefab"]
    assembly_definitions = [entry for entry in entries if entry["category"] == "assembly_definition"]
    textures = [entry for entry in entries if entry["category"] == "texture"]
    materials = [entry for entry in entries if entry["category"] == "material"]
    models = [entry for entry in entries if entry["category"] == "model"]
    warnings: list[str] = []
    if not unity_packages and prefabs:
        warnings.append("Loose prefab workflow requires explicit user confirmation before copying assets into the Unity project.")
    if textures and not prefabs and not unity_packages:
        warnings.append("Texture-only assets in an unpacked folder can be copied into the project; assigning them to materials is a separate supervised operation.")
    if not (unity_packages or prefabs or textures or materials or models or assembly_definitions or archive_inventory_pending):
        warnings.append("No UnityPackage or loose outfit assets were detected.")
    if unsafe_entry_count:
        warnings.append(f"Skipped {unsafe_entry_count} unsafe archive entr{'y' if unsafe_entry_count == 1 else 'ies'}.")
    if duplicate_entry_count:
        warnings.append(f"Skipped {duplicate_entry_count} duplicate archive entr{'y' if duplicate_entry_count == 1 else 'ies'}.")
    warnings.extend(warning for warning in (extra_warnings or []) if warning)
    single_fbx = is_single_fbx_folder(source_type, models, model_count=len(models), truncated=truncated)
    import_plan_kind = "unitypackage_import" if unity_packages else "loose_prefab_assets" if (prefabs or assembly_definitions or single_fbx) else "manual_review"
    return {
        "ok": True,
        "schema": INSPECTION_SCHEMA,
        "source": source_summary(path, source_type),
        "summary": {
            "unityPackageCount": len(unity_packages),
            "prefabCandidateCount": len(prefabs),
            "textureCount": len(textures),
            "materialCount": len(materials),
            "modelCount": len(models),
            "assemblyDefinitionCount": len(assembly_definitions),
            "entryCount": len(entries),
            "truncated": truncated,
            "importPlanKind": import_plan_kind,
            "canParseUnityPackagePathnames": can_parse_unitypackage,
            "unsafeEntryCount": unsafe_entry_count,
            "duplicateEntryCount": duplicate_entry_count,
        },
        "unityPackages": unity_packages[:200],
        "prefabCandidates": prefabs[:200],
        "textures": textures[:200],
        "materials": materials[:200],
        "models": models[:200],
        "assemblyDefinitions": assembly_definitions[:200],
        "warnings": warnings,
    }


def file_entry(name: str, size: int) -> dict[str, Any]:
    normalized = normalize_archive_name(name)
    return {
        "path": normalized,
        "name": Path(normalized).name,
        "size": int(size),
        "category": classify_asset_name(normalized),
    }


def classify_asset_name(name: str) -> str:
    lower = name.lower()
    suffix = Path(lower).suffix
    if suffix == ".unitypackage":
        return "unitypackage"
    if suffix in LOOSE_PREFAB_EXTENSIONS:
        return "prefab"
    if suffix in ASSEMBLY_DEFINITION_EXTENSIONS:
        return "assembly_definition"
    if suffix in TEXTURE_EXTENSIONS:
        return "texture"
    if suffix in MATERIAL_EXTENSIONS:
        return "material"
    if suffix in MODEL_EXTENSIONS:
        return "model"
    return "unsupported"


def source_summary(path: Path, source_type: str) -> dict[str, Any]:
    summary = {"type": source_type, "name": path.name, "path": str(path)}
    try:
        if path.exists() and path.is_file():
            summary["size"] = path.stat().st_size
    except OSError:
        pass
    return summary


def normalize_archive_name(name: str) -> str:
    return name.replace("\\", "/").strip("/")


def is_safe_archive_path(name: str) -> bool:
    normalized = normalize_archive_name(name)
    if not normalized or normalized.startswith("/") or normalized.startswith("\\"):
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    parts = [part for part in normalized.split("/") if part]
    return all(part not in {".", ".."} for part in parts)


def privacy_policy() -> dict[str, Any]:
    return {
        "localOnly": True,
        "readsArchiveDirectory": True,
        "readsUnityPackagePathnames": True,
        "readsAssetBinaryContents": False,
        "uploadsPaidAssets": False,
        "modelContextReceivesStructuralSummaryOnly": True,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
