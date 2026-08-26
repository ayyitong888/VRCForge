from __future__ import annotations

from external_tool_result_contract import (
    build_external_tool_error,
    canonical_result_facts,
    external_write_failure_view,
)


class StructuredWrapperError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Wrapper could not complete the call.")
        self.external_error = build_external_tool_error(
            error=str(self),
            error_code="wrapper_unknown",
            failure_layer="unknown",
            operation_kind="write",
            mutation_started=None,
            committed=None,
        )


def test_source_rejection_facts_beat_unknown_wrapper_facts_without_losing_raw_result() -> None:
    raw = {
        "ok": False,
        "errorCode": "core_pre_route_rejected",
        "error": "Core rejected before tool routing.",
        "failureLayer": "unity_core_pre_route",
        "failurePhase": "before_tool_routing",
        "toolRoutingStarted": False,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
    }

    error = build_external_tool_error(
        raw_result=raw,
        exception=StructuredWrapperError(),
        operation_kind="write",
        tool="vrcforge_fixture_write",
    )

    assert error["schema"] == "vrcforge.external_tool_error.v1"
    assert error["errorCode"] == "core_pre_route_rejected"
    assert error["error"] == "Core rejected before tool routing."
    assert error["failureLayer"] == "unity_core_pre_route"
    assert error["toolRoutingStarted"] is False
    assert error["mutationStarted"] is False
    assert error["committed"] is False
    assert error["commitState"] == "not_started"
    assert error["rawResult"] == raw
    assert external_write_failure_view(error)["mutationStarted"] is False


def test_missing_post_route_write_facts_remain_unknown() -> None:
    error = build_external_tool_error(
        error="Transport outcome is unknown.",
        error_code="transport_outcome_unknown",
        failure_layer="unity_core_transport",
        failure_phase="tool_dispatch_or_response",
        operation_kind="write",
        tool="vrcforge_fixture_write",
        tool_routing_started=None,
        mutation_started=None,
        committed=None,
    )

    assert error["mutationStarted"] is None
    assert error["committed"] is None
    assert error["commitState"] == "unknown"
    assert error["commitStateKnown"] is False


def test_canonical_facts_preserve_dispatch_retry_and_exact_recovery_flags() -> None:
    facts = canonical_result_facts(
        [
            {
                "ok": False,
                "errorCode": "write_response_timeout",
                "error": "The write response timed out after dispatch.",
                "toolRoutingStarted": True,
                "mutationStarted": None,
                "committed": None,
                "commitState": "unknown",
                "retryable": True,
                "checkpointRecoveryRequired": True,
                "temporaryCleanupRequired": False,
            }
        ],
        success=False,
        status="failed",
    )

    assert facts["toolRoutingStarted"] is True
    assert facts["retryable"] is True
    assert facts["safeToRetry"] is False
    assert facts["checkpointRecoveryRequired"] is True
    assert facts["temporaryCleanupRequired"] is False


def test_write_failure_view_only_exposes_known_retryability() -> None:
    unknown = build_external_tool_error(
        error="Unity was busy before the write started.",
        operation_kind="write",
        mutation_started=False,
        committed=False,
    )

    assert unknown["retryable"] is None
    assert "retryable" not in external_write_failure_view(unknown)

    for retryable in (False, True):
        known = build_external_tool_error(
            error="Known failure.",
            operation_kind="write",
            mutation_started=False,
            committed=False,
            retryable=retryable,
        )
        assert external_write_failure_view(known)["retryable"] is retryable


def test_exception_canonical_raw_result_is_preserved_once() -> None:
    class BoundaryError(RuntimeError):
        pass

    exc = BoundaryError("gateway rejected")
    exc.external_error = build_external_tool_error(
        error="gateway rejected",
        error_code="http_403",
        failure_layer="external_gateway_http",
        failure_phase="gateway_http_rejection",
        raw_result={"ok": False, "error": "Gateway disabled."},
        mutation_started=False,
        committed=False,
    )

    error = build_external_tool_error(
        error=str(exc),
        error_code="bridge_connection_error",
        failure_layer="external_stdio_http_transport",
        failure_phase="preflight_manifest_request",
        exception=exc,
        mutation_started=False,
        committed=False,
    )

    assert error["errorCode"] == "http_403"
    assert error["failureLayer"] == "external_gateway_http"
    assert error["rawResult"] == {"ok": False, "error": "Gateway disabled."}
    assert "rawResult" not in error["exception"]


def test_external_error_exposes_shared_cause_facts_without_dropping_raw_result() -> None:
    raw = {
        "ok": False,
        "errorCode": "upload_failed",
        "error": "Upload failed after readback.",
        "failureLayer": "vrchat_sdk_upload",
        "failurePhase": "upload",
        "observed": {"remoteId": "new"},
        "expected": {"visibility": "private"},
        "delta": {"visibility": ["public", "private"]},
        "evidence": [{"ref": "upload-job-1", "kind": "sdk_job"}],
        "causeChain": [{"code": "sdk_rejected"}],
        "nextAction": "Inspect the SDK error.",
        "recovery": {"manual": True},
    }
    error = build_external_tool_error(raw_result=raw, operation_kind="write")
    assert error["success"] is False
    assert error["status"] == "failed"
    assert error["failureLayer"] == "vrchat_sdk_upload"
    assert error["failurePhase"] == "upload"
    assert error["observed"] == raw["observed"]
    assert error["expected"] == raw["expected"]
    assert error["delta"] == raw["delta"]
    assert error["evidence"] == raw["evidence"]
    assert error["causeChain"] == raw["causeChain"]
    assert error["nextAction"] == raw["nextAction"]
    assert error["recovery"] == raw["recovery"]
    assert error["rawResult"] == raw
    write_failure = external_write_failure_view(error)
    assert write_failure["success"] is False
    assert write_failure["status"] == "failed"
    assert write_failure["failureCause"] == error["failureCause"]
    assert write_failure["rootCause"] == error["rootCause"]


def test_successful_gameobject_layer_is_not_misclassified_as_failure_layer() -> None:
    facts = canonical_result_facts(
        [
            {
                "ok": True,
                "name": "Avatar",
                "layer": 0,
                "layerName": "Default",
                "phase": "inspection_complete",
                "message": "The GameObject was inspected.",
            }
        ],
        success=True,
        status="ok",
    )

    assert facts == {"success": True, "status": "ok"}


def test_nested_details_keep_explicit_failure_cause_fields() -> None:
    facts = canonical_result_facts(
        [
            {
                "ok": False,
                "errorCode": "fixture_read_failed",
                "error": "The exact Unity read failed.",
                "details": {
                    "failureLayer": "unity_read",
                    "failurePhase": "descriptor_normalization",
                },
            }
        ],
        success=False,
        status="failed",
    )

    assert facts["failureLayer"] == "unity_read"
    assert facts["failurePhase"] == "descriptor_normalization"
    assert facts["failureCause"] == {
        "code": "fixture_read_failed",
        "message": "The exact Unity read failed.",
        "failureLayer": "unity_read",
        "failurePhase": "descriptor_normalization",
    }
    assert facts["rootCause"] == facts["failureCause"]
