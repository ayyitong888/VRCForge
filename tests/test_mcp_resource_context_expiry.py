from datetime import datetime, timedelta, timezone

import pytest

import mcp_resource_registry as module
from mcp_resource_registry import McpResourceError, McpResourceRegistry


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 9, 24, tzinfo=timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    monkeypatch.setattr(module, "datetime", Clock)
    return now


def publish(registry, kind):
    return registry.publish(base_uri=f"vrcforge://test/{kind}", name=kind,
                            resource_type=kind, data={"value": 1},
                            identity={"projectId": "p1"}, source_mode="test",
                            refresh_rule="Explicitly capture again.")


@pytest.mark.parametrize("kind", ["session_identity_lock", "unity_snapshot", "operation_receipt",
                                  "control_graph", "gm_runtime", "checkpoint_diff"])
def test_expired_context_rejected_but_history_remains_exact(tmp_path, clock, kind):
    registry = McpResourceRegistry(tmp_path)
    record = publish(registry, kind)
    clock[0] += timedelta(seconds=299)
    assert registry.validate_reference(record["uri"])["uri"] == record["uri"]
    clock[0] += timedelta(seconds=1)
    with pytest.raises(McpResourceError, match="expired.*recapture"):
        registry.validate_reference(record["uri"])
    assert registry.read(record["uri"])["structuredContent"] == record


@pytest.mark.parametrize("kind", ["session_identity_lock", "unity_snapshot", "control_graph", "gm_runtime"])
def test_explicit_recapture_renews_expired_state_without_rewriting_history(tmp_path, clock, kind):
    registry = McpResourceRegistry(tmp_path)
    first = publish(registry, kind)
    assert publish(registry, kind)["uri"] == first["uri"]
    clock[0] += timedelta(seconds=300)
    second = publish(registry, kind)
    assert second["revision"] == first["revision"] + 1
    assert registry.validate_reference(second["uri"])["capturedAt"] != first["capturedAt"]
    assert registry.read(first["uri"])["structuredContent"] == first


@pytest.mark.parametrize("kind", ["operation_receipt", "checkpoint_diff"])
def test_receipt_retry_does_not_refresh_old_operation_evidence(tmp_path, clock, kind):
    registry = McpResourceRegistry(tmp_path)
    first = publish(registry, kind)
    clock[0] += timedelta(seconds=300)
    assert publish(registry, kind) == first
    with pytest.raises(McpResourceError, match="expired"):
        registry.validate_reference(first["uri"])


def test_catalog_is_not_subject_to_current_context_expiry(tmp_path, clock):
    registry = McpResourceRegistry(tmp_path)
    record = publish(registry, "tool_catalog")
    clock[0] += timedelta(days=30)
    assert registry.validate_reference(record["uri"]) == record


def test_native_prompt_rejects_expired_context_while_resource_read_still_works(tmp_path, clock):
    from agent_mcp_standard import LATEST_PROTOCOL_VERSION, McpStandardRouter
    from mcp_prompt_registry import McpPromptRegistry

    registry = McpResourceRegistry(tmp_path)
    identity = publish(registry, "session_identity_lock")
    receipt = publish(registry, "operation_receipt")
    prompts = McpPromptRegistry(
        lambda: {"skills": [{"name": "inspect", "enabled": True, "available": True,
                             "source": "user", "skillType": "package",
                             "packageId": "test.inspect", "instructions": "Inspect only."}]},
        lambda _layer: [],
        resource_validate=lambda uri, kind, expected: registry.validate_reference(
            uri, expected_type=kind or None, expected_identity=expected),
    )
    router = McpStandardRouter(lambda: [], lambda *_args: {},
                               resource_read=registry.read, prompt_get=prompts.get)
    router.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "ttl-regression", "version": "1"}}})
    request = {"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {
        "name": "inspect", "arguments": {"identityLockUri": identity["uri"],
                                            "sessionContextUri": receipt["uri"]}}}
    assert "result" in router.handle(request)
    clock[0] += timedelta(seconds=300)
    rejected = router.handle(request)
    assert "expired" in rejected["error"]["message"]
    history = router.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                             "params": {"uri": receipt["uri"]}})
    assert "result" in history
    identity = publish(registry, "session_identity_lock")
    snapshot = publish(registry, "unity_snapshot")
    request["params"]["arguments"] = {"identityLockUri": identity["uri"],
                                      "sessionContextUri": snapshot["uri"]}
    assert "result" in router.handle(request)
