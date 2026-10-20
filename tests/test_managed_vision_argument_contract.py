"""Managed audit argument validation must not consume capture capabilities."""
import pytest
import dashboard_server as dashboard
from runtime_planner_service import (
    planner_tool_input_schema, validate_planner_tool_arguments,
    managed_multi_capture_receipt, managed_multi_visual_audit_consumed_without_retry,
)
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema


@pytest.mark.parametrize("tool,valid", [
    ("vrcforge_vision_audit", {}),
    ("vrcforge_vision_audit_multi", {"captureReceipt": "fixture-capability"}),
])
def test_public_audit_schema_matches_strict_handler(tool, valid):
    schema = planner_tool_input_schema(tool)
    assert schema["additionalProperties"] is False
    assert canonical_unity_read_tool_input_schema(tool) == schema
    assert validate_planner_tool_arguments(schema, valid)["ok"] is True
    assert validate_planner_tool_arguments(schema, {**valid, "prompt": "Inspect clothing fit"})["ok"] is False


def _bind_owner(monkeypatch):
    monkeypatch.setattr(dashboard.AGENT_GATEWAY, "consume_runtime_task_link", lambda args: (
        {"taskId": "fixture-task", "sessionId": "fixture-session", "managedVisualCaptureActionIds": ["capture-action"]},
        "fixture-session",
    ))


def test_multi_invalid_arguments_preserve_receipt_and_allow_correction(monkeypatch):
    _bind_owner(monkeypatch)
    dispatched = []
    monkeypatch.setattr(dashboard, "audit_managed_avatar_multi_screenshot_sync",
                        lambda request, **kw: dispatched.append(request.capture_receipt) or {"ok": True})
    failure = dashboard.audit_managed_avatar_multi_screenshot_for_agent({
        "captureReceipt": "fixture-capability", "prompt": "Inspect clothing fit"})
    assert not dispatched
    assert failure["errorCode"] == "invalid_tool_arguments"
    assert failure["captureReceiptConsumed"] is False
    assert failure["failurePhase"] == "argument_validation"
    assert failure["retryable"] is True
    assert "prompt" in failure["error"]
    steps = [
        {"tool": "vrcforge_capture_multi_screenshot", "status": "applied",
         "result": {"captureReceipt": "fixture-capability"}},
        {"tool": "vrcforge_vision_audit_multi", "status": "failed", "result": failure},
    ]
    assert managed_multi_capture_receipt(steps) == "fixture-capability"
    assert managed_multi_visual_audit_consumed_without_retry(steps) is False
    assert dashboard.audit_managed_avatar_multi_screenshot_for_agent({"captureReceipt": "fixture-capability"})["ok"] is True
    assert dispatched == ["fixture-capability"]


def test_single_invalid_arguments_do_not_dispatch(monkeypatch):
    dispatched = []
    monkeypatch.setattr(dashboard, "audit_avatar_screenshot_sync", lambda request: dispatched.append(request))
    failure = dashboard.audit_latest_managed_screenshot_for_agent({"prompt": "Inspect clothing fit"})
    assert not dispatched
    assert failure["errorCode"] == "invalid_tool_arguments"
    assert failure["retryable"] is True
    assert "prompt" in failure["error"]


@pytest.mark.parametrize("failure", [
    {"ok": False, "error": "ValidationError prompt extra_forbidden"},
    {"ok": False, "captureReceiptConsumed": False},
    {"ok": False, "failurePhase": "argument_validation"},
    {"ok": False, "captureReceiptConsumed": True, "failurePhase": "argument_validation"},
])
def test_unknown_or_consumed_audit_cannot_replay_original_receipt(failure):
    steps = [
        {"tool": "vrcforge_capture_multi_screenshot", "status": "applied", "result": {"captureReceipt": "original"}},
        {"tool": "vrcforge_vision_audit_multi", "status": "failed", "result": failure},
    ]
    assert managed_multi_capture_receipt(steps) == ""
    assert managed_multi_visual_audit_consumed_without_retry(steps) is True


@pytest.mark.parametrize("multi", [False, True])
def test_native_invalid_audit_can_correct_and_finish(tmp_path, multi):
    import json
    from tests.test_native_runtime_gateway import setup_gateway, call, finish, run

    audit_tool = "vrcforge_vision_audit_multi" if multi else "vrcforge_vision_audit"
    valid = {"captureReceipt": "fixture-capability"} if multi else {}

    def correct(request):
        observation = next(json.loads(message["content"])
                           for message in reversed(request["messages"]) if message["role"] == "tool")
        def find_id(value):
            if isinstance(value, dict):
                if str(value.get("actionId", "")).startswith("planner_validation_"):
                    return value["actionId"]
                for item in value.values():
                    found = find_id(item)
                    if found:
                        return found
            if isinstance(value, list):
                for item in value:
                    found = find_id(item)
                    if found:
                        return found
        action_id = find_id(observation)
        assert action_id, observation
        return call("correct-audit", "vrcforge_runtime_action", {
            "action": "correct", "correction_for_action_id": action_id,
            "tool": audit_tool, "arguments": valid,
        })

    replies = [call("capture", "vrcforge_capture_multi_screenshot", {})] if multi else []
    replies += [call("invalid-audit", audit_tool, {**valid, "prompt": "Inspect clothing fit"}), correct, finish]
    gateway, model, _ = setup_gateway(tmp_path, replies)
    # The generic native fixture omits input schemas; bind the production shared descriptor.
    from dataclasses import replace
    catalog = gateway.runtime_planner._catalog
    fixture_tool = catalog._tool
    catalog._tool = lambda value: replace(fixture_tool(value), block="core", input_schema=
        gateway.shared_agent_tool_descriptor(value.name, write=False, exposure_layer="execution")["inputSchema"]) if value.name == audit_tool else fixture_tool(value)
    fixture_read = catalog.read
    def read_catalog(*args, **kwargs):
        snapshot = fixture_read(*args, **kwargs)
        # Production exposes task-internal vision to Runtime, not external MCP.
        if multi:
            internal = gateway._tools[audit_tool]
            if gateway._tool_runtime_visible(internal, gateway.ensure_config()):
                snapshot = replace(snapshot, visible_tools=(*snapshot.visible_tools, catalog._tool(internal)))
        return snapshot
    catalog.read = read_catalog
    dispatched = []
    if multi:
        gateway.register_tool("vrcforge_capture_multi_screenshot", "When to use: capture. When NOT to use: audit.",
                              "read/debug", lambda params: {"ok": True, "captureReceipt": "fixture-capability"})
    gateway.register_tool(audit_tool, "When to use: audit. When NOT to use: capture.",
                          "read/debug", lambda params: dispatched.append(params) or {"ok": True, "audit": {"status": "pass"}})
    result = run(gateway, tmp_path, message="Audit the latest managed screenshot")
    assert len(dispatched) == 1, str((result["plan"], result["steps"]))
    assert "prompt" not in dispatched[0]
    assert len(model.requests) == 3
    if multi:
        # This fixture proves corrected dispatch, not fabricated visual acceptance.
        assert result["plan"]["nextStep"] == "needs_user_action"
        assert result["steps"][-1]["tool"] == audit_tool
        assert result["steps"][-1]["outcome"]["verification"]["state"] == "needs_user_action"
    else:
        assert result["plan"]["nextStep"] == "done"
