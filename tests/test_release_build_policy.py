from pathlib import Path


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
