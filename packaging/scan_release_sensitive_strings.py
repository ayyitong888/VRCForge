#!/usr/bin/env python3
"""Reject high-confidence secrets and machine-local paths before release packaging.

The source phase reads only files below the repository root.  It deliberately
does not read user profiles, application configuration outside the checkout,
or environment-variable values.  The artifact phase scans release files after
they are built.  Diagnostics name only the location and rule, never the match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Iterable


EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "artifacts",
        "build",
        "cache",
        "dist",
        "library",
        "logs",
        "node_modules",
        "obj",
        "target",
        "temp",
    }
)
BINARY_SUFFIXES = frozenset(
    {".7z", ".dll", ".exe", ".gif", ".ico", ".jar", ".jpeg", ".jpg", ".pdf", ".png", ".zip"}
)
CERTIFI_CA_BUNDLE_PATH = "backend/_internal/certifi/cacert.pem"
BASELINE_FILE = Path(__file__).with_name("release_sensitive_string_baseline.json")
SOURCE_INPUT_DIRECTORIES = (
    Path("Assets") / "VRCForge",
    Path("dashboard"),
    Path("installer"),
    Path("launcher"),
    Path("packaging"),
    Path("src"),
    Path("src-tauri"),
    Path("tools"),
)
COPIED_SOURCE_DIRECTORIES = frozenset(
    {Path("Assets") / "VRCForge", Path("dashboard"), Path("src"), Path("tools")}
)
SOURCE_INPUT_FILES = frozenset(
    {
        "DEPENDENCIES.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "USER_MANUAL.md",
        "VERSION",
        "dashboard_server.py",
        "index.html",
        "package-lock.json",
        "package.json",
        "postcss.config.js",
        "start_dashboard.cmd",
        "tailwind.config.ts",
        "tsconfig.json",
        "tsconfig.node.json",
        "vite.config.ts",
    }
)

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"^\s*-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")),
    ("certificate", re.compile(r"^\s*-----BEGIN CERTIFICATE-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}")),
    (
        "credential_url",
        re.compile(r"(?i)\b(?:https?|[a-z][a-z0-9+.-]*)://[^\s/@:]+:[^\s/@]+@"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?:[\"']?)\b(?:api[_-]?key|access[_-]?token|secret(?:[_-]?key)?|client[_-]?secret|password)\b"
            r"(?:[\"']?)\s*[:=]\s*[\"'][A-Za-z0-9._~+/-]{20,}[\"']"
        ),
    ),
    (
        "windows_machine_path",
        re.compile(r"(?i)(?:\b[A-Z]:\\(?:users|documents and settings)\\[^\\\r\n]+|\\\\[^\\\r\n]+\\users\\[^\\\r\n]+)"),
    ),
)
UNQUOTED_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|secret(?:[_-]?key)?|client[_-]?secret|password)"
    r"\s*[:=]\s*[A-Za-z0-9._~+/-]{20,}(?![A-Za-z0-9._~+/-])"
)
CONFIG_SUFFIXES = frozenset({".cfg", ".env", ".ini", ".properties", ".toml", ".yaml", ".yml"})
RAW_BINARY_RULES: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private_key", re.compile(br"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")),
    ("github_token", re.compile(br"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("openai_api_key", re.compile(br"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(br"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    ("bearer_token", re.compile(br"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}")),
)


def release_candidate_paths(repo_root: Path) -> list[Path]:
    """Return release build inputs, including ignored local files in copied roots."""
    paths: set[Path] = set()
    for source_root in SOURCE_INPUT_DIRECTORIES:
        absolute_root = repo_root / source_root
        if not absolute_root.is_dir():
            continue
        for absolute_path in absolute_root.rglob("*"):
            if not absolute_path.is_file():
                continue
            relative_path = absolute_path.relative_to(repo_root)
            if source_root in COPIED_SOURCE_DIRECTORIES or not any(
                part.casefold() in EXCLUDED_PARTS for part in relative_path.parts
            ):
                paths.add(relative_path)
    for direct_name in SOURCE_INPUT_FILES:
        direct_path = repo_root / direct_name
        if direct_path.is_file():
            paths.add(Path(direct_name))
    for direct_path in repo_root.glob("*.py"):
        if direct_path.is_file():
            paths.add(direct_path.relative_to(repo_root))
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def is_scannable(relative_path: Path, absolute_path: Path) -> bool:
    if relative_path.suffix.casefold() in BINARY_SUFFIXES or not absolute_path.is_file():
        return False
    return True


def load_baseline() -> frozenset[tuple[str, str, str]]:
    payload = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return frozenset((entry["path"], entry["rule"], entry["sha256"]) for entry in payload["entries"])


def decode_text(content: bytes) -> tuple[str, list[bytes]] | None:
    """Decode UTF-8/UTF-16 text while refusing ordinary NUL-bearing binaries."""
    if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        try:
            return content.decode("utf-32"), [line.encode("utf-8") for line in content.decode("utf-32").splitlines()]
        except UnicodeDecodeError:
            return None
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = content.decode("utf-16")
        except UnicodeDecodeError:
            return None
        return text, [line.encode("utf-8") for line in text.splitlines()]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if "\0" in text:
        return None
    return text, content.splitlines()


def is_configuration_path(display_path: str) -> bool:
    path = Path(display_path)
    return path.name.casefold().startswith(".env") or path.suffix.casefold() in CONFIG_SUFFIXES


def text_findings(
    display_path: str,
    content: bytes,
    baseline: frozenset[tuple[str, str, str]] = frozenset(),
    *,
    allow_certifi_bundle: bool = False,
) -> list[tuple[str, int, str]]:
    decoded = decode_text(content)
    if decoded is None:
        return []
    text, raw_lines = decoded
    findings: list[tuple[str, int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        fingerprint = hashlib.sha256(raw_lines[line_number - 1]).hexdigest()
        for rule, pattern in RULES:
            if (
                rule == "certificate"
                and allow_certifi_bundle
                and display_path.replace("\\", "/") == CERTIFI_CA_BUNDLE_PATH
            ):
                continue
            if pattern.search(line) and (display_path, rule, fingerprint) not in baseline:
                findings.append((display_path, line_number, rule))
        if (
            is_configuration_path(display_path)
            and UNQUOTED_CREDENTIAL_ASSIGNMENT.search(line)
            and (display_path, "credential_assignment", fingerprint) not in baseline
        ):
            findings.append((display_path, line_number, "credential_assignment"))
    return findings


def findings_for_file(repo_root: Path, relative_path: Path, baseline: frozenset[tuple[str, str, str]]) -> list[tuple[str, int, str]]:
    absolute_path = repo_root / relative_path
    if not is_scannable(relative_path, absolute_path):
        return []
    try:
        content = absolute_path.read_bytes()
    except OSError:
        return []
    return text_findings(relative_path.as_posix(), content, baseline)


def scan(repo_root: Path) -> list[tuple[str, int, str]]:
    baseline = load_baseline()
    findings: list[tuple[str, int, str]] = []
    for relative_path in release_candidate_paths(repo_root):
        findings.extend(findings_for_file(repo_root, relative_path, baseline))
    return findings


def _zip_entries(artifact_path: Path) -> Iterable[tuple[str, bytes]]:
    with zipfile.ZipFile(artifact_path) as archive:
        for entry in archive.infolist():
            if not entry.is_dir():
                yield entry.filename.replace("\\", "/"), archive.read(entry)


def _unitypackage_entries(artifact_path: Path) -> Iterable[tuple[str, bytes]]:
    with tarfile.open(artifact_path, mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        pathnames: dict[str, str] = {}
        for member in members:
            if not member.name.endswith("/pathname"):
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            pathnames[member.name.rsplit("/", 1)[0]] = source.read().decode("utf-8", errors="replace").strip()
        for member in members:
            if not member.name.endswith("/asset"):
                continue
            source = archive.extractfile(member)
            if source is not None:
                yield pathnames.get(member.name.rsplit("/", 1)[0], member.name), source.read()


def raw_binary_findings(display_path: str, content: bytes) -> list[tuple[str, int, str]]:
    return [(display_path, 0, rule) for rule, pattern in RAW_BINARY_RULES if pattern.search(content)]


def artifact_findings(artifact_path: Path) -> list[tuple[str, int, str]]:
    suffix = artifact_path.suffix.casefold()
    findings: list[tuple[str, int, str]] = []
    if suffix == ".zip":
        entries = _zip_entries(artifact_path)
    elif suffix == ".unitypackage":
        entries = _unitypackage_entries(artifact_path)
    else:
        try:
            return raw_binary_findings(artifact_path.name, artifact_path.read_bytes())
        except OSError:
            return [(artifact_path.name, 0, "artifact_unreadable")]
    try:
        for logical_path, content in entries:
            findings.extend(
                text_findings(
                    logical_path,
                    content,
                    allow_certifi_bundle=logical_path.replace("\\", "/") == CERTIFI_CA_BUNDLE_PATH,
                )
            )
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return [(artifact_path.name, 0, "artifact_unreadable")]
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="scan release inputs and artifacts without exposing matches")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.repo_root is None and not args.artifact:
        parser.error("one of --repo-root or --artifact is required")
    findings: list[tuple[str, int, str]] = []
    if args.repo_root is not None:
        findings.extend(scan(args.repo_root.resolve()))
    for artifact_path in args.artifact:
        findings.extend(artifact_findings(artifact_path.resolve()))
    for path, line, rule in findings:
        location = f"{path}:{line}" if line else path
        print(f"release-sensitive-scan: {location}: {rule}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
