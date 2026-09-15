from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
import dashboard_server


NAMES = {
    "vrcforge_list_directory",
    "vrcforge_read_text_file",
    "vrcforge_find_files",
    "vrcforge_search_text",
}


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    for name in NAMES:
        product_tool = dashboard_server.AGENT_GATEWAY._tools[name]
        gateway.register_tool(name, product_tool.description, product_tool.category, product_tool.handler)
    return gateway


def test_external_mcp_exposes_general_read_leaf_and_binds_server_root(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    note = root / "note.txt"
    note.write_text("source evidence", encoding="utf-8")

    listed = {
        item["name"]
        for item in gateway.build_external_mcp_tools("planning", ["project"])
    }
    assert NAMES <= listed
    search_descriptor = next(
        item for item in gateway.build_external_mcp_tools("planning", ["project"])
        if item["name"] == "vrcforge_search_text"
    )
    assert "query" in search_descriptor["inputSchema"]["required"]
    descriptor = next(
        item
        for item in gateway.build_external_mcp_tools("planning", ["project"])
        if item["name"] == "vrcforge_read_text_file"
    )
    assert "projectPath" in descriptor["inputSchema"]["properties"]
    assert "absolute existing source workspace root" in descriptor["inputSchema"]["properties"]["projectPath"]["description"].lower()
    assert "relative UTF-8 text file path" in descriptor["inputSchema"]["properties"]["path"]["description"]

    result = gateway.call_external_mcp_tool(
        "vrcforge_read_text_file",
        {"projectPath": str(root.resolve()), "path": "note.txt"},
    )
    assert result["status"] == "ok"
    assert result["result"]["text"] == "source evidence"


def test_external_mcp_general_read_rejects_missing_or_forged_scope(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("evidence", encoding="utf-8")

    with pytest.raises(AgentGatewayError, match="absolute projectPath"):
        gateway.call_external_mcp_tool("vrcforge_read_text_file", {"path": "note.txt"})

    with pytest.raises(AgentGatewayError, match="server-owned"):
        gateway.call_external_mcp_tool(
            "vrcforge_read_text_file",
            {
                "projectPath": str(root.resolve()),
                "path": "note.txt",
                "_generalAllowedRoots": [str(tmp_path)],
            },
        )

    escaped = gateway.call_external_mcp_tool(
        "vrcforge_read_text_file",
        {"projectPath": str(root.resolve()), "path": "../outside.txt"},
    )
    assert escaped["status"] == "failed"


def test_external_mcp_dispatches_each_general_read_handler(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("needle", encoding="utf-8")
    args = {"projectPath": str(root.resolve())}

    listed = gateway.call_external_mcp_tool(
        "vrcforge_list_directory", {**args, "path": "."}
    )
    found = gateway.call_external_mcp_tool(
        "vrcforge_find_files", {**args, "path": ".", "pattern": "*.txt"}
    )
    searched = gateway.call_external_mcp_tool(
        "vrcforge_search_text", {**args, "path": ".", "query": "needle"}
    )
    assert listed["status"] == found["status"] == searched["status"] == "ok"
    assert listed["result"]["entries"][0]["name"] == "note.txt"
    assert found["result"]["files"][0]["name"] == "note.txt"
    assert searched["result"]["matches"][0]["text"] == "needle"
