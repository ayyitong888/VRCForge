"""Real Gateway dispatch/publication; the live Core authority is an explicit fixture."""
from types import SimpleNamespace

import pytest

import agent_gateway
from agent_gateway import AgentGateway, AgentGatewayError
from execution_target import ExecutionTargetError, execution_target_digest
from mcp_resource_registry import McpResourceError
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


def _gateway(tmp_path, monkeypatch, handler, tool_name="vrcforge_scan_materials"):
    gateway = AgentGateway(tmp_path / "config/gateway.json", tmp_path / "audit")
    gateway.register_tool(tool_name, "Inspect Unity data", "unity", handler)
    monkeypatch.setattr(gateway, "ensure_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(gateway, "_external_mcp_read_tool_visible", lambda *args: True)
    return gateway


def _target(tmp_path):
    return {
        "schema": "vrcforge.execution_target.v1", "scope": "avatar",
        "namespace": "vrcforge://projects/project-a/avatars/avatar-a",
        "project": {"root": str(tmp_path), "projectId": "project-a"},
        "editor": {"unityPid": 123, "processStartTime": "1", "coreInstanceId": "core-a"},
        "scene": {"guid": "scene-a", "revision": "1", "digest": "scene-digest"},
        "avatar": {"globalObjectId": "avatar-a"},
    }


def test_optional_bound_read_publishes_verified_identity_and_digest(tmp_path, monkeypatch):
    target = _target(tmp_path)
    observed = []
    handler_arguments = []

    def verify(value, *, project_root, required_scope):
        observed.append((value, project_root, required_scope))
        return {**target, "verifiedByFixture": True}

    monkeypatch.setattr(agent_gateway, "validate_runtime_execution_target", verify)
    gateway = _gateway(
        tmp_path, monkeypatch,
        lambda arguments: handler_arguments.append(arguments) or {"ok": True, "clips": []},
        tool_name="vrcforge_scan_animation_bindings",
    )
    arguments = {"projectPath": str(tmp_path), "executionTarget": target}
    result = gateway.call_external_mcp_tool("vrcforge_scan_animation_bindings", arguments)
    gateway.publish_mcp_tool_result_resource("vrcforge_scan_animation_bindings", arguments, result, source_mode="test")
    receipt = gateway.read_mcp_resource(result["operationResource"])["structuredContent"]

    assert observed == [(target, str(tmp_path), "avatar")]
    assert handler_arguments[0]["executionTarget"]["project"]["projectId"] == "project-a"
    assert result["executionTarget"]["verifiedByFixture"] is True
    assert receipt["identity"]["projectId"] == "project-a"
    assert receipt["identity"]["coreInstanceId"] == "core-a"
    assert receipt["identity"]["avatarGlobalObjectId"] == "avatar-a"
    assert receipt["stale"] is False
    assert receipt["data"]["executionTargetDigest"] == execution_target_digest(target)


def test_stale_optional_target_is_rejected_before_the_read_handler(tmp_path, monkeypatch):
    calls = []
    gateway = _gateway(tmp_path, monkeypatch, lambda _: calls.append(True) or {"ok": True})

    def reject(*args, **kwargs):
        raise ExecutionTargetError("core_identity_mismatch", "Core changed")

    monkeypatch.setattr(agent_gateway, "validate_runtime_execution_target", reject)
    with pytest.raises(AgentGatewayError, match="identity lock rejected"):
        gateway.call_external_mcp_tool("vrcforge_scan_materials", {
            "projectPath": str(tmp_path), "executionTarget": _target(tmp_path),
        })
    assert calls == []


def test_ordinary_unbound_read_keeps_its_existing_behavior(tmp_path, monkeypatch):
    gateway = _gateway(tmp_path, monkeypatch, lambda _: {"ok": True, "materials": []})
    monkeypatch.setattr(agent_gateway, "validate_runtime_execution_target", lambda *a, **kw: pytest.fail("unexpected bind"))
    result = gateway.call_external_mcp_tool("vrcforge_scan_materials", {"projectPath": str(tmp_path)})
    assert result["ok"] is True
    assert "executionTarget" not in result


def test_unbound_read_resource_is_explicitly_unusable_as_current_identity(tmp_path):
    gateway = AgentGateway(tmp_path / "config/gateway.json", tmp_path / "audit")
    result = {"ok": True, "status": "ok", "operationId": "unbound-read", "value": 1}
    receipt = gateway.publish_mcp_tool_result_resource(
        "vrcforge_scan_animation_bindings", {"projectPath": str(tmp_path)}, result,
        source_mode="test",
    )

    assert receipt["stale"] is True
    assert receipt["staleReason"] == "execution_target_missing"
    with pytest.raises(McpResourceError, match="stale"):
        gateway._mcp_resources.validate_reference(receipt["uri"], expected_type="operation_receipt")


def test_animation_binding_schema_exposes_exact_optional_execution_target():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_animation_bindings"]
    target = schema["properties"]["executionTarget"]
    assert target["type"] == "object"
    assert "bind_execution_target" in target["description"]


@pytest.mark.parametrize(
    ("tool_name", "resource_type"),
    [
        ("vrcforge_checkpoint_diff", "checkpoint_diff"),
        ("vrcforge_scan_animator", "control_graph"),
        ("vrcforge_gesture_manager_status", "gm_runtime"),
    ],
)
def test_unbound_derived_resources_share_unknown_identity_state(tmp_path, tool_name, resource_type):
    gateway = AgentGateway(tmp_path / "config/gateway.json", tmp_path / "audit")
    result = {"ok": True, "status": "ok", "operationId": f"{resource_type}-unbound"}
    gateway.publish_mcp_tool_result_resource(tool_name, {}, result, source_mode="test")

    listed = gateway.list_mcp_resources()["resources"]
    derived = next(item for item in listed if item["_meta"]["resourceType"] == resource_type)
    assert derived["_meta"]["stale"] is True
    with pytest.raises(McpResourceError, match="stale"):
        gateway._mcp_resources.validate_reference(derived["uri"], expected_type=resource_type)


def test_optional_read_binding_does_not_replace_domain_owned_write_preparation(tmp_path, monkeypatch):
    gateway = _gateway(tmp_path, monkeypatch, lambda _: {"ok": True})
    monkeypatch.setattr(
        agent_gateway, "validate_runtime_execution_target",
        lambda *a, **kw: pytest.fail("read-only binding changed the installer preparation contract"),
    )
    assert gateway._validate_external_mcp_execution_target(
        "vrcforge_install_unity_core",
        {"projectPath": str(tmp_path), "executionTarget": _target(tmp_path)},
        write_handler=SimpleNamespace(requires_approved_execution_context=False),
    ) is None
