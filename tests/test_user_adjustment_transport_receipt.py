"""Regression for the real App -> Unity CLI result envelope."""

from copy import deepcopy

import pytest

from agent_task_loop import AgentTaskLoop, approval_completion, approval_task_context
from stage1_atomic_writes import user_adjustment_receipt
from tests.test_user_adjustment_runtime_boundary import TOOL, receipt, scripted_gateway


def app_execution_result(root, **changes):
    # Shape captured from the real approved prepare execution. The identifiers
    # use the existing synthetic fixture so no machine paths enter this test.
    data = receipt(root, **changes)["structuredContent"]["data"]
    return {"ok": True, "toolName": "vrc_user_adjustment_handoff", "result": {
        "exitCode": 0, "stdout": "", "stderr": "", "payload": {"data": data},
    }}


@pytest.mark.parametrize("operation,status", [
    ("user_adjustment_prepare", "awaiting_user"),
    ("user_adjustment_finalize", "finalized"),
    ("user_adjustment_abort", "aborted"),
])
def test_real_app_execution_envelope_exposes_verified_handoff(tmp_path, operation, status):
    result = app_execution_result(tmp_path, operation=operation, status=status)
    expected = result["result"]["payload"]["data"]
    assert user_adjustment_receipt(result) == expected
    assert user_adjustment_receipt(result) is not expected


@pytest.mark.parametrize("failure", ["exit", "outer_ok", "payload_success", "payload_is_error"])
def test_failed_app_execution_cannot_expose_nested_success_receipt(tmp_path, failure):
    result = app_execution_result(tmp_path)
    if failure == "exit":
        result["result"]["exitCode"] = 1
    elif failure == "outer_ok":
        result["ok"] = False
    elif failure == "payload_success":
        result["result"]["payload"]["success"] = False
    else:
        result["result"]["payload"]["isError"] = True
    assert user_adjustment_receipt(result) is None


def test_failed_exit_also_rejects_previously_supported_raw_result_wrapper(tmp_path):
    result = {"exitCode": 1, "rawResult": receipt(tmp_path)}
    assert user_adjustment_receipt(result) is None


def test_real_app_prepare_parks_runtime_before_next_provider(tmp_path, monkeypatch):
    raw = app_execution_result(tmp_path)
    gateway, calls = scripted_gateway(tmp_path, monkeypatch, raw)
    result = gateway.runtime_message({"message": "prepare test", "sessionId": "s",
                                      "clientTurnId": "c", "projectRoot": str(tmp_path)})
    assert len(calls) == 1
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert result["steps"][0]["outcome"]["status"] == "ok"
    stored = gateway._runtime_session_state.user_adjustment("s", str(tmp_path))
    assert stored["receipt"]["handoffId"] == raw["result"]["payload"]["data"]["handoffId"]


def test_real_app_prepare_approval_resume_parks_without_provider(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    arguments = {"projectPath": str(tmp_path), "arguments": {"mode": "transform"}}
    loop = AgentTaskLoop("prepare adjustment", session_id="s", turn_id="prepare-turn",
                         client_turn_id="c", project_root=str(tmp_path))
    context = approval_task_context(
        loop.approval_seed(requested_tool=TOOL, requested_arguments=arguments),
        tool=TOOL, arguments=arguments,
    )
    raw = app_execution_result(tmp_path)
    completion = approval_completion(context, raw_result=deepcopy(raw), outcome={
        "status": "ok", "summary": "prepared", "verification": {"state": "verified", "checks": []},
    })
    result = gateway.resume_runtime_task_after_approval(
        {"id": "prepare-approval", "taskContext": context, "arguments": arguments},
        {"status": "applied", "result": raw, "taskCompletion": completion},
    )
    assert result is not None
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert not calls
