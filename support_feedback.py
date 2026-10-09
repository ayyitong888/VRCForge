"""Local support feedback preview helpers.

This module deliberately stops at a redacted, user-reviewable draft. It does
not open a browser, upload an attachment, or contact a support service.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
import hashlib
import json
import zipfile


SUPPORT_FEEDBACK_SCHEMA = "vrcforge.support-feedback-preview.v1"


def build_support_feedback_preview(
    payload: Mapping[str, Any],
    *,
    redact: Callable[[Any], Any],
) -> dict[str, Any]:
    """Return a bounded, redacted draft for the user to review locally."""

    source = {
        "summary": str(payload.get("summary") or "").strip(),
        "steps": str(payload.get("steps") or "").strip(),
        "expected": str(payload.get("expected") or "").strip(),
        "actual": str(payload.get("actual") or "").strip(),
        "supportBundleAvailable": bool(payload.get("supportBundleAvailable")),
    }
    safe = redact(source)
    if not isinstance(safe, Mapping):
        safe = {"summary": "", "steps": "", "expected": "", "actual": "", "supportBundleAvailable": False}

    labels = (
        ("summary", "What happened"),
        ("steps", "Steps to reproduce"),
        ("expected", "Expected result"),
        ("actual", "Actual result"),
    )
    lines = [f"VRCForge support feedback ({SUPPORT_FEEDBACK_SCHEMA})"]
    for key, label in labels:
        value = str(safe.get(key) or "").strip()
        if value:
            lines.extend((f"\n{label}:", value[:4000]))
    bundle_note = "Yes — attach the reviewed support bundle manually." if safe.get("supportBundleAvailable") else "Not attached."
    lines.extend(("\nSupport bundle:", bundle_note))
    lines.extend(
        (
            "\nPrivacy reminder:",
            "Review the text and attachment before sharing. Remove secrets, private paths, paid asset contents, and screenshots you do not want to publish.",
        )
    )
    text = "\n".join(lines)
    return {
        "ok": True,
        "schema": SUPPORT_FEEDBACK_SCHEMA,
        "redacted": True,
        "reviewRequired": True,
        "externalSubmissionAuthorized": False,
        "supportBundleAvailable": bool(safe.get("supportBundleAvailable")),
        "text": text,
    }


def preview_latest_support_bundle(
    support_bundle_dir: Path,
    *,
    redact: Callable[[Any], Any],
) -> dict[str, Any]:
    """Read the newest locally generated bundle for user review.

    The path is selected by the app-owned support-bundle directory; callers
    cannot make this helper read an arbitrary local file.
    """

    owned_dir = Path(support_bundle_dir).resolve()
    candidates = sorted((path for path in owned_dir.glob("vrcforge-support-*.zip") if path.is_file() and not path.is_symlink()), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return {"ok": True, "schema": "vrcforge.support-bundle-preview.v1", "available": False, "members": []}
    bundle_path = candidates[0].resolve()
    if bundle_path.parent != owned_dir:
        return {"ok": False, "schema": "vrcforge.support-bundle-preview.v1", "available": False, "members": [], "error": "Bundle is outside the app-owned support directory."}
    digest = hashlib.sha256()
    with bundle_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    members: list[dict[str, Any]] = []
    with zipfile.ZipFile(bundle_path) as bundle:
        for info in bundle.infolist()[:32]:
            if info.is_dir():
                continue
            truncated = info.file_size > 8192
            with bundle.open(info) as member:
                raw = member.read(8192)
            try:
                value: Any = json.loads(raw.decode("utf-8"))
                safe = redact(value)
                full_excerpt = json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True)
                truncated = truncated or len(full_excerpt) > 1600
                excerpt = full_excerpt[:1600]
            except (UnicodeDecodeError, json.JSONDecodeError):
                safe_text = redact(raw.decode("utf-8", errors="replace"))
                full_excerpt = str(safe_text)
                truncated = truncated or len(full_excerpt) > 1600
                excerpt = full_excerpt[:1600]
            members.append({"name": info.filename, "bytes": info.file_size, "truncated": truncated, "preview": excerpt})
    return {
        "ok": True,
        "schema": "vrcforge.support-bundle-preview.v1",
        "available": True,
        "bundleName": bundle_path.name,
        "bundleSha256": digest.hexdigest(),
        "members": members,
        "reviewRequired": True,
    }
