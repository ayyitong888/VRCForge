from __future__ import annotations

from external_tool_result_contract import (
    build_external_tool_error,
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
