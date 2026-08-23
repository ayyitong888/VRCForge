import re
from pathlib import Path
from unittest.mock import patch

import dashboard_server


ROOT = Path(__file__).resolve().parents[1]
CORE_TOOL = ROOT / "Assets/VRCForge/Editor/Generic/InboundReferenceClosureTool.cs"
RESULT_SCHEMA = "vrcforge.inbound_reference_closure.v1"


def _valid_request() -> dict[str, object]:
    return {
        "projectPath": "D:/Project",
        "avatarPath": "Avatar",
        "targetPaths": ["Avatar/OldEar"],
    }


def test_gateway_never_turns_a_core_error_envelope_into_success() -> None:
    transport = dashboard_server.McpResult(
        exit_code=1,
        stdout="untrusted transport text",
        stderr="",
        payload={
            "isError": True,
            "structuredContent": {
                "code": "managed_peer_ineligible",
                "message": "The Unity Core rejected the read request.",
            },
        },
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport) as invoke,
    ):
        result = dashboard_server.scan_inbound_reference_closure_sync(_valid_request())

    assert invoke.call_args.kwargs["preserve_tool_error"] is True
    assert result == {
        "schema": RESULT_SCHEMA,
        "ok": False,
        "failureLayer": "unity_core_read",
        "errorCode": "managed_peer_ineligible",
        "error": "The Unity Core rejected the read request.",
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "requestMayHaveCommitted": False,
        "checkpointRecoveryRequired": False,
    }


def test_gateway_rejects_code_only_core_error_even_with_zero_exit_code() -> None:
    transport = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={
            "code": "managed_peer_ineligible",
            "message": "The Unity Core rejected the read request.",
        },
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport),
    ):
        result = dashboard_server.scan_inbound_reference_closure_sync(_valid_request())

    assert result["ok"] is False
    assert result["failureLayer"] == "unity_core_read"
    assert result["errorCode"] == "managed_peer_ineligible"
    assert result["mutationStarted"] is False
    assert result["requestMayHaveCommitted"] is False


def test_gateway_maps_transport_exception_to_confirmed_no_write_failure() -> None:
    failure = dashboard_server.UnityMcpError(
        "Unity Core is unavailable.",
        cause_code="unity_core_unavailable",
        retryable=True,
        core_tool="vrc_scan_inbound_reference_closure",
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", side_effect=failure),
    ):
        result = dashboard_server.scan_inbound_reference_closure_sync(_valid_request())

    assert result == {
        "schema": RESULT_SCHEMA,
        "ok": False,
        "failureLayer": "unity_core_transport",
        "errorCode": "unity_core_unavailable",
        "error": "Unity Core is unavailable.",
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "requestMayHaveCommitted": False,
        "checkpointRecoveryRequired": False,
    }


def test_invalid_component_index_returns_structured_gateway_validation_failure() -> None:
    result = dashboard_server.scan_inbound_reference_closure_sync(
        {
            "projectPath": "D:/Project",
            "avatarPath": "Avatar",
            "targetComponentSelectors": [
                {
                    "objectPath": "Avatar/OldEar",
                    "componentType": "VRCPhysBone",
                    "componentIndex": "not-an-int",
                }
            ],
        }
    )

    assert result == {
        "schema": RESULT_SCHEMA,
        "ok": False,
        "failureLayer": "gateway_validation",
        "errorCode": "invalid_component_index",
        "error": "componentIndex must be an integer that is zero or greater.",
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "requestMayHaveCommitted": False,
        "checkpointRecoveryRequired": False,
    }


def test_invalid_max_results_returns_structured_gateway_validation_failure() -> None:
    result = dashboard_server.scan_inbound_reference_closure_sync(
        {
            **_valid_request(),
            "maxResults": "not-an-int",
        }
    )

    assert result["schema"] == RESULT_SCHEMA
    assert result["ok"] is False
    assert result["failureLayer"] == "gateway_validation"
    assert result["errorCode"] == "invalid_max_results"
    assert result["mutationStarted"] is False
    assert result["requestMayHaveCommitted"] is False


def test_connection_settings_failure_returns_structured_gateway_failure() -> None:
    with patch(
        "dashboard_server.load_dashboard_settings",
        side_effect=ValueError("The project binding is invalid."),
    ):
        result = dashboard_server.scan_inbound_reference_closure_sync(_valid_request())

    assert result["schema"] == RESULT_SCHEMA
    assert result["ok"] is False
    assert result["failureLayer"] == "gateway_configuration"
    assert result["errorCode"] == "invalid_connection_settings"
    assert result["mutationStarted"] is False
    assert result["requestMayHaveCommitted"] is False


def test_malformed_core_result_returns_structured_confirmed_no_write_failure() -> None:
    transport = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload=["malformed"],
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport),
    ):
        result = dashboard_server.scan_inbound_reference_closure_sync(_valid_request())

    assert result["schema"] == RESULT_SCHEMA
    assert result["ok"] is False
    assert result["failureLayer"] == "unity_core_result"
    assert result["errorCode"] == "invalid_result"
    assert result["mutationStarted"] is False
    assert result["requestMayHaveCommitted"] is False


def test_core_scan_failure_is_structured_and_explicitly_read_only() -> None:
    source = CORE_TOOL.read_text(encoding="utf-8")

    assert "VRCForgeToolResult.FailedWithCode(" in source
    assert '"inbound_reference_closure_scan_failed"' in source
    assert 'schema = "vrcforge.inbound_reference_closure.v1"' in source
    assert 'failureLayer = "unity_read_scan"' in source
    assert "mutationStarted = false" in source
    assert "committed = false" in source
    assert 'commitState = "not_started"' in source
    assert "requestMayHaveCommitted = false" in source
    assert "checkpointRecoveryRequired = false" in source


def test_unresolved_path_like_strings_fail_closed() -> None:
    source = CORE_TOOL.read_text(encoding="utf-8")

    assert "IsPotentialTargetPathString" in source
    assert "AddUnresolvedPathString" in source
    assert re.search(
        r"LooksLikePathProperty\(iterator\.propertyPath, value\)\s*"
        r"\|\| IsPotentialTargetPathString\(state, value\)",
        source,
    )
    assert "The serialized path-like value could not be resolved safely" in source


def test_component_target_is_not_blocked_by_an_unrelated_carrier_transform_path() -> None:
    source = CORE_TOOL.read_text(encoding="utf-8")
    start = source.index("private static bool IsPotentialTargetPathString")
    end = source.index("private static bool PathsOverlap", start)
    method = source[start:end]

    assert "state.TargetRoots" in method
    assert "state.TargetComponents" not in method
    assert "A serialized GameObject path is not evidence that a component" in method


def test_transform_reference_identity_belongs_to_the_exact_matched_object() -> None:
    source = CORE_TOOL.read_text(encoding="utf-8")
    start = source.index("private static bool TryMatchTargetTransform")
    end = source.index("private static bool TryResolveTargetPathString", start)
    method = source[start:end]

    assert "GlobalObjectId.GetGlobalObjectIdSlow(transform.gameObject).ToString()" in method
    assert "targetIdentity = root.Identity;" not in method


def test_tool_stays_on_the_read_only_atomic_surface() -> None:
    source = CORE_TOOL.read_text(encoding="utf-8")

    assert "Access = VRCForgeCommandAccess.ReadOnly" in source
    assert '"vrcforge_scan_inbound_reference_closure"' not in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
    assert "vrc_scan_inbound_reference_closure" not in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST


def test_core_startup_lane_contract_counts_the_tenth_read_only_tool() -> None:
    server = (
        ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs"
    ).read_text(encoding="utf-8")

    assert "readOnly.Count != 10 || preview.Count != 24 || safety.Count != 2" not in server
    assert "approved.Count != 57" not in server
    assert "expectedReadOnly.Except(readOnly)" in server
    assert "missingApprovedPreview" in server
