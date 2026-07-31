import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_script() -> str:
    return (REPO_ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8")


def _authority_bundle_tool() -> str:
    return (
        REPO_ROOT / "packaging" / "build_protected_runtime_authority_bundle.py"
    ).read_text(encoding="utf-8")


def _load_authority_bundle_tool():
    path = REPO_ROOT / "packaging" / "build_protected_runtime_authority_bundle.py"
    spec = importlib.util.spec_from_file_location(
        "build_protected_runtime_authority_bundle_for_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_release_python_resolver_supports_the_windows_launcher() -> None:
    source = _build_script()
    resolver = source[
        source.index("function Resolve-PythonExe") : source.index(
            "function Resolve-MakeNsisExe"
        )
    ]

    assert "Get-Command python.exe" in resolver
    assert "Get-Command python" in resolver
    assert "Get-Command py.exe" in resolver
    assert resolver.index("Get-Command python.exe") < resolver.index("Get-Command py.exe")


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


def test_evidence_authority_inputs_are_external_and_fail_closed() -> None:
    source = _build_script()

    for binary in (
        "vrcforge_primitive_evidence_service",
        "vrcforge_primitive_evidence_controller",
        "vrcforge_primitive_evidence_install_helper",
        "vrcforge_primitive_lifecycle_driver",
        "vrcforge_primitive_bridge_launcher",
    ):
        assert f'"{binary}"' in source
        assert f'Join-Path $payloadRoot "tools\\{binary}.exe"' not in source

    assert "artifacts/primitive-evidence-authority/$headCommit/$evidenceRunId" in source
    assert "Copy-SafeRepositoryFileCreateNew" in source
    assert "$manifest.evidenceAuthority" not in source
    assert "$evidenceAuthority" not in source
    assert '"build_protected_runtime_authority_bundle.py"' in source
    assert '"authority-bundle.json"' in source
    assert '"protected-runtime-dependency-set.json"' in source
    assert '"protected-runtime-source-manifest.json"' in source
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
    assert "layout.binaryAnchor" in source
    assert "layout.stateAnchor" in source
    assert "layout.binaryBase" in source
    assert "layout.stateBase" in source
    assert "layout.binaryVersionRoot" in source
    assert "layout.stateVersionRoot" in source
    assert "Known Folder anchors" in source
    assert "layout.binaryRoot" not in source
    assert "layout.stateRoot" not in source
    assert "Evidence authority input changed after its initial copy" in source
    assert "ConvertTo-Json -Depth 7" in source
    assert '--plan' in source
    assert '--preview-install' not in source
    for forbidden in ("--install", "--provision", "--reset", "--delete"):
        assert forbidden not in source


def test_private_authority_finalization_occurs_after_public_manifest_seal() -> None:
    source = _build_script()
    tool = _authority_bundle_tool()

    manifest_write = source.index("$releaseManifestJson = $manifest | ConvertTo-Json -Depth 7")
    archive_readback = source.index("$bridgeArchiveVerifier =")
    private_finalize = source.index("$authorityBundleTool =")
    assert manifest_write < archive_readback < private_finalize
    assert "$manifest.evidenceAuthority" not in source
    assert '"--strict-release-manifest", $releaseManifestPath' in source
    assert (
        "$releaseManifestSha256AfterAuthority -cne "
        "$releaseManifestSha256BeforeAuthority"
    ) in source

    dependency_create = tool.index(
        "dependency_created = protected_runtime_dependency_set.create_dependency_set"
    )
    dependency_verify = tool.index(
        "dependency_verified = protected_runtime_dependency_set.verify_dependency_set"
    )
    source_create = tool.index(
        "source_created = protected_runtime_source_manifest.create_source_manifest"
    )
    source_verify = tool.index(
        "source_verified = protected_runtime_source_manifest.verify_source_manifest"
    )
    preview = tool.index('"--preview-install",')
    sidecar_write = tool.index("sidecar_record = _write_create_new")
    assert (
        dependency_create
        < dependency_verify
        < source_create
        < source_verify
        < preview
        < sidecar_write
    )


def test_private_authority_finalizer_binds_exactly_six_preview_payloads() -> None:
    source = _authority_bundle_tool()

    assert 'BUNDLE_SCHEMA = "vrcforge.primitive_evidence_authority_bundle.v3"' in source
    assert 'DEPENDENCY_FILE_NAME = "protected-runtime-dependency-set.json"' in source
    assert 'SOURCE_MANIFEST_FILE_NAME = "protected-runtime-source-manifest.json"' in source
    assert 'BUNDLE_FILE_NAME = "authority-bundle.json"' in source
    for field in (
        '"service"',
        '"controller"',
        '"installHelper"',
        '"lifecycleDriver"',
        '"bridgeLauncher"',
        '"runtimeSourceManifest"',
    ):
        assert field in source
    assert "or set(raw_content) != set(PREVIEW_PAYLOAD_ORDER)" in source
    assert '"--preview-install",' in source
    assert "previewPayloadCount" in source
    assert "protected_runtime_dependency_set.create_dependency_set" in source
    assert "protected_runtime_dependency_set.verify_dependency_set" in source
    assert "protected_runtime_source_manifest.create_source_manifest" in source
    assert "protected_runtime_source_manifest.verify_source_manifest" in source
    assert 'key.casefold() == "evidenceauthority"' in source
    for forbidden in ('"--install"', '"--provision"', '"--reset"', '"--delete"'):
        assert forbidden not in source

    tool = _load_authority_bundle_tool()
    assert tool.PREVIEW_PAYLOAD_ORDER == (
        "service",
        "controller",
        "installHelper",
        "lifecycleDriver",
        "bridgeLauncher",
        "runtimeSourceManifest",
    )


def test_private_authority_preview_validator_rejects_extra_payload() -> None:
    tool = _load_authority_bundle_tool()
    binary_anchor = r"C:\Program Files"
    state_anchor = r"C:\ProgramData"
    binary_base = rf"{binary_anchor}\VRCForgeEvidenceAuthority"
    state_base = rf"{state_anchor}\VRCForgeEvidenceAuthority"
    binary_version = rf"{binary_base}\v1"
    state_version = rf"{state_base}\v1"
    placeholder = "{authority-generation-sha256-lower}"
    binary_pattern = rf"{binary_version}\generations\{placeholder}"
    state_pattern = rf"{state_version}\generations\{placeholder}"
    plan = {
        "schema": tool.PLAN_SCHEMA,
        "mutationSupported": False,
        "trustedBoundaryReady": False,
        "candidatePayloadIncludesAuthority": False,
        "serviceSecuritySddl": "D:P(A;;FA;;;SY)",
        "generationPathPolicy": (
            "authority-generation-sha256-parent-create-new-never-reuse"
        ),
        "blockers": ["not-ready"],
        "layout": {
            "binaryAnchor": binary_anchor,
            "stateAnchor": state_anchor,
            "binaryBase": binary_base,
            "stateBase": state_base,
            "binaryVersionRoot": binary_version,
            "stateVersionRoot": state_version,
            "generationBinaryRootPattern": binary_pattern,
            "generationStateRootPattern": state_pattern,
            "serviceExecutablePattern": (
                rf"{binary_pattern}\vrcforge_primitive_evidence_service.exe"
            ),
            "controllerExecutablePattern": (
                rf"{binary_pattern}\vrcforge_primitive_evidence_controller.exe"
            ),
            "installHelperExecutablePattern": (
                rf"{binary_pattern}\vrcforge_primitive_evidence_install_helper.exe"
            ),
            "lifecycleDriverExecutablePattern": (
                rf"{binary_pattern}\vrcforge_primitive_lifecycle_driver.exe"
            ),
            "bridgeLauncherExecutablePattern": (
                rf"{binary_pattern}\vrcforge_primitive_bridge_launcher.exe"
            ),
        },
    }
    plan_layout = tool._validate_plan(plan)
    generation = "1" * 64
    generation_binary = rf"{binary_version}\generations\{generation}"
    generation_state = rf"{state_version}\generations\{generation}"
    payload_records = {
        name: {"sha256": str(index + 2) * 64, "byteLength": index + 1}
        for index, name in enumerate(tool.PREVIEW_PAYLOAD_ORDER)
    }
    preview = {
        "schema": tool.PREVIEW_SCHEMA,
        "operation": "install",
        "automaticExecutionAllowed": False,
        "nativeMutationBackendAvailable": False,
        "executionRequiresVerifiedElevatedMaintenanceCapability": True,
        "trustedBoundaryReady": False,
        "blockers": ["not-ready"],
        "steps": ["preview-only"],
        "generation": generation,
        "policySha256": "6" * 64,
        "planSha256": "7" * 64,
        "content": dict(payload_records),
        "layout": {
            "binaryAnchor": binary_anchor,
            "stateAnchor": state_anchor,
            "binaryBase": binary_base,
            "stateBase": state_base,
            "binaryVersionRoot": binary_version,
            "stateVersionRoot": state_version,
            "generationBinaryRoot": generation_binary,
            "generationStateRoot": generation_state,
            "serviceExecutable": (
                rf"{generation_binary}\vrcforge_primitive_evidence_service.exe"
            ),
            "controllerExecutable": (
                rf"{generation_binary}\vrcforge_primitive_evidence_controller.exe"
            ),
            "installHelperExecutable": (
                rf"{generation_binary}\vrcforge_primitive_evidence_install_helper.exe"
            ),
            "lifecycleDriverExecutable": (
                rf"{generation_binary}\vrcforge_primitive_lifecycle_driver.exe"
            ),
            "bridgeLauncherExecutable": (
                rf"{generation_binary}\vrcforge_primitive_bridge_launcher.exe"
            ),
            "runtimeSourceManifest": (
                rf"{generation_state}\runtime-source-manifest.json"
            ),
        },
        "fixedPolicy": {
            "service": {
                "binaryCommand": (
                    f'"{generation_binary}\\vrcforge_primitive_evidence_service.exe" '
                    "--service"
                ),
                "securitySddl": plan["serviceSecuritySddl"],
            }
        },
    }
    observed_generation, installed = tool._validate_preview(
        preview,
        plan=plan,
        plan_layout=plan_layout,
        payload_records=payload_records,
    )
    assert observed_generation == generation
    assert set(installed) == set(tool.PREVIEW_PAYLOAD_ORDER)

    legacy_preview = dict(preview)
    legacy_preview["schema"] = (
        "vrcforge.primitive_evidence_authority_maintenance_preview.v1"
    )
    legacy_preview["content"] = {
        name: record
        for name, record in preview["content"].items()
        if name not in {"lifecycleDriver", "bridgeLauncher"}
    }
    with pytest.raises(tool.AuthorityBundleError):
        tool._validate_preview(
            legacy_preview,
            plan=plan,
            plan_layout=plan_layout,
            payload_records=payload_records,
        )

    preview["content"]["extra"] = {"sha256": "8" * 64, "byteLength": 1}
    with pytest.raises(tool.AuthorityBundleError):
        tool._validate_preview(
            preview,
            plan=plan,
            plan_layout=plan_layout,
            payload_records=payload_records,
        )


def test_strict_evidence_requires_all_materialized_fixture_roots() -> None:
    source = _build_script()

    for scenario_id in (
        "component_feature_application",
        "parameter_optimization",
        "cross_avatar_accessory_copy",
        "model_part_composition",
    ):
        assert f'"{scenario_id}"' in source
    assert (
        "Strict evidence requires all four materialized protected-runtime fixtures "
        "and descriptors."
    ) in source
    assert "ProtectedRuntimeProjectRoot" in source
    assert "ProtectedRuntimeEditorBuiltinsRoot" in source
    assert "ProtectedRuntimePackageRoot" in source


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
def test_install_helper_process_preview_binds_all_six_protected_payloads(
    tmp_path: Path,
) -> None:
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
        "lifecycleDriver": tmp_path / "lifecycle-driver.exe",
        "bridgeLauncher": tmp_path / "bridge-launcher.exe",
        "runtimeSourceManifest": tmp_path / "protected-runtime-source.json",
    }
    original_payloads = {
        "service": b"service-payload-v1",
        "controller": b"controller-payload-v1",
        "installHelper": b"install-helper-payload-v1",
        "lifecycleDriver": b"lifecycle-driver-payload-v1",
        "bridgeLauncher": b"bridge-launcher-payload-v1",
        "runtimeSourceManifest": b'{"schema":"fixed-source-v1"}\n',
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
        "lifecycleDriverExecutablePattern",
        "bridgeLauncherExecutablePattern",
    ):
        assert Path(plan["layout"][field]).parent == planned_binary_root
    assert "serviceExecutable" not in plan["layout"]
    assert "controllerExecutable" not in plan["layout"]
    assert "installHelperExecutable" not in plan["layout"]
    assert "lifecycleDriverExecutable" not in plan["layout"]
    assert "bridgeLauncherExecutable" not in plan["layout"]

    preview = invoke(
        "--preview-install",
        payloads["service"],
        payloads["controller"],
        payloads["installHelper"],
        payloads["lifecycleDriver"],
        payloads["bridgeLauncher"],
        payloads["runtimeSourceManifest"],
    )
    assert preview["schema"] == (
        "vrcforge.primitive_evidence_authority_maintenance_preview.v4"
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
        "lifecycleDriver": "vrcforge_primitive_lifecycle_driver.exe",
        "bridgeLauncher": "vrcforge_primitive_bridge_launcher.exe",
    }
    for content_name, expected_name in expected_names.items():
        source = payloads[content_name]
        content = preview["content"][content_name]
        installed = Path(preview["layout"][f"{content_name}Executable"])
        assert content["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert content["byteLength"] == source.stat().st_size
        assert installed.parent == generation_root
        assert installed.name == expected_name
    source_content = preview["content"]["runtimeSourceManifest"]
    source_path = payloads["runtimeSourceManifest"]
    assert source_content["sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert source_content["byteLength"] == source_path.stat().st_size
    installed_source = Path(preview["layout"]["runtimeSourceManifest"])
    assert installed_source.parent == Path(preview["layout"]["generationStateRoot"])
    assert installed_source.name == "runtime-source-manifest.json"

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
            payloads["lifecycleDriver"],
            payloads["bridgeLauncher"],
            payloads["runtimeSourceManifest"],
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
