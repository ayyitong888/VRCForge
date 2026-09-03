from types import SimpleNamespace

import agent_gateway
import dashboard_server


def test_bootstrap_tools_are_shared_core_reads_with_strict_public_schemas() -> None:
    names = {
        "vrcforge_list_execution_targets",
        "vrcforge_bind_execution_target",
        "vrcforge_refresh_execution_target",
    }
    assert names <= agent_gateway.EXTERNAL_MCP_READ_TOOL_BLOCKS["core"]
    assert names <= set(dashboard_server.AGENT_GATEWAY._tools)

    get_property = agent_gateway.canonical_unity_read_tool_input_schema("vrcforge_get_property")
    assert "executionTarget" in get_property["required"]
    assert "executionTarget" in get_property["properties"]
    assert "executionTarget" not in agent_gateway.canonical_unity_read_tool_input_schema(
        "vrcforge_list_execution_targets"
    )["properties"]

    for name, schema in agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.items():
        if "projectPath" not in schema.get("properties", {}):
            continue
        projected = agent_gateway.canonical_unity_write_tool_input_schema(name)
        assert "executionTarget" in projected["properties"]
        assert "executionTarget" in projected["required"]


def test_list_execution_targets_accepts_one_core_issued_target(monkeypatch) -> None:
    target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": "project",
        "namespace": "vrcforge://projects/abc",
        "project": {"root": "D:/Unity/Avatar", "projectId": "abc"},
        "editor": {"unityPid": 123, "processStartTime": "1.0", "coreInstanceId": "core"},
    }
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, tool, arguments: SimpleNamespace(
            payload={"structuredContent": {"success": True, "data": target}}
        ),
    )
    result = dashboard_server.list_execution_targets_sync(
        {"projectPath": "D:/Unity/Avatar", "scope": "project"}
    )
    assert result["candidateCount"] == 1
    assert result["targets"] == [target]


def test_bind_requires_exactly_one_candidate_and_uses_same_gateway_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "list_execution_targets_sync",
        lambda _params: {"targets": [{"scope": "project"}], "projectPath": "D:/Unity/Avatar"},
    )
    observed = {}

    def bind(target, *, project_root):
        observed.update({"target": target, "projectRoot": project_root})
        return {"executionTargetHandle": "vrcforge-target-test", "executionTarget": target}

    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "bind_execution_target", bind)
    result = dashboard_server.bind_execution_target_sync(
        {"projectPath": "D:/Unity/Avatar", "scope": "project"}
    )
    assert result["status"] == "bound"
    assert observed == {"target": {"scope": "project"}, "projectRoot": "D:/Unity/Avatar"}
