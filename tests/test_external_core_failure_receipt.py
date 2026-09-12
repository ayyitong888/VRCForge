from __future__ import annotations

from external_tool_result_contract import build_external_tool_error
from agent_gateway import AgentGateway


def _core_failure_data() -> dict:
    return {
        "schema": "vrcforge.material_keyword_edit.v1",
        "ok": False,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "checkpointRecoveryRequired": False,
    }


def _nested_424_result() -> dict:
    return {
        "ok": False,
        "status": "failed",
        "operationId": "mcpwrite_fixture",
        "commitState": "unknown",
        "mutationStarted": False,
        "result": {
            "isError": True,
            "operationId": "mcpwrite_fixture",
            "structuredContent": {
                "success": False,
                "operationId": "mcpwrite_fixture",
                "data": _core_failure_data(),
            },
        },
    }


def _build(raw: dict) -> dict:
    return build_external_tool_error(
        error="Material keyword edit rejected.",
        failure_layer="external_mcp_transaction",
        operation_kind="write",
        tool="vrcforge_set_material_shader",
        raw_result=raw,
    )


def test_424_nested_core_failure_promotes_exact_no_write_state() -> None:
    error = _build(_nested_424_result())
    assert error["mutationStarted"] is False
    assert error["committed"] is False
    assert error["commitState"] == "not_started"
    assert error["commitStateKnown"] is True


def test_direct_core_failure_shape_uses_the_same_bounded_projection() -> None:
    raw = {
        "isError": True,
        "operationId": "mcpwrite_fixture",
        "structuredContent": {
            "success": False,
            "operationId": "mcpwrite_fixture",
            "data": _core_failure_data(),
        },
    }
    error = _build(raw)
    assert error["commitState"] == "not_started"
    assert error["commitStateKnown"] is True


def test_nested_data_is_ignored_without_exact_transport_failure_shape() -> None:
    raw = _nested_424_result()
    raw["result"]["isError"] = False
    error = _build(raw)
    assert error["commitState"] == "unknown"
    assert error["commitStateKnown"] is False


def test_nested_data_is_ignored_on_operation_or_outer_state_conflict() -> None:
    mismatched = _nested_424_result()
    mismatched["result"]["operationId"] = "different-operation"
    error = _build(mismatched)
    assert error["commitState"] == "unknown"
    assert error["commitStateKnown"] is False

    conflicted = _nested_424_result()
    conflicted["commitState"] = "partial"
    conflicted["mutationStarted"] = True
    error = _build(conflicted)
    assert error["commitState"] == "partial"
    assert error["commitStateKnown"] is True
    assert error["mutationStarted"] is True


def test_gateway_projection_repairs_unknown_outer_commit_state_from_validated_outcome() -> None:
    raw = _nested_424_result()
    projected = AgentGateway._external_mcp_write_result(
        AgentGateway.__new__(AgentGateway), "vrcforge_set_material_shader", raw
    )
    assert projected["commitState"] == "not_started"
    assert projected["outcome"]["commitState"] == "not_started"
    assert projected["mutationStarted"] is False


def test_gateway_projection_keeps_explicit_outer_conflict_conservative() -> None:
    raw = _nested_424_result()
    raw["commitState"] = "partial"
    raw["mutationStarted"] = True
    projected = AgentGateway._external_mcp_write_result(
        AgentGateway.__new__(AgentGateway), "vrcforge_set_material_shader", raw
    )
    assert projected["commitState"] == "partial"
    assert projected["mutationStarted"] is True
