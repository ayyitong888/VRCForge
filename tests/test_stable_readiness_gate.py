from __future__ import annotations

import importlib.util
import json
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
        "version": "0.9.5-rc",
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
        "VERSION": "0.9.5-rc\n",
        "package.json": '{"version":"0.9.5-rc"}',
        "package-lock.json": '{"version":"0.9.5-rc"}',
        "src-tauri/tauri.conf.json": '{"version":"0.9.5-rc"}',
        "src-tauri/Cargo.toml": 'version = "0.9.5-rc"',
    }.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    public_terms = (
        "0.9.5-rc Install and first run Connect Unity Provider / BYOK / local-only / no-provider "
        "Doctor First validation report First rollback Booth outfit model optimization external agents .vsk support bundle "
        "Privacy Boundary API key Gateway token paid asset private files .vsk export"
    )
    for path in ("README.md", "USER_MANUAL.md", "packaging/README.md"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(public_terms, encoding="utf-8")
    issue = tmp_path / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
    issue.parent.mkdir(parents=True, exist_ok=True)
    issue.write_text("0.9.5-rc support bundle API key Gateway token paid asset private files", encoding="utf-8")

    compatibility = (
        "Unity VRChat SDK Modular Avatar NDMF VRCFury AAO LAC TTT Meshia "
        "MA2BT-Pro Thry lilToon Poiyomi Known conflicts Known safe profiles"
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
    for name, content in {
        "VRCForge.unitypackage": b"unitypackage",
        "VRCForge_Offline_Installer_x64.exe": b"offline",
        "VRCForge_Web_Installer_x64.exe": b"web",
        "VRCForge_Windows_x64_0.9.5-rc.zip": b"zip",
    }.items():
        sha256 = write_bytes(release_dir / name, content)
        artifacts.append({"name": name, "path": name, "sha256": sha256})
    write_json(release_dir / "release-manifest.json", {"version": "0.9.5-rc", "commit": "abc", "artifacts": artifacts})

    support_bundle = tmp_path / "artifacts" / "packaged-backend-smoke-095" / "support.zip"
    write_bytes(support_bundle, b"support")
    write_json(
        tmp_path / "artifacts" / "packaged-backend-smoke-095" / "packaged-bootstrap-summary.json",
        {
            "schema": "vrcforge.packaged_backend_smoke.v1",
            "ok": True,
            "version": "0.9.5-rc",
            "portableMode": True,
            "bootstrapOk": True,
            "proofIndexOk": True,
            "supportBundleOk": True,
            "supportBundlePath": str(support_bundle),
        },
    )
    write_json(
        tmp_path / "artifacts" / "payload-smoke" / "v095-zip-unpack" / "summary.json",
        {
            "schema": "vrcforge.payload_zip_unpack.v1",
            "ok": True,
            "version": "0.9.5-rc",
            "missing": [],
            "zip": str(release_dir / "VRCForge_Windows_x64_0.9.5-rc.zip"),
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
            "summary": {"status": "passed", "failedSteps": [], "phases": {"install": "passed", "uninstall": "passed"}},
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


def test_stable_readiness_gate_writes_report(tmp_path):
    gate = load_gate()
    report = {"ok": True, "summary": {"status": "passed"}}

    path = gate.write_report(report, str(tmp_path / "reports"))

    assert path.parent == (tmp_path / "reports").resolve()
    assert json.loads(path.read_text(encoding="utf-8")) == report
