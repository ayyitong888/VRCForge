from __future__ import annotations

import re
from pathlib import Path

from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES, READ_ONLY_TOOL_NAMES

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


def test_direct_mcp_tool_call_can_only_queue_explicit_read_only_tools() -> None:
    assert 'string.Equals(method, "tools/call"' in SERVER
    assert "descriptor.Permission == VRCForgeToolPermission.ReadOnly" in SERVER
    assert "InvocationLane.DirectRead" in SERVER
    assert 'IsStrictBlendshapePayloadRead(toolName, arguments)' in SERVER
    assert 'string.Equals(toolName, "vrc_export_blendshapes", StringComparison.Ordinal)' in SERVER
    assert "arguments == null || arguments.Count != 3" in SERVER
    assert 'arguments["outputPath"]' in SERVER
    assert "string.IsNullOrEmpty((string)outputPath)" in SERVER
    assert "!refreshAssets.Value<bool>()" in SERVER
    assert "returnPayloadOnly.Value<bool>()" in SERVER
    assert "This tool requires the VRCForge App approval and checkpoint lane." in SERVER
    assert "var descriptor = FindTool(pending.ToolName);" in SERVER
    assert "return descriptor.Permission == VRCForgeToolPermission.ReadOnly" in SERVER
    assert "IsStrictBlendshapePayloadRead(pending.ToolName, pending.Arguments)" in SERVER


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


def test_core_has_a_fixed_64_tool_contract_and_never_rediscoveres_at_invoke_time() -> None:
    assert "ToolCount = 64" in CONTRACT
    assert CONTRACT.count('{ "vrc_') == 64
    assert "SnapshotExact" in SERVER
    assert "var registry = VRCForgeToolRegistry.DiscoverLoadedAssemblies();" not in SERVER
    assert "ApprovedAppCoreTools" in SERVER
    assert '"vrcforge_apply_blendshapes"' not in SERVER


def test_csharp_contract_exactly_matches_the_64_owned_tool_declarations() -> None:
    contract = dict(re.findall(r'\{ "(vrc_[^"]+)", "([^"]+)" \}', CONTRACT))
    declared: dict[str, str] = {}
    for path in (ROOT / "Assets" / "VRCForge" / "Editor").rglob("*.cs"):
        source = path.read_text(encoding="utf-8-sig")
        namespace = re.search(r"namespace\s+([\w.]+)", source)
        for match in re.finditer(
            r'\[VRCForgeTool\(\s*name:\s*"([^"]+)"[\s\S]{0,700}?\)\]\s*'
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
        CONTRACT.index("internal static ISet<string> ExpectedToolNames")
    ]
    csharp_read_only = set(re.findall(r'"(vrc_[^"]+)"', read_only_block))

    assert csharp_read_only == READ_ONLY_TOOL_NAMES
    assert "ExpectedReadOnlyNames.Contains(descriptor.Name)" in CONTRACT
    assert "!readOnly.SetEquals(VRCForgeMcpToolContract.ExpectedReadOnlyToolNames)" in SERVER


def test_fixed_lanes_are_8_read_only_8_preview_2_safety_and_54_approved_write() -> None:
    assert "readOnly.Count != 8 || preview.Count != 8 || safety.Count != 2" in SERVER
    assert "approved.Count != 54" in SERVER
    assert "readOnly.SetEquals(VRCForgeMcpToolContract.ExpectedReadOnlyToolNames)" in SERVER
    assert "readOnly.Overlaps(preview)" in SERVER
    assert "preview.Overlaps(safety)" in SERVER
    assert "!approved.IsSupersetOf(preview)" in SERVER


def test_preview_lane_requires_a_strict_true_preview_flag_while_approved_write_keeps_preview_tools() -> None:
    assert "!PreviewTools.Contains(toolName) || !HasExplicitPreviewRequest(arguments)" in SERVER
    assert 'arguments["preview"].Type == JTokenType.Boolean' in SERVER
    assert 'arguments["preview"].Value<bool>()' in SERVER
    assert "approved.ExceptWith(preview);" not in SERVER


def test_screenshot_and_parameter_preview_cannot_bypass_read_only_permission() -> None:
    assert "vrc_capture_scene_view" not in SERVER
    assert "statusOnly" not in SERVER
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
