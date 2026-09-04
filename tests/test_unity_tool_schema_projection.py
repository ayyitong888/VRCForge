from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import agent_gateway
import unity_tool_schema_projection as projection


def test_gateway_exports_the_same_canonical_projection_functions() -> None:
    for name in ("_with_execution_target_schema", "canonical_unity_read_tool_input_schema", "canonical_unity_write_tool_input_schema"):
        assert getattr(agent_gateway, name) is getattr(projection, name)
    tree = ast.parse(Path(projection.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {
        "__future__", "copy", "typing", "unity_read_input_schemas", "unity_write_input_schemas",
    }
    assert {item.name for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names} == {"runtime_planner_service"}


def test_projection_never_mutates_shared_nested_schemas() -> None:
    source = projection.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_manage_fx_animator"]
    before = deepcopy(source)
    write = projection.canonical_unity_write_tool_input_schema("vrcforge_manage_fx_animator")
    preview = projection.canonical_unity_read_tool_input_schema("vrcforge_preview_manage_fx_animator")
    assert write == preview
    assert "executionTarget" in write["required"]
    write["properties"]["action"]["enum"].clear()
    write["required"].clear()
    assert source == before
    assert preview == projection.canonical_unity_read_tool_input_schema("vrcforge_preview_manage_fx_animator")


def test_planner_schema_fallback_remains_late_bound(monkeypatch) -> None:
    hint = {"type": "object", "properties": {"projectPath": {"type": "string"}}, "required": ["projectPath"]}
    monkeypatch.setattr(agent_gateway.planner_policy, "planner_tool_input_schema", lambda _name: hint)
    result = agent_gateway.canonical_unity_write_tool_input_schema("newly_registered_tool")
    assert result["required"] == ["projectPath", "executionTarget"]
    assert "executionTarget" not in hint["properties"]
