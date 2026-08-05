from __future__ import annotations

import hashlib
import re
from pathlib import Path

from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES, PLANNING_TOOL_NAMES, READ_ONLY_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs").read_text(
    encoding="utf-8-sig"
)
CONTRACT = (ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs").read_text(
    encoding="utf-8-sig"
)
BOOTSTRAP = (ROOT / "Assets" / "VRCForge" / "Editor" / "McpBridgeBootstrap.cs").read_text(
    encoding="utf-8-sig"
)
COMMAND_CONTRACT = (ROOT / "Assets" / "VRCForge" / "Core" / "MCP" / "VRCForgeCommandAttribute.cs").read_text(
    encoding="utf-8-sig"
)
INPUT_CONTRACT = (ROOT / "Assets" / "VRCForge" / "Core" / "MCP" / "VRCForgeInputAttribute.cs").read_text(
    encoding="utf-8-sig"
)
RESULT_CONTRACT = (ROOT / "Assets" / "VRCForge" / "Core" / "MCP" / "VRCForgeToolResult.cs").read_text(
    encoding="utf-8-sig"
)
SOURCE_MIGRATION = (ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpSourceMigration.cs").read_text(
    encoding="utf-8-sig"
)
UNINSTALLER = (ROOT / "Assets" / "VRCForge" / "Editor" / "VRCForgeUninstaller.cs").read_text(
    encoding="utf-8-sig"
)


def test_direct_mcp_tool_call_can_only_queue_explicit_read_only_tools() -> None:
    assert 'string.Equals(method, "tools/call"' in SERVER
    assert "descriptor.Permission == VRCForgeCommandAccess.ReadOnly" in SERVER
    assert "InvocationLane.DirectRead" in SERVER
    assert 'IsStrictNoWritePayloadRead(toolName, arguments)' in SERVER
    assert 'string.Equals(toolName, "vrc_export_blendshapes", StringComparison.Ordinal)' in SERVER
    assert "if (arguments == null)" in SERVER
    assert "HasExactKeys(arguments" in SERVER
    assert 'arguments["outputPath"]' in SERVER
    assert "HasEmptyOutputPath(arguments)" in SERVER
    assert "HasFalseBoolean(arguments, \"refreshAssets\")" in SERVER
    assert "HasTrueBoolean(arguments, \"returnPayloadOnly\")" in SERVER
    assert "This tool requires the VRCForge App approval and checkpoint lane." in SERVER
    assert "var descriptor = FindTool(pending.ToolName);" in SERVER
    assert "return descriptor.Permission == VRCForgeCommandAccess.ReadOnly" in SERVER
    assert "IsStrictNoWritePayloadRead(pending.ToolName, pending.Arguments)" in SERVER


def test_direct_mcp_no_write_allowlist_requires_exact_nonwriting_payload_shapes() -> None:
    gate_start = SERVER.index("private static bool IsStrictNoWritePayloadRead")
    gate_end = SERVER.index("private static JObject QueueInvocation", gate_start)
    gate = SERVER[gate_start:gate_end]
    for tool_name in (
        "vrc_export_blendshapes",
        "vrc_scan_avatar_controls",
        "vrc_scan_avatar_parameters",
        "vrc_scan_wardrobe",
        "vrc_scan_thry_avatar_performance",
        "vrc_scan_avatar_materials",
        "vrc_scan_avatar_items",
        "vrc_scan_fx_animator",
        "vrc_scan_animation_bindings",
        "vrc_scan_avatar_performance",
        "vrc_capture_scene_view",
    ):
        assert f'"{tool_name}"' in gate
    assert "HasEmptyOutputPath(arguments)" in gate
    assert 'HasFalseBoolean(arguments, "refreshAssets")' in gate
    assert 'HasTrueBoolean(arguments, "returnPayloadOnly")' in gate
    assert 'HasTrueBoolean(arguments, "statusOnly")' in gate
    assert 'HasBoundedInteger(arguments, "maxItems", 1, 2000)' in gate
    assert 'HasBoundedInteger(arguments, "maxClips", 1, 2000)' in gate
    assert "HasStringArray(arguments, \"clipPaths\")" in gate
    assert "values.Count <= 2000" in gate
    assert "arguments.Count == names.Length" in gate
    assert "HasExactKeys(arguments" in gate
    assert "vrc_capture_scene_view" in gate
    assert "vrc_create_safe_backup" not in gate
    assert "vrc_toggle_scene_object" not in gate
    assert "vrc_apply_blendshapes" not in gate
    assert "vrc_rollback_avatar_parameters" not in gate


def test_approved_write_uses_process_bound_one_use_context_without_file_bootstrap() -> None:
    assert "vrcforge/execution/register" not in SERVER
    assert "vrcforge/execute-approved" not in SERVER
    assert "executionGrant" not in SERVER
    assert "one-time-hmac-bootstrap" not in SERVER
    assert "unity-mcp-authority" not in SERVER
    assert '"read-direct-app-process-approved-writes"' in SERVER
    assert "TryScreenManagedBackendPeer" in SERVER
    assert "ValidateApprovedExecutionContext" in SERVER
    assert "ConsumeApprovedExecutionId" in SERVER
    assert '"argumentsSha256"' in SERVER
    assert '"checkpointId"' in SERVER
    assert "ConsumedExecutionExpirations" in SERVER
    assert "PurgeExpiredExecutionIds" in SERVER
    assert "ConsumedExecutionExpirations.Count >= MaxConsumedExecutionIds" in SERVER
    assert "ConsumedExecutionExpirations.Remove(expired.Key)" in SERVER
    assert "ConsumedExecutionIds.Remove(ConsumedExecutionOrder.Dequeue())" not in SERVER
    assert "now >= expiresAt" in SERVER


def test_only_mcp_2026_protocol_and_newline_transport_are_present() -> None:
    assert 'ModernProtocolVersion = "2026-07-28"' in SERVER
    assert '"server/discover"' in SERVER
    assert '"tcp-newline-jsonrpc"' in SERVER
    assert "2025-11-25" not in SERVER
    assert "LegacyProtocolVersion" not in SERVER
    assert "tcp-length-prefixed-jsonrpc" not in SERVER
    assert "notifications/initialized" not in SERVER


def test_owned_command_input_and_result_contracts_replace_the_historical_types() -> None:
    core_root = ROOT / "Assets" / "VRCForge" / "Core" / "MCP"
    assert "sealed class VRCForgeCommandAttribute" in COMMAND_CONTRACT
    assert "sealed class VRCForgeInputAttribute" in INPUT_CONTRACT
    assert "sealed class VRCForgeToolResult" in RESULT_CONTRACT
    for retired in (
        "VRCForgeToolAttribute.cs",
        "VRCForgeParameterAttribute.cs",
        "VRCForgeResponse.cs",
    ):
        assert not (core_root / retired).exists()
    combined = COMMAND_CONTRACT + INPUT_CONTRACT + RESULT_CONTRACT
    assert "Coplay" not in combined
    assert "McpForUnity" not in combined


def test_owned_tool_result_has_explicit_complete_failure_and_waiting_wire_shapes() -> None:
    assert 'new JObject { ["success"] = IsSuccessful }' in RESULT_CONTRACT
    assert 'result["message"] = Message;' in RESULT_CONTRACT
    assert 'result["data"] = JToken.FromObject(Payload);' in RESULT_CONTRACT
    assert 'result["code"] = ErrorCode;' in RESULT_CONTRACT
    assert 'result["error"] = Message;' in RESULT_CONTRACT
    assert 'result["_mcp_status"] = "pending";' in RESULT_CONTRACT
    assert 'result["_mcp_poll_interval"] = ContinuationDelaySeconds;' in RESULT_CONTRACT
    assert "commandResult.ToStructuredContent()" in SERVER
    assert "commandResult != null && !commandResult.IsSuccessful" in SERVER


def test_core_error_diagnostics_use_only_fixed_machine_codes() -> None:
    assert '["structuredContent"] = new JObject' in SERVER
    for code in (
        "managed_peer_ineligible",
        "app_process_binding_invalid",
        "approved_execution_invalid",
        "invocation_revalidation_failed",
        "tool_handler_exception",
    ):
        assert f'"{code}"' in SERVER
    importer = (ROOT / "Assets" / "VRCForge" / "Editor" / "OutfitPackageImporter.cs").read_text(
        encoding="utf-8-sig"
    )
    for code in (
        "unitypackage_project_preflight_failed",
        "unitypackage_identity_failed",
        "unitypackage_import_failed",
        "unitypackage_async_failed",
        "unitypackage_async_readback_failed",
        "unitypackage_async_cancelled",
    ):
        assert f'"{code}"' in importer
    assert "VRCForgeToolResult.Failed(failureCode)" in importer


def test_unitypackage_import_poll_is_a_narrow_managed_app_lane() -> None:
    assert 'string.Equals(laneName, "app_unitypackage_import_poll"' in SERVER
    assert "InvocationLane.AppUnityPackageImportPoll" in SERVER
    assert "IsStrictUnityPackageImportJobPoll(toolName, arguments)" in SERVER
    assert 'string.Equals(toolName, "vrc_import_unitypackage"' in SERVER
    assert "arguments.Properties().Count() != 1" in SERVER
    assert 'arguments["jobId"]' in SERVER
    assert "ValidateManagedAppInstanceContext(executionContext)" in SERVER
    assert "ReverifyManagedPeer(pending)" in SERVER


def test_unitypackage_import_events_bind_to_the_approved_package_name_before_start() -> None:
    importer = (ROOT / "Assets" / "VRCForge" / "Editor" / "OutfitPackageImporter.cs").read_text(
        encoding="utf-8-sig"
    )
    assert "expectedEventPackageName = Path.GetFileNameWithoutExtension(packagePath)" in importer
    started = importer[
        importer.index("private static void OnImportStarted") : importer.index("private static void OnImportCompleted")
    ]
    assert started.index("MatchesExpectedPackageEvent(job, packageName)") < started.index("job.startedForThisJob = true")
    terminal = importer[
        importer.index("private static ImportJob ActiveJobForEvent") : importer.index("private static void CompleteFailedJob")
    ]
    assert "MatchesExpectedPackageEvent(job, packageName)" in terminal
    assert "string.Equals(job.importEventPackageName, packageName ??" in terminal


def test_import_migration_deletes_only_byte_verified_retired_owned_assets() -> None:
    retired_paths = (
        "Assets/VRCForge/Core/MCP/VRCForgeToolAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeParameterAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeResponse.cs",
        "Assets/VRCForge/Editor/UnityPluginUninstaller.cs",
        "Assets/VRCForge/Editor/ShaderFixtureTool.cs",
        "Assets/VRCForge/ThirdPartyNotices/CoplayDev-Unity-MCP-LICENSE.txt",
        "Assets/VRCForge/ThirdPartyNotices/CoplayDev-Unity-MCP-DISTRIBUTION-NOTES.txt",
    )
    for retired in retired_paths:
        assert hashlib.sha256(retired.encode("utf-8")).hexdigest().upper() in SOURCE_MIGRATION
        assert retired not in SOURCE_MIGRATION
    retired_folders = (
        "Assets/VRCForge/ThirdPartyNotices",
        "Assets/VRCForge/Runtime",
        "Assets/VRCForge/Runtime/AvatarEncryption",
        "Assets/VRCForge/Generated",
    )
    for retired_folder in retired_folders:
        assert hashlib.sha256(retired_folder.encode("utf-8")).hexdigest().upper() in SOURCE_MIGRATION
        assert retired_folder not in SOURCE_MIGRATION
    assert SOURCE_MIGRATION.count("8DFC78A36DF5A97080EA95B4B1C04125F4CB7ACC992ED6E0CF6E964780C5C8CC") == 2
    assert "RetiredPaths.TryGetValue(ComputeTextSha256(assetPath)" in SOURCE_MIGRATION
    assert "RetiredPaths.Values.Any(values => values.Contains(digest))" in SOURCE_MIGRATION
    assert "McpForUnity" not in SOURCE_MIGRATION
    assert "CoplayDev" not in SOURCE_MIGRATION
    assert "VRCForgeToolAttribute" not in SOURCE_MIGRATION
    assert "VRCForgeParameterAttribute" not in SOURCE_MIGRATION
    assert "VRCForgeResponse" not in SOURCE_MIGRATION
    assert "AssetDatabase.DeleteAsset(assetPath)" in SOURCE_MIGRATION
    assert "AssetDatabase.DeleteAsset(assetDirectory)" in SOURCE_MIGRATION
    assert "allowedDigests.Contains(ComputeSha256(fullPath))" in SOURCE_MIGRATION
    assert "Preserved modified retired asset" in SOURCE_MIGRATION
    assert "Preserved unknown or renamed retired asset" in SOURCE_MIGRATION
    assert "TryRemoveVerifiedAsset(projectRoot, assetPath, allowedDigests, removed)" in SOURCE_MIGRATION
    assert "TryRemoveMatchedOrphanMeta" in SOURCE_MIGRATION
    assert "RetiredMetaPaths.TryGetValue(ComputeTextSha256(assetPath)" in SOURCE_MIGRATION
    assert "FileUtil.DeleteFileOrDirectory(fullMetaPath)" in SOURCE_MIGRATION
    assert "Preserved modified retired orphan metadata" in SOURCE_MIGRATION
    assert "Directory.EnumerateFileSystemEntries(" in SOURCE_MIGRATION
    assert "escaped the project Assets root" in SOURCE_MIGRATION
    assert "FileAttributes.ReparsePoint" in SOURCE_MIGRATION
    assert "RejectReparsePoints" in SOURCE_MIGRATION
    assert "catch (Exception exception)" in SOURCE_MIGRATION
    assert "if (Directory.EnumerateFileSystemEntries(fullDirectory).Any())" in SOURCE_MIGRATION
    assert 'var folderMetaPath = fullDirectory + ".meta"' in SOURCE_MIGRATION
    assert "allowedMetaDigests.Contains(ComputeSha256(folderMetaPath))" in SOURCE_MIGRATION
    assert "Preserved non-empty retired folder" in SOURCE_MIGRATION
    assert "Preserved retired folder with unknown or modified metadata" in SOURCE_MIGRATION
    assert "Directory.Delete" not in SOURCE_MIGRATION
    assert "File.Delete" not in SOURCE_MIGRATION


def test_core_rejects_older_client_protocols_with_an_update_instruction() -> None:
    assert 'metadata["io.modelcontextprotocol/protocolVersion"]' in SERVER
    assert "string.CompareOrdinal(requested, ModernProtocolVersion) < 0" in SERVER
    assert "MCP client protocol is outdated. Update the client to protocol version 2026-07-28." in SERVER
    gate = SERVER[SERVER.index("private static VRCForgeMcpMetadataError ValidateProtocolVersion") : SERVER.index("private static VRCForgeMcpMetadataError ValidateModernMetadata")]
    assert len([line for line in gate.splitlines() if line.strip()]) <= 9


def test_editor_autoconnect_retries_after_domain_reload_with_a_pre_registered_main_thread_pump() -> None:
    assert "[InitializeOnLoad]" in BOOTSTRAP
    assert "[InitializeOnLoadMethod]" in BOOTSTRAP
    assert "EditorApplication.update += EnsureAutoConnected;" in BOOTSTRAP
    assert "EditorApplication.update -= EnsureAutoConnected;" in BOOTSTRAP
    assert "EditorApplication.delayCall" not in BOOTSTRAP
    assert "VRCForgeMcpCoreServer.IsReady" in BOOTSTRAP
    assert "AutoConnectRetrySeconds" in BOOTSTRAP
    assert "public static bool IsReady" in SERVER
    pump = SERVER[
        SERVER.index("[InitializeOnLoadMethod]\n        private static void RegisterEditorDomainInvocationPump()") :
        SERVER.index("public static void Start()")
    ]
    start = SERVER[SERVER.index("private static void StartExclusive()") : SERVER.index("public static void Stop()")]
    stop = SERVER[SERVER.index("private static void StopLocked(") : SERVER.index("private static void JoinThreads(")]
    assert "[InitializeOnLoadMethod]" in pump
    assert "EditorApplication.update -= DrainInvocations;" in pump
    assert "EditorApplication.update += DrainInvocations;" in pump
    assert "EditorApplication.update" not in start
    assert "EditorApplication.update" not in stop


def test_editor_domain_startup_warns_only_for_explicit_third_party_mcp_packages() -> None:
    assert 'ThirdPartyMcpPackageIds = { "com.coplaydev.unity-mcp", "com.gamelovers.unity-mcp" }' in BOOTSTRAP
    assert "WarnThirdPartyMcpPackages();" in BOOTSTRAP
    assert 'Path.Combine(Directory.GetParent(Application.dataPath).FullName, "Packages")' in BOOTSTRAP
    assert 'Path.Combine(packages, "manifest.json")' in BOOTSTRAP
    assert 'Directory.Exists(Path.Combine(packages, packageId))' in BOOTSTRAP
    assert "Debug.LogWarning" in BOOTSTRAP
    assert "remove it from Packages if it is unused" in BOOTSTRAP
    warning = BOOTSTRAP[BOOTSTRAP.index("private static void WarnThirdPartyMcpPackages()") : BOOTSTRAP.index("private static void QueueAutoConnect()")]
    assert len([line for line in warning.splitlines() if line.strip()]) <= 10
    assert "File.Write" not in warning
    assert "Delete" not in warning


def test_uninstall_menu_stops_core_clears_only_owned_preference_and_removes_product_root() -> None:
    assert '[MenuItem("VRCForge/Uninstall VRCForge...")]' in UNINSTALLER
    assert "EditorUtility.DisplayDialog(" in UNINSTALLER
    assert "McpBridgeBootstrap.PrepareForUninstall();" in UNINSTALLER
    assert "McpBridgeBootstrap.ResumeAfterFailedUninstall();" in UNINSTALLER
    assert "McpBridgeBootstrap.CompleteUninstall();" in UNINSTALLER
    assert 'AssetDatabase.DeleteAsset(ProductRoot)' in UNINSTALLER
    assert 'ProductRoot = "Assets/VRCForge"' in UNINSTALLER
    assert "Directory.Delete" not in UNINSTALLER
    assert "File.Delete" not in UNINSTALLER
    assert "EditorPrefs.DeleteKey(AutoConnectKey);" in BOOTSTRAP
    lifecycle = BOOTSTRAP[
        BOOTSTRAP.index("internal static void PrepareForUninstall()") :
        BOOTSTRAP.index("private static void StopBridge()")
    ]
    assert "VRCForgeMcpCoreServer.Stop();" in lifecycle
    assert "EditorApplication.update -= EnsureAutoConnected;" in lifecycle
    assert "QueueAutoConnect();" in lifecycle
    assert lifecycle.count("EditorPrefs.DeleteKey(") == 1


def test_core_has_a_fixed_64_tool_contract_and_never_rediscoveres_at_invoke_time() -> None:
    assert "ToolCount = 64" in CONTRACT
    assert CONTRACT.count('{ "vrc_') == 64
    assert "SnapshotExact" in SERVER
    assert "var registry = VRCForgeToolRegistry.DiscoverLoadedAssemblies();" not in SERVER
    assert "ApprovedAppCoreTools" in SERVER
    assert '"When to use: " + whenToUse' in SERVER
    assert '"\\nWhen NOT to use: "' in SERVER
    assert '"Negative example: "' in SERVER
    assert '["negativeExample"]' in SERVER
    assert 'ExpectedPlanningToolNames.Contains(descriptor.Name)' in SERVER
    assert 'string.IsNullOrEmpty(exposureLayer) ? "planning" : exposureLayer' in SERVER
    assert '"vrcforge_apply_blendshapes"' not in SERVER


def test_csharp_contract_exactly_matches_the_64_owned_tool_declarations() -> None:
    contract = dict(re.findall(r'\{ "(vrc_[^"]+)", "([^"]+)" \}', CONTRACT))
    declared: dict[str, str] = {}
    for path in (ROOT / "Assets" / "VRCForge" / "Editor").rglob("*.cs"):
        source = path.read_text(encoding="utf-8-sig")
        namespace = re.search(r"namespace\s+([\w.]+)", source)
        for match in re.finditer(
            r'\[VRCForgeCommand\(\s*toolId:\s*"([^"]+)"[\s\S]{0,700}?\)\]\s*'
            r"(?:public|internal)\s+(?:static\s+)?class\s+(\w+)",
            source,
        ):
            declared[match.group(1)] = f"{namespace.group(1)}.{match.group(2)}"
    assert len(contract) == 64
    assert set(contract) == EXPECTED_TOOL_NAMES
    assert contract == declared


def test_csharp_and_python_contracts_pin_the_same_exact_read_only_tools() -> None:
    read_only_block = CONTRACT[
        CONTRACT.index("private static readonly HashSet<string> ExpectedReadOnlyNames") :
        CONTRACT.index("private static readonly HashSet<string> ExpectedPlanningNames")
    ]
    csharp_read_only = set(re.findall(r'"(vrc_[^"]+)"', read_only_block))

    assert csharp_read_only == READ_ONLY_TOOL_NAMES
    assert "ExpectedReadOnlyNames.Contains(descriptor.Name)" in CONTRACT
    assert "!readOnly.SetEquals(VRCForgeMcpToolContract.ExpectedReadOnlyToolNames)" in SERVER


def test_csharp_and_python_contracts_pin_the_same_planning_exposure_tools() -> None:
    planning_block = CONTRACT[
        CONTRACT.index("private static readonly HashSet<string> ExpectedPlanningNames") :
        CONTRACT.index("internal static ISet<string> ExpectedToolNames")
    ]
    csharp_planning_additions = set(re.findall(r'"(vrc_[^"]+)"', planning_block))
    assert READ_ONLY_TOOL_NAMES | csharp_planning_additions == PLANNING_TOOL_NAMES


def test_fixed_lanes_are_8_read_only_21_preview_2_safety_and_54_approved_write() -> None:
    assert "readOnly.Count != 8 || preview.Count != 21 || safety.Count != 2" in SERVER
    assert "approved.Count != 54" in SERVER
    assert "readOnly.SetEquals(VRCForgeMcpToolContract.ExpectedReadOnlyToolNames)" in SERVER
    assert "readOnly.Overlaps(preview)" in SERVER
    assert "preview.Overlaps(safety)" in SERVER
    assert "!approved.IsSupersetOf(preview)" in SERVER


def test_preview_lane_requires_a_strict_true_preview_flag_while_approved_write_keeps_preview_tools() -> None:
    assert "!HasAllowedPreviewRequest(toolName, arguments)" in SERVER
    assert "return PreviewTools.Contains(toolName) && HasExplicitPreviewRequest(arguments);" in SERVER
    assert 'arguments["preview"].Type == JTokenType.Boolean' in SERVER
    assert 'arguments["preview"].Value<bool>()' in SERVER
    assert "approved.ExceptWith(preview);" not in SERVER


def test_restore_backup_preview_is_managed_only_and_requires_the_canonical_no_write_shape() -> None:
    assert '"vrc_restore_safe_backup",' in SERVER
    assert 'if (string.Equals(toolName, "vrc_restore_safe_backup", StringComparison.Ordinal))' in SERVER
    predicate_start = SERVER.index("private static bool HasStrictRestoreBackupPreviewRequest")
    predicate_end = SERVER.index("private static bool HasExplicitPreviewRequest", predicate_start)
    predicate = SERVER[predicate_start:predicate_end]
    assert "HasExactKeys(arguments," in predicate
    for field_name in (
        "backupPath",
        "backupId",
        "assetPaths",
        "confirmRestore",
        "allowProjectMismatch",
        "allowOverwriteChanged",
        "refreshAssets",
        "backupRoot",
    ):
        assert f'"{field_name}"' in predicate
    assert "HasStringArray(arguments, \"assetPaths\")" in predicate
    assert "((JArray)arguments[\"assetPaths\"]).Count <= 2000" in predicate
    assert "HasFalseBoolean(arguments, \"confirmRestore\")" in predicate
    assert "HasFalseBoolean(arguments, \"allowProjectMismatch\")" in predicate
    assert "HasFalseBoolean(arguments, \"allowOverwriteChanged\")" in predicate
    assert "HasFalseBoolean(arguments, \"refreshAssets\")" in predicate


def test_setup_outfit_preview_is_managed_only_and_requires_confirm_false() -> None:
    assert 'if (string.Equals(toolName, "vrc_setup_outfit", StringComparison.Ordinal))' in SERVER
    predicate_start = SERVER.index("private static bool HasStrictSetupOutfitPreviewRequest")
    predicate_end = SERVER.index("private static bool HasExplicitPreviewRequest", predicate_start)
    predicate = SERVER[predicate_start:predicate_end]
    assert 'HasExactKeys(arguments, "avatarPath", "outfitPath", "confirmSetup", "saveScene")' in predicate
    assert 'HasNonEmptyString(arguments, "outfitPath")' in predicate
    assert 'HasFalseBoolean(arguments, "confirmSetup")' in predicate
    assert 'arguments["saveScene"].Type == JTokenType.Boolean' in predicate
    assert "HasAllowedPreviewRequest(pending.ToolName, pending.Arguments)" in SERVER
    assert "IsStrictNoWritePayloadRead(toolName, arguments)" in SERVER


def test_safety_lane_accepts_only_exact_app_or_complete_live_bound_shapes_and_rechecks_them() -> None:
    predicate_start = SERVER.index("private static bool IsStrictSafetyControlRequest")
    predicate_end = SERVER.index("private static bool HasStrictRestoreBackupPreviewRequest", predicate_start)
    predicate = SERVER[predicate_start:predicate_end]
    assert '"vrc_prepare_checkpoint"' in predicate
    assert '"vrc_reload_after_checkpoint_restore"' in predicate
    assert 'isPrepareCheckpoint && HasExactKeys(arguments, "projectPath")' in predicate
    assert 'isPrepareCheckpoint && HasExactKeys(arguments,' in predicate
    assert 'HasExactKeys(arguments, "projectPath")' in predicate
    assert 'HasExactKeys(arguments, "projectPath", "phase")' in predicate
    assert 'HasExactKeys(arguments, "projectPath", "phase", "scenePaths", "activeScenePath")' in predicate
    assert 'string.Equals(phase, "prepare_restore", StringComparison.Ordinal)' in predicate
    assert 'string.Equals(phase, "reload", StringComparison.Ordinal)' in predicate
    assert 'HasStringArray(arguments, "scenePaths")' in predicate
    assert 'HasString(arguments, "activeScenePath")' in predicate
    for field_name in (
        "projectPath",
        "expectedRunIdDigest",
        "expectedProjectPathDigest",
        "expectedUnityProcessId",
        "expectedUnityProcessStartedAtUtc",
        "expectedUnityExecutableDigest",
    ):
        assert f'"{field_name}"' in predicate
    assert 'HasBoundedInteger(arguments, "expectedUnityProcessId", 1, int.MaxValue)' in predicate
    assert 'HasCompleteSafetyControlLiveBinding(arguments)' in predicate
    assert "arguments.Count == names.Length" in SERVER
    assert "string.IsNullOrWhiteSpace((string)arguments[name])" in SERVER
    assert "if (!IsStrictSafetyControlRequest(toolName, arguments))" in SERVER
    assert "return IsStrictSafetyControlRequest(pending.ToolName, pending.Arguments);" in SERVER
    assert "ReverifyManagedPeer(pending)" in SERVER


def test_only_status_only_scene_view_read_is_allowed_and_screenshot_paths_stay_blocked() -> None:
    assert 'string.Equals(toolName, "vrc_capture_scene_view", StringComparison.Ordinal)' in SERVER
    assert 'HasExactKeys(arguments, "statusOnly", "requirePlayMode")' in SERVER
    assert 'HasTrueBoolean(arguments, "statusOnly")' in SERVER
    assert 'HasBoolean(arguments, "requirePlayMode")' in SERVER
    assert "screenshotPath" not in SERVER
    capture_start = SERVER.index('return string.Equals(toolName, "vrc_capture_scene_view"')
    capture_gate = SERVER[capture_start:SERVER.index("private static bool HasExactKeys", capture_start)]
    assert "outputPath" not in capture_gate
    assert "restoreView" not in SERVER
    assert "PreviewCapableTools" not in SERVER
    assert "BoundedScanTools" not in SERVER


def test_setup_outfit_poll_is_a_peer_and_instance_bound_exact_operation_not_a_read_only_tool() -> None:
    assert "InvocationLane.AppSetupOutfitPoll" in SERVER
    assert 'string.Equals(laneName, "app_setup_outfit_poll"' in SERVER
    assert 'string.Equals(toolName, "vrc_setup_outfit"' in SERVER
    assert "arguments.Properties().Count() != 1" in SERVER
    assert 'arguments["jobId"]' in SERVER
    assert 'Guid.TryParseExact((string)jobId, "N"' in SERVER
    assert "ValidateManagedAppInstanceContext" in SERVER
    assert "ReverifyManagedPeer(pending)" in SERVER
    assert '"vrc_setup_outfit"' not in repr(sorted(READ_ONLY_TOOL_NAMES))


def test_modern_discovery_uses_final_2026_wire_field_names_and_result_identity_metadata() -> None:
    assert '["supportedVersions"] = new JArray(ModernProtocolVersion)' in SERVER
    assert '["supportedProtocolVersions"] = new JArray(ModernProtocolVersion)' in SERVER  # project descriptor only
    assert '["io.modelcontextprotocol/serverInfo"]' in SERVER
    assert '["serverInfo"] = new JObject' not in SERVER
    assert '["supported"] = new JArray(ModernProtocolVersion)' in SERVER
    assert '["requested"]' in SERVER
