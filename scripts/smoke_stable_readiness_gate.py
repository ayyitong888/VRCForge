from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "vrcforge.stable_readiness_gate.v1"

VERSIONED_PUBLIC_DOCS = (
    "README.md",
    "USER_MANUAL.md",
    "packaging/README.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
)
PUBLIC_GOLDEN_PATH_TERMS = (
    "Install and first run",
    "Connect Unity",
    "Provider / BYOK / local-only / no-provider",
    "Doctor",
    "First validation report",
    "First rollback",
    "Booth outfit",
    "model optimization",
    "external agents",
    ".vsk",
    "support bundle",
)
PRIVACY_TERMS = (
    "Privacy Boundary",
    "API key",
    "Gateway token",
    "paid asset",
    "private files",
    "support bundle",
    ".vsk export",
)
COMPATIBILITY_TERMS = (
    "Unity",
    "VRChat SDK",
    "Modular Avatar",
    "NDMF",
    "VRCFury",
    "AAO",
    "LAC",
    "TTT",
    "Meshia",
    "MA2BT-Pro",
    "Thry",
    "lilToon",
    "Poiyomi",
    "Known conflicts",
    "Known safe profiles",
)
AVATAR_ENCRYPTION_BOUNDARY_TERMS = (
    "Avatar Encryption / Anti-Rip addon",
    "private-addon connector",
    "private module required",
    "approval",
    "checkpoint",
    "rollback",
)
OPTIMIZER_STATE_TERMS = (
    "missing_dependency",
    "detected",
    "plan_available",
    "request_blocked_missing_options",
    "request_ready",
    "approval_pending",
    "checkpoint_created",
    "applied",
    "validation_done",
    "rollback_requested",
    "rollback_done",
    "proof_passed",
    "proof_failed",
    "stable_candidate",
    "experimental_only",
)
METADATA_FILES = (
    "VERSION",
    "package.json",
    "package-lock.json",
    "src-tauri/tauri.conf.json",
    "src-tauri/Cargo.toml",
)
RELEASE_ARTIFACTS = (
    "VRCForge.unitypackage",
    "VRCForge_Offline_Installer_x64.exe",
    "VRCForge_Web_Installer_x64.exe",
)


def main() -> int:
    args = parse_args()
    report = build_stable_readiness_gate(args)
    output = write_report(report, args.artifacts_dir)
    print(json.dumps({"ok": report["ok"], "status": report["summary"]["status"], "reportPath": str(output)}, indent=2))
    return 0 if report["ok"] or args.allow_blocked else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check VRCForge public-stable readiness and release evidence gates.")
    parser.add_argument("--version", default=read_text_file(Path("VERSION")).strip() if Path("VERSION").is_file() else "")
    parser.add_argument("--compatibility-matrix", default="docs/COMPATIBILITY_MATRIX.md")
    parser.add_argument("--release-manifest", default="dist/release/release-manifest.json")
    parser.add_argument("--packaged-backend-smoke", default="")
    parser.add_argument("--payload-zip-smoke", default="")
    parser.add_argument("--golden-path-matrix", default="")
    parser.add_argument("--optimizer-request-guard-smoke", default="")
    parser.add_argument("--external-agent-smoke", default="")
    parser.add_argument("--installer-smoke", default="")
    parser.add_argument("--release-evidence", default="docs/RELEASE_EVIDENCE.md")
    parser.add_argument("--proof-matrix", default="docs/PROOF_MATRIX.md")
    parser.add_argument("--artifacts-dir", default="artifacts/stable-readiness-gate")
    parser.add_argument("--stale-version", action="append", default=["0.9.0-beta"], help="Stale version string that must not appear in current public docs.")
    parser.add_argument("--allow-blocked", action="store_true", help="Exit 0 even when required readiness gates are blocked.")
    parser.add_argument(
        "--max-artifact-age-hours",
        type=float,
        default=0.0,
        help="If > 0, each smoke/proof artifact must have been written within this many hours, else the gate blocks. Default 0 disables the freshness guard.",
    )
    parser.add_argument(
        "--require-live-writes",
        action="store_true",
        help="Require the Golden Path Matrix artifact to prove real live writes (safeDefault=false) instead of accepting the safe-default writes-skipped run.",
    )
    return parser.parse_args()


def build_stable_readiness_gate(args: argparse.Namespace) -> dict[str, Any]:
    version = str(args.version or "").strip()
    stale_versions = tuple(str(item) for item in (args.stale_version or []) if str(item).strip())
    steps: list[dict[str, Any]] = []

    steps.append(check_metadata_versions(version))
    steps.extend(check_versioned_public_docs(version, stale_versions))
    steps.append(check_public_doc_terms("public_docs.golden_paths", PUBLIC_GOLDEN_PATH_TERMS, VERSIONED_PUBLIC_DOCS))
    steps.append(check_public_doc_terms("public_docs.privacy_boundary", PRIVACY_TERMS, ("README.md", "USER_MANUAL.md", ".github/ISSUE_TEMPLATE/bug_report.yml")))
    steps.append(check_public_doc_terms("public_docs.avatar_encryption_preview_boundary", AVATAR_ENCRYPTION_BOUNDARY_TERMS, ("README.md", "USER_MANUAL.md", "docs/COMPATIBILITY_MATRIX.md")))
    steps.append(check_doc_contains(Path(args.compatibility_matrix), "compatibility_matrix.exists", COMPATIBILITY_TERMS, required=True))
    steps.append(check_doc_contains(Path("docs/OPTIMIZATION_STRATEGY.md"), "optimizer_state_machine.public", OPTIMIZER_STATE_TERMS, required=True))
    steps.append(check_doc_contains(Path("docs/RELEASE_CHECKLIST.md"), "release_checklist.stable_gate", ("smoke_stable_readiness_gate.py", "COMPATIBILITY_MATRIX", "support bundle"), required=True))
    release_manifest_path = Path(args.release_manifest)
    steps.append(check_release_manifest(release_manifest_path, version))
    manifest_hashes = release_manifest_hashes(release_manifest_path)

    # Resolve smoke/proof artifacts once so the same path feeds both the content
    # check and the optional freshness guard below.
    max_age_hours = float(getattr(args, "max_artifact_age_hours", 0.0) or 0.0)
    require_live_writes = bool(getattr(args, "require_live_writes", False))
    packaged_backend_path = resolve_evidence_path(args.packaged_backend_smoke, "artifacts/packaged-backend-smoke-*/packaged-bootstrap-summary.json")
    payload_zip_path = resolve_evidence_path(args.payload_zip_smoke, "artifacts/payload-smoke/*/summary.json")
    golden_path_matrix_path = resolve_evidence_path(args.golden_path_matrix, "artifacts/golden-path-matrix/*.json")
    optimizer_guard_path = resolve_evidence_path(args.optimizer_request_guard_smoke, "artifacts/optimizer-request-guard-smoke/*.json")
    external_agent_path = resolve_evidence_path(args.external_agent_smoke, "artifacts/external-agent-smoke/*.json")
    installer_path = resolve_evidence_path(args.installer_smoke, "artifacts/installer-smoke/installer-install-uninstall-*.json")

    payload_name = f"VRCForge_Windows_x64_{version}.zip"
    steps.append(check_packaged_backend(packaged_backend_path, version, manifest_hashes.get(payload_name, "")))
    steps.append(check_payload_zip(payload_zip_path, version, manifest_hashes.get(payload_name, "")))
    steps.append(check_golden_path_matrix(golden_path_matrix_path, require_live_writes=require_live_writes))
    steps.append(check_optimizer_request_guard(optimizer_guard_path))
    steps.append(check_external_agent_smoke(external_agent_path))
    steps.append(
        check_installer_smoke(
            installer_path,
            version,
            manifest_hashes.get("VRCForge_Offline_Installer_x64.exe", ""),
        )
    )

    # Freshness guard (opt-in). Default 0 disables it so existing callers keep their
    # exact behavior; CI/release can pass a window so a stale-but-passing artifact
    # cannot carry the gate. Run artifacts only — versioned docs and the fixed
    # release manifest are intentionally not age-checked.
    if max_age_hours > 0:
        for fresh_name, fresh_path in (
            ("packaged_backend_smoke", packaged_backend_path),
            ("payload_zip_smoke", payload_zip_path),
            ("golden_path_matrix", golden_path_matrix_path),
            ("optimizer_request_guard_smoke", optimizer_guard_path),
            ("external_agent_smoke", external_agent_path),
            ("installer_smoke", installer_path),
        ):
            steps.append(check_artifact_freshness(f"freshness.{fresh_name}", fresh_path, max_age_hours))

    steps.append(check_doc_contains(Path(args.release_evidence), "local_release_evidence.current", (version, "Golden Path Matrix", "Installer install/uninstall"), required=False))
    steps.append(check_doc_contains(Path(args.proof_matrix), "local_proof_matrix.current", (version, "Golden Path Matrix", "External-agent request"), required=False))

    blocking = [step for step in steps if step.get("required", True) and not step.get("ok")]
    warnings = [step for step in steps if not step.get("required", True) and not step.get("ok")]
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
            "warningCount": len(warnings),
            "blockingSteps": [str(step.get("name")) for step in blocking],
            "warningSteps": [str(step.get("name")) for step in warnings],
        },
        "steps": steps,
        "policy": {
            "publicDocsMustMatchCurrentVersion": True,
            "releaseArtifactsAndPackagedSmokesRequired": True,
            "compatibilityMatrixRequiredBeforeStable": True,
            "privacyBoundaryRequiredBeforeStable": True,
            "installerAdminBlockedKeepsGateBlockedUnlessAllowBlocked": True,
            "localEvidenceDocsAreAdvisoryForFreshClones": True,
            "maxArtifactAgeHours": max_age_hours if max_age_hours > 0 else None,
            "requireLiveWrites": require_live_writes,
        },
    }


def check_metadata_versions(version: str) -> dict[str, Any]:
    missing_files: list[str] = []
    missing_version: list[str] = []
    for item in METADATA_FILES:
        path = Path(item)
        if not path.is_file():
            missing_files.append(item)
            continue
        if version not in read_text_file(path):
            missing_version.append(item)
    return {
        "name": "metadata.version_alignment",
        "ok": bool(version and not missing_files and not missing_version),
        "required": True,
        "version": version,
        "files": list(METADATA_FILES),
        "missingFiles": missing_files,
        "missingVersion": missing_version,
        "error": "" if version and not missing_files and not missing_version else "version metadata is missing or not aligned",
    }


def check_versioned_public_docs(version: str, stale_versions: tuple[str, ...]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in VERSIONED_PUBLIC_DOCS:
        path = Path(item)
        if not path.is_file():
            steps.append({"name": f"public_doc.{item}.exists", "ok": False, "required": True, "path": item, "error": "document is missing"})
            continue
        text = read_text_file(path)
        stale_found = [stale for stale in stale_versions if stale and stale in text]
        steps.append(
            {
                "name": f"public_doc.{item}.current_version",
                "ok": bool(version in text and not stale_found),
                "required": True,
                "path": item,
                "version": version,
                "staleVersions": stale_found,
                "error": "" if version in text and not stale_found else "public document does not match the current release version",
            }
        )
    return steps


def check_public_doc_terms(name: str, required_terms: tuple[str, ...], paths: tuple[str, ...]) -> dict[str, Any]:
    existing_paths = [Path(item) for item in paths if Path(item).is_file()]
    combined = "\n".join(read_text_file(path) for path in existing_paths)
    missing = [term for term in required_terms if term.lower() not in combined.lower()]
    return {
        "name": name,
        "ok": not missing,
        "required": True,
        "paths": [str(path) for path in existing_paths],
        "missingTerms": missing,
        "error": "" if not missing else "required stable-readiness terms are missing from public docs",
    }


def check_doc_contains(path: Path, name: str, required_terms: tuple[str, ...], required: bool) -> dict[str, Any]:
    if not path.is_file():
        return {"name": name, "ok": False, "required": required, "path": str(path), "error": "document is missing"}
    text = read_text_file(path)
    missing = [term for term in required_terms if term.lower() not in text.lower()]
    return {
        "name": name,
        "ok": not missing,
        "required": required,
        "path": str(path),
        "missingTerms": missing,
        "error": "" if not missing else "required terms are missing",
    }


def check_release_manifest(path: Path, version: str) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("release_artifacts.manifest", False, True, path, reason=parse_error, category="release")
    artifact_items = normalize_manifest_artifacts(payload)
    artifacts_by_name = {Path(str(item.get("path") or item.get("name") or "")).name: item for item in artifact_items}
    required_names = [*RELEASE_ARTIFACTS, f"VRCForge_Windows_x64_{version}.zip"]
    missing: list[str] = []
    checksum_mismatches: list[str] = []
    invalid_checksums: list[str] = []
    base_dir = path.parent
    for name in required_names:
        item = artifacts_by_name.get(name)
        if not item:
            missing.append(f"manifest:{name}")
            continue
        artifact_path = Path(str(item.get("path") or item.get("name") or name))
        if not artifact_path.is_absolute():
            artifact_path = base_dir / artifact_path
        if not artifact_path.is_file():
            missing.append(str(artifact_path))
            continue
        expected_sha = str(item.get("sha256") or item.get("sha256sum") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            invalid_checksums.append(name)
        elif sha256_file(artifact_path) != expected_sha:
            checksum_mismatches.append(name)
    manifest_version = str(payload.get("version") or "").strip()
    manifest_commit = str(payload.get("commit") or "").strip().lower()
    head_commit = current_git_commit()
    commit_ok = bool(
        re.fullmatch(r"[0-9a-f]{40}", manifest_commit)
        and (not head_commit or manifest_commit == head_commit)
    )
    uv_sha = str(payload.get("uvDownloadSha256") or "").strip().lower()
    uv_sha_ok = bool(re.fullmatch(r"[0-9a-f]{64}", uv_sha))
    ok = bool(
        manifest_version == version
        and commit_ok
        and uv_sha_ok
        and not missing
        and not invalid_checksums
        and not checksum_mismatches
    )
    return evidence_step(
        "release_artifacts.manifest",
        ok,
        True,
        path,
        category="release",
        schema="release-manifest",
        fields_checked=["version", "commit", "uvDownloadSha256", "artifacts[].name", "artifacts[].sha256"],
        missing=missing,
        reason="" if ok else "release manifest is missing required artifacts or checksum evidence",
        details={
            "version": manifest_version,
            "expectedVersion": version,
            "commit": manifest_commit,
            "expectedCommit": head_commit,
            "commitMatches": commit_ok,
            "uvDownloadSha256Present": uv_sha_ok,
            "invalidChecksums": invalid_checksums,
            "checksumMismatches": checksum_mismatches,
        },
    )


def release_manifest_hashes(path: Path) -> dict[str, str]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return {}
    return {
        Path(str(item.get("path") or item.get("name") or "")).name: str(
            item.get("sha256") or item.get("sha256sum") or ""
        ).strip().lower()
        for item in normalize_manifest_artifacts(payload)
    }


def current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = result.stdout.strip().lower() if result.returncode == 0 else ""
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else ""


def check_packaged_backend(path: Path, version: str, expected_payload_sha256: str) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("packaged_backend.support_bundle", False, True, path, reason=parse_error, category="packaged-runtime")
    support_path = Path(str(payload.get("supportBundlePath") or ""))
    ok = bool(
        payload.get("schema") == "vrcforge.packaged_backend_smoke.v1"
        and payload.get("ok") is True
        and payload.get("version") == version
        and payload.get("portableMode") is True
        and payload.get("bootstrapOk") is True
        and payload.get("proofIndexOk") is True
        and payload.get("supportBundleOk") is True
        and str(payload.get("supportBundlePath") or "")
        and support_path.is_file()
        and bool(expected_payload_sha256)
        and payload.get("payloadZipSha256") == expected_payload_sha256
    )
    return evidence_step(
        "packaged_backend.support_bundle",
        ok,
        True,
        path,
        category="packaged-runtime",
        schema=str(payload.get("schema") or ""),
        fields_checked=["ok", "version", "portableMode", "bootstrapOk", "proofIndexOk", "supportBundleOk", "supportBundlePath", "payloadZipSha256"],
        reason="" if ok else "packaged backend/support bundle smoke did not pass",
        details={"payloadZipSha256": payload.get("payloadZipSha256"), "expectedPayloadZipSha256": expected_payload_sha256},
    )


def check_payload_zip(path: Path, version: str, expected_payload_sha256: str) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("payload_zip.unpack", False, True, path, reason=parse_error, category="packaged-runtime")
    zip_path = Path(str(payload.get("zip") or ""))
    missing = payload.get("missing")
    recorded_sha = str(payload.get("archiveSha256") or "").strip().lower()
    current_sha = sha256_file(zip_path) if zip_path.is_file() else ""
    ok = bool(
        payload.get("schema") == "vrcforge.payload_zip_unpack.v1"
        and payload.get("ok") is True
        and payload.get("version") == version
        and isinstance(missing, list)
        and not missing
        and zip_path.is_file()
        and bool(expected_payload_sha256)
        and recorded_sha == expected_payload_sha256
        and current_sha == expected_payload_sha256
    )
    return evidence_step(
        "payload_zip.unpack",
        ok,
        True,
        path,
        category="packaged-runtime",
        schema=str(payload.get("schema") or ""),
        fields_checked=["ok", "version", "missing", "zip", "archiveSha256"],
        missing=[str(item) for item in missing] if isinstance(missing, list) else ["missing[]"],
        reason="" if ok else "portable zip unpack smoke did not pass",
        details={"archiveSha256": recorded_sha, "currentSha256": current_sha, "expectedSha256": expected_payload_sha256},
    )


def check_golden_path_matrix(path: Path, require_live_writes: bool = False) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("golden_path_matrix.safe_default", False, True, path, reason=parse_error, category="golden-path")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    safe_default = summary.get("safeDefault")
    # Default: the packaged safe-default (writes-skipped) artifact is accepted, which
    # is correct for a clean clone with no Unity attached. With --require-live-writes
    # the gate instead demands a run that actually performed live writes
    # (safeDefault is False), so a real apply/rollback path was exercised.
    safe_default_ok = (safe_default is False) if require_live_writes else (safe_default is True)
    ok = bool(
        payload.get("schema") == "vrcforge.golden_path_matrix.v1"
        and payload.get("ok") is True
        and summary.get("status") == "passed"
        and int(summary.get("failedCount") or 0) == 0
        and safe_default_ok
    )
    if ok:
        reason = ""
    elif require_live_writes and safe_default is not False:
        reason = "live-writes mode requires a Golden Path Matrix run with safeDefault=false (real writes proven)"
    else:
        reason = "packaged safe-default Golden Path Matrix did not pass"
    return evidence_step(
        "golden_path_matrix.safe_default",
        ok,
        True,
        path,
        category="golden-path",
        schema=str(payload.get("schema") or ""),
        fields_checked=["ok", "summary.status", "summary.failedCount", "summary.safeDefault"],
        reason=reason,
        details={"summary": summary, "requireLiveWrites": require_live_writes},
    )


def check_optimizer_request_guard(path: Path) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("optimizer_request_guard.explicit_approval", False, True, path, reason=parse_error, category="safety")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    by_name = {str(step.get("name")): step for step in steps if isinstance(step, dict)}
    required_step_names = ("optimizer.request_approval", "optimizer.request_auto")
    request_steps_ok = all(
        by_name.get(name, {}).get("requiresExplicitApproval") is True
        and by_name.get(name, {}).get("autoApprovalBlocked") is True
        and by_name.get(name, {}).get("requestStatus") == "pending"
        for name in required_step_names
    )
    tested_modes = summary.get("testedModes") if isinstance(summary.get("testedModes"), list) else []
    ok = bool(
        payload.get("schema") == "vrcforge.optimizer_request_guard_smoke.v1"
        and payload.get("ok") is True
        and summary.get("failedSteps") == []
        and {"approval", "auto"}.issubset(set(tested_modes))
        and request_steps_ok
    )
    return evidence_step(
        "optimizer_request_guard.explicit_approval",
        ok,
        True,
        path,
        category="safety",
        schema=str(payload.get("schema") or ""),
        fields_checked=["summary.failedSteps", "summary.testedModes", "requiresExplicitApproval", "autoApprovalBlocked", "requestStatus"],
        reason="" if ok else "optimizer request guard did not prove explicit approval in approval and auto modes",
        details={"testedModes": tested_modes},
    )


def check_external_agent_smoke(path: Path) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("external_agent.request_only", False, True, path, reason=parse_error, category="safety")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    by_name = {str(step.get("name")): step for step in steps if isinstance(step, dict)}
    stdio = by_name.get("stdio.bridge_preflight", {})
    manifest = by_name.get("gateway.manifest", {})
    mcp_tools = by_name.get("mcp.tools_list", {})
    ok = bool(
        payload.get("schema") == "vrcforge.external_agent_bridge_smoke.v1"
        and payload.get("ok") is True
        and summary.get("failedSteps") == []
        and stdio.get("advertisesRequestApply") is True
        and stdio.get("advertisesDirectApply") is False
        and manifest.get("directApplyAdvertised") == []
        and mcp_tools.get("directApplyListed") == []
        and mcp_tools.get("requestApplyListed") is True
    )
    return evidence_step(
        "external_agent.request_only",
        ok,
        True,
        path,
        category="safety",
        schema=str(payload.get("schema") or ""),
        fields_checked=["failedSteps", "advertisesRequestApply", "advertisesDirectApply", "directApplyAdvertised", "directApplyListed"],
        reason="" if ok else "external-agent smoke did not prove request-only write exposure",
    )


def check_installer_smoke(path: Path, version: str, expected_installer_sha256: str) -> dict[str, Any]:
    payload, parse_error = read_json_document(path)
    if parse_error:
        return evidence_step("installer.install_uninstall", False, True, path, reason=parse_error, category="installer")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    phases = summary.get("phases") if isinstance(summary.get("phases"), dict) else {}
    failed_steps = summary.get("failedSteps") if isinstance(summary.get("failedSteps"), list) else []
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    by_name = {str(step.get("name") or ""): step for step in steps if isinstance(step, dict)}
    admin_blocked = bool(
        payload.get("schema") == "vrcforge.installer_install_uninstall_smoke.v1"
        and summary.get("status") == "blocked"
        and failed_steps == ["admin.check"]
        and phases.get("install") == "blocked"
        and phases.get("uninstall") == "blocked"
        and "admin" in str(summary.get("blockedReason") or "").lower()
    )
    required_steps_ok = all(
        by_name.get(name, {}).get("ok") is True
        for name in (
            "admin.check",
            "install.payload_verify",
            "installed_backend.health",
            "installed_backend.cleanup",
            "uninstall.command",
            "uninstall.removed",
            "preservation.after_uninstall",
        )
    )
    ok = bool(
        payload.get("schema") == "vrcforge.installer_install_uninstall_smoke.v1"
        and payload.get("ok") is True
        and summary.get("status") == "passed"
        and failed_steps == []
        and phases.get("install") == "passed"
        and phases.get("uninstall") == "passed"
        and phases.get("preservation") == "passed"
        and required_steps_ok
        and by_name.get("installed_backend.health", {}).get("version") == version
        and by_name.get("installed_backend.health", {}).get("portableMode") is True
        and bool(expected_installer_sha256)
        and payload.get("installerSha256") == expected_installer_sha256
    )
    return evidence_step(
        "installer.install_uninstall",
        ok,
        True,
        path,
        category="installer",
        schema=str(payload.get("schema") or ""),
        status="passed" if ok else ("blocked" if admin_blocked else "failed"),
        fields_checked=["ok", "summary.status", "failedSteps", "phases.install", "phases.uninstall", "phases.preservation", "installed_backend.health", "installed_backend.cleanup", "preservation.after_uninstall", "installerSha256", "blockedReason"],
        reason="" if ok else ("installer requires Administrator elevation" if admin_blocked else "installer install/uninstall smoke did not pass"),
        details={
            "adminBlocked": admin_blocked,
            "summary": summary,
            "requiredStepsOk": required_steps_ok,
            "installerSha256": payload.get("installerSha256"),
            "expectedInstallerSha256": expected_installer_sha256,
        },
    )


def evidence_step(
    name: str,
    ok: bool,
    required: bool,
    path: Path,
    *,
    category: str = "",
    schema: str = "",
    status: str | None = None,
    fields_checked: list[str] | None = None,
    missing: list[str] | None = None,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "required": required,
        "status": status or ("passed" if ok else "failed"),
        "ok": ok,
        "path": str(path),
        "schema": schema,
        "fieldsChecked": fields_checked or [],
        "missing": missing or [],
        "reason": reason,
        "details": details or {},
    }


def read_json_document(path: Path) -> tuple[dict[str, Any], str]:
    if not path or not path.is_file():
        return {}, f"evidence file is missing: {path}"
    try:
        payload = json.loads(read_text_file(path))
    except Exception as exc:  # noqa: BLE001
        return {}, f"evidence JSON parse failed: {exc}"
    if not isinstance(payload, dict):
        return {}, "evidence JSON root must be an object"
    return payload, ""


def resolve_evidence_path(explicit: str, pattern: str) -> Path:
    if explicit:
        return Path(explicit)
    candidates = [path for path in Path().glob(pattern) if path.is_file()]
    if not candidates:
        return Path(pattern)
    return max(candidates, key=lambda path: path.stat().st_mtime)


def check_artifact_freshness(name: str, path: Path, max_age_hours: float) -> dict[str, Any]:
    """Blocking step: a smoke/proof artifact must have been written recently.

    The newest-by-mtime artifact selection means an old passing run can carry the
    gate forever. This guard fails when the resolved artifact is older than the
    configured window so the gate reflects a current run, not a fossilized one.
    """
    if not path.is_file():
        return {
            "name": name,
            "category": "freshness",
            "required": True,
            "ok": False,
            "path": str(path),
            "maxAgeHours": max_age_hours,
            "error": "evidence artifact is missing for freshness check",
        }
    age_hours = max(0.0, (time.time() - path.stat().st_mtime) / 3600.0)
    ok = age_hours <= max_age_hours
    return {
        "name": name,
        "category": "freshness",
        "required": True,
        "ok": ok,
        "path": str(path),
        "ageHours": round(age_hours, 2),
        "maxAgeHours": max_age_hours,
        "error": "" if ok else f"evidence artifact is older than {max_age_hours}h (age {round(age_hours, 2)}h)",
    }


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
    root = Path(artifacts_dir or "artifacts/stable-readiness-gate").resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = root / f"stable-readiness-gate-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
