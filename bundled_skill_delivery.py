"""Deliver the application-owned guide through the existing package projection.

Only the fixed application data below is a delivery source. Files are opened
for the duration of each read; writes belong to the existing locked, atomic
projection into this user's Skill directory. No network, credentials, package
signer trust, or Unity permissions are introduced by bundled distribution.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from skill_package_projection import SkillPackageProjectionService
from skill_packages import (
    SkillPackageError, SkillPackageService, _compare_semver,
    _path_contains_symlink_like,
)


SKILL_NAME = "vrcforge-first-run-guide"
PACKAGE_ID = "com.vrcforge.workflows.first_run_guide"
DOCS_SKILL_NAME = "vrcforge-docs"
DOCS_PACKAGE_ID = "com.vrcforge.workflows.docs"


def bundled_source_dir() -> Path:
    # PyInstaller collects these same files at the same relative path.
    return Path(__file__).resolve().parent / "examples" / "skill-packages" / SKILL_NAME


def _manifest(source: Path, version: str, *, name: str = SKILL_NAME, package_id: str = PACKAGE_ID) -> dict[str, Any]:
    service = SkillPackageService(source.parent, vrcforge_version=version)
    manifest = service.validate_manifest(
        json.loads((source / "manifest.json").read_text(encoding="utf-8")),
        package_root=source,
    )
    if (
        manifest["id"] != package_id
        or manifest.get("skill_name") != name
        or manifest.get("execution", "agentic") != "agentic"
    ):
        raise SkillPackageError("Bundled guide identity or execution mode is invalid.")
    if _compare_semver(version, manifest["min_vrcforge_version"]) < 0:
        raise SkillPackageError("Bundled guide requires a newer VRCForge version.")
    return manifest


def deliver_bundled_guide(
    projection: SkillPackageProjectionService,
    skills_root: Path,
    *,
    version: str,
) -> dict[str, Any]:
    """Seed a new profile; never replace an existing or disabled user Skill."""
    return _deliver(projection, skills_root, bundled_source_dir(), version=version, name=SKILL_NAME, package_id=PACKAGE_ID)


def bundled_docs_source_dir() -> Path:
    return Path(__file__).resolve().parent / "examples" / "skill-packages" / DOCS_SKILL_NAME


def deliver_bundled_docs(
    projection: SkillPackageProjectionService, skills_root: Path, *, version: str,
) -> dict[str, Any]:
    """Use the same atomic projection and preserve existing user content."""
    return _deliver(projection, skills_root, bundled_docs_source_dir(), version=version, name=DOCS_SKILL_NAME, package_id=DOCS_PACKAGE_ID)


def _deliver(
    projection: SkillPackageProjectionService, skills_root: Path, source: Path,
    *, version: str, name: str, package_id: str,
) -> dict[str, Any]:
    manifest = _manifest(source, version, name=name, package_id=package_id)
    # Hold the same lock across the absence check and the projection publish.
    # The caller owns this lock (the package and user-Skill lock in the App).
    if os.path.lexists(skills_root / name):
        return {"status": "preserved", "name": name}
    projected = projection.project_installed(source, manifest, enabled=True)
    return {"status": "installed", "distribution": "bundled", "skill": projected}


def bundled_guide_audit_context(
    skill: dict[str, Any], skills_root: Path, package_store: Path,
) -> dict[str, Any]:
    """Verify exact bundled content; a package state marker alone grants nothing.

    Registered .vsk packages retain their existing trust/revocation validator.
    This identity is only for the shipped agentic instructions, never a signed
    package claim or deterministic execution authorization.
    """
    if skill.get("name") == SKILL_NAME and skill.get("packageId") == PACKAGE_ID:
        name, package_id, source = SKILL_NAME, PACKAGE_ID, bundled_source_dir()
    elif skill.get("name") == DOCS_SKILL_NAME and skill.get("packageId") == DOCS_PACKAGE_ID:
        name, package_id, source = DOCS_SKILL_NAME, DOCS_PACKAGE_ID, bundled_docs_source_dir()
    else:
        return {}
    try:
        raw_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest = _manifest(source, raw_manifest["min_vrcforge_version"], name=name, package_id=package_id)
        service = SkillPackageService(package_store, vrcforge_version=manifest["min_vrcforge_version"])
        if package_id in service.load_registry()["skills"]:
            return {}
        target = skills_root / name
        if Path(str(skill.get("storagePath") or "")) != target / "SKILL.md":
            return {}
        declared = set(manifest["entrypoints"].values()) - {manifest["entrypoints"]["skill"]}
        if set(skill.get("supportFiles") or []) != declared:
            return {}
        service.validate_manifest(manifest, package_root=target)
        digests: dict[str, str] = {}
        for name, relative in manifest["entrypoints"].items():
            projected_relative = "SKILL.md" if name == "skill" else relative
            original = source / relative
            projected = target / projected_relative
            if _path_contains_symlink_like(projected, skills_root):
                return {}
            content = original.read_bytes()
            if content != projected.read_bytes():
                return {}
            digests[projected_relative] = hashlib.sha256(content).hexdigest()
        return {
            "packageId": package_id,
            "packageVersion": manifest["version"],
            "authorId": manifest["author"],
            "distribution": "bundled",
            "signatureStatus": "not_signed",
            "signerFingerprint": None,
            "signerTrustStatus": "not_applicable",
            "contentSha256": hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest(),
            "execution": "agentic",
            "executionMode": "agentic",
        }
    except (OSError, ValueError, KeyError, TypeError, SkillPackageError):
        return {}
