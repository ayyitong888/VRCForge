import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_script() -> str:
    return (REPO_ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8")


def test_strict_evidence_mode_is_strict_but_never_publishable() -> None:
    source = _build_script()

    assert "[switch]$StrictEvidence" in source
    assert "$StrictEvidence -and ($AllowDirty -or $AllowUnpushed -or $AllowVersionMismatch)" in source
    assert "$strictSourceBuild = -not ($AllowDirty -or $AllowUnpushed -or $AllowVersionMismatch)" in source
    assert "$strictEvidenceBuild = $strictSourceBuild -and [bool]$StrictEvidence" in source
    assert "$strictReleaseBuild = $strictSourceBuild -and -not $strictEvidenceBuild" in source
    assert '"strict-evidence"' in source
    assert "releaseEligible = [bool]$strictReleaseBuild" in source
    assert "if ($strictEvidenceBuild)" in source
    assert "$buildPolicy.evidenceEligible = $true" in source
    assert '"dist\\evidence\\$headCommit\\$evidenceRunId"' in source
    assert '$releaseRoot = Join-Path $evidenceBuildRoot "release"' in source

    # Every source/provenance gate shared by release and evidence builds must
    # use the common strict state, not the publishability bit.
    assert "-RequireVerifiedDownload $strictSourceBuild" in source
    assert "if ($strictSourceBuild -and $headCommit -ne $originMainCommit)" in source
    assert "if ($strictSourceBuild -and (" in source


def test_publish_path_only_accepts_strict_release_policy() -> None:
    source = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")

    assert '[string]$buildPolicy.mode -ne "strict"' in source
    assert "$buildPolicy.releaseEligible -ne $true" in source


def test_strict_evidence_attestor_stays_outside_candidate_payload() -> None:
    source = _build_script()

    assert "cargo build failed for the external evidence attestor." in source
    assert "vrcforge_primitive_attestor.exe" in source
    assert "artifacts/primitive-origin-tools/$headCommit/$evidenceRunId" in source
    assert "repositoryRelativePath = $evidenceRelativePath" in source
    assert "trustedBoundaryReady = $false" in source
    assert 'schema = "vrcforge.payload-integrity.v1"' in source
    assert 'Join-Path $payloadRoot "tools\\vrcforge_primitive_attestor.exe"' not in source


def test_strict_evidence_outputs_reject_reparse_and_overwrite_paths() -> None:
    source = _build_script()

    assert "function Resolve-SafeRepositoryPath" in source
    assert "function New-SafeRepositoryDirectory" in source
    assert "function Copy-SafeRepositoryFileCreateNew" in source
    assert "[System.IO.FileAttributes]::ReparsePoint" in source
    assert "[System.IO.FileMode]::CreateNew" in source
    assert 'dist\\evidence\\$headCommit\\$evidenceRunId' in source
    assert "Copy-Item -LiteralPath $attestorBuildExe" not in source


def test_evidence_authority_bundle_is_external_noninstalling_and_fail_closed() -> None:
    source = _build_script()

    for binary in (
        "vrcforge_primitive_evidence_service",
        "vrcforge_primitive_evidence_controller",
        "vrcforge_primitive_evidence_install_helper",
    ):
        assert f'"{binary}"' in source
        assert f'Join-Path $payloadRoot "tools\\{binary}.exe"' not in source

    assert "artifacts/primitive-evidence-authority/$headCommit/$evidenceRunId" in source
    assert "Copy-SafeRepositoryFileCreateNew" in source
    assert 'schema = "vrcforge.primitive_evidence_authority_bundle.v1"' in source
    assert "installationSupported = $false" in source
    assert "trustedBoundaryReady = $false" in source
    assert "candidatePayloadIncluded = $false" in source
    assert "$manifest.evidenceAuthority = $evidenceAuthority" in source
    assert (
        'generationPathPolicy -ne "authority-generation-sha256-parent-create-new-never-reuse"'
        in source
    )
    for field in (
        "generationBinaryRootPattern",
        "generationStateRootPattern",
        "serviceExecutablePattern",
        "controllerExecutablePattern",
        "installHelperExecutablePattern",
    ):
        assert field in source
    assert 'installMode = "create-new-never-reuse"' in source
    assert "installedPath = [string]$authorityInstalledPaths.service" in source
    assert "installedPath = [string]$authorityInstalledPaths.controller" in source
    assert "installedPath = [string]$authorityInstalledPaths.installHelper" in source
    assert "policySha256 = [string]$authorityPreview.policySha256" in source
    assert "planSha256 = [string]$authorityPreview.planSha256" in source
    assert "files = $authorityInstalledFiles" in source
    assert "layout.binaryAnchor" in source
    assert "layout.stateAnchor" in source
    assert "layout.binaryBase" in source
    assert "layout.stateBase" in source
    assert "layout.binaryVersionRoot" in source
    assert "layout.stateVersionRoot" in source
    assert "Known Folder anchors" in source
    assert "layout.binaryRoot" not in source
    assert "layout.stateRoot" not in source
    assert "authorityPreviewFile.byteLength" in source
    assert "Evidence authority bundle changed after maintenance preview" in source
    assert "ConvertTo-Json -Depth 7" in source
    assert '--plan' in source
    assert '--preview-install' in source
    for forbidden in ("--install", "--provision", "--reset", "--delete"):
        assert forbidden not in source


def test_evidence_authority_machine_layout_uses_system_known_folders() -> None:
    source = (
        REPO_ROOT / "src-tauri" / "src" / "primitive_evidence_authority_windows.rs"
    ).read_text(encoding="utf-8")

    assert "SHGetKnownFolderPath" in source
    assert "FOLDERID_ProgramFiles" in source
    assert "FOLDERID_ProgramData" in source
    assert 'env::var_os("ProgramFiles")' not in source
    assert 'env::var_os("ProgramData")' not in source


@pytest.mark.skipif(os.name != "nt", reason="the helper uses Windows known folders")
def test_install_helper_process_preview_binds_all_three_generation_files(tmp_path: Path) -> None:
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("cargo is unavailable")

    manifest = REPO_ROOT / "src-tauri" / "Cargo.toml"
    build = subprocess.run(
        [
            cargo,
            "build",
            "--quiet",
            "--locked",
            "--manifest-path",
            str(manifest),
            "--bin",
            "vrcforge_primitive_evidence_install_helper",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    helper = (
        REPO_ROOT
        / "src-tauri"
        / "target"
        / "debug"
        / "vrcforge_primitive_evidence_install_helper.exe"
    )
    assert helper.is_file()

    payloads = {
        "service": tmp_path / "service.exe",
        "controller": tmp_path / "controller.exe",
        "installHelper": tmp_path / "install-helper.exe",
    }
    original_payloads = {
        "service": b"service-payload-v1",
        "controller": b"controller-payload-v1",
        "installHelper": b"install-helper-payload-v1",
    }
    for content_name, content_bytes in original_payloads.items():
        payloads[content_name].write_bytes(content_bytes)

    def invoke(*arguments: str | Path) -> dict:
        completed = subprocess.run(
            [str(helper), *(str(argument) for argument in arguments)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    plan = invoke("--plan")
    assert plan["mutationSupported"] is False
    assert plan["trustedBoundaryReady"] is False
    assert (
        plan["generationPathPolicy"]
        == "authority-generation-sha256-parent-create-new-never-reuse"
    )
    assert plan["serviceSecuritySddl"]
    generation_placeholder = "{authority-generation-sha256-lower}"
    binary_anchor = Path(plan["layout"]["binaryAnchor"])
    state_anchor = Path(plan["layout"]["stateAnchor"])
    binary_base = Path(plan["layout"]["binaryBase"])
    state_base = Path(plan["layout"]["stateBase"])
    binary_version_root = Path(plan["layout"]["binaryVersionRoot"])
    state_version_root = Path(plan["layout"]["stateVersionRoot"])
    assert binary_base.parent == binary_anchor
    assert state_base.parent == state_anchor
    assert binary_base.name == "VRCForgeEvidenceAuthority"
    assert state_base.name == "VRCForgeEvidenceAuthority"
    assert binary_version_root.parent == binary_base
    assert state_version_root.parent == state_base
    assert binary_version_root.name == "v1"
    assert state_version_root.name == "v1"
    planned_binary_root = Path(plan["layout"]["generationBinaryRootPattern"])
    assert planned_binary_root.name == generation_placeholder
    assert planned_binary_root.parent.name == "generations"
    for field in (
        "serviceExecutablePattern",
        "controllerExecutablePattern",
        "installHelperExecutablePattern",
    ):
        assert Path(plan["layout"][field]).parent == planned_binary_root
    assert "serviceExecutable" not in plan["layout"]
    assert "controllerExecutable" not in plan["layout"]
    assert "installHelperExecutable" not in plan["layout"]

    preview = invoke(
        "--preview-install",
        payloads["service"],
        payloads["controller"],
        payloads["installHelper"],
    )
    assert preview["schema"] == (
        "vrcforge.primitive_evidence_authority_maintenance_preview.v1"
    )
    assert preview["operation"] == "install"
    assert preview["nativeMutationBackendAvailable"] is False
    assert preview["trustedBoundaryReady"] is False
    assert len(preview["generation"]) == 64
    assert len(preview["policySha256"]) == 64
    assert len(preview["planSha256"]) == 64

    generation_root = Path(preview["layout"]["generationBinaryRoot"])
    assert Path(preview["layout"]["binaryAnchor"]) == binary_anchor
    assert Path(preview["layout"]["stateAnchor"]) == state_anchor
    assert Path(preview["layout"]["binaryBase"]) == binary_base
    assert Path(preview["layout"]["stateBase"]) == state_base
    assert Path(preview["layout"]["binaryVersionRoot"]) == binary_version_root
    assert Path(preview["layout"]["stateVersionRoot"]) == state_version_root
    assert generation_root.name == preview["generation"]
    assert generation_root.parent.name == "generations"
    expected_names = {
        "service": "vrcforge_primitive_evidence_service.exe",
        "controller": "vrcforge_primitive_evidence_controller.exe",
        "installHelper": "vrcforge_primitive_evidence_install_helper.exe",
    }
    for content_name, expected_name in expected_names.items():
        source = payloads[content_name]
        content = preview["content"][content_name]
        installed = Path(preview["layout"][f"{content_name}Executable"])
        assert content["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert content["byteLength"] == source.stat().st_size
        assert installed.parent == generation_root
        assert installed.name == expected_name

    expected_command = f'"{preview["layout"]["serviceExecutable"]}" --service'
    assert preview["fixedPolicy"]["service"]["binaryCommand"] == expected_command
    assert (
        preview["fixedPolicy"]["service"]["securitySddl"]
        == plan["serviceSecuritySddl"]
    )

    for changed_name in payloads:
        payloads[changed_name].write_bytes(original_payloads[changed_name] + b"-changed")
        changed = invoke(
            "--preview-install",
            payloads["service"],
            payloads["controller"],
            payloads["installHelper"],
        )
        assert changed["generation"] != preview["generation"]
        assert changed["planSha256"] != preview["planSha256"]
        assert changed["policySha256"] == preview["policySha256"]
        for content_name in payloads:
            if content_name == changed_name:
                assert changed["content"][content_name] != preview["content"][content_name]
            else:
                assert changed["content"][content_name] == preview["content"][content_name]
        payloads[changed_name].write_bytes(original_payloads[changed_name])
