from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "vrcforge.payload_zip_unpack.v1"
REQUIRED_PAYLOAD_FILES = (
    "VRCForge.exe",
    "VERSION",
    "start_dashboard.cmd",
    "backend/vrcforge_backend.exe",
    "dashboard/index.html",
    "tools/uv/uv.exe",
    "tools/uv/uvx.exe",
    "unity_plugin/VRCForge.unitypackage",
    "licenses/VRCForge-GPL-3.0.txt",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unpack the portable release ZIP and emit stable-gate evidence."
    )
    parser.add_argument("--version", default="", help="Expected payload version. Defaults to VERSION.")
    parser.add_argument(
        "--zip",
        dest="zip_path",
        default="",
        help="Portable release ZIP. Defaults to dist/release/VRCForge_Windows_x64_<version>.zip.",
    )
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Artifact root. A payload-smoke/<run> directory is created below it.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def read_expected_version(value: str) -> str:
    version = str(value or "").strip()
    if version:
        return version
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unsafe_archive_members(infos: list[zipfile.ZipInfo]) -> list[str]:
    unsafe: list[str] = []
    seen: set[str] = set()
    for info in infos:
        raw_name = info.filename.replace("\\", "/")
        path = PurePosixPath(raw_name)
        key = raw_name.rstrip("/").casefold()
        drive_like = bool(path.parts and ":" in path.parts[0])
        if not raw_name or "\x00" in raw_name or path.is_absolute() or drive_like or ".." in path.parts:
            unsafe.append(info.filename)
        elif key and not info.is_dir() and key in seen:
            unsafe.append(f"duplicate:{info.filename}")
        elif key and not info.is_dir():
            seen.add(key)
    return unsafe


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    version = read_expected_version(args.version)
    default_zip = REPO_ROOT / "dist" / "release" / f"VRCForge_Windows_x64_{version}.zip"
    zip_path = Path(args.zip_path).expanduser() if args.zip_path else default_zip
    if not zip_path.is_absolute():
        zip_path = REPO_ROOT / zip_path
    zip_path = zip_path.resolve()
    artifacts_root = Path(args.artifacts_dir).expanduser()
    if not artifacts_root.is_absolute():
        artifacts_root = REPO_ROOT / artifacts_root
    run_name = f"v{version.replace('.', '')}-zip-unpack-{run_stamp()}-{os.getpid()}"
    run_dir = artifacts_root.resolve() / "payload-smoke" / run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    summary_path = run_dir / "summary.json"

    missing: list[str] = []
    assertions: list[str] = []
    unsafe_members: list[str] = []
    bad_member = ""
    embedded_version = ""
    entry_count = 0
    extracted_file_count = 0
    extracted_bytes = 0
    archive_bytes = zip_path.stat().st_size if zip_path.is_file() else 0
    archive_sha256 = ""
    extraction_dir = Path(tempfile.mkdtemp(prefix="vrcforge-payload-smoke-"))
    cleanup_ok = False

    try:
        if not zip_path.is_file():
            missing.extend(REQUIRED_PAYLOAD_FILES)
            raise FileNotFoundError(f"portable ZIP was not found: {zip_path}")
        archive_sha256 = sha256_file(zip_path)
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            entry_count = len(infos)
            unsafe_members = unsafe_archive_members(infos)
            if unsafe_members:
                raise RuntimeError(f"portable ZIP has unsafe or duplicate members: {unsafe_members[:10]}")
            bad_member = str(archive.testzip() or "")
            if bad_member:
                raise RuntimeError(f"portable ZIP CRC failed for {bad_member}")
            archive.extractall(extraction_dir)

        for relative in REQUIRED_PAYLOAD_FILES:
            candidate = extraction_dir.joinpath(*PurePosixPath(relative).parts)
            if not candidate.is_file():
                missing.append(relative)

        version_path = extraction_dir / "VERSION"
        if version_path.is_file():
            embedded_version = version_path.read_text(encoding="utf-8").strip()
            if embedded_version != version:
                missing.append(f"VERSION(expected={version},actual={embedded_version or '<empty>'})")

        for path in extraction_dir.rglob("*"):
            if path.is_file():
                extracted_file_count += 1
                extracted_bytes += path.stat().st_size
        if missing:
            assertions.append(f"portable payload is missing or mismatches required entries: {missing}")
    except (OSError, RuntimeError, zipfile.BadZipFile, UnicodeError) as exc:
        assertions.append(str(exc))
    finally:
        try:
            shutil.rmtree(extraction_dir)
            cleanup_ok = not extraction_dir.exists()
        except OSError as exc:
            assertions.append(f"temporary extraction cleanup failed: {exc}")

    if not cleanup_ok:
        assertions.append("temporary extraction directory still exists")
    assertions = list(dict.fromkeys(assertions))
    ok = bool(
        zip_path.is_file()
        and not missing
        and not unsafe_members
        and not bad_member
        and embedded_version == version
        and cleanup_ok
        and not assertions
    )
    summary = {
        "schema": SCHEMA,
        "ok": ok,
        "generatedAt": utc_now(),
        "version": version,
        "zip": str(zip_path),
        "missing": missing,
        "required": list(REQUIRED_PAYLOAD_FILES),
        "embeddedVersion": embedded_version,
        "archiveBytes": archive_bytes,
        "archiveSha256": archive_sha256,
        "entryCount": entry_count,
        "extractedFileCount": extracted_file_count,
        "extractedBytes": extracted_bytes,
        "unsafeMembers": unsafe_members,
        "badMember": bad_member,
        "temporaryExtractionRemoved": cleanup_ok,
        "assertions": assertions,
    }
    write_summary(summary_path, summary)
    print(summary_path)
    if not ok:
        for assertion in assertions:
            print(assertion, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
