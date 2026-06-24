from __future__ import annotations

import hashlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_rc_release_gate.py"


def load_rc_gate():
    spec = importlib.util.spec_from_file_location("smoke_rc_release_gate", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_args(tmp_path: Path, **overrides: object) -> Namespace:
    values = {
        "version": "0.9.5-rc",
        "release_manifest": str(tmp_path / "dist" / "release" / "release-manifest.json"),
        "release_evidence": str(tmp_path / "docs" / "RELEASE_EVIDENCE.md"),
        "proof_matrix": str(tmp_path / "docs" / "PROOF_MATRIX.md"),
        "artifacts_dir": str(tmp_path / "reports"),
        "allow_blocked": False,
    }
    values.update(overrides)
    return Namespace(**values)


def write_artifact(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def write_minimum_release_tree(tmp_path: Path) -> None:
    release_dir = tmp_path / "dist" / "release"
    release_dir.mkdir(parents=True)
    artifacts = []
    for name, content in {
        "VRCForge.unitypackage": b"unitypackage",
        "VRCForge_Offline_Installer_x64.exe": b"offline",
        "VRCForge_Web_Installer_x64.exe": b"web",
        "VRCForge_Windows_x64_0.9.5-rc.zip": b"portable-zip",
    }.items():
        sha256 = write_artifact(release_dir / name, content)
        artifacts.append({"name": name, "path": name, "sha256": sha256})
    (release_dir / "release-manifest.json").write_text(
        json.dumps({"version": "0.9.5-rc", "commit": "abc123", "artifacts": artifacts}, indent=2),
        encoding="utf-8",
    )

    evidence_artifact = tmp_path / "artifacts" / "golden-path-matrix" / "matrix.json"
    proof_artifact = tmp_path / "artifacts" / "external-agent-smoke" / "smoke.json"
    write_artifact(evidence_artifact, b"{}")
    write_artifact(proof_artifact, b"{}")
    required = (
        "0.9.5-rc Golden Path Matrix External-agent request Proof viewer "
        "Support bundle Installer install/uninstall Portable zip"
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "RELEASE_EVIDENCE.md").write_text(
        f"{required}\n`artifacts\\golden-path-matrix\\matrix.json`\n",
        encoding="utf-8",
    )
    (docs_dir / "PROOF_MATRIX.md").write_text(
        f"{required}\n`artifacts\\external-agent-smoke\\smoke.json`\n",
        encoding="utf-8",
    )


def test_rc_release_gate_passes_with_manifest_docs_and_artifacts(tmp_path, monkeypatch):
    gate = load_rc_gate()
    write_minimum_release_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    report = gate.build_rc_release_gate(make_args(tmp_path))

    assert report["ok"] is True
    assert report["summary"]["status"] == "passed"
    assert report["summary"]["blockingSteps"] == []
    assert any(step["name"] == "release_artifact.VRCForge_Windows_x64_0.9.5-rc.zip" for step in report["steps"])


def test_rc_release_gate_blocks_missing_manifest(tmp_path, monkeypatch):
    gate = load_rc_gate()
    monkeypatch.chdir(tmp_path)

    report = gate.build_rc_release_gate(make_args(tmp_path))

    assert report["ok"] is False
    assert report["summary"]["status"] == "blocked"
    assert "release_manifest.exists" in report["summary"]["blockingSteps"]


def test_rc_release_gate_blocks_checksum_mismatch(tmp_path, monkeypatch):
    gate = load_rc_gate()
    write_minimum_release_tree(tmp_path)
    manifest_path = tmp_path / "dist" / "release" / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = gate.build_rc_release_gate(make_args(tmp_path))

    assert report["ok"] is False
    assert "release_artifact.VRCForge.unitypackage" in report["summary"]["blockingSteps"]


def test_rc_release_gate_writes_report(tmp_path):
    gate = load_rc_gate()
    report = {"ok": True, "summary": {"status": "passed"}}

    path = gate.write_report(report, str(tmp_path / "reports"))

    assert path.parent == (tmp_path / "reports").resolve()
    assert json.loads(path.read_text(encoding="utf-8")) == report
