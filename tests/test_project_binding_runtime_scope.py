from __future__ import annotations

import pytest

from agent_gateway import (
    AgentGatewayError,
    UNITY_READ_TOOL_INPUT_SCHEMAS,
    bind_runtime_unity_project,
    canonical_unity_read_tool_input_schema,
)


def test_optional_unity_read_is_bound_to_runtime_project() -> None:
    schema = canonical_unity_read_tool_input_schema("vrcforge_scan_fx_animator")
    bound = bind_runtime_unity_project(schema, {"avatarPath": "Avatar"}, r"D:\Projects\A")
    assert bound["projectPath"] == r"D:\Projects\A"


def test_explicit_unity_project_mismatch_is_rejected() -> None:
    schema = canonical_unity_read_tool_input_schema("vrcforge_scan_fx_animator")
    with pytest.raises(AgentGatewayError, match="does not match"):
        bind_runtime_unity_project(
            schema,
            {"projectPath": r"D:\Projects\B"},
            r"D:\Projects\A",
        )


def test_non_project_tool_arguments_are_unchanged() -> None:
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    arguments = {"value": "kept"}
    assert bind_runtime_unity_project(schema, arguments, r"D:\Projects\A") == arguments


def test_optional_project_read_schemas_are_known_and_project_bindable() -> None:
    optional = [
        name
        for name, schema in UNITY_READ_TOOL_INPUT_SCHEMAS.items()
        if "projectPath" in (schema.get("properties") or {})
        and "projectPath" not in (schema.get("required") or [])
    ]
    assert len(optional) == 11
    for name in optional:
        bound = bind_runtime_unity_project(
            canonical_unity_read_tool_input_schema(name), {}, r"D:\Projects\A"
        )
        assert bound["projectPath"] == r"D:\Projects\A"


def test_external_optional_project_read_fails_closed_after_two_scopes(tmp_path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway._guard_external_mcp_project_scope(  # noqa: SLF001 - focused policy test
        "vrcforge_scan_fx_animator", {"projectPath": r"D:\Projects\A"}
    )
    gateway._guard_external_mcp_project_scope(  # noqa: SLF001
        "vrcforge_scan_fx_animator", {"projectPath": r"D:\Projects\B"}
    )
    with pytest.raises(AgentGatewayError, match="multiple Unity project scopes"):
        gateway._guard_external_mcp_project_scope("vrcforge_scan_fx_animator", {})  # noqa: SLF001


def test_internal_scopes_also_make_external_optional_read_fail_closed(tmp_path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway._register_runtime_project_scope(r"D:\Projects\A")  # noqa: SLF001
    gateway._register_runtime_project_scope(r"D:\Projects\B")  # noqa: SLF001
    with pytest.raises(AgentGatewayError, match="multiple Unity project scopes"):
        gateway._guard_external_mcp_project_scope("vrcforge_scan_fx_animator", {})  # noqa: SLF001
