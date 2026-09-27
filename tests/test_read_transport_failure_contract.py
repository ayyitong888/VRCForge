"""Regression from a captured binding-read Core transport-cap rejection.

The exception text/code and stale envelope shape are reduced from a real
read-only scan receipt. No Core, provider or model is called by these tests.
"""
import pytest

from agent_tool_result_contract import normalize_agent_tool_result
from external_tool_result_contract import build_external_tool_error
from runtime_planner_service import RuntimePlannerService
from vrchat_blendshape_agent import UnityMcpError


MESSAGE = "Unity MCP Core rejected the request (code=-32000): VRCForge Core response exceeds the bounded transport limit.."
WRITE_RETRY = "Read back the exact target state before retrying the write."


def captured_transport_exception():
    # Mixed-capability scans are outside the static READ_ONLY_TOOL_NAMES set.
    # This reproduces the exact lower transport wrapper before the registered
    # gateway read tool supplies its authoritative no-mutation facts.
    return UnityMcpError(
        MESSAGE, cause_code="unity_core_jsonrpc_error", core_tool="vrc_scan_animation_bindings",
        failure_layer="unity_core_transport", failure_phase="tool_dispatch_or_response",
        operation_kind="write", mutation_started=None, committed=None,
    )


def project_boundary(exc, kind="read", mutation=False, committed=False):
    error = build_external_tool_error(
        exception=exc, operation_kind=kind, tool="vrcforge_scan_animation_bindings",
        failure_layer="agent_tool_handler", failure_phase="tool_handler_exception",
        tool_routing_started=True, mutation_started=mutation, committed=committed,
    )
    result = {"ok": False, "status": "failed", "error": MESSAGE, "errorDetails": error}
    outcome = normalize_agent_tool_result(result, fallback_summary="Read animation bindings.", write=kind != "read")
    observation = RuntimePlannerService._llm_loop_step_observation(None, {
        "tool": "vrcforge_scan_animation_bindings", "status": "failed", "result": result, "outcome": outcome,
    })
    return error, outcome, observation


def test_captured_binding_read_cap_failure_keeps_no_mutation_through_native_projection():
    exc = captured_transport_exception()
    assert exc.external_error["commitState"] == "unknown"  # recorded upstream shape
    error, outcome, observation = project_boundary(exc)
    for facts in (error, outcome):
        assert facts["success"] is False
        assert facts["mutationStarted"] is False and facts["committed"] is False
        assert facts["commitState"] == "not_started"
        assert facts["commitStateKnown"] is True
        assert facts["recovery"]["required"] is False
        assert facts["errorCode"] == "unity_core_jsonrpc_error"
        assert facts["retryable"] is False  # cap is not solved by blindly retrying
        assert "write" not in str(facts.get("nextAction", ""))
    assert error["operationKind"] == "read"
    assert MESSAGE in observation
    assert WRITE_RETRY not in observation
    assert '"commitState":"not_started"' in observation


@pytest.mark.parametrize("kind,mutation,committed", [
    ("write", None, None), ("write", False, False), ("unknown", False, False),
    ("read", None, None), ("read", True, False),
])
def test_uncertain_write_or_unknown_operation_is_not_promoted_to_no_write(kind, mutation, committed):
    error, outcome, _ = project_boundary(captured_transport_exception(), kind, mutation, committed)
    for facts in (error, outcome):
        assert facts["commitState"] == "unknown"
        assert facts["recovery"]["required"] is True
        assert facts["safeToRetry"] is False


def test_known_read_keeps_custom_recovery_and_next_action():
    exc = captured_transport_exception()
    recovery = {"required": True, "reason": "Inspect the separate failed checkpoint before continuing."}
    exc.external_error.update(recovery=recovery, nextAction="Inspect checkpoint status.")
    error, outcome, observation = project_boundary(exc)
    for facts in (error, outcome):
        assert facts["commitState"] == "not_started"
        assert facts["recovery"] == recovery
        assert facts["nextAction"] == "Inspect checkpoint status."
    assert "Inspect checkpoint status." in observation


def test_known_read_does_not_erase_explicit_checkpoint_recovery():
    exc = captured_transport_exception()
    exc.external_error["checkpointRecoveryRequired"] = True
    error, outcome, _ = project_boundary(exc)
    for facts in (error, outcome):
        assert facts["commitState"] == "not_started"
        assert facts["checkpointRecoveryRequired"] is True
        assert facts["recovery"]["required"] is True
