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
