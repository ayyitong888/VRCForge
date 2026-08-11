from __future__ import annotations

from agent_tool_result_contract import (
    completion_gate_plan,
    normalize_agent_tool_result,
)


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
