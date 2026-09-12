from __future__ import annotations

from pathlib import Path

from external_mcp_tool_blocks import EXTERNAL_MCP_WRITE_TOOL_BLOCKS
from mcp_tool_descriptor import identity_scope, standardize_tool_descriptor
from agent_gateway import AgentWriteHandler
from test_agent_gateway_integrity import _external_gateway


ROOT = Path(__file__).resolve().parents[1]


def test_flatten_names_are_public_material_tools_with_project_identity() -> None:
    assert "vrcforge_flatten_material_variant" in EXTERNAL_MCP_WRITE_TOOL_BLOCKS["materials"]
    for name in ("vrcforge_preview_material_variant_flatten", "vrcforge_flatten_material_variant"):
        assert identity_scope(name, write=name.startswith("vrcforge_flatten_")) == "project"
        descriptor = standardize_tool_descriptor({"name": name, "category": "materials"}, write=name.startswith("vrcforge_flatten_"), block="materials")
        assert descriptor["requiredIdentity"]["scope"] == "project"
        assert descriptor["requiredIdentity"]["parameterScope"]["properties"]["executionTarget"]["properties"]["scope"] == {"const": "project"}


def test_public_agent_gateway_tools_list_exposes_preview_and_write(tmp_path: Path) -> None:
    gateway = _external_gateway(tmp_path)
    gateway.register_tool(
        "vrcforge_preview_material_variant_flatten",
        "Preview flattening one exact material variant.",
        "materials/preview",
        lambda _params: {"ok": True},
    )
    gateway._write_handlers["vrcforge_flatten_material_variant"] = AgentWriteHandler(
        name="vrcforge_flatten_material_variant",
        description="When to use: flatten one exact material variant. When NOT to use: do not modify scene components. Negative example: do not flatten every material.",
        risk_level="medium",
        handler=lambda _params: {"ok": True},
    )
    gateway.register_external_mcp_unity_tool("vrcforge_flatten_material_variant", "materials")
    listed = gateway.build_external_mcp_tools("execution", tool_blocks=["materials"])
    names = {item["name"] for item in listed}
    assert {"vrcforge_preview_material_variant_flatten", "vrcforge_flatten_material_variant"} <= names
