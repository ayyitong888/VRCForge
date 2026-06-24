from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "vrcforge.rc_release_gate.v1"
DEFAULT_REQUIRED_ARTIFACTS = (
    "VRCForge.unitypackage",
    "VRCForge_Offline_Installer_x64.exe",
    "VRCForge_Web_Installer_x64.exe",
)
DEFAULT_REQUIRED_EVIDENCE = (
    "Golden Path Matrix",
    "External-agent request",
    "Proof viewer",
    "Support bundle",
    "Installer install/uninstall",
    "Portable zip",
)


def main() -> int:
    args = parse_args()
    report = build_rc_release_gate(args)
    output = write_report(report, args.artifacts_dir)
    print(json.dumps({"ok": report["ok"], "status": report["summary"]["status"], "reportPath": str(output)}, indent=2))
    return 0 if report["ok"] or args.allow_blocked else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check VRCForge 0.9.x/RC release evidence readiness.")
    parser.add_argument("--version", default=read_text_file(Path("VERSION")).strip() if Path("VERSION").is_file() else "")
    parser.add_argument("--release-manifest", default="dist/release/release-manifest.json")
    parser.add_argument("--release-evidence", default="docs/RELEASE_EVIDENCE.md")
    parser.add_argument("--proof-matrix", default="docs/PROOF_MATRIX.md")
    parser.add_argument("--artifacts-dir", default="artifacts/rc-release-gate")
    parser.add_argument("--allow-blocked", action="store_true", help="Exit 0 even when required evidence is missing.")
    return parser.parse_args()


def build_rc_release_gate(args: argparse.Namespace) -> dict[str, Any]:
    version = str(args.version or "").strip()
    manifest_path = Path(args.release_manifest)
    evidence_path = Path(args.release_evidence)
    proof_matrix_path = Path(args.proof_matrix)
    steps: list[dict[str, Any]] = []

    manifest_step, manifest_payload = check_release_manifest(manifest_path, version)
    steps.append(manifest_step)
    steps.extend(check_release_artifacts(manifest_path, manifest_payload, version))
    steps.append(check_doc_contains(evidence_path, "release_evidence.exists", version, DEFAULT_REQUIRED_EVIDENCE))
    steps.append(check_doc_contains(proof_matrix_path, "proof_matrix.exists", version, DEFAULT_REQUIRED_EVIDENCE))
    steps.extend(check_referenced_artifacts([evidence_path, proof_matrix_path]))

    blocking = [step for step in steps if step.get("required", True) and not step.get("ok")]
    status = "passed" if not blocking else "blocked"
    return {
        "ok": not blocking,
        "schema": SCHEMA,
        "generatedAt": utc_now(),
        "version": version,
        "summary": {
            "status": status,
            "stepCount": len(steps),
            "blockingCount": len(blocking),
            "blockingSteps": [str(step.get("name")) for step in blocking],
        },
        "steps": steps,
        "policy": {
            "requiresReleaseManifest": True,
            "requiresFourReleaseArtifacts": True,
            "requiresEvidenceDocs": True,
            "liveUnityEvidenceMayBeCarryForwardOnlyWhenDocumented": True,
            "installerAdminGateMayBeBlockedOnlyWithBlockedArtifact": True,
        },
    }


def check_release_manifest(path: Path, version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file():
        return (
            {
                "name": "release_manifest.exists",
                "ok": False,
                "required": True,
                "path": str(path),
                "error": "release manifest is missing; run the strict release build before RC closure",
            },
            {},
        )
    try:
        payload = json.loads(read_text_file(path))
    except Exception as exc:  # noqa: BLE001
        return (
            {
                "name": "release_manifest.parse",
                "ok": False,
                "required": True,
                "path": str(path),
                "error": str(exc),
            },
            {},
        )
    manifest_version = str(payload.get("version") or "").strip()
    artifacts = normalize_manifest_artifacts(payload)
    return (
        {
            "name": "release_manifest.matches_version",
            "ok": bool(manifest_version == version and artifacts),
            "required": True,
            "path": str(path),
            "version": manifest_version,
            "expectedVersion": version,
            "artifactCount": len(artifacts),
            "commit": payload.get("commit") or payload.get("gitCommit") or payload.get("targetCommitish") or "",
            "error": "" if manifest_version == version and artifacts else "manifest version or artifact list is incomplete",
        },
        payload,
    )


def check_release_artifacts(manifest_path: Path, manifest: dict[str, Any], version: str) -> list[dict[str, Any]]:
    artifacts = normalize_manifest_artifacts(manifest)
    by_name = {Path(str(item.get("path") or item.get("name") or "")).name: item for item in artifacts}
    required_names = [*DEFAULT_REQUIRED_ARTIFACTS, f"VRCForge_Windows_x64_{version}.zip"]
    steps: list[dict[str, Any]] = []
    base_dir = manifest_path.parent
    for name in required_names:
        item = by_name.get(name)
        raw_path = str((item or {}).get("path") or name)
        path = Path(raw_path)
        if not path.is_absolute():
            path = base_dir / path
        exists = path.is_file()
        expected_sha = str((item or {}).get("sha256") or (item or {}).get("sha256sum") or "").lower()
        actual_sha = sha256_file(path) if exists and expected_sha else ""
        sha_ok = not expected_sha or actual_sha == expected_sha
        steps.append(
            {
                "name": f"release_artifact.{name}",
                "ok": bool(item and exists and sha_ok),
                "required": True,
                "path": str(path),
                "manifestEntry": bool(item),
                "exists": exists,
                "size": path.stat().st_size if exists else 0,
                "sha256": actual_sha,
                "expectedSha256": expected_sha,
                "error": "" if item and exists and sha_ok else "artifact missing from manifest, filesystem, or checksum mismatch",
            }
        )
    return steps


def check_doc_contains(path: Path, name: str, version: str, required_terms: tuple[str, ...]) -> dict[str, Any]:
    if not path.is_file():
        return {"name": name, "ok": False, "required": True, "path": str(path), "error": "document is missing"}
    text = read_text_file(path)
    missing = [term for term in (version, *required_terms) if term and term.lower() not in text.lower()]
    return {
        "name": name,
        "ok": not missing,
        "required": True,
        "path": str(path),
        "missingTerms": missing,
        "error": "" if not missing else "required release evidence terms are missing",
    }


def check_referenced_artifacts(paths: list[Path]) -> list[dict[str, Any]]:
    artifact_refs: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        artifact_refs.extend(extract_artifact_refs(read_text_file(path)))
    unique_refs = sorted(set(artifact_refs))
    steps: list[dict[str, Any]] = []
    for ref in unique_refs:
        artifact_path = Path(ref)
        steps.append(
            {
                "name": f"referenced_artifact.{ref}",
                "ok": artifact_path.is_file(),
                "required": True,
                "path": ref,
                "error": "" if artifact_path.is_file() else "referenced evidence artifact is missing",
            }
        )
    if not steps:
        steps.append(
            {
                "name": "referenced_artifacts.present",
                "ok": False,
                "required": True,
                "error": "no artifacts\\*.json evidence references were found",
            }
        )
    return steps


def extract_artifact_refs(text: str) -> list[str]:
    refs: list[str] = []
    for raw in text.replace("/", "\\").split("`"):
        item = raw.strip()
        if item.lower().startswith("artifacts\\") and item.lower().endswith(".json"):
            refs.append(item)
    return refs


def normalize_manifest_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("artifacts")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        result: list[dict[str, Any]] = []
        for name, value in raw.items():
            if isinstance(value, dict):
                result.append({"name": str(name), **value})
            else:
                result.append({"name": str(name), "path": str(value)})
        return result
    return []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_report(report: dict[str, Any], artifacts_dir: str) -> Path:
    root = Path(artifacts_dir or "artifacts/rc-release-gate").resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = root / f"rc-release-gate-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
