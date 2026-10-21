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

from agent_gateway import PROJECTED_SKILL_STATE_NAME

from skill_package_projection import (
    PROJECTION_DIGEST_DOMAIN, PROJECTION_MAX_ENTRIES, PROJECTION_MAX_FILE_BYTES,
    PROJECTION_MAX_TOTAL_BYTES, SkillPackageProjectionService,
)
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
    """Validate the application-owned source; no runtime copy is needed."""
    _manifest(bundled_source_dir(), version, name=SKILL_NAME, package_id=PACKAGE_ID)
    return {"status": "application-owned", "name": SKILL_NAME}


def bundled_docs_source_dir() -> Path:
    return Path(__file__).resolve().parent / "examples" / "skill-packages" / DOCS_SKILL_NAME


def _runtime_version() -> str:
    from dashboard_foundation import read_vrcforge_version

    return read_vrcforge_version()


def deliver_bundled_docs(
    projection: SkillPackageProjectionService, skills_root: Path, *, version: str,
) -> dict[str, Any]:
    """Validate the application-owned source; no runtime copy is needed."""
    _manifest(bundled_docs_source_dir(), version, name=DOCS_SKILL_NAME, package_id=DOCS_PACKAGE_ID)
    return {"status": "application-owned", "name": DOCS_SKILL_NAME}


def application_skills(version: str | None = None) -> list[dict[str, Any]]:
    """Return the two fixed bundled agentic sources after manifest validation."""
    result = []
    for source, name, package_id in (
        (bundled_source_dir(), SKILL_NAME, PACKAGE_ID),
        (bundled_docs_source_dir(), DOCS_SKILL_NAME, DOCS_PACKAGE_ID),
    ):
        manifest = _manifest(source, version or _runtime_version(), name=name, package_id=package_id)
        file_hashes = {
            relative: hashlib.sha256((source / relative).read_bytes()).hexdigest()
            for relative in manifest["entrypoints"].values()
        }
        content_digest = hashlib.sha256(json.dumps(
            {"manifest": manifest, "files": file_hashes}, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        result.append({
            "packageId": package_id, "packageVersion": manifest["version"],
            "bundleRoot": str(source.resolve()), "storagePath": str((source / "SKILL.md").resolve()),
            "supportFiles": [value for key, value in manifest["entrypoints"].items() if key != "skill"],
            "contentDigest": content_digest,
        })
    return result


def validate_application_skill_source(skill: dict[str, Any]) -> Path:
    """Revalidate fixed application identity and the complete manifest before reads."""
    for item in application_skills():
        if (
            item["packageId"] == skill.get("packageId")
            and item["storagePath"] == str(Path(str(skill.get("storagePath") or "")).resolve())
            and item["bundleRoot"] == str(Path(str(skill.get("bundleRoot") or "")).resolve())
            and item["contentDigest"] == skill.get("contentDigest")
        ):
            return Path(item["bundleRoot"])
    raise SkillPackageError("Application Skill identity or source path is invalid.")


def is_exact_legacy_projection(skill: dict[str, Any], package_store: Path | None = None) -> bool:
    """Recognize an untouched former app projection by its existing owner + tree digest."""
    try:
        source = next(item for item in application_skills() if item["packageId"] == skill.get("packageId"))
        target = Path(str(skill.get("storagePath") or "")).parent
        if target.name != Path(source["storagePath"]).parent.name:
            return False
        state_path = target / PROJECTED_SKILL_STATE_NAME
        if _path_contains_symlink_like(state_path, target) or state_path.stat(follow_symlinks=False).st_size > 4096:
            return False
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("packageId") != source["packageId"]:
            return False
        if state.get("projectionDigest") != _projection_digest(target):
            return False
        # Reuse the package service's existing official identity. The projection
        # owner/digest above detects user edits; loading always uses the fixed
        # application source, so no second per-file package verification is needed.
        if package_store is not None:
            service = SkillPackageService(package_store, vrcforge_version=_runtime_version())
            installed = service.load_registry()["skills"]
            if source["packageId"] in installed:
                decorated = {item["id"]: item for item in service.list_installed()}.get(source["packageId"], {})
                if not decorated.get("official") or not decorated.get("verified"):
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError, StopIteration, SkillPackageError):
        return False


def _projection_digest(root: Path) -> str:
    """Match the existing projection owner's tree digest for legacy ownership checks."""
    entries: list[tuple[bytes, str, Path | None]] = []
    for path in root.rglob("*"):
        if _path_contains_symlink_like(path, root):
            raise OSError("legacy projection contains a linked path")
        if path == root / PROJECTED_SKILL_STATE_NAME:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append((b"D", relative, None))
        elif path.is_file():
            metadata = path.stat(follow_symlinks=False)
            if metadata.st_size > PROJECTION_MAX_FILE_BYTES:
                raise OSError("legacy projection file exceeds size limit")
            entries.append((b"F", relative, path))
        else:
            raise OSError("legacy projection contains a non-regular path")
        if len(entries) > PROJECTION_MAX_ENTRIES:
            raise OSError("legacy projection contains too many entries")
    entries.sort(key=lambda item: (item[1].casefold(), item[1], item[0]))
    digest = hashlib.sha256(PROJECTION_DIGEST_DOMAIN)
    digest.update(len(entries).to_bytes(8, "big"))
    total_bytes = 0
    for kind, relative, path in entries:
        encoded = relative.encode("utf-8")
        digest.update(kind)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        if path is not None:
            size = path.stat(follow_symlinks=False).st_size
            total_bytes += size
            if total_bytes > PROJECTION_MAX_TOTAL_BYTES:
                raise OSError("legacy projection exceeds total size limit")
            digest.update(size.to_bytes(8, "big"))
            consumed = 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    consumed += len(chunk)
                    if consumed > size:
                        raise OSError("legacy projection changed during digest verification")
                    digest.update(chunk)
            if consumed != size:
                raise OSError("legacy projection changed during digest verification")
    return digest.hexdigest()


def bundled_guide_audit_context(
    skill: dict[str, Any], skills_root: Path, package_store: Path,
) -> dict[str, Any]:
    """Verify exact bundled content; a package state marker alone grants nothing.

    Registered .vsk packages retain their existing trust/revocation validator.
    This identity is only for the shipped agentic instructions, never a signed
    package claim or deterministic execution authorization.
    """
    if not isinstance(skill, dict):
        return {}
    if skill.get("source") == "builtin" and skill.get("distribution") == "bundled":
        try:
            root = validate_application_skill_source(skill)
            raw_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest = _manifest(root, _runtime_version(), name=str(skill.get("name")), package_id=str(skill.get("packageId")))
            if skill.get("storagePath") != str((root / "SKILL.md").resolve()):
                return {}
            declared = set(manifest["entrypoints"].values()) - {manifest["entrypoints"]["skill"]}
            if set(skill.get("supportFiles") or []) != declared:
                return {}
            files = {}
            for key, relative in manifest["entrypoints"].items():
                data = (root / relative).read_bytes()
                files[relative] = hashlib.sha256(data).hexdigest()
            return {
                "packageId": manifest["id"], "packageVersion": manifest["version"],
                "authorId": manifest["author"], "distribution": "bundled",
                "signatureStatus": "not_signed", "signerFingerprint": None,
                "signerTrustStatus": "not_applicable",
                "contentSha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                "execution": "agentic", "executionMode": "agentic",
            }
        except (OSError, ValueError, KeyError, TypeError, SkillPackageError):
            return {}
    if skill.get("name") == SKILL_NAME and skill.get("packageId") == PACKAGE_ID:
        name, package_id, source = SKILL_NAME, PACKAGE_ID, bundled_source_dir()
    elif skill.get("name") == DOCS_SKILL_NAME and skill.get("packageId") == DOCS_PACKAGE_ID:
        name, package_id, source = DOCS_SKILL_NAME, DOCS_PACKAGE_ID, bundled_docs_source_dir()
    else:
        return {}
    try:
        raw_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest = _manifest(source, _runtime_version(), name=name, package_id=package_id)
        service = SkillPackageService(package_store, vrcforge_version=_runtime_version())
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
