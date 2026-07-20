from __future__ import annotations

import importlib.util
import json
import time
from argparse import Namespace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_stable_readiness_gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("smoke_stable_readiness_gate", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_args(tmp_path: Path, **overrides: object) -> Namespace:
    values = {
        "version": "1.0.1",
        "latest_stable": "",
        "stable_refresh": False,
        "compatibility_matrix": str(tmp_path / "docs" / "COMPATIBILITY_MATRIX.md"),
        "release_manifest": str(tmp_path / "dist" / "release" / "release-manifest.json"),
        "packaged_backend_smoke": "",
        "payload_zip_smoke": "",
        "golden_path_matrix": "",
        "skill_ecosystem_smoke": "",
        "optimizer_request_guard_smoke": "",
        "external_agent_smoke": "",
        "installer_smoke": "",
        "release_evidence": str(tmp_path / "docs" / "RELEASE_EVIDENCE.md"),
        "proof_matrix": str(tmp_path / "docs" / "PROOF_MATRIX.md"),
        "artifacts_dir": str(tmp_path / "reports"),
        "stale_version": ["0.9.0-beta"],
        "allow_blocked": False,
    }
    values.update(overrides)
    return Namespace(**values)


def write_minimum_tree(tmp_path: Path) -> None:
    for path, text in {
        "VERSION": "1.0.1\n",
        "package.json": '{"version":"1.0.1"}',
        "package-lock.json": '{"version":"1.0.1"}',
        "src-tauri/tauri.conf.json": '{"version":"1.0.1"}',
        "src-tauri/Cargo.toml": 'version = "1.0.1"',
    }.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    public_terms = (
        "1.0.1 Install and first run Connect Unity Provider / BYOK / local-only / no-provider "
        "Doctor First validation report First rollback Booth outfit model optimization external agents .vsk support bundle "
        "Privacy Boundary API key Gateway token paid asset private files .vsk export "
        "Current source version: `1.0.1` Latest published stable release: `1.0.0` "
        "Avatar Encryption / Anti-Rip addon private-addon connector private module required approval checkpoint rollback"
    )
    for path in ("README.md", "USER_MANUAL.md", "packaging/README.md"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(public_terms, encoding="utf-8")
    issue = tmp_path / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
    issue.parent.mkdir(parents=True, exist_ok=True)
    issue.write_text("1.0.1 support bundle API key Gateway token paid asset private files", encoding="utf-8")

    compatibility = (
        "Unity VRChat SDK Modular Avatar NDMF VRCFury AAO LAC TTT Meshia "
        "MA2BT-Pro Thry lilToon Poiyomi Known conflicts Known safe profiles "
        "Avatar Encryption / Anti-Rip addon private-addon connector private module required approval checkpoint rollback"
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "COMPATIBILITY_MATRIX.md").write_text(compatibility, encoding="utf-8")
    states = (
        "missing_dependency detected plan_available request_blocked_missing_options "
        "request_ready approval_pending checkpoint_created applied validation_done "
        "rollback_requested rollback_done proof_passed proof_failed stable_candidate experimental_only"
    )
    (docs_dir / "OPTIMIZATION_STRATEGY.md").write_text(states, encoding="utf-8")
    (docs_dir / "RELEASE_CHECKLIST.md").write_text(
        "smoke_stable_readiness_gate.py COMPATIBILITY_MATRIX support bundle",
        encoding="utf-8",
    )
    write_evidence_tree(tmp_path)


def write_bytes(path: Path, content: bytes) -> str:
    import hashlib

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_evidence_tree(tmp_path: Path) -> None:
    release_dir = tmp_path / "dist" / "release"
    artifacts = []
    artifact_hashes = {}
    for name, content in {
        "VRCForge.unitypackage": b"unitypackage",
        "VRCForge_Offline_Installer_x64.exe": b"offline",
        "VRCForge_Web_Installer_x64.exe": b"web",
        "VRCForge_Windows_x64_1.0.1.zip": b"zip",
    }.items():
        sha256 = write_bytes(release_dir / name, content)
        artifact_hashes[name] = sha256
        artifacts.append({"name": name, "path": name, "sha256": sha256})
    write_json(
        release_dir / "release-manifest.json",
        {
            "version": "1.0.1",
            "commit": "a" * 40,
            "uvDownloadSha256": "b" * 64,
            "artifacts": artifacts,
        },
    )

    support_bundle = tmp_path / "artifacts" / "packaged-backend-smoke-101" / "support.zip"
    write_bytes(support_bundle, b"support")
    write_json(
        tmp_path / "artifacts" / "packaged-backend-smoke-101" / "packaged-bootstrap-summary.json",
        {
            "schema": "vrcforge.packaged_backend_smoke.v1",
            "ok": True,
            "version": "1.0.1",
            "portableMode": True,
            "bootstrapOk": True,
            "proofIndexOk": True,
            "supportBundleOk": True,
            "supportBundlePath": str(support_bundle),
            "payloadZipSha256": artifact_hashes["VRCForge_Windows_x64_1.0.1.zip"],
        },
    )
    write_json(
        tmp_path / "artifacts" / "payload-smoke" / "v101-zip-unpack" / "summary.json",
        {
            "schema": "vrcforge.payload_zip_unpack.v1",
            "ok": True,
            "version": "1.0.1",
            "missing": [],
            "zip": str(release_dir / "VRCForge_Windows_x64_1.0.1.zip"),
            "archiveSha256": artifact_hashes["VRCForge_Windows_x64_1.0.1.zip"],
        },
    )
    write_json(
        tmp_path / "artifacts" / "golden-path-matrix" / "golden-path-matrix.json",
        {
            "schema": "vrcforge.golden_path_matrix.v1",
            "ok": True,
            "version": "1.0.1",
            "releaseBinding": {
                "version": "1.0.1",
                "manifestCommit": "a" * 40,
                "payloadZipSha256": artifact_hashes["VRCForge_Windows_x64_1.0.1.zip"],
                "payloadMatchesManifest": True,
            },
            "paths": [],
            "summary": {"status": "passed", "failedCount": 0, "safeDefault": True},
        },
    )
    write_json(
        tmp_path / "artifacts" / "optimizer-request-guard-smoke" / "guard.json",
        {
            "schema": "vrcforge.optimizer_request_guard_smoke.v1",
            "ok": True,
            "summary": {"failedSteps": [], "testedModes": ["approval", "auto"]},
            "steps": [
                {"name": "optimizer.request_approval", "requiresExplicitApproval": True, "autoApprovalBlocked": True, "requestStatus": "pending"},
                {"name": "optimizer.request_auto", "requiresExplicitApproval": True, "autoApprovalBlocked": True, "requestStatus": "pending"},
            ],
        },
    )
    write_json(
        tmp_path / "artifacts" / "external-agent-smoke" / "external.json",
        {
            "schema": "vrcforge.external_agent_bridge_smoke.v1",
            "ok": True,
            "summary": {"failedSteps": []},
            "steps": [
                {"name": "stdio.bridge_preflight", "advertisesRequestApply": True, "advertisesDirectApply": False},
                {"name": "gateway.manifest", "directApplyAdvertised": []},
                {"name": "mcp.tools_list", "directApplyListed": [], "requestApplyListed": True},
            ],
        },
    )
    write_json(
        tmp_path / "artifacts" / "installer-smoke" / "installer-install-uninstall-test.json",
        {
            "schema": "vrcforge.installer_install_uninstall_smoke.v1",
            "ok": True,
            "installerSha256": artifact_hashes["VRCForge_Offline_Installer_x64.exe"],
            "summary": {
                "status": "passed",
                "failedSteps": [],
                "phases": {"install": "passed", "uninstall": "passed", "preservation": "passed", "upgrade": "skipped"},
            },
            "steps": [
                {"name": "admin.check", "ok": True},
                {"name": "install.payload_verify", "ok": True},
                {"name": "installed_backend.health", "ok": True, "version": "1.0.1", "portableMode": True},
                {"name": "installed_backend.cleanup", "ok": True, "portReleased": True},
                {"name": "uninstall.command", "ok": True},
                {"name": "uninstall.removed", "ok": True},
                {"name": "preservation.after_uninstall", "ok": True},
            ],
        },
    )


def test_stable_readiness_gate_passes_with_public_docs_and_matrix(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    assert report["ok"] is True
    assert report["summary"]["status"] == "passed"
    assert report["summary"]["blockingSteps"] == []
    assert "local_release_evidence.current" in report["summary"]["warningSteps"]


def test_stable_readiness_gate_blocks_stale_public_doc_version(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    (tmp_path / "README.md").write_text((tmp_path / "README.md").read_text(encoding="utf-8") + " 0.9.0-beta", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    assert report["ok"] is False
    assert "public_doc.README.md.current_version" in report["summary"]["blockingSteps"]


def test_latest_stable_docs_require_explicit_published_version(tmp_path, monkeypatch):
    gate = load_gate()
    monkeypatch.chdir(tmp_path)
    for item, text in {
        "README.md": "Current source / target release: `1.3.0`. Latest published stable release: `1.2.0`.",
        "USER_MANUAL.md": "Latest published stable release: `1.2.0`.",
        "packaging/README.md": "`1.3.0` target; `1.2.0` remains the latest published stable package.",
    }.items():
        path = tmp_path / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    assert gate.check_latest_stable_docs("1.3.0", "1.2.0")["ok"] is True
    step = gate.check_latest_stable_docs("1.3.0", "1.1.2")
    assert step["ok"] is False
    assert sorted(step["mismatched"]) == ["README.md", "USER_MANUAL.md", "packaging/README.md"]
    assert gate.check_latest_stable_docs("1.3.0", "9.9.9")["ok"] is False

    for item in ("README.md", "USER_MANUAL.md"):
        (tmp_path / item).write_text("Latest published stable release: `1.3.0`.", encoding="utf-8")
    (tmp_path / "packaging" / "README.md").write_text(
        "`1.3.0` remains the latest published stable package.", encoding="utf-8"
    )
    assert gate.check_latest_stable_docs("1.3.0", "1.3.0")["ok"] is False
    assert gate.check_latest_stable_docs("1.3.0", "1.3.0", allow_equal=True)["ok"] is True


def test_stable_readiness_gate_blocks_missing_compatibility_term(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    (tmp_path / "docs" / "COMPATIBILITY_MATRIX.md").write_text("Unity VRChat SDK", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    assert report["ok"] is False
    assert "compatibility_matrix.exists" in report["summary"]["blockingSteps"]


def test_stable_readiness_gate_blocks_missing_avatar_encryption_boundary(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    (tmp_path / "README.md").write_text("1.0.1 Install and first run Privacy Boundary", encoding="utf-8")
    (tmp_path / "USER_MANUAL.md").write_text("1.0.1 API key support bundle", encoding="utf-8")
    (tmp_path / "docs" / "COMPATIBILITY_MATRIX.md").write_text(
        "Unity VRChat SDK Modular Avatar NDMF VRCFury AAO LAC TTT Meshia MA2BT-Pro Thry lilToon Poiyomi Known conflicts Known safe profiles",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    assert report["ok"] is False
    assert "public_docs.avatar_encryption_preview_boundary" in report["summary"]["blockingSteps"]


def test_stable_readiness_gate_writes_report(tmp_path):
    gate = load_gate()
    report = {"ok": True, "summary": {"status": "passed"}}

    path = gate.write_report(report, str(tmp_path / "reports"))

    assert path.parent == (tmp_path / "reports").resolve()
    assert json.loads(path.read_text(encoding="utf-8")) == report


# --- Fix #1: freshness guard + require-live-writes ---------------------------


def _step(report: dict, name: str) -> dict:
    return next(step for step in report["steps"] if step["name"] == name)


def test_freshness_guard_disabled_by_default(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    assert not any(step["name"].startswith("freshness.") for step in report["steps"])
    assert report["policy"]["maxArtifactAgeHours"] is None


def test_freshness_guard_passes_when_artifacts_are_recent(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path, max_artifact_age_hours=24.0))

    assert _step(report, "freshness.golden_path_matrix")["ok"] is True
    assert report["ok"] is True
    assert report["policy"]["maxArtifactAgeHours"] == 24.0


def test_freshness_guard_blocks_stale_artifact(tmp_path, monkeypatch):
    import os

    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    stale = tmp_path / "artifacts" / "golden-path-matrix" / "golden-path-matrix.json"
    old = time.time() - 100 * 3600
    os.utime(stale, (old, old))

    report = gate.build_stable_readiness_gate(make_args(tmp_path, max_artifact_age_hours=24.0))

    assert report["ok"] is False
    assert "freshness.golden_path_matrix" in report["summary"]["blockingSteps"]


def test_require_live_writes_blocks_safe_default_artifact(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    report = gate.build_stable_readiness_gate(make_args(tmp_path, require_live_writes=True))

    assert report["ok"] is False
    assert "golden_path_matrix.safe_default" in report["summary"]["blockingSteps"]
    assert report["policy"]["requireLiveWrites"] is True


def test_require_live_writes_passes_when_live_writes_proven(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    write_json(
        tmp_path / "artifacts" / "golden-path-matrix" / "golden-path-matrix.json",
        {
            "schema": "vrcforge.golden_path_matrix.v1",
            "ok": True,
            "version": "1.0.1",
            "releaseBinding": {
                "version": "1.0.1",
                "manifestCommit": "a" * 40,
                "payloadZipSha256": gate.sha256_file(
                    tmp_path / "dist" / "release" / "VRCForge_Windows_x64_1.0.1.zip"
                ),
                "payloadMatchesManifest": True,
            },
            "paths": [],
            "summary": {"status": "passed", "failedCount": 0, "safeDefault": False},
        },
    )

    report = gate.build_stable_readiness_gate(make_args(tmp_path, require_live_writes=True))

    assert _step(report, "golden_path_matrix.safe_default")["ok"] is True


def test_golden_path_matrix_requires_matching_version_and_vsk_for_1_3(tmp_path):
    gate = load_gate()
    path = tmp_path / "golden.json"
    write_json(
        path,
        {
            "schema": "vrcforge.golden_path_matrix.v1",
            "ok": True,
            "version": "1.2.0",
            "paths": [{"id": "vsk_import_dry_run_cleanup", "status": "skipped", "ok": True, "steps": []}],
            "summary": {"status": "passed", "failedCount": 0, "safeDefault": True},
        },
    )

    step = gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)

    assert step["ok"] is False
    assert step["details"]["expectedVersion"] == "1.3.0"
    assert step["details"]["vskStatus"] == "skipped"

    passing = {
        "schema": "vrcforge.golden_path_matrix.v1",
        "ok": True,
        "version": "1.3.0",
        "releaseBinding": {
            "version": "1.3.0",
            "manifestCommit": "d" * 40,
            "payloadZipSha256": "c" * 64,
            "artifactLocationSafe": True,
            "payloadMatchesManifest": True,
            "runtimeProvenance": {
                "ok": True,
                "portableMode": True,
                "listenerUnique": True,
                "executableInsideProgramDir": True,
                "executableMatchesArchive": True,
                "executableName": "vrcforge_backend.exe",
                "hash": "e" * 64,
            },
        },
        "paths": [
            {
                "id": "vsk_import_dry_run_cleanup",
                "status": "passed",
                "ok": True,
                "required": True,
                "steps": [
                    {"name": name, "ok": True}
                    for name in (
                        "vsk.preflight",
                        "vsk.dry_run",
                        "vsk.import",
                        "vsk.disable",
                        "vsk.uninstall",
                    )
                ],
            }
        ],
        "summary": {"status": "passed", "failedCount": 0, "safeDefault": True},
    }
    write_json(path, passing)
    assert gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)["ok"] is True

    missing_provenance = json.loads(json.dumps(passing))
    del missing_provenance["releaseBinding"]["runtimeProvenance"]
    write_json(path, missing_provenance)
    missing_step = gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)
    assert missing_step["ok"] is False
    assert missing_step["details"]["runtimeProvenanceRequired"] is True
    assert missing_step["details"]["runtimeProvenanceOk"] is False

    false_provenance = json.loads(json.dumps(passing))
    false_provenance["releaseBinding"]["runtimeProvenance"]["executableMatchesArchive"] = False
    write_json(path, false_provenance)
    false_step = gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)
    assert false_step["ok"] is False
    assert "packaged runtime provenance" in false_step["reason"]

    legacy_without_provenance = json.loads(json.dumps(passing))
    legacy_without_provenance["version"] = "1.2.0"
    legacy_without_provenance["releaseBinding"]["version"] = "1.2.0"
    del legacy_without_provenance["releaseBinding"]["runtimeProvenance"]
    write_json(path, legacy_without_provenance)
    legacy_step = gate.check_golden_path_matrix(path, "1.2.0", "c" * 64, "d" * 40, require_vsk=False)
    assert legacy_step["ok"] is True

    malformed_count = json.loads(json.dumps(passing))
    malformed_count["summary"]["failedCount"] = "0"
    write_json(path, malformed_count)
    assert gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)["ok"] is False

    malformed_step = json.loads(json.dumps(passing))
    malformed_step["paths"][0]["steps"].append("not-a-step")
    write_json(path, malformed_step)
    assert gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)["ok"] is False

    passing["paths"][0]["steps"] = [
        item for item in passing["paths"][0]["steps"] if item["name"] != "vsk.dry_run"
    ]
    write_json(path, passing)
    assert gate.check_golden_path_matrix(path, "1.3.0", "c" * 64, "d" * 40, require_vsk=True)["ok"] is False


def test_skill_ecosystem_smoke_requires_full_packaged_contract(tmp_path):
    gate = load_gate()
    path = tmp_path / "skills.json"
    package_ids = (
        "community.examples.validation-report-extension",
        "community.examples.material-preset-pack",
        "community.examples.outfit-naming-helper",
        "community.examples.optimizer-report-helper",
    )
    packages = []
    for package_id in package_ids:
        packages.append(
            {
                "id": package_id,
                "signatureVerified": True,
                "preflightSignatureVerified": True,
                "preflightSignerUntrusted": True,
                "preflightUntrustedDefaultDisabled": True,
                "preflightDryRunNoWrite": True,
                "preflightStateUnchanged": True,
                "trusted": True,
                "imported": True,
                "projected": True,
                "runtimeStatus": "loaded" if "material-preset" in package_id else "executed",
                "requestOnly": "material-preset" in package_id,
                "supportFilesVerified": True,
                "directTargetCalls": 0,
                "runtimeResultVerified": True,
                "entrypointVerified": True,
                "runtimeAuditVerified": True,
                "cleanupUninstalled": True,
            }
        )
    payload_sha = "c" * 64
    manifest_commit = "d" * 40
    payload = {
            "schema": "vrcforge.packaged_skill_ecosystem_probe.v1",
            "ok": True,
            "strictReleaseBinding": True,
            "launchSource": "manifest-directory-portable-zip-extracted-to-isolated-evidence-root",
            "payloadMatchesManifest": True,
            "version": "1.3.0",
            "payloadZipSha256": payload_sha,
            "manifestCommit": manifest_commit,
            "releaseBinding": {
                "strict": True,
                "manifestReleaseEligible": True,
                "strictBuildPolicy": True,
                "headEqualsOriginMain": True,
                "manifestEqualsHead": True,
                "portableDigestVerified": True,
                "extractedExecutableVerified": True,
                "portableManifestEntryUnique": True,
                "portableManifestPathSafe": True,
                "worktreeClean": True,
                "worktreeCleanAfterFixtureBuild": True,
                "executableLaunchLockVerified": True,
                "completionExecutableVerified": True,
                "worktreeCleanAtCompletion": True,
                "completionHeadMatches": True,
                "completionOriginMainMatches": True,
                "completionVersionMatches": True,
                "extractedBackendVerified": True,
                "embeddedVersion": "1.3.0",
                "buildPolicy": {
                    "mode": "strict",
                    "releaseEligible": True,
                    "allowDirty": False,
                    "allowUnpushed": False,
                    "allowVersionMismatch": False,
                },
            },
            "runtimeBinding": {
                "authenticatedHealth": True,
                "appAuthMissingTokenRejected": True,
                "appAuthWrongTokenRejected": True,
                "portableMode": True,
                "versionMatches": True,
                "programDirMatchesExtraction": True,
                "isolatedDataPathsVerified": True,
                "listenerUnique": True,
                "listenerExecutableExact": True,
                "backendDigestVerified": True,
                "executableName": "vrcforge_backend.exe",
                "backendSha256": "e" * 64,
            },
            "fixtures": {
                "sourceMode": "immutable-git-object-snapshot",
                "sourceCommit": manifest_commit,
                "sourceDigest": "f" * 64,
                "sourceDigestVerified": True,
                "sourcePackageBytesVerified": True,
                "privateKeyPersisted": False,
                "builderStorePersisted": False,
            },
            "transports": ["packaged-webview-dom", "packaged-webview-tauri-ipc", "authenticated-loopback-rest"],
            "packages": packages,
            "pathToSkill": {
                "previewViaTauri": True,
                "writeViaRest": True,
                "writtenRecipeContract": True,
                "writtenSourceMatchesResponse": True,
                "exportedVsk": True,
                "exportedVskPreflight": True,
                "exportedVskContentMatches": True,
                "positiveTemporaryResidueAbsent": True,
                "genericEntrypointPreserved": True,
                "negativeTemporaryResidueAbsent": True,
                "negativeSensitiveResidueAbsent": True,
                "secretRejected": True,
                "secretRejectedNoOutput": True,
                "parentTraversalRejected": True,
                "privateUrlRedacted": True,
                "existingSourceRejected": True,
                "existingPackageRejected": True,
                "confirmationInvalidated": True,
                "paidPayloadRejected": True,
                "paidPayloadRejectedNoOutput": True,
                "recipes": {
                    "tttMaterialGroup": True,
                    "boothImportPreflight": True,
                    "parameterCompression": True,
                    "pcQuestUploadPass": True,
                },
            },
            "governance": {
                "disableBlockedExecution": True,
                "reenableWorked": True,
                "safeModeBlockedRiskyEnable": True,
                "safeModeTargetsRestored": True,
                "revokeBlockedExecution": True,
                "revokedSignerRetrustRejected": True,
                "blockDisabledProjection": True,
                "uninstallRemovedState": True,
            },
            "ui": {
                "skillsWorkspaceVisible": True,
                "pathToSkillVisible": True,
                "auditSearchVisible": True,
                "auditSignerVisible": True,
                "auditFilterExercised": True,
                "auditGovernanceFieldsVisible": True,
                "auditPaginationExercised": True,
                "auditAriaLive": True,
                "contextualPathToSkillPrefill": True,
            },
            "privacy": {
                "privateKeyAbsent": True,
                "tokensAbsent": True,
                "sourcePathsAbsent": True,
                "paidPayloadAbsent": True,
                "artifactsFullyScanned": True,
                "rawDiagnosticSecretsAbsent": True,
                "rawDiagnosticsFullyScanned": True,
                "supportBundleClean": True,
            },
            "cleanup": {
                "installedPackagesClear": True,
                "projectedSkillsClear": True,
                "registryEntriesClear": True,
                "packageFilesClear": True,
                "projectedFilesClear": True,
                "processesClear": True,
                "portsClear": True,
                "stagingClear": True,
                "filesystemResidueClear": True,
                "rejectedOutputsClear": True,
                "trackedTreeClear": True,
                "processTrackingComplete": True,
                "ephemeralSigningKeyClear": True,
                "builderStoreClear": True,
            },
            "processBoundary": {
                "rootTracked": True,
                "trackedProcessCountEver": 3,
                "descendantProcessCountEver": 2,
                "trackedGenerationCountEver": 3,
                "trackedUniquePidCountEver": 3,
                "descendantGenerationCountEver": 2,
                "processNamesEver": ["VRCForge.exe", "vrcforge_backend.exe", "msedgewebview2.exe"],
                "trackedTreeClear": True,
                "startEventWatcherVerified": True,
                "watcherSettleVerified": True,
                "watcherSettleMs": 5000,
                "portQuerySucceeded": True,
                "samplingErrorCount": 0,
            },
            "assertions": [],
        }
    write_json(path, payload)

    assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is True
    baseline = json.loads(json.dumps(payload))

    extra_package = json.loads(json.dumps(baseline))
    extra_package_item = json.loads(json.dumps(extra_package["packages"][0]))
    extra_package_item["id"] = "community.examples.unexpected-extra"
    extra_package["packages"].append(extra_package_item)
    write_json(path, extra_package)
    assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is False

    duplicate_package = json.loads(json.dumps(baseline))
    duplicate_package["packages"].append(json.loads(json.dumps(duplicate_package["packages"][0])))
    write_json(path, duplicate_package)
    assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is False

    missing_package = json.loads(json.dumps(baseline))
    missing_package["packages"].pop()
    write_json(path, missing_package)
    assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is False

    payload["packages"][0]["runtimeAuditVerified"] = False
    write_json(path, payload)
    assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is False

    payload["packages"][0]["runtimeAuditVerified"] = True
    payload["strictReleaseBinding"] = False
    write_json(path, payload)
    step = gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)
    assert step["ok"] is False
    assert step["details"]["strictReleaseBinding"] is False

    for field in (
        "strict",
        "manifestReleaseEligible",
        "strictBuildPolicy",
        "worktreeClean",
        "worktreeCleanAfterFixtureBuild",
        "executableLaunchLockVerified",
        "completionExecutableVerified",
        "worktreeCleanAtCompletion",
        "completionHeadMatches",
        "completionOriginMainMatches",
        "completionVersionMatches",
        "headEqualsOriginMain",
        "manifestEqualsHead",
        "portableDigestVerified",
        "extractedExecutableVerified",
        "extractedBackendVerified",
        "portableManifestEntryUnique",
        "portableManifestPathSafe",
    ):
        invalid = json.loads(json.dumps(baseline))
        invalid["releaseBinding"][field] = False
        write_json(path, invalid)
        rejected = gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)
        assert rejected["ok"] is False, field
        assert rejected["details"]["strictReleaseBindingOk"] is False, field

    for field in ("launchSource", "payloadMatchesManifest", "releaseBinding"):
        invalid = json.loads(json.dumps(baseline))
        invalid.pop(field)
        write_json(path, invalid)
        assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is False, field

    for field in ("mode", "releaseEligible", "allowDirty", "allowUnpushed", "allowVersionMismatch"):
        invalid = json.loads(json.dumps(baseline))
        policy = invalid["releaseBinding"]["buildPolicy"]
        policy[field] = "local-acceptance" if field == "mode" else not policy[field]
        write_json(path, invalid)
        assert gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)["ok"] is False, field

    def assert_missing_or_invalid(parts, invalid_value) -> None:
        label = ".".join(str(part) for part in parts)
        for missing in (True, False):
            invalid = json.loads(json.dumps(baseline))
            target = invalid
            for part in parts[:-1]:
                target = target[part]
            if missing:
                target.pop(parts[-1])
            else:
                target[parts[-1]] = invalid_value
            write_json(path, invalid)
            rejected = gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)
            case = "missing" if missing else f"invalid={invalid_value!r}"
            assert rejected["ok"] is False, f"{label} {case}"

    for package_index in range(len(package_ids)):
        for field in (
            "preflightSignatureVerified",
            "preflightSignerUntrusted",
            "preflightUntrustedDefaultDisabled",
            "preflightDryRunNoWrite",
            "preflightStateUnchanged",
            "entrypointVerified",
            "runtimeResultVerified",
        ):
            assert_missing_or_invalid(("packages", package_index, field), False)

    for field in (
        "authenticatedHealth",
        "appAuthMissingTokenRejected",
        "appAuthWrongTokenRejected",
        "portableMode",
        "versionMatches",
        "programDirMatchesExtraction",
        "isolatedDataPathsVerified",
        "listenerUnique",
        "listenerExecutableExact",
        "backendDigestVerified",
    ):
        assert_missing_or_invalid(("runtimeBinding", field), False)
    assert_missing_or_invalid(("runtimeBinding", "executableName"), "python.exe")
    assert_missing_or_invalid(("runtimeBinding", "backendSha256"), "not-a-sha256")

    assert_missing_or_invalid(("releaseBinding", "embeddedVersion"), "1.2.0")
    assert_missing_or_invalid(("fixtures", "sourceMode"), "working-tree-local-preacceptance")
    assert_missing_or_invalid(("fixtures", "sourceCommit"), "a" * 40)
    assert_missing_or_invalid(("fixtures", "sourceDigest"), "not-a-sha256")
    assert_missing_or_invalid(("fixtures", "sourceDigestVerified"), False)
    assert_missing_or_invalid(("fixtures", "sourcePackageBytesVerified"), False)
    assert_missing_or_invalid(("fixtures", "privateKeyPersisted"), True)
    assert_missing_or_invalid(("fixtures", "builderStorePersisted"), True)

    for field in (
        "exportedVskPreflight",
        "writtenRecipeContract",
        "writtenSourceMatchesResponse",
        "exportedVskContentMatches",
        "positiveTemporaryResidueAbsent",
        "genericEntrypointPreserved",
        "negativeTemporaryResidueAbsent",
        "negativeSensitiveResidueAbsent",
        "secretRejectedNoOutput",
        "parentTraversalRejected",
        "privateUrlRedacted",
        "paidPayloadRejectedNoOutput",
    ):
        assert_missing_or_invalid(("pathToSkill", field), False)
    assert_missing_or_invalid(("governance", "revokedSignerRetrustRejected"), False)
    assert_missing_or_invalid(("governance", "safeModeTargetsRestored"), False)
    assert_missing_or_invalid(("ui", "contextualPathToSkillPrefill"), False)
    assert_missing_or_invalid(("ui", "auditGovernanceFieldsVisible"), False)
    for field in (
        "artifactsFullyScanned",
        "rawDiagnosticSecretsAbsent",
        "rawDiagnosticsFullyScanned",
    ):
        assert_missing_or_invalid(("privacy", field), False)
    for field in (
        "trackedTreeClear",
        "processTrackingComplete",
        "ephemeralSigningKeyClear",
        "builderStoreClear",
        "registryEntriesClear",
        "packageFilesClear",
        "projectedFilesClear",
        "filesystemResidueClear",
    ):
        assert_missing_or_invalid(("cleanup", field), False)

    for field in (
        "rootTracked",
        "trackedTreeClear",
        "startEventWatcherVerified",
        "watcherSettleVerified",
        "portQuerySucceeded",
    ):
        assert_missing_or_invalid(("processBoundary", field), False)
    assert_missing_or_invalid(("processBoundary", "watcherSettleMs"), 4999)
    assert_missing_or_invalid(("processBoundary", "trackedProcessCountEver"), 1)
    assert_missing_or_invalid(("processBoundary", "descendantProcessCountEver"), 0)
    assert_missing_or_invalid(("processBoundary", "trackedGenerationCountEver"), 1)
    assert_missing_or_invalid(("processBoundary", "trackedUniquePidCountEver"), 1)
    assert_missing_or_invalid(("processBoundary", "descendantGenerationCountEver"), 0)
    assert_missing_or_invalid(("processBoundary", "processNamesEver"), ["VRCForge.exe"])
    assert_missing_or_invalid(("processBoundary", "samplingErrorCount"), 1)

    material_package_index = package_ids.index("community.examples.material-preset-pack")
    for package_index in range(len(package_ids)):
        assert_missing_or_invalid(("packages", package_index, "supportFilesVerified"), False)
    numeric_type_cases = ("not-a-number", True, False, None)
    for parts in (
        ("processBoundary", "trackedProcessCountEver"),
        ("processBoundary", "descendantProcessCountEver"),
        ("processBoundary", "trackedGenerationCountEver"),
        ("processBoundary", "trackedUniquePidCountEver"),
        ("processBoundary", "descendantGenerationCountEver"),
        ("packages", material_package_index, "directTargetCalls"),
    ):
        for invalid_value in numeric_type_cases:
            invalid = json.loads(json.dumps(baseline))
            target = invalid
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = invalid_value
            write_json(path, invalid)
            rejected = gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)
            label = ".".join(str(part) for part in parts)
            assert rejected["ok"] is False, f"{label} invalid={invalid_value!r}"

    for invalid_value in (True, False, "0", "not-a-number"):
        invalid = json.loads(json.dumps(baseline))
        invalid["processBoundary"]["samplingErrorCount"] = invalid_value
        write_json(path, invalid)
        rejected = gate.check_skill_ecosystem_smoke(path, "1.3.0", payload_sha, manifest_commit)
        assert rejected["ok"] is False, f"samplingErrorCount invalid={invalid_value!r}"


def test_release_manifest_requires_commit_uv_hash_and_artifact_hashes(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    manifest_path = tmp_path / "dist" / "release" / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commit"] = ""
    manifest["uvDownloadSha256"] = ""
    manifest["artifacts"][0]["sha256"] = ""
    write_json(manifest_path, manifest)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    step = _step(report, "release_artifacts.manifest")
    assert step["ok"] is False
    assert step["details"]["commitMatches"] is False
    assert step["details"]["uvDownloadSha256Present"] is False
    assert step["details"]["invalidChecksums"]


def test_release_manifest_rejects_local_build_even_after_commit_matches(tmp_path, monkeypatch):
    gate = load_gate()
    release_root = tmp_path / "dist" / "release"
    release_root.mkdir(parents=True)
    names = [*gate.RELEASE_ARTIFACTS, "VRCForge_Windows_x64_1.3.0.zip"]
    artifacts = []
    for name in names:
        path = release_root / name
        path.write_bytes(f"payload:{name}".encode())
        artifacts.append({"name": name, "sha256": gate.sha256_file(path)})
    manifest_path = release_root / "release-manifest.json"
    payload = {
        "version": "1.3.0",
        "commit": "a" * 40,
        "buildPolicy": {
            "mode": "local-acceptance",
            "releaseEligible": False,
            "allowDirty": False,
            "allowUnpushed": True,
            "allowVersionMismatch": True,
        },
        "uvDownloadUrl": "https://example.invalid/uv-x86_64-pc-windows-msvc.zip",
        "uvDownloadSha256": "b" * 64,
        "uvRuntime": {
            "source": "download",
            "downloadUrl": "https://example.invalid/uv-x86_64-pc-windows-msvc.zip",
            "archiveSha256": "b" * 64,
            "archiveDigestVerified": True,
            "files": [
                {"name": "uv.exe", "sha256": "c" * 64},
                {"name": "uvx.exe", "sha256": "d" * 64},
            ],
        },
        "artifacts": artifacts,
    }
    write_json(manifest_path, payload)
    monkeypatch.setattr(gate, "current_git_commit", lambda: "a" * 40)
    monkeypatch.setattr(gate, "current_origin_main_commit", lambda: "a" * 40)

    step = gate.check_release_manifest(manifest_path, "1.3.0")

    assert step["ok"] is False
    assert step["details"]["commitMatches"] is True
    assert step["details"]["strictBuildProvenanceOk"] is False
    assert "local-acceptance" in step["reason"]

    payload["buildPolicy"] = {
        "mode": "strict",
        "releaseEligible": True,
        "allowDirty": False,
        "allowUnpushed": False,
        "allowVersionMismatch": False,
    }
    write_json(manifest_path, payload)
    strict_step = gate.check_release_manifest(manifest_path, "1.3.0")
    assert strict_step["ok"] is True
    assert strict_step["details"]["uvRuntimeProvenanceOk"] is True

    tampered_uv_runtime = json.loads(json.dumps(payload))
    tampered_uv_runtime["uvRuntime"]["files"][0]["sha256"] = "not-a-sha256"
    write_json(manifest_path, tampered_uv_runtime)
    tampered_step = gate.check_release_manifest(manifest_path, "1.3.0")
    assert tampered_step["ok"] is False
    assert tampered_step["details"]["uvRuntimeProvenanceOk"] is False
    write_json(manifest_path, payload)

    monkeypatch.setattr(gate, "current_origin_main_commit", lambda: "c" * 40)
    behind = gate.check_release_manifest(manifest_path, "1.3.0")
    assert behind["ok"] is False
    assert behind["details"]["pushedBindingOk"] is False
    monkeypatch.setattr(gate, "current_origin_main_commit", lambda: "a" * 40)

    payload["artifacts"][0]["path"] = str(tmp_path / "outside" / payload["artifacts"][0]["name"])
    write_json(manifest_path, payload)
    unsafe = gate.check_release_manifest(manifest_path, "1.3.0")
    assert unsafe["ok"] is False
    assert unsafe["details"]["unsafeLocations"] == [payload["artifacts"][0]["name"]]

    payload["artifacts"][0].pop("path")
    unexpected_payload = json.loads(json.dumps(payload))
    unexpected_payload["artifacts"].append({"name": "unexpected.txt", "sha256": "e" * 64})
    write_json(manifest_path, unexpected_payload)
    unexpected = gate.check_release_manifest(manifest_path, "1.3.0")
    assert unexpected["ok"] is False
    assert unexpected["details"]["unexpectedEntries"] == ["unexpected.txt"]

    payload["artifacts"].append(dict(payload["artifacts"][0]))
    write_json(manifest_path, payload)
    duplicate = gate.check_release_manifest(manifest_path, "1.3.0")
    assert duplicate["ok"] is False
    assert duplicate["details"]["duplicateEntries"] == [payload["artifacts"][0]["name"]]


def test_release_manifest_helpers_share_one_parsed_snapshot(tmp_path, monkeypatch):
    gate = load_gate()
    release_root = tmp_path / "dist" / "release"
    release_root.mkdir(parents=True)
    names = [*gate.RELEASE_ARTIFACTS, "VRCForge_Windows_x64_1.3.0.zip"]
    artifacts = []
    for name in names:
        path = release_root / name
        path.write_bytes(f"payload:{name}".encode())
        artifacts.append({"name": name, "sha256": gate.sha256_file(path)})
    manifest_path = release_root / "release-manifest.json"
    original = {
        "version": "1.3.0",
        "commit": "a" * 40,
        "buildPolicy": {
            "mode": "strict",
            "releaseEligible": True,
            "allowDirty": False,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        },
        "uvDownloadUrl": "https://example.invalid/uv-x86_64-pc-windows-msvc.zip",
        "uvDownloadSha256": "b" * 64,
        "uvRuntime": {
            "source": "download",
            "downloadUrl": "https://example.invalid/uv-x86_64-pc-windows-msvc.zip",
            "archiveSha256": "b" * 64,
            "archiveDigestVerified": True,
            "files": [
                {"name": "uv.exe", "sha256": "c" * 64},
                {"name": "uvx.exe", "sha256": "d" * 64},
            ],
        },
        "artifacts": artifacts,
    }
    write_json(manifest_path, original)
    snapshot, parse_error = gate.read_json_document(manifest_path)
    write_json(
        manifest_path,
        {
            "version": "9.9.9",
            "commit": "c" * 40,
            "buildPolicy": {"mode": "local-acceptance"},
            "artifacts": [],
        },
    )
    monkeypatch.setattr(gate, "current_git_commit", lambda: "a" * 40)
    monkeypatch.setattr(gate, "current_origin_main_commit", lambda: "a" * 40)

    step = gate.check_release_manifest(
        manifest_path,
        "1.3.0",
        payload=snapshot,
        parse_error=parse_error,
    )
    hashes = gate.release_manifest_hashes(
        manifest_path,
        payload=snapshot,
        parse_error=parse_error,
    )
    commit = gate.release_manifest_commit(
        manifest_path,
        payload=snapshot,
        parse_error=parse_error,
    )

    assert step["ok"] is True
    assert hashes == {item["name"]: item["sha256"] for item in artifacts}
    assert commit == "a" * 40


def test_payload_and_backend_smokes_must_match_manifest_zip_hash(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    backend_path = tmp_path / "artifacts" / "packaged-backend-smoke-101" / "packaged-bootstrap-summary.json"
    payload_path = tmp_path / "artifacts" / "payload-smoke" / "v101-zip-unpack" / "summary.json"
    backend = json.loads(backend_path.read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    backend["payloadZipSha256"] = "0" * 64
    payload["archiveSha256"] = "0" * 64
    write_json(backend_path, backend)
    write_json(payload_path, payload)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    assert _step(report, "packaged_backend.support_bundle")["ok"] is False
    assert _step(report, "payload_zip.unpack")["ok"] is False


def test_packaged_backend_gate_requires_doctor_and_cli_self_tests(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    backend_path = tmp_path / "artifacts" / "packaged-backend-smoke-101" / "packaged-bootstrap-summary.json"
    backend = json.loads(backend_path.read_text(encoding="utf-8"))
    backend["schema"] = "vrcforge.packaged_backend_smoke.v2"
    backend["version"] = "1.3.4"
    write_json(backend_path, backend)

    step = gate.check_packaged_backend(
        backend_path,
        "1.3.4",
        backend["payloadZipSha256"],
    )

    assert step["ok"] is False
    assert step["details"]["doctorEvidenceRequired"] is True


def test_packaged_backend_gate_preserves_legacy_v1_evidence(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    backend_path = tmp_path / "artifacts" / "packaged-backend-smoke-101" / "packaged-bootstrap-summary.json"
    backend = json.loads(backend_path.read_text(encoding="utf-8"))

    step = gate.check_packaged_backend(
        backend_path,
        "1.0.1",
        backend["payloadZipSha256"],
    )

    assert step["ok"] is True
    assert step["details"]["doctorEvidenceRequired"] is False


def test_installer_smoke_requires_full_phase_and_cleanup_evidence(tmp_path, monkeypatch):
    gate = load_gate()
    write_minimum_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    installer_path = tmp_path / "artifacts" / "installer-smoke" / "installer-install-uninstall-test.json"
    installer = json.loads(installer_path.read_text(encoding="utf-8"))
    installer["steps"] = []
    installer["summary"]["phases"]["preservation"] = "skipped"
    write_json(installer_path, installer)

    report = gate.build_stable_readiness_gate(make_args(tmp_path))

    step = _step(report, "installer.install_uninstall")
    assert step["ok"] is False
    assert step["details"]["requiredStepsOk"] is False
