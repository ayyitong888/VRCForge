from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import httpx

from agent_gateway import AgentGateway, create_agent_mcp_app
from agent_mcp_2026 import PROTOCOL_VERSION
from agent_tool_result_contract import normalize_agent_tool_result
from external_tool_result_contract import build_external_tool_error


TOOL_NAME = "vrcforge_result_parity_fixture"
CANONICAL_CAUSE_FIELDS = (
    "failureLayer",
    "failurePhase",
    "failureCause",
    "rootCause",
    "observed",
    "expected",
    "delta",
    "evidence",
    "causeChain",
    "nextAction",
    "recovery",
)
CANONICAL_SUCCESS_FACT_FIELDS = (
    "observed",
    "expected",
    "delta",
    "evidence",
    "nextAction",
    "recovery",
)
CANONICAL_FAILURE_EXECUTION_FIELDS = (
    "errorCode",
    "mutationStarted",
    "committed",
    "commitState",
    "commitStateKnown",
    "safeToRetry",
)


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(
        tmp_path / "config" / "agent_gateway.json",
        tmp_path / "audit",
    )
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    return gateway


def _mcp_2026_tool_call(
    app: object,
    *,
    token: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    async def call() -> dict[str, object]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "tool-result-parity-test",
                        "version": "1",
                    },
                },
                "name": TOOL_NAME,
                "arguments": arguments,
            },
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": "tools/call",
            "Mcp-Name": TOOL_NAME,
            "Authorization": f"Bearer {token}",
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post("/mcp", headers=headers, json=request)
        assert response.status_code == 200
        return response.json()["result"]["structuredContent"]

    return asyncio.run(call())


def test_internal_agent_and_external_mcp_2026_keep_identical_canonical_results(
    tmp_path: Path,
) -> None:
    success_payload = {
        "ok": True,
        "status": "completed",
        "summary": "Synthetic avatar readiness inspection completed.",
        "observed": {"isPlayMode": False, "sdkUserAuthenticated": True},
        "expected": {"isPlayMode": False, "sdkUserAuthenticated": True},
        "delta": {"isPlayMode": False, "sdkUserAuthenticated": False},
        "evidence": [
            {
                "ref": "synthetic://avatar-readiness/success",
                "kind": "readiness",
                "sha256": "1" * 64,
            }
        ],
        "nextAction": "Keep the exact readiness binding for the approved action.",
        "recovery": {"required": False},
        "failedStep": "none",
        "diagnostics": {"schema": "synthetic.v1", "checks": ["rest", "play"]},
    }
    failure_payload = {
        "ok": False,
        "status": "failed",
        "errorCode": "avatar_upload_readiness_blocked",
        "error": (
            "Upload readiness is blocked: Unity Play Mode must be stopped before "
            "upload; the VRChat SDK has no authenticated current user."
        ),
        "failureLayer": "vrchat_sdk_upload_readiness",
        "failurePhase": "inspect",
        "failureCause": {
            "code": "avatar_upload_readiness_blocked",
            "message": (
                "Play Mode is active and the VRChat SDK user is not authenticated."
            ),
        },
        "rootCause": {
            "primary": "Unity Play Mode must be stopped before upload.",
            "secondary": "The VRChat SDK has no authenticated current user.",
        },
        "observed": {
            "isPlayMode": True,
            "sdkUserId": "",
            "canPublishAvatars": False,
        },
        "expected": {
            "isPlayMode": False,
            "sdkUserId": "authenticated-user-id",
            "canPublishAvatars": True,
        },
        "delta": {
            "isPlayMode": {"observed": True, "expected": False},
            "sdkUserId": {"observed": "", "expected": "authenticated-user-id"},
            "canPublishAvatars": {"observed": False, "expected": True},
        },
        "evidence": [
            {
                "ref": "synthetic://avatar-readiness/failure",
                "kind": "readiness",
                "sha256": "2" * 64,
            }
        ],
        "causeChain": [
            {
                "order": 1,
                "cause": "Unity is in Play Mode.",
                "effect": "The upload preflight rejects the request.",
            },
            {
                "order": 2,
                "cause": "The VRChat SDK current user is absent.",
                "effect": "Ownership and publish permission cannot be proven.",
            },
        ],
        "nextAction": [
            "Stop Unity Play Mode.",
            "Authenticate the intended VRChat SDK owner.",
            "Rerun upload readiness and bind a new digest.",
        ],
        "recovery": {
            "required": False,
            "reason": "No build or upload mutation started.",
        },
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "commitStateKnown": True,
        "safeToRetry": False,
        "failedStep": "inspect_neck_seam",
        "diagnostics": {"schema": "synthetic.v1", "cause": "bone binding"},
    }
    readiness_blocked_payload = {
        "ok": True,
        "status": "completed",
        "ready": False,
        "blockingReasons": [
            "Unity Play Mode must be stopped before upload.",
            "The VRChat SDK has no authenticated current user.",
        ],
        "failureLayer": "vrchat_sdk_upload_readiness",
        "failurePhase": "inspect",
        "failureCause": {
            "code": "avatar_upload_readiness_blocked",
            "reasons": [
                "Unity Play Mode must be stopped before upload.",
                "The VRChat SDK has no authenticated current user.",
            ],
        },
        "rootCause": {
            "blockingReasons": [
                "Unity Play Mode must be stopped before upload.",
                "The VRChat SDK has no authenticated current user.",
            ]
        },
        "observed": {"ready": False, "sdkUserAuthenticated": False},
        "expected": {"ready": True, "sdkUserAuthenticated": True},
        "delta": {"blockingReasonCount": 2},
        "evidence": [{"ref": "synthetic://avatar-readiness/blocked"}],
        "causeChain": [
            {
                "cause": "Unity Play Mode must be stopped before upload.",
                "effect": "Upload readiness remains blocked.",
            },
            {
                "cause": "The VRChat SDK has no authenticated current user.",
                "effect": "Avatar ownership cannot be proven.",
            },
        ],
        "nextAction": ["Stop Play Mode.", "Authenticate the intended SDK owner."],
        "recovery": {"required": False, "reason": "No mutation started."},
    }
    payloads = {
        "success": success_payload,
        "failure": failure_payload,
        "readiness_blocked": readiness_blocked_payload,
    }

    def synthetic_handler(arguments: dict[str, object]) -> dict[str, object]:
        return deepcopy(payloads[str(arguments["case"])])

    gateway = _gateway(tmp_path)
    gateway.register_tool(
        TOOL_NAME,
        "Inspect one synthetic result for internal/external result parity.",
        "read/debug",
        synthetic_handler,
    )
    gateway.register_external_mcp_unity_tool(TOOL_NAME, "diagnostics")
    app = create_agent_mcp_app(gateway)

    results: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for case, raw_payload in payloads.items():
        internal = gateway.call_tool(
            TOOL_NAME,
            {"case": case},
            agent_name="internal-runtime",
        )
        external = _mcp_2026_tool_call(
            app,
            token=gateway.ensure_config().token,
            arguments={"case": case},
        )

        assert internal["result"] == raw_payload
        assert external["result"] == raw_payload
        assert external["outcome"] == internal["outcome"]
        for field, internal_value in internal["outcome"].items():
            assert external["outcome"][field] == internal_value
        for field in ("failedStep", "diagnostics"):
            if field in raw_payload:
                assert internal["outcome"][field] == raw_payload[field]
                assert external["outcome"][field] == raw_payload[field]
        results[case] = (internal, external)

    internal_success, external_success = results["success"]
    assert internal_success["result"]["status"] == "completed"
    assert external_success["result"]["status"] == "completed"
    assert internal_success["outcome"]["success"] is True
    assert external_success["outcome"]["success"] is True
    assert internal_success["outcome"]["status"] == "ok"
    assert external_success["outcome"]["status"] == "ok"
    for field in CANONICAL_SUCCESS_FACT_FIELDS:
        assert field in internal_success["outcome"]
        assert external_success["outcome"][field] == internal_success["outcome"][field]
        assert internal_success["outcome"][field] == success_payload[field]

    internal_failure, external_failure = results["failure"]
    for field in CANONICAL_CAUSE_FIELDS:
        assert field in internal_failure["outcome"]
        assert external_failure["outcome"][field] == internal_failure["outcome"][field]
        assert internal_failure["outcome"][field] == failure_payload[field]
    assert internal_failure["outcome"]["success"] is False
    assert external_failure["outcome"]["success"] is False
    assert internal_failure["outcome"]["status"] == "failed"
    assert external_failure["outcome"]["status"] == "failed"
    assert internal_failure["outcome"]["failureCause"] == failure_payload["failureCause"]
    assert external_failure["outcome"]["failureCause"] == failure_payload["failureCause"]
    for field in CANONICAL_FAILURE_EXECUTION_FIELDS:
        assert internal_failure["outcome"][field] == failure_payload[field]
        assert external_failure["outcome"][field] == internal_failure["outcome"][field]

    internal_blocked, external_blocked = results["readiness_blocked"]
    assert internal_blocked["outcome"]["success"] is True
    assert external_blocked["outcome"]["success"] is True
    assert internal_blocked["outcome"]["status"] == "ok"
    assert external_blocked["outcome"]["status"] == "ok"
    assert internal_blocked["outcome"]["ready"] is False
    assert external_blocked["outcome"]["ready"] is False
    assert internal_blocked["outcome"]["blockingReasons"] == readiness_blocked_payload[
        "blockingReasons"
    ]
    assert external_blocked["outcome"]["blockingReasons"] == readiness_blocked_payload[
        "blockingReasons"
    ]
    for field in CANONICAL_CAUSE_FIELDS:
        assert field in internal_blocked["outcome"]
        assert external_blocked["outcome"][field] == internal_blocked["outcome"][field]
        assert internal_blocked["outcome"][field] == readiness_blocked_payload[field]


def test_external_write_re_normalizes_precise_nested_rejection(tmp_path: Path) -> None:
    """A generic approval wrapper must not hide the exact Unity write failure."""

    exact = {
        "ok": False,
        "status": "failed",
        "errorCode": "component_property_failed_after_mutation",
        "error": "Unity persisted readback did not match the requested property.",
        "failureLayer": "unity_editor_tool",
        "failurePhase": "persisted_readback",
        "failureCause": {
            "code": "component_property_failed_after_mutation",
            "message": "The property value differed after persisted readback.",
        },
        "rootCause": {"component": "SkinnedMeshRenderer", "property": "bones[3]"},
        "causeChain": [
            {"type": "UnityException", "message": "Serialized property mismatch."},
            {"type": "InvalidOperationException", "message": "Readback failed."},
        ],
        "failedStep": "persisted_readback",
        "diagnostics": {
            "exception": {
                "type": "UnityException",
                "message": "Serialized property mismatch.",
                "innerChain": [{"type": "InvalidOperationException", "message": "Readback failed."}],
            }
        },
        "mutationStarted": True,
        "committed": False,
        "commitState": "partial",
    }
    applied = {
        "ok": False,
        "status": "failed",
        "outcome": {
            "success": False,
            "status": "failed",
            "errorCode": "agent_gateway_rejected",
            "failureLayer": "unknown",
            "failurePhase": "wrapper",
            "error": "The write was rejected by the gateway.",
        },
        "result": exact,
        "rawResult": exact,
    }

    gateway = _gateway(tmp_path)
    external = gateway._external_mcp_write_result(TOOL_NAME, applied)
    internal = {
        "outcome": normalize_agent_tool_result(
            {
                **exact,
                "errorDetails": build_external_tool_error(
                    error=exact["error"],
                    failure_layer="agent_tool_result",
                    failure_phase="tool_returned_rejection",
                    operation_kind="write",
                    tool=TOOL_NAME,
                    tool_routing_started=True,
                    raw_result=exact,
                ),
            },
            fallback_summary=f"{TOOL_NAME} failed.",
            write=True,
        )
    }
    mcp_structured = normalize_agent_tool_result(
        external,
        fallback_summary=f"{TOOL_NAME} completed.",
        write=True,
    )

    for field in (
        "success", "status", "errorCode", "failureLayer", "failurePhase",
        "failureCause", "rootCause", "causeChain", "failedStep", "diagnostics",
        "mutationStarted", "committed", "commitState",
    ):
        assert internal["outcome"].get(field) == mcp_structured.get(field)
    for field in (
        "errorCode", "failureLayer", "failurePhase", "failureCause",
        "rootCause", "causeChain", "failedStep", "mutationStarted",
        "committed", "commitState",
    ):
        assert internal["outcome"].get(field) == exact.get(field)


def test_write_preparation_rejection_keeps_boundary_cause_over_exception_wrapper(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)
    error = RuntimeError(
        r"source_mode default could not find examples\\mvp_blendshapes_export.json"
    )
    error.cause_code = "agent_gateway_rejected"

    external = gateway._external_mcp_no_write_error(
        "vrcforge_apply_blendshapes",
        "write_preparation",
        error,
    )
    outcome = external["outcome"]
    assert external["errorDetails"]["errorCode"] == "external_write_preparation_rejected"
    for view in (external["errorDetails"], outcome):
        assert view["failureCause"]["code"] == "external_write_preparation_rejected"
        assert view["failureCause"]["failureLayer"] == "write_preparation"
        assert view["failureCause"]["failurePhase"] == "before_write_handler"
        assert view["rootCause"] == view["failureCause"]
        assert view["causeChain"][0]["code"] == "agent_gateway_rejected"
    assert "RuntimeError" in str(external["errorDetails"]["exception"])
    assert normalize_agent_tool_result(
        external,
        fallback_summary="External write preparation failed.",
        write=True,
    ) == outcome
