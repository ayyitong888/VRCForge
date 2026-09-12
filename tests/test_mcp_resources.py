from __future__ import annotations

import json
import pytest

from agent_gateway import AgentGateway
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import LATEST_PROTOCOL_VERSION, McpStandardRouter
from mcp_resource_registry import McpResourceError, McpResourceRegistry, RESOURCE_TEMPLATES
from operation_context import ensure_operation_result


def _standard_initialize(router: McpStandardRouter) -> dict:
    return router.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "resource-test", "version": "1"},
            },
        }
    )


def _v2_request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            **(params or {}),
        },
    }


def test_registry_persists_immutable_revisions_and_never_synthesizes_unknown_reads(tmp_path) -> None:
    store = tmp_path / "resources"
    registry = McpResourceRegistry(store)
    first = registry.publish(
        base_uri="vrcforge://operation/op-1/receipt",
        name="Read receipt",
        resource_type="operation_receipt",
        data={"operationId": "op-1", "value": 1},
        identity={"projectId": "project-1"},
        source_mode="test",
        refresh_rule="Invoke again.",
    )
    unchanged = registry.publish(
        base_uri="vrcforge://operation/op-1/receipt",
        name="Read receipt",
        resource_type="operation_receipt",
        data={"operationId": "op-1", "value": 1},
        identity={"projectId": "project-1"},
        source_mode="test",
        refresh_rule="Invoke again.",
    )
    second = registry.publish(
        base_uri="vrcforge://operation/op-1/receipt",
        name="Read receipt",
        resource_type="operation_receipt",
        data={"operationId": "op-1", "value": 2},
        identity={"projectId": "project-1"},
        source_mode="test",
        refresh_rule="Invoke again.",
    )

    assert first["revision"] == unchanged["revision"] == 1
    assert second["revision"] == 2
    assert registry.read(first["uri"])["structuredContent"]["data"]["value"] == 1
    assert McpResourceRegistry(store).read(second["uri"])["structuredContent"]["data"]["value"] == 2
    try:
        registry.read("vrcforge://snapshot/scene/not-captured")
    except McpResourceError as exc:
        assert "explicitly" in str(exc)
    else:  # pragma: no cover - fail-closed invariant
        raise AssertionError("unknown resources/read must fail")


def test_validate_reference_requires_exact_revision_and_resource_type(tmp_path) -> None:
    registry = McpResourceRegistry(tmp_path / "resources")
    resource = registry.publish(
        base_uri="vrcforge://session/current/identity",
        name="Identity",
        resource_type="session_identity_lock",
        data={"status": "bound"},
        identity={"projectId": "p1"},
        source_mode="test",
        refresh_rule="Replace explicitly.",
    )

    assert registry.validate_reference(
        resource["uri"], expected_type="session_identity_lock"
    )["revision"] == 1
    stale = registry.publish(
        base_uri="vrcforge://session/stale/identity",
        name="Stale", resource_type="session_identity_lock", data={},
        identity={"projectId": "p1"}, source_mode="test", refresh_rule="Replace.", stale=True,
        stale_reason="superseded",
    )
    with pytest.raises(McpResourceError, match="stale"):
        registry.validate_reference(stale["uri"], expected_type="session_identity_lock")
    for uri, expected in (
        (resource["uri"].replace("revision=1", "revision=9"), "session_identity_lock"),
        (resource["uri"], "operation_receipt"),
        ("vrcforge://session/current/identity", "session_identity_lock"),
    ):
        try:
            registry.validate_reference(uri, expected_type=expected)
        except McpResourceError:
            pass
        else:  # pragma: no cover - fail-closed invariant
            raise AssertionError("invalid Resource reference must fail")


def test_standard_mcp_projects_native_resources_and_change_notification(tmp_path) -> None:
    registry = McpResourceRegistry(tmp_path / "resources")

    def call_tool(name, _arguments):
        registry.publish(
            base_uri="vrcforge://operation/read-1/receipt",
            name="Read receipt",
            resource_type="operation_receipt",
            data={"tool": name, "operationId": "read-1"},
            identity={},
            source_mode="external_agent",
            refresh_rule="Immutable.",
        )
        return {"ok": True, "operationId": "read-1"}

    router = McpStandardRouter(
        lambda: [{"name": "read"}],
        call_tool,
        resource_list=lambda params: registry.list(cursor=str(params.get("cursor") or "")),
        resource_templates=lambda _params: {"resourceTemplates": registry.templates()},
        resource_read=registry.read,
        resource_list_revision=lambda: registry.generation,
    )
    initialized = _standard_initialize(router)
    assert initialized["result"]["capabilities"]["resources"] == {"listChanged": True}
    templates = router.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list", "params": {}})
    assert {item["resourceType"] for item in templates["result"]["resourceTemplates"]} == {
        item["resourceType"] for item in RESOURCE_TEMPLATES
    }
    router.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read", "arguments": {}}})
    assert router.drain_notifications() == [
        {"jsonrpc": "2.0", "method": "notifications/resources/list_changed"}
    ]
    listed = router.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}})
    uri = listed["result"]["resources"][0]["uri"]
    read = router.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": uri}})
    assert json.loads(read["result"]["contents"][0]["text"])["data"]["operationId"] == "read-1"


def test_vrcforge_2026_projects_same_resource_contract(tmp_path) -> None:
    registry = McpResourceRegistry(tmp_path / "resources")
    resource = registry.publish(
        base_uri="vrcforge://catalog/tools/generation-1",
        name="Tools",
        resource_type="tool_catalog",
        data={"catalogGeneration": 1},
        identity={},
        source_mode="gateway_registry",
        refresh_rule="Registry change.",
    )
    router = Mcp2026Router(
        lambda _params: [],
        lambda _name, _arguments: {},
        resource_list=lambda params: registry.list(cursor=str(params.get("cursor") or "")),
        resource_templates=lambda _params: {"resourceTemplates": registry.templates()},
        resource_read=registry.read,
        resource_list_revision=lambda: registry.generation,
    )
    discover, status = router.handle(_v2_request("server/discover"))
    assert status == 200
    assert discover["result"]["capabilities"]["resources"] == {"listChanged": True}
    listed, status = router.handle(_v2_request("resources/list", request_id=2))
    assert status == 200
    assert listed["result"]["resources"][0]["uri"] == resource["uri"]
    read, status = router.handle(_v2_request("resources/read", {"uri": resource["uri"]}, request_id=3))
    assert status == 200
    assert read["result"]["structuredContent"]["resourceType"] == "tool_catalog"


def test_gateway_internal_and_external_agents_share_one_resource_registry(tmp_path) -> None:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    internal_result = {"requestId": "internal-1", "ok": True}
    internal = gateway.publish_mcp_tool_result_resource(
        "vrcforge_status",
        {},
        internal_result,
        source_mode="internal_agent",
    )
    external = gateway.publish_mcp_tool_result_resource(
        "vrcforge_status",
        {},
        {"operationId": "external-1", "ok": True},
        source_mode="external_agent",
    )
    listed = gateway.list_mcp_resources()
    uris = {item["uri"] for item in listed["resources"]}
    assert internal["uri"] in uris
    assert external["uri"] in uris
    assert gateway.read_mcp_resource(internal["uri"])["structuredContent"]["sourceMode"] == "internal_agent"
    assert gateway.read_mcp_resource(external["uri"])["structuredContent"]["sourceMode"] == "external_agent"
    assert internal_result["resources"]["operationReceiptUri"] == internal["uri"]


@pytest.mark.parametrize("source_mode", ["internal_agent", "external_agent"])
@pytest.mark.parametrize("succeeded", [True, False])
def test_operation_resource_resolves_to_persisted_receipt_without_inventing_state(
    tmp_path, source_mode, succeeded,
) -> None:
    config_path = tmp_path / "config" / "gateway.json"
    audit_path = tmp_path / "audit"
    gateway = AgentGateway(config_path, audit_path)
    response = ensure_operation_result(
        {
            "operationId": "property-operation",
            "ok": succeeded,
            "status": "success" if succeeded else "failed",
        },
        write=True,
    )
    receipt = gateway.publish_mcp_tool_result_resource(
        "vrcforge_set_property", {"value": "requested-only"}, response,
        source_mode=source_mode,
    )

    assert response["operationResource"] == receipt["uri"]
    assert response["operationResource"] == response["resources"]["operationReceiptUri"]
    assert response["resources"]["operationReceiptHandle"]["contentHash"] == receipt["contentHash"]
    for key in ("beforeResource", "afterResource", "diffResource"):
        assert response[key] is None

    reopened = AgentGateway(config_path, audit_path)
    captured = reopened.read_mcp_resource(response["operationResource"])["structuredContent"]
    assert captured["resourceType"] == "operation_receipt"
    assert captured["sourceMode"] == source_mode
    assert captured["data"]["operationId"] == "property-operation"
    assert captured["data"]["result"]["ok"] is succeeded
