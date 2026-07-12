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
        "compatibility_matrix": str(tmp_path / "docs" / "COMPATIBILITY_MATRIX.md"),
        "release_manifest": str(tmp_path / "dist" / "release" / "release-manifest.json"),
        "packaged_backend_smoke": "",
        "payload_zip_smoke": "",
        "golden_path_matrix": "",
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
            "summary": {"status": "passed", "failedCount": 0, "safeDefault": False},
        },
    )

    report = gate.build_stable_readiness_gate(make_args(tmp_path, require_live_writes=True))

    assert _step(report, "golden_path_matrix.safe_default")["ok"] is True


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
