from __future__ import annotations

from agent_tool_result_contract import (
    completion_gate_plan,
    normalize_agent_tool_result,
)
from agent_task_loop import _bounded_outcome
from external_tool_result_contract import build_external_tool_error
from runtime_planner_service import (
    RUNTIME_PLANNER_CAUSAL_OBSERVATION_MAX_CHARS,
    RuntimePlannerService,
)


def test_internal_and_external_outcomes_project_the_same_cause_facts() -> None:
    raw = {
        "ok": False,
        "status": "failed",
        "errorCode": "readback_mismatch",
        "error": "The observed value differs from the approved value.",
        "failureLayer": "unity_tool_handler",
        "failurePhase": "readback",
        "failureCause": {"code": "readback_mismatch", "message": "Value differs."},
        "rootCause": "source asset drift",
        "observed": {"value": 1},
        "expected": {"value": 0},
        "delta": {"value": 1},
        "evidence": [{"ref": "readback-1", "kind": "unity_readback"}],
        "causeChain": [{"code": "source_asset_changed"}],
        "nextAction": "Re-run preview.",
        "recovery": {"required": True},
    }
    internal = normalize_agent_tool_result(raw, fallback_summary="Readback failed.", write=True)
    external = build_external_tool_error(raw_result=raw, operation_kind="write")
    shared = (
        "success", "status", "failureLayer", "failurePhase", "failureCause", "rootCause",
        "observed", "expected", "delta", "evidence", "causeChain", "nextAction", "recovery",
    )
    assert {key: internal.get(key) for key in shared} == {key: external.get(key) for key in shared}


def test_nested_tool_failure_cannot_be_wrapped_as_success() -> None:
    outcome = normalize_agent_tool_result(
        {
            "ok": True,
            "result": {
                "ok": False,
                "status": "failed",
                "code": "unity_read_failed",
                "error": "Unity readback failed.",
            },
        },
        fallback_summary="Read the Unity state.",
        write=False,
    )

    assert outcome["schema"] == "vrcforge.tool_result.v1"
    assert outcome["status"] == "failed"
    assert outcome["summary"] == "Unity readback failed."
    assert outcome["error"] == {
        "type": "tool",
        "code": "unity_read_failed",
        "likelyCauses": [],
        "nextActions": [],
        "retryable": False,
    }
    assert outcome["verification"] == {"state": "not_required", "checks": []}


def test_internal_outcome_preserves_canonical_failure_facts_for_planner_diagnostics() -> None:
    source_error = {
        "schema": "vrcforge.external_tool_error.v1",
        "errorCode": "unity_core_contract_invalid",
        "error": "Core descriptor is invalid.",
        "failureLayer": "unity_core_pre_route",
        "failurePhase": "core_handshake",
        "toolRoutingStarted": False,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "retryable": False,
        "checkpointRecoveryRequired": False,
        "temporaryCleanupRequired": False,
        "rawResult": {"causeCode": "unity_core_contract_invalid", "private": "bounded-not-promoted-to-summary"},
    }

    outcome = normalize_agent_tool_result(
        {"ok": False, "status": "failed", "errorDetails": source_error},
        fallback_summary="Read Unity state.",
        write=False,
    )

    assert outcome["status"] == "failed"
    assert outcome["summary"] == "Core descriptor is invalid."
    assert outcome["error"]["code"] == "unity_core_contract_invalid"
    assert outcome["diagnostics"]["schema"] == "vrcforge.internal_tool_diagnostics.v1"
    assert outcome["diagnostics"]["sourceError"]["failureLayer"] == "unity_core_pre_route"
    assert outcome["diagnostics"]["sourceError"]["mutationStarted"] is False
    assert outcome["diagnostics"]["sourceError"]["commitState"] == "not_started"
    assert outcome["diagnostics"]["sourceError"]["rawResult"]["causeCode"] == "unity_core_contract_invalid"


def test_structured_tool_error_preserves_only_bounded_correction_fields() -> None:
    outcome = normalize_agent_tool_result(
        {
            "ok": False,
            "status": "failed",
            "error": {
                "type": "unity_core",
                "code": "unity_core_not_ready",
                "message": "The selected Unity project has not started its Core bridge.",
                "likelyCauses": ["Unity is compiling", "The wrong project is selected"],
                "nextActions": ["Wait for compilation", "Select the open Unity project"],
                "retryable": True,
                "rawDump": "must-not-be-projected",
            },
        },
        fallback_summary="Read Unity state.",
        write=False,
    )

    assert outcome["status"] == "failed"
    assert outcome["summary"] == "The selected Unity project has not started its Core bridge."
    assert outcome["error"] == {
        "type": "unity_core",
        "code": "unity_core_not_ready",
        "likelyCauses": ["Unity is compiling", "The wrong project is selected"],
        "nextActions": ["Wait for compilation", "Select the open Unity project"],
        "retryable": True,
    }
    assert "rawDump" not in str(outcome)


def test_visual_provider_error_preserves_bounded_route_and_image_disposition() -> None:
    outcome = normalize_agent_tool_result(
        {
            "ok": False,
            "status": "needs_user_action",
            "summary": (
                "DeepSeek · deepseek-chat (source=main, errorType=provider_rejected): "
                "HTTP 400 images are not supported. Original images were discarded."
            ),
            "error": {
                "type": "provider_rejected",
                "code": "provider_rejected",
                "summary": "HTTP 400 images are not supported",
                "retryable": False,
                "provider": "deepseek",
                "providerLabel": "DeepSeek",
                "model": "deepseek-chat",
                "source": "main",
                "retainImages": False,
                "disposition": "discarded",
                "rawResponse": "must-not-be-projected",
            },
        },
        fallback_summary="Audit the managed images.",
        write=False,
    )

    assert outcome["status"] == "needs_user_action"
    assert "HTTP 400 images are not supported" in outcome["summary"]
    assert outcome["error"] == {
        "type": "provider_rejected",
        "code": "provider_rejected",
        "likelyCauses": [],
        "nextActions": [
            "Inspect the tool result and complete the required verification."
        ],
        "retryable": False,
        "provider": "deepseek",
        "providerLabel": "DeepSeek",
        "model": "deepseek-chat",
        "source": "main",
        "retainImages": False,
        "disposition": "discarded",
    }
    assert "rawResponse" not in str(outcome)


def test_unverified_write_requires_user_action_and_blocks_completion_claim() -> None:
    outcome = normalize_agent_tool_result(
        {
            "ok": True,
            "summary": "Blendshape values were applied.",
            "readbackVerified": False,
            "visualProof": {
                "status": "unavailable",
                "reason": "No exact target screenshot was captured.",
            },
        },
        fallback_summary="Apply Blendshape values.",
        write=True,
    )

    assert outcome["status"] == "needs_user_action"
    assert outcome["verification"]["state"] == "needs_user_action"
    assert outcome["verification"]["checks"] == [
        {"kind": "readback", "state": "failed"},
        {"kind": "visual", "state": "unavailable"},
    ]

    gated = completion_gate_plan(
        {
            "summary": "The task is complete.",
            "reply": "完成了。",
            "nextStep": "done",
            "continueLoop": True,
        },
        outcome,
    )
    assert gated is not None
    assert gated["nextStep"] == "needs_user_action"
    assert gated["continueLoop"] is False
    assert "verification" in str(gated["reply"]).lower()
    assert "完成了" not in str(gated["reply"])


def test_verified_or_read_only_results_stay_lightweight() -> None:
    verified = normalize_agent_tool_result(
        {"ok": True, "summary": "Applied and verified.", "readbackVerified": True},
        fallback_summary="Apply the change.",
        write=True,
    )
    assert verified == {
        "schema": "vrcforge.tool_result.v1",
        "success": True,
        "status": "ok",
        "summary": "Applied and verified.",
        "data": {},
        "error": None,
        "evidence": [],
        "verification": {
            "state": "passed",
            "checks": [{"kind": "readback", "state": "passed"}],
        },
    }

    read_only = normalize_agent_tool_result(
        "plain result",
        fallback_summary="Read the state.",
        write=False,
    )
    assert read_only["status"] == "ok"
    assert read_only["verification"] == {"state": "not_required", "checks": []}
    assert completion_gate_plan({"nextStep": "done"}, read_only) is None


def test_explicit_execution_status_separates_report_generation_from_domain_gate() -> None:
    outcome = normalize_agent_tool_result(
        {
            "ok": False,
            "status": "blocked",
            "toolExecutionStatus": "completed",
            "schema": "vrcforge.build_test_readiness.v1",
            "summary": "The report was generated and found blocking project issues.",
        },
        fallback_summary="Generate the read-only readiness report.",
        write=False,
    )

    assert outcome["status"] == "ok"
    assert outcome["summary"] == "The report was generated and found blocking project issues."
    assert outcome["verification"] == {"state": "not_required", "checks": []}


def test_deferred_visual_proof_only_blocks_when_visual_verification_is_required() -> None:
    deferred = {
        "ok": True,
        "summary": "Applied and read back exactly.",
        "readbackVerified": True,
        "visualProof": {"status": "unavailable"},
    }

    optional = normalize_agent_tool_result(
        deferred,
        fallback_summary="Apply the change.",
        write=True,
    )
    required = normalize_agent_tool_result(
        {**deferred, "verification": {"required": ["visual"]}},
        fallback_summary="Apply the change.",
        write=True,
    )

    assert optional["status"] == "ok"
    assert optional["verification"]["checks"] == [
        {"kind": "readback", "state": "passed"},
        {"kind": "visual", "state": "unavailable"},
    ]
    assert required["status"] == "needs_user_action"


def test_failed_outcome_replaces_model_completion_and_data_is_bounded() -> None:
    outcome = normalize_agent_tool_result(
        {
            "ok": False,
            "error": "read failed",
            "data": {
                "large": "x" * 1000,
                "nested": {"one": {"two": {"three": {"secret": "not copied"}}}},
            },
        },
        fallback_summary="Read the state.",
        write=False,
    )

    gated = completion_gate_plan(
        {"reply": "完成了。", "summary": "done", "nextStep": "done"},
        outcome,
    )

    assert len(outcome["data"]["large"]) == 600
    assert "not copied" not in str(outcome["data"])
    assert gated is not None
    assert gated["nextStep"] == "tool_failed"
    assert gated["reply"] == "read failed"


def test_top_level_pending_approval_requires_action_without_misreading_nested_progress() -> None:
    pending = normalize_agent_tool_result(
        {"ok": True, "status": "pending", "approval": {"id": "approval-1"}},
        fallback_summary="Request project change.",
        write=True,
    )
    nested_progress = normalize_agent_tool_result(
        {"ok": True, "result": {"status": "pending", "progress": 0.5}},
        fallback_summary="Read progress.",
        write=False,
    )

    assert pending["status"] == "needs_user_action"
    assert completion_gate_plan({"nextStep": "done"}, pending)["nextStep"] == "needs_user_action"
    assert nested_progress["status"] == "ok"


def test_unknown_commit_facts_are_shared_and_forbid_blind_retry() -> None:
    raw = {
        "ok": False,
        "status": "failed",
        "errorCode": "write_response_timeout",
        "error": "The write response timed out after dispatch.",
        "failureLayer": "unity_core_transport",
        "failurePhase": "write_response",
        "mutationStarted": None,
        "committed": None,
        "commitState": "unknown",
        "commitStateKnown": False,
    }

    internal = normalize_agent_tool_result(
        raw,
        fallback_summary="Write failed.",
        write=True,
    )
    external = build_external_tool_error(raw_result=raw, operation_kind="write")
    shared = (
        "success",
        "status",
        "errorCode",
        "failureLayer",
        "failurePhase",
        "mutationStarted",
        "committed",
        "commitState",
        "commitStateKnown",
        "safeToRetry",
        "nextAction",
        "recovery",
    )

    assert {key: internal.get(key) for key in shared} == {
        key: external.get(key) for key in shared
    }
    assert internal["commitState"] == "unknown"
    assert internal["commitStateKnown"] is False
    assert internal["safeToRetry"] is False
    assert "Read back the exact target state" in internal["nextAction"]
    assert internal["recovery"]["required"] is True
    observation = RuntimePlannerService._llm_loop_step_observation(
        object(),
        {
            "tool": "vrcforge_fixture_write",
            "status": internal["status"],
            "result": raw,
            "outcome": internal,
        },
    )
    assert '"commitState":"unknown"' in observation
    assert '"commitStateKnown":false' in observation
    assert '"safeToRetry":false' in observation
    assert "Read back the exact target state before retrying the write." in observation


def test_nested_precise_error_beats_generic_wrapper_and_wrapper_remains_traceable() -> None:
    raw = {
        "ok": False,
        "status": "failed",
        "error": "Wrapper could not complete the call.",
        "errorCode": "external_tool_rejected",
        "failureLayer": "unknown",
        "failurePhase": "wrapper",
        "errorDetails": {
            "schema": "vrcforge.external_tool_error.v1",
            "success": False,
            "status": "failed",
            "error": "Core rejected before tool routing.",
            "errorCode": "core_pre_route_rejected",
            "failureLayer": "unity_core_pre_route",
            "failurePhase": "before_tool_routing",
            "mutationStarted": False,
            "committed": False,
            "commitState": "not_started",
            "commitStateKnown": True,
        },
    }

    internal = normalize_agent_tool_result(
        raw,
        fallback_summary="Write failed.",
        write=True,
    )
    external = build_external_tool_error(raw_result=raw, operation_kind="write")

    assert internal["errorCode"] == "core_pre_route_rejected"
    assert internal["error"]["code"] == "core_pre_route_rejected"
    assert internal["summary"] == "Core rejected before tool routing."
    assert internal["failureLayer"] == "unity_core_pre_route"
    assert internal["failurePhase"] == "before_tool_routing"
    assert external["errorCode"] == internal["errorCode"]
    assert external["failureLayer"] == internal["failureLayer"]
    assert external["failurePhase"] == internal["failurePhase"]
    assert {
        "kind": "wrapper",
        "code": "external_tool_rejected",
        "message": "Wrapper could not complete the call.",
        "failureLayer": "unknown",
        "failurePhase": "wrapper",
    } in internal["causeChain"]
    assert internal["causeChain"] == external["causeChain"]


def test_internal_planner_observation_keeps_domain_cause_and_commit_facts() -> None:
    raw = {
        "ok": True,
        "status": "completed",
        "ready": False,
        "blockingReasons": ["Unity Play Mode must be stopped before upload."],
        "failureLayer": "vrchat_sdk_upload_readiness",
        "failurePhase": "inspect",
        "failureCause": {
            "code": "avatar_upload_readiness_blocked",
            "password": "planner-password-sentinel",
            "control_token": "planner-control-token-sentinel",
            "controltoken": "planner-controltoken-sentinel",
            "client_secret": "planner-client-secret-sentinel",
        },
        "rootCause": {"blockingReasons": ["Unity Play Mode must be stopped before upload."]},
        "observed": {"ready": False, "playModeStopped": False},
        "expected": {"ready": True, "playModeStopped": True},
        "delta": {"playModeMustStop": True},
        "evidence": [{"ref": "avatar-upload-readiness:test", "kind": "readiness"}],
        "causeChain": [{"cause": "Play Mode is active."}],
        "nextAction": ["Stop Play Mode, then rerun readiness."],
        "recovery": {"required": False},
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "commitStateKnown": True,
    }
    outcome = normalize_agent_tool_result(
        raw,
        fallback_summary="Inspect avatar upload readiness.",
        write=False,
    )
    bounded = _bounded_outcome(outcome)
    observation = RuntimePlannerService._llm_loop_step_observation(
        object(),
        {
            "tool": "vrcforge_avatar_upload_readiness",
            "status": outcome["status"],
            "result": raw,
            "outcome": outcome,
        },
    )

    for key in (
        "success",
        "ready",
        "blockingReasons",
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
        "mutationStarted",
        "committed",
        "commitState",
        "commitStateKnown",
    ):
        assert bounded[key] == outcome[key]
    assert '"success":true' in observation
    assert '"ready":false' in observation
    assert '"blockingReasons":["Unity Play Mode must be stopped before upload."]' in observation
    assert '"observed":{"ready":false,"playModeStopped":false}' in observation
    assert '"expected":{"ready":true,"playModeStopped":true}' in observation
    assert '"delta":{"playModeMustStop":true}' in observation
    assert '"causeChain":[{"cause":"Play Mode is active."}]' in observation
    assert '"nextAction":["Stop Play Mode, then rerun readiness."]' in observation
    assert '"recovery":{"required":false}' in observation
    assert '"commitState":"not_started"' in observation
    assert '"commitStateKnown":true' in observation
    for sentinel in (
        "planner-password-sentinel",
        "planner-control-token-sentinel",
        "planner-controltoken-sentinel",
        "planner-client-secret-sentinel",
    ):
        assert sentinel not in observation
    assert observation.count('"<redacted>"') >= 4
    assert '"code":"avatar_upload_readiness_blocked"' in observation
    assert len(observation) <= RUNTIME_PLANNER_CAUSAL_OBSERVATION_MAX_CHARS
