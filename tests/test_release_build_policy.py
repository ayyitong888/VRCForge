import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_and_localized_about_versions_follow_release_version() -> None:
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    runtime_sources = (
        REPO_ROOT / "agent_mcp_2026.py",
        REPO_ROOT / "agent_gateway.py",
        REPO_ROOT / "tools" / "vrcforge_agent_mcp_stdio.py",
    )
    for path in runtime_sources:
        source = path.read_text(encoding="utf-8")
        assert f'server_version="{version}"' in source or f'server_version: str = "{version}"' in source

    for locale_name in ("en-US", "ja-JP", "zh-CN", "zh-TW"):
        locale = json.loads(
            (REPO_ROOT / "src" / "locales" / f"{locale_name}.json").read_text(
                encoding="utf-8"
            )
        )
        assert locale["settings"]["aboutProduct"] == f"VRCForge {version}"


def test_tauri_manifest_selects_the_desktop_app_as_default_binary() -> None:
    manifest = tomllib.loads(
        (REPO_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    )

    assert manifest["package"]["default-run"] == "vrcforge-agentic-app"


def test_vite_dev_watcher_excludes_generated_and_evidence_trees() -> None:
    config = (REPO_ROOT / "vite.config.ts").read_text(encoding="utf-8")

    assert '"**/artifacts/**"' in config
    assert '"**/src-tauri/target/**"' in config


def test_desktop_project_selection_is_confirmed_before_unity_readiness() -> None:
    commands = (REPO_ROOT / "src-tauri" / "src" / "commands.rs").read_text(
        encoding="utf-8"
    )
    api = (REPO_ROOT / "src" / "lib" / "api" / "app.ts").read_text(
        encoding="utf-8"
    )
    hook = (
        REPO_ROOT / "src" / "hooks" / "use-dashboard-project-selection.ts"
    ).read_text(encoding="utf-8")

    assert '"/api/state".to_string()' in commands
    assert 'serde_json::json!({"projectPath": request.project_path})' in commands
    assert '"select_unity_project"' in api
    assert hook.index("await selectUnityProject(endpoint, projectPath)") < hook.index(
        "await refreshUnityReadiness(endpoint)"
    )
    assert "state.selectedProjectPath" in hook
    assert "!projectPath.trim()" in hook
    assert "confirmedProjectPath" in hook
    assert "normalizeProjectPathKey(confirmedProjectPath) === normalizeProjectPathKey(projectPath)" in hook


def test_desktop_restores_authoritative_project_without_guessing_first_item() -> None:
    app = (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8")
    types = (REPO_ROOT / "src" / "lib" / "api" / "types.ts").read_text(encoding="utf-8")

    restored = app.index("bootstrap?.health.state?.selectedProjectPath")
    applied = app.index("setActiveProjectPath(authoritativeSelectedProjectPath)")
    assert restored < applied
    assert "activeMcpProjects.length === 1" not in app
    assert "setActiveProjectPath(projectKey(projectItems[0]))" not in app
    assert "state?: ProjectSelectionState" in types


def test_user_requested_temporary_chat_is_not_overwritten_by_authoritative_project() -> None:
    app = (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8")

    helper = app.index("function openTemporaryChat()")
    marks_initialization_complete = app.index("projectInitRef.current = true;", helper)
    clears_project_scope = app.index("newTemporaryChat();", marks_initialization_complete)
    restore_guard = app.index(
        "if (projectInitRef.current || activeProjectPath || !authoritativeSelectedProjectPath)"
    )
    authoritative_restore = app.index("setActiveProjectPath(authoritativeSelectedProjectPath)")

    assert helper < marks_initialization_complete < clears_project_scope
    assert restore_guard < authoritative_restore
    assert "onNewTemporaryChat={openTemporaryChat}" in app


def test_app_approval_actions_keep_the_pending_items_exact_project_scope() -> None:
    hook = (
        REPO_ROOT / "src" / "hooks" / "use-approval-execution.ts"
    ).read_text(encoding="utf-8")

    assert "const approvalScope = scopeForApproval(approval.id);" in hook
    assert "const approvalScope = scopeForApproval(approvalId, allowFutureCategory);" in hook
    assert hook.count("const approvalScope = scopeForApproval(approvalId);") == 1
    assert "approveAgentApproval(endpoint, approvalId, approvalScope)" in hook
    assert "rejectAgentApproval(endpoint, approvalId, approvalScope)" in hook
    assert "? approval.projectRoot?.trim() || \"\"" in hook
    assert ": activeRuntimeProjectPath.trim();" in hook
    assert "expectedProjectRoot: projectRoot || undefined" in hook


def test_approval_allow_split_button_keeps_allow_once_primary_and_future_scoped() -> None:
    split_button = (
        REPO_ROOT / "src" / "components" / "approvals" / "approval-allow-split-button.tsx"
    ).read_text(encoding="utf-8")
    scoped_card = (
        REPO_ROOT / "src" / "components" / "approvals" / "scoped-pending-approval-card.tsx"
    ).read_text(encoding="utf-8")
    pending_strip = (
        REPO_ROOT / "src" / "components" / "approvals" / "pending-approvals-strip.tsx"
    ).read_text(encoding="utf-8")

    assert 'onClick={() => onApprove(approvalId)}' in split_button
    assert "onApprove(approvalId, true);" in split_button
    assert 'aria-haspopup="menu"' in split_button
    assert "ApprovalAllowSplitButton" in scoped_card
    assert "ApprovalAllowSplitButton" in pending_strip


def test_tauri_approval_command_moves_blocking_backend_request_off_the_ui_thread() -> None:
    commands = (REPO_ROOT / "src-tauri" / "src" / "commands.rs").read_text(encoding="utf-8")
    approval_start = commands.index("pub async fn approve_agent_approval(")
    approval_end = commands.index("\n#[tauri::command]", approval_start + 1)
    approval_command = commands[approval_start:approval_end]

    assert "blocking_backend_json_request(move ||" in approval_command
    assert "backend_json_request(" in approval_command
    assert ".await" in approval_command


def _build_script() -> str:
    return (REPO_ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8")


def test_smoke_flavor_is_compile_time_scoped_and_cannot_enter_release_build() -> None:
    release_build = _build_script()
    publish = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")

    # The normal build/publish paths do not accept the smoke compiler token or
    # smoke output names, so a smoke binary cannot become a release-manifest asset.
    assert "SMOKE_ID" not in release_build
    assert "VRCFORGE_SMOKE_BUILD" not in release_build
    assert "VRCForge-Smoke-" not in release_build
    assert "SMOKE_ID" not in publish
    assert "VRCFORGE_SMOKE_BUILD" not in publish
    assert "VRCForge-Smoke-" not in publish

    for name in ("VRCForge_Offline_Installer_x64.nsi", "VRCForge_Web_Installer_x64.nsi"):
        source = (REPO_ROOT / "installer" / name).read_text(encoding="utf-8")
        assert '!error "SMOKE_ID cannot be supplied directly' in source
        assert "!ifdef VRCFORGE_SMOKE_BUILD" in source
        system_command = source.split("!system", 1)[1].split("= 0", 1)[0]
        assert "${SMOKE_ID}" not in system_command
        assert "VRCFORGE_NSIS_SMOKE_ID" not in system_command
        assert "ValidateNsisSmokeIdentity.ps1" in system_command
        assert '!define SMOKE_ID "$%VRCFORGE_NSIS_SMOKE_ID%"' in source

        assert '!define INSTALL_LEAF "VRCForge-Smoke-${SMOKE_ID}"' in source
        assert '!define UNINSTALL_KEY "VRCForge-Smoke-${SMOKE_ID}"' in source
        assert '!define INSTALLER_LANGUAGE_KEY "Software\\VRCForge\\InstallerSmoke\\${SMOKE_ID}"' in source
        assert '!define START_MENU_GROUP "VRCForge Smoke ${SMOKE_ID}"' in source
        assert '!define DESKTOP_SHORTCUT "VRCForge Smoke ${SMOKE_ID}.lnk"' in source
        assert '!define USER_DATA_RELATIVE "VRCForge\\installer-smoke\\${SMOKE_ID}"' in source
        assert "!else" in source
        assert '!define INSTALL_LEAF "VRCForge"' in source
        assert 'InstallDir "$PROGRAMFILES64\\${INSTALL_LEAF}"' in source
        assert "Function ${Prefix}ValidateScopedInstallDir" in source
        assert 'StrCmp "$INSTDIR" "$PROGRAMFILES64\\${INSTALL_LEAF}"' in source
        assert "Call ValidateScopedInstallDir" in source
        assert "Call un.ValidateScopedInstallDir" in source
        assert '-ExpectedInstallLeaf "${INSTALL_LEAF}" -StateTag "${STATE_TAG}"' in source
        assert 'DeleteRegKey HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${UNINSTALL_KEY}"' in source
        assert 'DeleteRegKey /ifempty HKCU "${INSTALLER_LANGUAGE_KEY}"' in source
        assert '!define APP_USER_MODEL_ID "app.vrcforge.agentic"' in source
        assert 'WriteRegStr HKCU "Software\\Classes\\AppUserModelId\\${APP_USER_MODEL_ID}" "DisplayName" "VRCForge"' in source
        assert 'WriteRegStr HKCU "Software\\Classes\\AppUserModelId\\${APP_USER_MODEL_ID}" "IconUri" "$INSTDIR\\VRCForge.png"' in source
        assert 'DeleteRegKey HKCU "Software\\Classes\\AppUserModelId\\${APP_USER_MODEL_ID}"' in source

    validator = (REPO_ROOT / "installer" / "ValidateNsisSmokeIdentity.ps1").read_text(encoding="utf-8")
    assert '[Environment]::GetEnvironmentVariable("VRCFORGE_NSIS_SMOKE_ID", "Process")' in validator
    assert '$smokeId -cnotmatch "^[a-f0-9]{32}$"' in validator


def test_installer_smoke_documentation_uses_the_exact_isolated_identity() -> None:
    source = (REPO_ROOT / "packaging" / "README.md").read_text(encoding="utf-8")

    assert '$smokeId = [guid]::NewGuid().ToString("N")' in source
    assert "compiler-scoped smoke flavor" in source
    assert "Never point this command at `dist\\release`" in source
    assert "artifacts\\installer-smoke-build\\$smokeId\\VRCForge_Offline_Installer_x64.exe" in source
    assert "--installer dist\\release\\VRCForge_Offline_Installer_x64.exe" not in source
    assert "--smoke-id $smokeId" in source
    assert '--install-dir "$env:ProgramFiles\\VRCForge-Smoke-$smokeId"' in source
    assert '--user-data-root "$env:LOCALAPPDATA\\VRCForge\\installer-smoke\\$smokeId"' in source
    assert "--scope production-clean" in source
    assert "--production-clean-confirmation I-OWN-THIS-DISPOSABLE-WINDOWS-ENVIRONMENT" in source
    assert '--upgrade-installer "<downloaded official v1.4.0 offline installer>"' in source
    assert "Do not run `production-clean` on a normal workstation" in source
    assert "--upgrade-from-installer-sha256 58bf32ec8cb4f71dd6272db427dd0218e7161c5730314e9ae4f9516a50c02901" in source


def test_payload_helper_accepts_only_the_exact_compiled_scope_identity() -> None:
    source = (REPO_ROOT / "installer" / "VRCForge_WebPayload.ps1").read_text(encoding="utf-8")

    assert '[string]$ExpectedInstallLeaf = "VRCForge"' in source
    assert '[string]$StateTag = "VRCForge"' in source
    assert '$ExpectedInstallLeaf -cne $StateTag' in source
    assert "$ExpectedInstallLeaf -cmatch '^VRCForge-Smoke-[a-f0-9]{32}$'" in source
    assert '[string]::Equals([IO.Path]::GetFileName($destination), $ExpectedInstallLeaf, [StringComparison]::Ordinal)' in source
    assert '"VRCForge Installer Staging-$ExpectedInstallLeaf"' in source
    assert '"$script:InstallSiblingPrefix-$Kind-"' in source


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


def test_release_packaged_backend_smoke_reuses_resolved_python() -> None:
    source = _build_script()

    assert "$pythonExe = Resolve-PythonExe" in source
    assert "& $pythonExe .\\scripts\\smoke_packaged_backend.py" in source
    assert "& python .\\scripts\\smoke_packaged_backend.py" not in source
    assert source.index("$pythonExe = Resolve-PythonExe") < source.index(
        "& $pythonExe .\\scripts\\smoke_packaged_backend.py"
    )


def test_backend_packaging_collects_checkpoint_recovery_service() -> None:
    source = (REPO_ROOT / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")

    assert "--hidden-import agent_checkpoint_recovery" in source


def test_backend_packaging_collects_approval_transaction_service() -> None:
    source = (REPO_ROOT / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")

    assert "--hidden-import agent_approval_transactions" in source


def test_backend_packaging_collects_skill_registry_service() -> None:
    source = (REPO_ROOT / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")

    assert "--hidden-import agent_skill_registry" in source


def test_backend_packaging_collects_and_verifies_winpty_runtime_files() -> None:
    source = (REPO_ROOT / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")

    assert "--hidden-import winpty" in source
    assert "--collect-data winpty" in source
    assert '"_internal\\winpty\\OpenConsole.exe"' in source
    assert '"_internal\\winpty\\winpty-agent.exe"' in source
    assert "PyInstaller did not collect required PTY runtime file" in source


def test_publish_path_only_accepts_strict_release_policy() -> None:
    source = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")

    assert '[string]$buildPolicy.mode -ne "strict"' in source
    assert "$buildPolicy.releaseEligible -ne $true" in source


def test_publish_path_stops_after_verified_draft_and_never_publishes_it() -> None:
    source = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")

    assert '"--draft"' in source
    assert re.search(r"\bdraft\s*=\s*false\b", source, re.IGNORECASE) is None
    assert re.search(r"-ExpectedDraft\s+\$false\b", source, re.IGNORECASE) is None
    assert re.search(r"--method\s+PATCH\b", source, re.IGNORECASE) is None
    assert [value.lower() for value in re.findall(r"-ExpectedDraft\s+\$(true|false)\b", source, re.IGNORECASE)] == ["true"]

    final_verification = source.rindex("Assert-GitHubReleaseSnapshot `")
    final_tail = source[final_verification:]
    assert "-ExpectedDraft $true `" in final_tail
    assert "-ExpectedDraft $false" not in final_tail
    assert "Get-GitHubReleaseSnapshot" not in final_tail
    assert 'Write-Host "Created, uploaded, and verified Draft GitHub Release $tag. It remains unpublished."' in final_tail


def test_publish_path_requires_version_bound_release_notes_and_reads_them_back() -> None:
    source = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    notes = (REPO_ROOT / "docs" / f"RELEASE_NOTES_{version}.md").read_text(encoding="utf-8")

    assert "function Get-VersionBoundReleaseNotes" in source
    assert '"${Target}:docs/RELEASE_NOTES_$Version.md"' in source
    assert "Get-VersionBoundReleaseNotes -Version $Version -Target $target" in source
    assert "Missing version-bound release notes in target commit:" in source
    assert "ReadAllText($releaseNotesPath" not in source
    assert '$stagedReleaseNotesPath = Join-Path $stagingRoot "release-notes.md"' in source
    assert '[System.IO.File]::WriteAllText($stagedReleaseNotesPath, $releaseNotes' in source
    assert '"--notes-file", $stagedReleaseNotesPath' in source
    assert "-ExpectedBody $releaseNotes" in source
    assert "MCP 2.0 (`2026-07-28`)" in notes
    assert "not code-signed" in notes


def test_release_build_binds_web_payload_to_exact_official_url_and_length() -> None:
    source = _build_script()

    assert '"https://github.com/ayyitong888/VRCForge/releases/download/v$Version/VRCForge_Windows_x64_$Version.zip"' in source
    assert "PayloadDownloadUrl must exactly match the official version-bound release asset URL" in source
    assert '"/DPAYLOAD_LENGTH=$payloadLength"' in source
    assert '"/DWEB_PAYLOAD_HELPER=$payloadWebPayloadHelper"' in source
    assert '"/DWEB_PAYLOAD_HELPER_SHA256=$webPayloadHelperSha256"' in source
    assert '"/DPAYLOAD_ZIP=$payloadZip"' in source
    assert "VRCForge_WebPayload.ps1" in source
    assert "Assert-MakeNsisVersion" in source
    assert "NSIS 3.12 or newer is required" in source
    assert "source or snapshot changed while the installers were being compiled." in source


def test_installers_execute_only_the_protected_hash_checked_helper() -> None:
    helper = (REPO_ROOT / "installer" / "VRCForge_WebPayload.ps1").read_text(encoding="utf-8")
    build = _build_script()
    installers = [
        (REPO_ROOT / "installer" / "VRCForge_Offline_Installer_x64.nsi").read_text(encoding="utf-8"),
        (REPO_ROOT / "installer" / "VRCForge_Web_Installer_x64.nsi").read_text(encoding="utf-8"),
    ]

    assert 'Copy-Item -LiteralPath $webPayloadHelper -Destination $payloadWebPayloadHelper -Force' in build
    assert '$payloadWebPayloadHelperSha256 -cne $webPayloadHelperSha256' in build
    assert "changed while it was being copied into the payload" in build
    assert "Assert-InstallNotRunning" in helper
    assert "Assert-NoReparseTree" in helper
    assert "Get-InstallSiblingPath \"Stage\"" in helper
    assert "Get-InstallSiblingPath \"Backup\"" in helper
    assert "prior installation could not be restored" in helper
    assert "FileShare]::None" in helper
    for installer in installers:
        assert '-File "$PLUGINSDIR' not in installer
        assert "taskkill" not in installer
        assert "WEB_PAYLOAD_GZIP_BASE64" not in installer
        assert "EncodedCommand" not in installer
        assert 'File "$PLUGINSDIR\\VRCForge_WebPayload.ps1"' not in installer
        assert "GetTempFileName" in installer
        assert "icacls.exe" in installer
        assert "Get-FileHash -Algorithm SHA256" in installer
        assert "$$a=(Get-FileHash" in installer
        assert "if($$a -ieq" in installer
        assert "if($$a -ceq" not in installer
        assert 'DefineProtectedHelperFunctions "un."' in installer
        assert "Call ValidateInstallBoundary" in installer
        assert "Call un.ValidateInstallBoundary" in installer
        assert "ExecutionPolicy Bypass" in installer
        assert "SetEnvironmentVariable" not in installer
    assert 'File /oname=payload.zip "${PAYLOAD_ZIP}"' in installers[0]


def test_release_payload_bundles_public_docs_and_requires_all_license_notices() -> None:
    source = _build_script()
    payload_smoke = (
        REPO_ROOT / "scripts" / "smoke_payload_zip_unpack.py"
    ).read_text(encoding="utf-8")
    web_payload = (REPO_ROOT / "installer" / "VRCForge_WebPayload.ps1").read_text(
        encoding="utf-8"
    )
    notification_icon = (REPO_ROOT / "src-tauri" / "icons" / "icon.png").read_bytes()

    for document in ("README.md", "USER_MANUAL.md", "DEPENDENCIES.md"):
        assert (
            f'Copy-Item -LiteralPath .\\{document} '
            f'-Destination (Join-Path $payloadRoot "{document}") -Force'
        ) in source

    assert (
        'Copy-Item -LiteralPath .\\src-tauri\\icons\\icon.ico '
        '-Destination (Join-Path $payloadRoot "VRCForge.ico") -Force'
    ) in source
    assert (
        'Copy-Item -LiteralPath .\\src-tauri\\icons\\icon.png '
        '-Destination (Join-Path $payloadRoot "VRCForge.png") -Force'
    ) in source
    assert notification_icon[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(notification_icon[16:20], "big") == 256
    assert int.from_bytes(notification_icon[20:24], "big") == 256
    assert 'relativePath = "VRCForge.png"' in source
    assert '"notificationIcon"' in web_payload
    assert '"VRCForge.png"' in web_payload

    for required_member in (
        "README.md",
        "USER_MANUAL.md",
        "DEPENDENCIES.md",
        "VRCForge.png",
        "licenses/VRCForge-GPL-3.0.txt",
        "licenses/VRCForge-NOTICE.txt",
        "licenses/uv-LICENSE-MIT.txt",
        "licenses/uv-LICENSE-APACHE-2.0.txt",
        "licenses/uv-DISTRIBUTION-NOTES.txt",
    ):
        assert f'"{required_member}"' in payload_smoke


def test_release_publish_rechecks_web_payload_manifest_binding() -> None:
    source = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")

    assert 'Get-RequiredProperty -InputObject $manifest -Name "webPayload"' in source
    assert "$webPayloadDownloadUrl -cne $expectedWebPayloadDownloadUrl" in source
    assert "$webPayloadLength -ne $actualPayloadLength" in source
    assert "$webPayloadHelperSha256.ToLowerInvariant() -cne $expectedWebPayloadHelperSha256" in source
    assert "web payload digest must match the portable payload artifact digest" in source


def test_strict_evidence_attestor_stays_outside_candidate_payload() -> None:
    source = _build_script()

    assert "cargo build failed for the external evidence attestor." in source
    assert "vrcforge_primitive_attestor.exe" in source
    assert "artifacts/primitive-origin-tools/$headCommit/$evidenceRunId" in source
    assert "repositoryRelativePath = $evidenceRelativePath" in source
    assert "trustedBoundaryReady = $false" in source
    assert 'schema = "vrcforge.payload-integrity.v1"' in source
    assert 'Join-Path $payloadRoot "tools\\vrcforge_primitive_attestor.exe"' not in source


def test_release_pairs_unity_core_with_exact_desktop_and_backend_payloads() -> None:
    source = _build_script()

    desktop_hash = source.index("$trustedDesktopSha256 =")
    backend_hash = source.index("$trustedBackendSha256 =")
    generated_core = source.index("$trustedReleaseSourcePath =")
    package_build = source.index(
        "-SourceAssetsPath $vrcforgeCorePayloadRoot -OutputPath $UnityPackagePath"
    )
    manifest = source.index('$payloadIntegrityManifest = [ordered]@{')

    assert desktop_hash < generated_core < package_build < manifest
    assert backend_hash < generated_core
    assert 'internal const string DesktopSha256 = "$trustedDesktopSha256";' in source
    assert 'internal const string BackendSha256 = "$trustedBackendSha256";' in source
    assert "sha256 = $trustedDesktopSha256" in source
    assert "sha256 = $trustedBackendSha256" in source
    assert '-SourceAssetsPath "Assets\\VRCForge"' not in source


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
    private_finalize = source.index("$authorityBundleTool =")
    assert manifest_write < private_finalize
    assert "$bridgeArchiveVerifier" not in source
    assert '"bridgeTargetRuntime"' not in source
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
