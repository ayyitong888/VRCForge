"""Exercise registered reader schemas and production forwarding with fake I/O."""
import pytest
from jsonschema import Draft202012Validator

import dashboard_server as server


def _public_schema(name):
    tools = {tool["name"]: tool for tool in server.AGENT_GATEWAY.build_external_mcp_tools(
        "execution", tool_blocks=["*"])}
    schema = tools[name]["inputSchema"]
    assert schema == server.AGENT_GATEWAY.shared_agent_tool_descriptor(name, write=False)["inputSchema"]
    return schema


@pytest.mark.parametrize("field,value", [("outputPath", "Assets/Requested.json"), ("refreshAssets", True)])
def test_material_reader_does_not_advertise_ignored_write_options(field, value):
    schema = _public_schema("vrcforge_scan_materials")
    assert field not in schema["properties"]
    assert list(Draft202012Validator(schema).iter_errors({field: value}))
    # Neither option exists on the actual reader request model. Do not forward
    # them to Core just to match the old inaccurate public declaration.
    assert field not in {item.alias or name for name, item in server.ShaderMaterialScanRequest.model_fields.items()}


@pytest.mark.parametrize("override,effective", [(-1, 2450), (2470, 2470)])
def test_material_inspection_preserves_queue_facts_through_shared_handler_and_mcp(monkeypatch, override, effective):
    from external_mcp_result_projection import project_result
    from test_external_mcp_result_projection import protocol, URI

    facts = {
        "schema": "vrcforge.material_property_inspection.v1",
        "shaderName": "Fixture",
        "shader": {"name": "Fixture", "assetPath": "Assets/Fixture.shader", "defaultRenderQueue": 2450},
        "renderQueue": {"materialOverride": override, "effective": effective},
        "properties": [{"name": f"_Property{i}", "type": "Float", "value": i} for i in range(400)],
    }
    calls = []
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda result: result)
    def invoke(_settings, tool, arguments):
        calls.append((tool, arguments))
        return facts
    monkeypatch.setattr(server, "invoke_unity_mcp", invoke)
    result = server.inspect_material_properties_sync({"materialAssetPath": "Assets/Fixture.mat", "search": "Stencil"})
    assert result == facts
    assert calls == [("vrc_inspect_material_properties", {"materialAssetPath": "Assets/Fixture.mat", "search": "Stencil"})]
    internal = server.AGENT_GATEWAY.shared_agent_tool_descriptor("vrcforge_inspect_material_properties", write=False)
    assert "shader.defaultRenderQueue" in internal["description"]
    assert "renderQueue.materialOverride (-1 follows" in internal["description"]
    _public_schema("vrcforge_inspect_material_properties")
    receipt = {"ok": True, "operationResource": URI, "result": result}
    for transport in ("2026", "standard"):
        request, protocol_calls = protocol(transport, receipt)
        projected = request()["result"]["structuredContent"]["result"]
        assert projected["shader"] == facts["shader"]
        assert projected["renderQueue"] == facts["renderQueue"]
        assert len(protocol_calls) == 1
    assert project_result(receipt, mode="full", resource_readable=True)["result"] == facts
