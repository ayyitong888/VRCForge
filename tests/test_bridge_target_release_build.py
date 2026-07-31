from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "packaging" / "bridge_target_packager_probe.py"
BUILD_PATH = REPO_ROOT / "packaging" / "build_bridge_target.ps1"
RELEASE_PATH = REPO_ROOT / "packaging" / "build_release.ps1"
PUBLISH_PATH = REPO_ROOT / "packaging" / "publish_release.ps1"
VERIFY_PATH = REPO_ROOT / "packaging" / "verify_bridge_target_release.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_fixed_target_configuration_is_independent_onedir_and_binds_connector() -> None:
    probe = _read(PROBE_PATH)
    build = _read(BUILD_PATH)
    ast.parse(probe)

    assert 'EXPECTED_PYINSTALLER_VERSION = "6.19.0"' in probe
    assert "primitive_bridge_target_adapter as adapter" in probe
    assert "importlib.metadata.distribution" in probe
    assert "adapter.FIXED_CONNECTOR_DISTRIBUTION" in probe
    assert "adapter.FIXED_CONNECTOR_VERSION" in probe
    assert "adapter.FIXED_CONNECTOR_MODULE_SHA256" in probe
    assert "adapter.FIXED_CONNECTOR_MODULE_BYTES" in probe
    assert "primitive_bridge_target_entry.py" in build
    assert "dashboard_server.py" not in build
    assert '"--onedir"' in build
    assert '"--name" "vrcforge_bridge_target"' in build
    assert '"--console"' in build
    assert '"--noupx"' in build
    assert '"--contents-directory" "_internal"' in build
    assert '"--hidden-import" ([string]$packagerConfig.module)' in build
    assert '"--copy-metadata" ([string]$packagerConfig.distribution)' in build
    assert '"--add-data" $connectorDataArgument' in build
    assert "vrcforge_bridge_target.spec" not in build


def test_packager_probe_is_read_only_and_matches_installed_fixed_inputs() -> None:
    completed = subprocess.run(
        ["python", str(PROBE_PATH)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["schema"] == "vrcforge.bridge_target_packager_probe.v1"
    assert payload["packagerVersion"] == "6.19.0"
    assert payload["connectorVersion"] == "9.6.8"
    assert payload["module"] == "main"
    assert Path(payload["connectorSource"]).is_file()


def test_bridge_target_builder_is_strict_manifested_and_never_executes_target() -> None:
    source = _read(BUILD_PATH)

    for relaxation in ("AllowDirty", "AllowUnpushed", "AllowVersionMismatch", "StrictEvidence"):
        assert relaxation not in source
    assert "bridge_target_packager_probe.py" in source
    assert "python" in source.casefold()
    assert "-m" in source and "PyInstaller" in source
    assert "--noconfirm" in source
    assert "--clean" in source
    assert "bridge_target_manifest.py" in source
    assert source.count('"--build"') == 1
    assert "ManifestPath must stay outside OutputDir" in source
    assert source.index("ManifestPath must stay outside OutputDir") < source.index(
        "Remove-Item -LiteralPath $resolvedOutputDir"
    )
    assert 'Join-Path $resolvedOutputDir "vrcforge_bridge_target.exe"' in source
    assert "$buildReceipt.mode -ne \"build\"" in source
    assert "$verifyReceipt.mode -ne \"verify\"" in source
    assert "$verifyReceipt.treeDigest -cne $buildReceipt.treeDigest" in source
    assert "$verifyReceipt.directoryCount -ne $buildReceipt.directoryCount" in source
    assert "$verifyReceipt.entryCount -ne $buildReceipt.entryCount" in source
    assert "$verifyReceipt.byteCount -ne $buildReceipt.byteCount" in source
    assert '$fixedConnectorModuleSha256 = "e8effb923d0fbd1427f1d89ea6f1d6a69914658b1ba18cd86a52f37ccd269fa4"' in source
    assert "$fixedConnectorModuleBytes = 39869" in source
    assert '[string]$_.path -ceq "_internal/main.py"' in source
    assert "$connectorRecords.Count -ne 1" in source
    assert "[string]$connectorRecords[0].sha256 -cne $fixedConnectorModuleSha256" in source
    assert "[uint64]$connectorRecords[0].length -ne $fixedConnectorModuleBytes" in source
    assert not re.search(r"&\s*\$bridgeTargetExecutable(?:\s|`|$)", source)


def test_strict_release_build_includes_and_reverifies_bound_bridge_tree() -> None:
    source = _read(RELEASE_PATH)

    assert "$bridgeTargetRuntime = $null" in source
    assert "if ($strictSourceBuild)" in source
    assert 'Join-Path $payloadRoot "bridge_target"' in source
    assert 'Join-Path $payloadRoot "bridge-target-manifest.json"' in source
    assert "build_bridge_target.ps1" in source
    assert "bridge_target_manifest.py" in source
    assert "$bridgeTargetVerifyReceipt.mode -ne \"verify\"" in source
    assert "$bridgeTargetVerifyReceipt.treeDigest" in source
    assert "$bridgeTargetManifestSha256" in source
    assert 'schema = "vrcforge.bridge_target_runtime.v1"' in source
    assert 'runtimeRelativeRoot = "bridge_target"' in source
    assert 'executableRelativePath = "bridge_target/vrcforge_bridge_target.exe"' in source
    assert "executableSha256 = $bridgeExecutableSha256" in source
    assert 'manifestRelativePath = "bridge-target-manifest.json"' in source
    assert "candidatePayloadIncluded = $true" in source
    assert "strictSourceBound = $true" in source
    assert "verifiedAfterBuild = $true" in source
    assert "$payloadIntegrityManifest.bridgeTargetRuntime = $bridgeTargetRuntime" in source
    assert "$manifest.bridgeTargetRuntime = $bridgeTargetRuntime" in source
    assert "LOCAL acceptance build omits the fixed bridge runtime tree" in source
    assert source.index("build_bridge_target.ps1") < source.index("payload-integrity.json")
    assert source.index("$payloadIntegrityManifest.bridgeTargetRuntime") < source.index(
        "Compress-Archive"
    )
    invocation = source[
        source.index("build_bridge_target.ps1") : source.index("build_bridge_target.ps1")
        + 420
    ]
    for relaxation in ("AllowDirty", "AllowUnpushed", "AllowVersionMismatch"):
        assert relaxation not in invocation


def test_release_manifest_binds_manifest_bytes_and_tree_summary_only_by_relative_path() -> None:
    source = _read(RELEASE_PATH)
    runtime_start = source.index('schema = "vrcforge.bridge_target_runtime.v1"')
    runtime_end = source.index("    }", runtime_start) + len("    }")
    runtime_block = source[runtime_start:runtime_end]

    for field in (
        "manifestSha256",
        "executableSha256",
        "treeDigest",
        "directoryCount",
        "entryCount",
        "byteCount",
    ):
        assert field in runtime_block
    assert "$bridgeTargetRoot" not in runtime_block
    assert "$bridgeTargetManifestPath" not in runtime_block


def test_build_and_publish_both_require_archive_derived_bridge_receipts() -> None:
    verifier = _read(VERIFY_PATH)
    build = _read(RELEASE_PATH)
    publish = _read(PUBLISH_PATH)
    ast.parse(verifier)

    assert "verify_release_bridge_target" in verifier
    assert "zipfile.ZipFile" in verifier
    assert "bridge_target_manifest" in verifier
    assert "extract(" not in verifier
    assert "extractall(" not in verifier
    for source in (build, publish):
        assert "verify_bridge_target_release.py" in source
        assert '"--release-manifest"' in source
        assert '"--payload-zip"' in source
        assert '"vrcforge.bridge_target_release_verification.v1"' in source
        assert "$bridgeArchiveReceipt.verifiedFromArchive -ne $true" in source
        assert "$bridgeArchiveReceipt.executableSha256" in source
    assert build.index("Compress-Archive") < build.index("verify_bridge_target_release.py")
    assert build.index("release-manifest.json") < build.rindex(
        "verify_bridge_target_release.py"
    )
    assert publish.index("verify_bridge_target_release.py") < publish.index(
        "$tag = \"v$Version\""
    )


@pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell is unavailable")
def test_bridge_build_and_release_scripts_parse_without_execution() -> None:
    for path in (BUILD_PATH, RELEASE_PATH, PUBLISH_PATH):
        command = (
            "$errors=$null; "
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',"
            "[ref]$null,[ref]$errors); "
            "if($errors.Count -gt 0){$errors | ForEach-Object {$_.Message}; exit 1}"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
