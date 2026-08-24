from __future__ import annotations

import hashlib
import re
from pathlib import Path

from unity_mcp_tool_contract import (
    CORE_IDENTITY,
    EXPECTED_TOOL_NAMES,
    HANDSHAKE_PROTOCOL,
    PLANNING_TOOL_NAMES,
    PRODUCT_VERSION,
    READ_ONLY_TOOL_NAMES,
    TOOL_CONTRACT_VERSION,
)

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
GESTURE_MANAGER_RUNTIME = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "GestureManagerRuntimeTool.cs"
).read_text(encoding="utf-8-sig")
EDITOR_STATE_TOOLS = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "EditorStateTools.cs"
).read_text(encoding="utf-8-sig")
BUILD_TEST_TOOL = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "VrchatBuildTestTool.cs"
).read_text(encoding="utf-8-sig")
CONSTRAINT_CONVERSION_TOOL = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "VrchatConstraintConversionTool.cs"
).read_text(encoding="utf-8-sig")
COMPONENT_CRUD = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "Generic" / "UnityComponentCrud.cs"
).read_text(encoding="utf-8-sig")


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
    assert "This tool requires a one-use managed write authorization." in SERVER
    assert "var descriptor = FindTool(pending.ToolName);" in SERVER
    assert "return descriptor.Permission == VRCForgeCommandAccess.ReadOnly" in SERVER
    assert "IsStrictNoWritePayloadRead(pending.ToolName, pending.Arguments)" in SERVER


def test_worker_requests_wake_the_unity_main_thread_without_relying_only_on_editor_updates() -> None:
    assert "private static SynchronizationContext editorSynchronizationContext;" in SERVER
    assert "editorSynchronizationContext = SynchronizationContext.Current;" in SERVER
    assert "private static void RequestInvocationDrain()" in SERVER
    assert "context.Post(_ => DrainInvocations(), null);" in SERVER
    queue_start = SERVER.index("private static JObject QueueInvocation")
    queue_end = SERVER.index("private static bool HasAllowedPreviewRequest", queue_start)
    assert "RequestInvocationDrain();" in SERVER[queue_start:queue_end]


def test_authenticated_core_dispatch_exceptions_return_structured_errors_instead_of_closing_transport() -> None:
    handle_start = SERVER.index("private static void HandleClient")
    handle_end = SERVER.index("private static JObject ReadEnvelope", handle_start)
    handle = SERVER[handle_start:handle_end]
    assert "response = BuildUnhandledMessageResponse(message, session, exception);" in handle
    assert 'ToolError("unity_core_unhandled_exception", humanMessage, true)' in handle
    assert 'structured["failureLayer"] = "unity_core_dispatch";' in handle
    assert 'structured["failurePhase"] = "request_dispatch_exception";' in handle
    assert 'structured["toolRoutingStarted"] = JValue.CreateNull();' in handle
    assert '["exceptionType"] = exceptionType' in handle
    assert '["exceptionMessage"] = exceptionMessage' in handle
    assert '["exceptionStack"] = exceptionStack' in handle


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


def test_optional_capture_status_boolean_is_null_safe_for_real_write_payloads() -> None:
    helper_start = SERVER.index("private static bool HasBoolean")
    helper_end = SERVER.index("private static bool HasTrueBoolean", helper_start)
    helper = SERVER[helper_start:helper_end]
    assert "arguments == null ? null : arguments[name]" in helper
    assert "value != null && value.Type == JTokenType.Boolean" in helper


def test_animation_binding_read_lane_requires_boolean_detail_flag_and_rejects_extra_keys() -> None:
    gate_start = SERVER.index('if (string.Equals(toolName, "vrc_scan_animation_bindings"')
    gate_end = SERVER.index('if (string.Equals(toolName, "vrc_scan_avatar_performance"', gate_start)
    gate = SERVER[gate_start:gate_end]
    assert 'HasExactKeys(arguments, "avatarPath", "outputPath", "controllerPath", "clipPaths", "includeAllProjectClips", "includeBindingDetails", "maxClips", "refreshAssets")' in gate
    assert 'HasBoolean(arguments, "includeBindingDetails")' in gate
    # Exact keys keep both compact=false and detail=true in the read-only lane,
    # while any unrecognized argument is rejected before invocation.
    assert "arguments.Count == names.Length" in SERVER
    assert "HasExactKeys(arguments" in gate


def test_animation_binding_core_preserves_detail_compatibility_and_compact_mode() -> None:
    asset_tools = (ROOT / "Assets" / "VRCForge" / "Editor" / "AssetTools.cs").read_text(encoding="utf-8")
    assert "public bool? includeBindingDetails { get; set; } = true;" in asset_tools
    assert "parameters.includeBindingDetails ?? true" in asset_tools
    assert "include_binding_details = includeBindingDetails" in asset_tools
    assert "bindings = includeBindingDetails ? bindings" in asset_tools
    assert "warnings = includeBindingDetails ? warnings : null" in asset_tools
    assert "public int warning_count;" in asset_tools


def test_animation_binding_compact_mode_does_not_write_default_inventory() -> None:
    asset_tools = (ROOT / "Assets" / "VRCForge" / "Editor" / "AssetTools.cs").read_text(encoding="utf-8")
    handle = asset_tools[asset_tools.index("public static object HandleCommand") : asset_tools.index("private static AnimationBindingsPayload BuildAnimationBindingsPayload")]
    assert 'var requestedPath = parameters.outputPath ?? "";' in handle
    assert 'if (!string.IsNullOrWhiteSpace(requestedPath))' in handle


def test_avatar_parameter_scan_uses_ndmf_parameter_introspection_for_merged_usage() -> None:
    scanner = (ROOT / "Assets" / "VRCForge" / "Editor" / "AvatarParameterScanner.cs").read_text(
        encoding="utf-8-sig"
    )
    bridge = (ROOT / "Assets" / "VRCForge" / "Editor" / "NdmfParameterUsageBridge.cs").read_text(
        encoding="utf-8-sig"
    )
    assert 'inspectionStage = "ndmf_parameter_introspection"' in scanner
    assert "sourceDescriptorUsage" in scanner
    assert "mergedParameterUsage" in scanner
    assert "ParameterInfo.ForUI" in bridge
    assert '"nadena.dev.ndmf.ParameterInfo"' in bridge
    assert "GetParametersForObject" in bridge
    assert "BitUsage" in bridge
    assert "WantSynced" in bridge
    assert "IsAnimatorOnly" in bridge
    assert "pluginBreakdown" in bridge


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


def test_external_mcp_write_shares_only_exact_one_use_core_authority() -> None:
    assert "ExternalMcpWrite = 6" in SERVER
    assert 'string.Equals(laneName, "external_mcp_write"' in SERVER
    assert "ValidateExternalMcpExecutionContext" in SERVER
    assert 'RequiredBoundedString(context, "operationId", 256)' in SERVER
    assert 'context["approvalId"] != null || context["checkpointId"] != null' in SERVER
    assert '"external_mcp_execution_" + externalExecutionFailure' in SERVER
    for failure_code in (
        "context_missing",
        "identity_invalid",
        "internal_fields_present",
        "tool_not_approved",
        "tool_mismatch",
        "arguments_hash_invalid",
        "arguments_mismatch",
        "project_mismatch",
        "instance_mismatch",
        "lifetime_invalid",
        "not_yet_valid",
        "expired",
        "replayed",
        "context_invalid",
    ):
        assert f'failureCode = "{failure_code}"' in SERVER
    assert "was rejected before the Unity tool started" in SERVER
    revalidation = SERVER[
        SERVER.index("private static bool IsInvocationStillAuthorized") :
        SERVER.index("private static bool ReverifyManagedPeer")
    ]
    assert "pending.Lane == InvocationLane.ExternalMcpWrite" in revalidation
    assert "ConsumeApprovedExecutionId" in revalidation


def test_managed_argument_hash_is_cross_runtime_and_float_bit_exact() -> None:
    hashing = SERVER[
        SERVER.index("private static string ComputeCanonicalJsonHash") :
        SERVER.index("private static void CancelPendingInvocations")
    ]
    assert "AppendCanonicalArgumentToken" in hashing
    assert "BitConverter.DoubleToInt64Bits" in hashing
    assert 'bits.ToString("x16", CultureInfo.InvariantCulture)' in hashing
    assert "Convert.ToBase64String" in hashing
    assert "properties.Sort((left, right) => string.CompareOrdinal" in hashing
    assert "GetBytes(left.Name)" in hashing
    assert "GetBytes(right.Name)" in hashing
    assert "CanonicalizeJson" not in hashing


def test_only_mcp_2026_protocol_and_newline_transport_are_present() -> None:
    assert 'ModernProtocolVersion = "2026-07-28"' in SERVER
    assert '"server/discover"' in SERVER
    assert '"tcp-newline-jsonrpc"' in SERVER
    assert "2025-11-25" not in SERVER
    assert "LegacyProtocolVersion" not in SERVER
    assert "tcp-length-prefixed-jsonrpc" not in SERVER
    assert "notifications/initialized" not in SERVER


def test_core_requires_a_stateful_protocol_handshake_per_connection() -> None:
    assert "sealed class VRCForgeMcpConnectionSession" in SERVER
    assert "var session = new VRCForgeMcpConnectionSession();" in SERVER
    assert "HandleMessage(message, client, session)" in SERVER
    discover = SERVER[SERVER.index('if (string.Equals(method, "server/discover"') : SERVER.index('if (string.Equals(method, "tools/list"')]
    assert "session.HandshakeComplete = true;" in discover
    assert 'session.ProtocolVersion = (string)((parameters["_meta"] as JObject)["io.modelcontextprotocol/protocolVersion"]);' in discover
    assert "if (!session.HandshakeComplete" in discover
    assert '"MCP handshake is required before using Core methods."' in discover
    assert '["requiredMethod"] = "server/discover"' in discover


def test_pre_handshake_core_info_reports_compiled_identity_and_compile_snapshot() -> None:
    handler = SERVER[SERVER.index("private static JObject HandleMessage") : SERVER.index("private static VRCForgeMcpMetadataError ValidateProtocolVersion")]
    core_info_branch = handler.index('if (string.Equals(method, "server/core-info"')
    metadata_gate = handler.index("var metadataError = ValidateModernMetadata(parameters);")
    assert core_info_branch < metadata_gate
    assert 'data["coreInfo"] = CoreInfoResult();' in handler
    core_info = SERVER[SERVER.index("private static JObject CoreInfoResult") : SERVER.index("private static JObject ToolsListResult")]
    assert '["versionSource"] = "compiled_constant"' in core_info
    assert '["projectIdSource"] = "normalized_project_path_sha256"' in core_info
    assert '["compileSnapshot"] = CompileErrorMonitor.ReadCoreInfoSnapshot(30)' in core_info


def test_core_and_backend_handshake_identity_matches_the_product_version() -> None:
    assert PRODUCT_VERSION == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert f'internal const string CoreIdentity = "{CORE_IDENTITY}";' in CONTRACT
    assert f'internal const string HandshakeProtocol = "{HANDSHAKE_PROTOCOL}";' in CONTRACT
    assert f'internal const string ProductVersion = "{PRODUCT_VERSION}";' in CONTRACT
    assert f'internal const string ToolContractVersion = "{TOOL_CONTRACT_VERSION}";' in CONTRACT
    metadata = SERVER[SERVER.index("private static VRCForgeMcpMetadataError ValidateModernMetadata") : SERVER.index("private static JObject InvokeTool")]
    assert 'string.IsNullOrWhiteSpace((string)clientInfo["version"])' in metadata
    assert 'metadata["io.vrcforge/projectBinding"] as JObject' in metadata
    assert 'projectBinding["projectId"], ComputeProjectId(GetProjectRoot())' in metadata
    assert 'projectBinding["instanceId"], descriptorInstanceId' in metadata


def test_client_identity_validation_condition_is_syntactically_balanced() -> None:
    metadata = SERVER[SERVER.index("private static VRCForgeMcpMetadataError ValidateModernMetadata") : SERVER.index("private static JObject InvokeTool")]
    assert 'clientInfo["version"].Type != JTokenType.String)\n            {' in metadata
    assert 'clientInfo["version"].Type != JTokenType.String))' not in metadata


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


def test_pre_mutation_rejections_report_known_no_write_state() -> None:
    assert "public static VRCForgeToolResult RejectedBeforeMutation(" in RESULT_CONTRACT
    assert '["mutationStarted"] = false' in RESULT_CONTRACT
    assert '["committed"] = false' in RESULT_CONTRACT
    assert '["commitState"] = "not_started"' in RESULT_CONTRACT
    assert '["commitStateKnown"] = true' in RESULT_CONTRACT
    assert '"scene_unsaved_changes"' in COMPONENT_CRUD
    assert '"scene_precondition"' in COMPONENT_CRUD
    assert 'Save or revert the selected scene' in COMPONENT_CRUD


def test_core_error_diagnostics_use_only_fixed_machine_codes() -> None:
    assert 'catch (Exception exception)' in SERVER
    assert '"handlerException"' in SERVER
    assert '"innerChain"' in SERVER
    assert '"vrcforge.unity_tool_handler_diagnostics.v1"' in SERVER
    assert '"failedStep"' in SERVER
    assert '["structuredContent"] = structured' in SERVER
    assert '["failureLayer"] = noWriteProven ? "unity_core_pre_route" : "unity_tool_handler"' in SERVER
    assert '["failurePhase"] = noWriteProven ? "before_tool_routing" : "tool_handler_exception"' in SERVER
    assert '["mutationStarted"] = noWriteProven ? new JValue(false) : JValue.CreateNull()' in SERVER
    assert '["commitState"] = noWriteProven ? "not_started" : "unknown"' in SERVER
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


def test_unitypackage_import_events_bind_to_the_actual_started_event_before_terminal() -> None:
    importer = (ROOT / "Assets" / "VRCForge" / "Editor" / "OutfitPackageImporter.cs").read_text(
        encoding="utf-8-sig"
    )
    assert "expectedEventPackageName = Path.GetFileNameWithoutExtension(packagePath)" in importer
    started = importer[
        importer.index("private static void OnImportStarted") : importer.index("private static void OnImportCompleted")
    ]
    assert "MatchesExpectedPackageEvent" not in started
    assert "job.mutationStarted" in started
    assert "string.Equals(importInvocationJobId, job.jobId, StringComparison.Ordinal)" in started
    assert "!string.IsNullOrWhiteSpace(packageName)" in started
    assert "job.importEventPackageName = packageName" in started
    terminal = importer[
        importer.index("private static ImportJob ActiveJobForEvent") : importer.index("private static void CompleteFailedJob")
    ]
    assert "MatchesExpectedPackageEvent" not in terminal
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


def test_core_negotiates_a_protocol_compatibility_range() -> None:
    assert 'metadata["io.modelcontextprotocol/protocolVersion"]' in SERVER
    assert 'metadata["io.vrcforge/protocolRange"] as JObject' in SERVER
    assert "string.CompareOrdinal(MinimumProtocolVersion, requested) <= 0" in SERVER
    assert "string.CompareOrdinal(requested, MaximumProtocolVersion) <= 0" in SERVER
    assert "No compatible MCP protocol version was negotiated." in SERVER
    gate = SERVER[SERVER.index("private static VRCForgeMcpMetadataError ValidateProtocolVersion") : SERVER.index("private static VRCForgeMcpMetadataError ValidateModernMetadata")]
    assert '"coreRange"' in gate


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
    assert "EditorApplication.playModeStateChanged -= HandlePlayModeStateChanged;" in pump
    assert "EditorApplication.playModeStateChanged += HandlePlayModeStateChanged;" in pump
    assert "ScheduleInvocationPumpRegistration();" in pump
    assert "EditorApplication.update" not in start
    assert "EditorApplication.update" not in stop


def test_every_core_start_schedules_a_next_turn_invocation_pump_rebind() -> None:
    public_start = SERVER[SERVER.index("public static void Start()") : SERVER.index("private static void StartExclusive()")]
    assert "ScheduleInvocationPumpRegistration();" in public_start
    assert public_start.index("ScheduleInvocationPumpRegistration();") < public_start.index("StartExclusive();")


def test_build_test_job_starts_before_the_core_request_returns() -> None:
    assert "RunJob(job.JobId);" in BUILD_TEST_TOOL
    assert "EditorApplication.delayCall += () => RunJob(job.JobId);" not in BUILD_TEST_TOOL
    build_finally = BUILD_TEST_TOOL[
        BUILD_TEST_TOOL.index("finally", BUILD_TEST_TOOL.index("private static async void RunJob")) :
        BUILD_TEST_TOOL.index("private static async Task<IVRCSdkAvatarBuilderApi> AcquireBuilderAsync")
    ]
    assert "VRCForgeMcpCoreServer.ScheduleInvocationPumpRegistration();" in build_finally


def test_build_test_selects_the_exact_avatar_before_calling_the_sdk() -> None:
    run_job = BUILD_TEST_TOOL[
        BUILD_TEST_TOOL.index("private static async void RunJob") :
        BUILD_TEST_TOOL.index("private static async Task<IVRCSdkAvatarBuilderApi> AcquireBuilderAsync")
    ]
    select = "builder.SelectAvatar(descriptor.gameObject);"
    invoke = "await builder.BuildAndTest(descriptor.gameObject);"
    assert select in run_job
    assert run_job.index(select) < run_job.index(invoke)
    assert 'RecordEvent(job, "sdk_avatar_selected", job.AvatarPath);' in run_job


def test_build_test_concurrent_start_rejection_never_inherits_active_job_mutation() -> None:
    rejection = BUILD_TEST_TOOL[
        BUILD_TEST_TOOL.index("private static JObject BuildConcurrentStartRejection") :
        BUILD_TEST_TOOL.index("private static JObject RejectedJobPayload")
    ]
    assert '["failurePhase"] = "before_job_start"' in rejection
    assert '["mutationStarted"] = false' in rejection
    assert '["writeOccurred"] = false' in rejection
    assert '["committed"] = false' in rejection
    assert '["commitState"] = "not_started"' in rejection
    assert '["requestMayHaveCommitted"] = false' in rejection
    assert '["activeJob"] = new JObject' in rejection


def test_avatar_upload_readiness_compiles_against_the_installed_sdk_namespaces() -> None:
    readiness = BUILD_TEST_TOOL[
        BUILD_TEST_TOOL.index("internal static Readiness Inspect") :
        BUILD_TEST_TOOL.index("internal static async Task<IVRCSdkAvatarBuilderApi> AcquireBuilderAsync")
    ]
    assert "IVRCSdkAvatarBuilderApi builder = null;" in readiness
    assert "IVRCSdkAvatarBuilderApi builder;" not in readiness
    assert "UnityEditor.PackageManager.PackageInfo.FindForAssembly(" in BUILD_TEST_TOOL
    assert "var package = PackageInfo.FindForAssembly(" not in BUILD_TEST_TOOL


def test_constraint_conversion_uses_the_official_sdk_converter_with_animation_rebinding() -> None:
    assert "AvatarDynamicsSetup.DoConvertUnityConstraints(" in CONSTRAINT_CONVERSION_TOOL
    assert "new[] { immediate.Constraint }" in CONSTRAINT_CONVERSION_TOOL
    assert "immediate.Avatar," in CONSTRAINT_CONVERSION_TOOL
    assert "true);" in CONSTRAINT_CONVERSION_TOOL
    assert 'toolId: "vrc_convert_unity_constraint"' in CONSTRAINT_CONVERSION_TOOL
    assert '["convertReferencedAnimationClips"] = true' in CONSTRAINT_CONVERSION_TOOL
    assert '["automaticRollbackAttempted"] = false' in CONSTRAINT_CONVERSION_TOOL


def test_core_startup_failure_preserves_the_actual_exception_for_console_diagnosis() -> None:
    start = SERVER[SERVER.index("private static void StartExclusive()") : SERVER.index("public static void Stop()")]
    assert "catch (Exception exception)" in start
    assert "startupFailure = exception;" in start
    assert '"[VRCForge MCP] Core failed to start: "' in start
    assert "startupFailure.GetType().Name" in start
    assert "startupFailure.Message" in start


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


def test_core_has_a_fixed_80_tool_contract_and_never_rediscoveres_at_invoke_time() -> None:
    assert "ToolCount = 80" in CONTRACT
    assert CONTRACT.count('{ "vrc_') == 80
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


def test_csharp_contract_exactly_matches_the_80_owned_tool_declarations() -> None:
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
    assert len(contract) == 80
    assert set(contract) == EXPECTED_TOOL_NAMES
    assert contract == declared


def test_gesture_manager_adapter_is_reflection_only_and_enumerates_runtime_parameters() -> None:
    assert "using BlackStartX" not in GESTURE_MANAGER_RUNTIME
    assert 'ManagerTypeName = "BlackStartX.GestureManager.GestureManager"' in GESTURE_MANAGER_RUNTIME
    assert 'Packages/vrchat.blackstartx.gesture-manager/GestureManager.prefab' in GESTURE_MANAGER_RUNTIME
    assert 'ReadMember(binding.Module, "Params")' in GESTURE_MANAGER_RUNTIME
    assert 'ReadMember(binding.Module, "UserFilteredParams")' in GESTURE_MANAGER_RUNTIME
    assert 'ReadMember(binding.Module, "VrcFilteredParams")' in GESTURE_MANAGER_RUNTIME
    assert "menuTree = DescribeMenu(menu)" in GESTURE_MANAGER_RUNTIME
    assert "MaxMenuDepth = 8" in GESTURE_MANAGER_RUNTIME
    assert "MaxMenuControlsPerNode = 8" in GESTURE_MANAGER_RUNTIME
    assert "returnedRuntimeParameterCount = runtimeParameters.Length" in GESTURE_MANAGER_RUNTIME
    assert "FilterParameters(" in GESTURE_MANAGER_RUNTIME
    assert 'toolId: "vrc_gesture_manager_enter_play_mode"' in GESTURE_MANAGER_RUNTIME
    assert 'ManagerEditorTypeName = "BlackStartX.GestureManager.Editor.GestureManagerEditor"' in GESTURE_MANAGER_RUNTIME
    assert 'ModuleHelperTypeName = "BlackStartX.GestureManager.Editor.Modules.ModuleHelper"' in GESTURE_MANAGER_RUNTIME
    assert 'candidate.Name, "CreateAndPing"' in GESTURE_MANAGER_RUNTIME
    assert 'candidate.Name, "SetModule"' in GESTURE_MANAGER_RUNTIME
    assert "EditorApplication.EnterPlaymode();" in GESTURE_MANAGER_RUNTIME
    assert "GestureManagerPlayModeCoordinator.Prepare(managerPath, avatarPath);" in GESTURE_MANAGER_RUNTIME
    assert '"gesture_manager_active_instance_ambiguous"' in GESTURE_MANAGER_RUNTIME
    assert '"gesture_manager_avatar_ambiguous"' in GESTURE_MANAGER_RUNTIME
    assert 'toolId: "vrc_gesture_manager_set_parameter"' in GESTURE_MANAGER_RUNTIME
    assert "GestureManagerRuntimeBridge.TryReadParameter" in GESTURE_MANAGER_RUNTIME
    assert "float.IsNaN(value) || float.IsInfinity(value)" in GESTURE_MANAGER_RUNTIME
    assert 'commitState = "runtime_applied"' in GESTURE_MANAGER_RUNTIME


def test_gesture_manager_status_filters_remain_a_strict_direct_read_shape() -> None:
    for name in (
        "includeGestureManagerParameters",
        "gestureManagerParameterNames",
        "gestureManagerParameterPrefix",
    ):
        assert f'"{name}"' in SERVER
    assert '((JArray)arguments["gestureManagerParameterNames"]).Count <= 128' in SERVER
    assert '((string)arguments["gestureManagerParameterPrefix"] ?? string.Empty).Length <= 256' in SERVER


def test_editor_state_atoms_select_with_readback_and_schedule_explicit_play_mode() -> None:
    assert 'toolId: "vrc_select_scene_object"' in EDITOR_STATE_TOOLS
    assert 'GetType("UnityEditor.SceneHierarchyWindow"' in EDITOR_STATE_TOOLS
    assert 'method.Name == "SetSearchFilter"' in EDITOR_STATE_TOOLS
    assert '"scene_object_hierarchy_reveal_failed"' in EDITOR_STATE_TOOLS
    assert 'commitState = "editor_state_partial"' in EDITOR_STATE_TOOLS
    assert "Selection.activeGameObject = target;" in EDITOR_STATE_TOOLS
    assert "EditorGUIUtility.PingObject(target);" in EDITOR_STATE_TOOLS
    assert 'commitState = "editor_state_applied"' in EDITOR_STATE_TOOLS
    assert 'toolId: "vrc_set_play_mode"' in EDITOR_STATE_TOOLS
    assert "EditorApplication.isCompiling" in EDITOR_STATE_TOOLS
    assert "EditorApplication.isUpdating" in EDITOR_STATE_TOOLS
    assert "var entryBlockedByEditorWork = requested &&" in EDITOR_STATE_TOOLS
    assert "if (entryBlockedByEditorWork || isTransitioning)" in EDITOR_STATE_TOOLS
    assert '"unity_editor_state"' in EDITOR_STATE_TOOLS
    assert '"play_mode_precondition"' in EDITOR_STATE_TOOLS
    assert "EditorApplication.EnterPlaymode();" in EDITOR_STATE_TOOLS
    assert "EditorApplication.ExitPlaymode();" in EDITOR_STATE_TOOLS
    assert 'commitState = "transition_scheduled"' in EDITOR_STATE_TOOLS
    assert "EditorSceneManager.Save" not in EDITOR_STATE_TOOLS
    assert "AssetDatabase.SaveAssets" not in EDITOR_STATE_TOOLS


def test_csharp_and_python_contracts_pin_the_same_exact_read_only_tools() -> None:
    read_only_block = CONTRACT[
        CONTRACT.index("private static readonly HashSet<string> ExpectedReadOnlyNames") :
        CONTRACT.index("private static readonly HashSet<string> ExpectedPlanningNames")
    ]
    csharp_read_only = set(re.findall(r'"(vrc_[^"]+)"', read_only_block))

    assert csharp_read_only == READ_ONLY_TOOL_NAMES
    assert "ExpectedReadOnlyNames.Contains(descriptor.Name)" in CONTRACT
    assert "!readOnly.SetEquals(expectedReadOnly)" in SERVER


def test_csharp_and_python_contracts_pin_the_same_planning_exposure_tools() -> None:
    planning_block = CONTRACT[
        CONTRACT.index("private static readonly HashSet<string> ExpectedPlanningNames") :
        CONTRACT.index("internal static ISet<string> ExpectedToolNames")
    ]
    csharp_planning_additions = set(re.findall(r'"(vrc_[^"]+)"', planning_block))
    assert READ_ONLY_TOOL_NAMES | csharp_planning_additions == PLANNING_TOOL_NAMES


def test_fixed_lanes_derive_approved_write_without_stale_hardcoded_counts() -> None:
    preview_block = SERVER[SERVER.index("PreviewTools =") : SERVER.index("SafetyControlTools =")]
    safety_block = SERVER[SERVER.index("SafetyControlTools =") : SERVER.index("private enum InvocationLane")]
    preview_names = set(re.findall(r'"(vrc_[^"]+)"', preview_block))
    safety_names = set(re.findall(r'"(vrc_[^"]+)"', safety_block))
    assert len(preview_names) == 25
    assert "vrc_convert_unity_constraint" in preview_names
    assert len(safety_names) == 2
    assert preview_names <= EXPECTED_TOOL_NAMES
    assert safety_names <= EXPECTED_TOOL_NAMES
    assert not (preview_names & READ_ONLY_TOOL_NAMES)
    assert not (safety_names & READ_ONLY_TOOL_NAMES)
    assert not (preview_names & safety_names)
    assert "readOnly.Count != 10 || preview.Count != 24 || safety.Count != 2" not in SERVER
    assert "approved.Count != 57" not in SERVER
    assert "approved.Count != 60" not in SERVER
    assert "expectedAll.Except(all)" in SERVER
    assert "expectedReadOnly.Except(readOnly)" in SERVER
    assert "missingApprovedPreview" in SERVER
    assert "readOnly.SetEquals(expectedReadOnly)" in SERVER
    assert "readOnly.Overlaps(preview)" in SERVER
    assert "preview.Overlaps(safety)" in SERVER
    assert "preview.Except(approved)" in SERVER


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
    assert 'HasExactKeys(arguments, "projectPath", "phase", "scenePaths", "activeScenePath", "refreshAssets")' in predicate
    assert 'string.Equals(phase, "prepare_restore", StringComparison.Ordinal)' in predicate
    assert 'string.Equals(phase, "reload", StringComparison.Ordinal)' in predicate
    assert 'HasStringArray(arguments, "scenePaths")' in predicate
    assert 'HasString(arguments, "activeScenePath")' in predicate
    assert 'HasBoolean(arguments, "refreshAssets")' in predicate
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
    assert 'HasExactKeys(arguments, "statusOnly", "requirePlayMode", "captureMode")' in SERVER
    assert 'HasTrueBoolean(arguments, "statusOnly")' in SERVER
    assert 'HasBoolean(arguments, "requirePlayMode")' in SERVER
    assert 'HasCaptureMode(arguments)' in SERVER
    assert 'value == "auto" || value == "scene_view" || value == "game_view"' in SERVER
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
    assert '["requested"]' in SERVER
    assert '["coreIdentity"] = VRCForgeMcpToolContract.CoreIdentity' in SERVER
    assert '["handshakeProtocol"] = VRCForgeMcpToolContract.HandshakeProtocol' in SERVER
    assert '["productVersion"] = VRCForgeMcpToolContract.ProductVersion' in SERVER
    assert '["toolContractVersion"] = VRCForgeMcpToolContract.ToolContractVersion' in SERVER
    assert '["instanceId"] = descriptorInstanceId' in SERVER
    assert '["projectId"] = ComputeProjectId(GetProjectRoot())' in SERVER
    assert '["version"] = VRCForgeMcpToolContract.ProductVersion' in SERVER
