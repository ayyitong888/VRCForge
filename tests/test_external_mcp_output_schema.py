"""All registered external descriptors accept their actual result presentation."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from external_mcp_result_projection import project_result, project_tool
from operation_context import ensure_operation_result
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import McpStandardRouter, LATEST_PROTOCOL_VERSION


@pytest.fixture(scope="module")
def catalogue():
    import dashboard_server as server
    tools = server.AGENT_GATEWAY.build_external_mcp_tools("execution", tool_blocks=["*"])
    assert len(tools) >= 246
    assert len({tool["name"] for tool in tools}) == len(tools)
    return tools


@pytest.mark.parametrize("mode", ["compact", "full"])
@pytest.mark.parametrize("state", ["read", "pending_write", "failed_write", "available"])
def test_entire_external_catalogue_accepts_projected_results(catalogue, mode, state):
    for tool in catalogue:
        write = state in {"pending_write", "failed_write"}
        raw = ensure_operation_result({
            "ok": state != "failed_write",
            "status": {"pending_write": "pending", "failed_write": "failed"}.get(state, "success"),
            "result": {"executionTargetRef": "target-ref", "targets": [{"ref": "target-ref"}]},
        }, write=write)
        raw["operationResource"] = "vrcforge://operation/schema-fixture/receipt?revision=1"
        if state == "available":
            raw.update(nextAction="Read the receipt.", beforeResource="vrcforge://before",
                       afterResource="vrcforge://after", diffResource="vrcforge://diff",
                       resources={"status": "available", "operationReceiptUri": raw["operationResource"]},
                       promptSkillProvenance={"skillId": "fixture", "version": "1"})
        original = deepcopy(raw)
        result = project_result(raw, mode=mode, resource_readable=True)
        for descriptor_mode in ("compact", "full"):
            descriptor = project_tool(tool, mode=descriptor_mode)
            schema = descriptor["outputSchema"]
            Draft202012Validator.check_schema(schema)
            errors = list(Draft202012Validator(schema).iter_errors(result))
            assert not errors, (tool["name"], state, mode, descriptor_mode, [e.message for e in errors])
            # Keep value constraints and real receipt facts authoritative.
            invalid = {**result, "retryable": "yes"}
            assert not Draft202012Validator(schema).is_valid(invalid)
            invalid = {key: value for key, value in result.items() if key != "operationId"}
            assert not Draft202012Validator(schema).is_valid(invalid)
        assert result["result"] == original["result"]
        assert raw == original


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_external_write_schema_accepts_returned_confirmation(mode: str) -> None:
    source_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "number"}},
    }
    tool = project_tool(
        {
            "name": "vrcforge_set_property",
            "inputSchema": source_schema,
            "_meta": {"permission": "Write"},
        },
        mode=mode,
    )
    confirmation = {
        "schema": "vrcforge.external_write_confirmation.v1",
        "operationId": "external_mcp_write_fixture",
        "targetTool": "vrcforge_set_property",
        "argumentsDigest": "a" * 64,
        "executionTargetDigest": "b" * 64,
        "planDigest": "c" * 64,
        "toolDefinitionDigest": "d" * 64,
        "expiresAt": "2026-11-04T10:00:00+00:00",
        "decisionSurface": "vrcforge_app",
    }

    Draft202012Validator(tool["inputSchema"]).validate({"value": 1, "confirmation": confirmation})
    assert "confirmation" not in source_schema["properties"]


@pytest.mark.parametrize("name", [
    "vrcforge_refresh_asset_database",
    "vrcforge_install_unity_core",
    "vrcforge_restore_unity_core",
    "vrcforge_install_vpm_package",
])
def test_reported_write_workflows_accept_confirmation_in_external_manifest(catalogue, name: str) -> None:
    descriptor = next(item for item in catalogue if item["name"] == name)
    router = Mcp2026Router(lambda _params: [descriptor], lambda _name, _arguments: {})
    listing, status = router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                               "io.modelcontextprotocol/clientCapabilities": {}}},
    })
    assert status == 200
    tool = listing["result"]["tools"][0]
    schema = tool["inputSchema"]
    arguments = {}
    for key in schema.get("required", []):
        property_schema = schema.get("properties", {}).get(key, {})
        if key == "executionTarget":
            arguments[key] = {
                "schema": "vrcforge.execution_target_ref.v1",
                "ref": "vrcforge-target-abcdefghijklmnop",
            }
        elif key in {"backupSha256", "installedSha256"}:
            arguments[key] = "a" * 64
        elif property_schema.get("const") is not None:
            arguments[key] = property_schema["const"]
        elif isinstance(property_schema.get("enum"), list):
            arguments[key] = property_schema["enum"][0]
        elif property_schema.get("type") == "string":
            arguments[key] = "D:/Unity/Avatar" if key == "projectPath" else "fixture"
        elif property_schema.get("type") == "integer":
            arguments[key] = property_schema.get("minimum", 0)
        elif property_schema.get("type") == "boolean":
            arguments[key] = False
        elif property_schema.get("type") == "object":
            arguments[key] = {}
        else:
            pytest.fail(f"No fixture value for required field {name}.{key}")
    if name == "vrcforge_install_vpm_package":
        arguments.update(packageId="jp.lilxyzw.liltoon", packageVersion="1.8.5")
    arguments["confirmation"] = {
        "schema": "vrcforge.external_write_confirmation.v1",
        "operationId": "external_mcp_write_fixture",
        "targetTool": name,
        "argumentsDigest": "a" * 64,
        "executionTargetDigest": "b" * 64,
        "planDigest": "c" * 64,
        "toolDefinitionDigest": "d" * 64,
        "expiresAt": "2026-11-04T10:00:00+00:00",
        "decisionSurface": "vrcforge_app",
    }

    assert descriptor["_meta"]["permission"] == "Write"
    Draft202012Validator(schema).validate(arguments)


@pytest.mark.parametrize("transport", ["standard", "2026"])
@pytest.mark.parametrize("name", ["vrcforge_bind_execution_target", "vrcforge_list_execution_targets"])
def test_strict_protocol_client_keeps_target_refs(catalogue, transport, name):
    descriptor = next(tool for tool in catalogue if tool["name"] == name)
    refs = {"executionTargetRef": "target-ref", "targets": [{"ref": "target-ref"}]}
    receipt = ensure_operation_result({"ok": True, "result": refs}, write=False)
    receipt["operationResource"] = "vrcforge://operation/strict-target-fixture"
    calls = []

    def call(tool_name, arguments):
        calls.append((tool_name, arguments))
        return deepcopy(receipt)

    cls = McpStandardRouter if transport == "standard" else Mcp2026Router
    router = cls(lambda *_: [descriptor], call, resource_read=lambda _: {})
    if transport == "standard":
        router.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "strict-contract-test", "version": "1"}}})
    meta = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {}}

    def request(method, **params):
        reply = router.handle({"jsonrpc": "2.0", "id": 1, "method": method,
                               "params": {"_meta": meta, **params}})
        return (reply[0] if transport == "2026" else reply)["result"]

    schema = request("tools/list")["tools"][0]["outputSchema"]
    reply = request("tools/call", name=name, arguments={})["structuredContent"]
    Draft202012Validator(schema).validate(reply)
    assert reply["result"] == refs
    assert calls == [(name, {})]
