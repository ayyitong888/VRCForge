from __future__ import annotations

import jsonschema
import pytest

from agent_gateway import AgentGateway
from runtime_planner_service import bounded_planner_tool_schema
from sub_agent_delegate import build_sub_agent_roles


def test_delegate_subagent_registry_schema_discovers_and_validates_catalog_roles(tmp_path):
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool(
        "vrcforge_delegate_subagent",
        "Delegate one bounded read-only task.",
        "plan/preview",
        lambda _params: {"ok": True},
    )

    entry = next(
        tool
        for tool in gateway.build_tool_registry()["tools"]
        if tool["name"] == "vrcforge_delegate_subagent"
    )
    schema = entry["inputsSchema"]
    expected_role_ids = [role.id for role in build_sub_agent_roles()]

    assert "role" not in schema.get("required", [])
    assert schema["properties"]["role"]["enum"] == expected_role_ids
    assert "researcher" not in expected_role_ids
    jsonschema.validate({}, schema)
    jsonschema.validate(
        {"role": "project_index_review", "projectPath": "D:/Unity", "task": "Review the selected scope."},
        schema,
    )
    # Reproduction from the 2026-10-19 native call: the package path was only
    # mentioned in task text, so it was absent from worker params and reached
    # the role handler as an empty packagePath.
    original_failed_call = {
        "role": "outfit_package_inspection",
        "task": "Inspect the package at E:/.../SampleAvatar_AssetPack_v1.zip",
    }
    jsonschema.validate(original_failed_call, schema)

    corrected_call = {
        **original_failed_call,
        "params": {"packagePath": "E:/.../SampleAvatar_AssetPack_v1.zip"},
    }
    jsonschema.validate(corrected_call, schema)
    params_schema = schema["properties"]["params"]
    assert params_schema["properties"]["packagePath"]["type"] == "string"
    assert params_schema["properties"]["toolName"]["type"] == "string"
    assert not any(key in schema for key in ("anyOf", "oneOf", "allOf", "if", "then", "not"))
    jsonschema.validate({"role": "skill_delegate", "params": {"toolName": "vrcforge_read_text_file"}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"role": "researcher", "task": "Review the selected scope."},
            schema,
        )

    provider_schema = bounded_planner_tool_schema(schema)
    assert provider_schema["properties"]["params"]["properties"]["packagePath"]["type"] == "string"
    assert "Required in params: packagePath" in provider_schema["properties"]["role"]["description"]
    assert not any(key in provider_schema for key in ("anyOf", "oneOf", "allOf", "if", "then", "not"))
