from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

import dashboard_server
from agent_gateway import AgentGatewayConfig, render_skill_markdown
from agent_mcp_2026 import PROTOCOL_VERSION
from tools import vrcforge_agent_mcp_stdio as stdio


@contextmanager
def isolated_gateway(root: Path) -> Iterator[None]:
    gateway = dashboard_server.AGENT_GATEWAY
    original_config = gateway.config_path
    original_audit = gateway.audit_dir
    gateway.configure_paths(root / "config" / "agent_gateway.json", root / "audit")
    gateway.save_config(
        AgentGatewayConfig(
            enabled=True,
            require_token=False,
            allow_write_requests=True,
            execution_mode="full",
        )
    )
    try:
        skill_dir = root / "skills" / "installed-stdio-skill"
        support_file = skill_dir / "references" / "workflow.md"
        support_file.parent.mkdir(parents=True)
        support_file.write_text("Read this declared workflow only.", encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(
            render_skill_markdown(
                {
                    "name": "installed-stdio-skill",
                    "title": "Installed STDIO Skill",
                    "description": "Exercise shared installed Skill discovery.",
                    "permissionMode": "approval_required",
                    "riskLevel": "high",
                    "allowedTools": ["vrcforge_health"],
                    "supportFiles": ["references/workflow.md"],
                    "enabled": True,
                    "instructions": "Inspect before requesting supervised writes.",
                }
            ),
            encoding="utf-8",
        )
        yield
    finally:
        gateway.configure_paths(original_config, original_audit)


@pytest.mark.parametrize("exposure_layer", ["planning", "execution"])
def test_installed_skill_stdio_branch_loads_only_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exposure_layer: str,
) -> None:
    gateway = dashboard_server.AGENT_GATEWAY

    class GatewayBridge:
        @staticmethod
        def preflight() -> dict[str, object]:
            return {"runtimeOnline": True}

        @staticmethod
        def manifest(layer: str, tool_blocks: list[str]) -> dict[str, object]:
            return {
                "tools": gateway.build_external_mcp_tools(layer, tool_blocks=tool_blocks)
            }

        @staticmethod
        def call_tool(
            name: str,
            arguments: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            return gateway.call_external_mcp_tool(name, arguments)

    captured: dict[str, object] = {}
    monkeypatch.setattr(stdio, "run_stdio_loop", lambda router: captured.setdefault("router", router))

    with isolated_gateway(tmp_path):
        stdio.run_stdio_server(
            GatewayBridge(),
            protocol_profile="vrcforge-2026",
            exposure_layer=exposure_layer,
        )
        router = captured["router"]
        meta = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "installed-stdio-test", "version": "1"},
        }

        def rpc(request_id: int, method: str, params: dict[str, object]) -> dict[str, object]:
            response, status = router.handle(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": {"_meta": meta, **params},
                }
            )
            assert status == 200
            return response["result"]

        def call(request_id: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
            return rpc(
                request_id,
                "tools/call",
                {"name": name, "arguments": arguments},
            )["structuredContent"]

        installed_tools = {"vrcforge_list_installed_skills", "vrcforge_read_installed_skill"}
        before = {item["name"] for item in rpc(1, "tools/list", {})["tools"]}
        assert installed_tools.isdisjoint(before)

        inventory = call(2, "vrcforge_list_tool_blocks", {"block": "skills"})
        skill_branch = inventory["tree"]["children"][0]
        children = {item["name"]: item for item in skill_branch["children"]}
        assert set(children) == {"skills/vsk", "skills/installed"}
        assert children["skills/vsk"]["index"] == "11.1"
        assert children["skills/installed"]["index"] == "11.2"

        legacy = call(3, "vrcforge_load_tool_block", {"block": "skills"})
        assert legacy["loadedBlocks"] == ["core", "skills/vsk"]
        still_hidden = {item["name"] for item in rpc(4, "tools/list", {})["tools"]}
        assert installed_tools.isdisjoint(still_hidden)

        loaded = call(5, "vrcforge_load_tool_block", {"block": "11.2"})
        assert loaded["block"] == "skills/installed"
        after = {item["name"] for item in rpc(6, "tools/list", {})["tools"]}
        assert installed_tools <= after

        listing = call(7, "vrcforge_list_installed_skills", {})
        assert listing["ok"] is True
        assert [item["name"] for item in listing["result"]["skills"]] == [
            "installed-stdio-skill"
        ]
        assert "instructions" not in listing["result"]["skills"][0]

        skill = call(8, "vrcforge_read_installed_skill", {"name": "installed-stdio-skill"})
        assert skill["result"]["instructions"] == "Inspect before requesting supervised writes."
        document = call(
            9,
            "vrcforge_read_installed_skill",
            {"name": "installed-stdio-skill", "file": "references/workflow.md"},
        )
        assert document["result"]["content"] == "Read this declared workflow only."

        unloaded = call(10, "vrcforge_unload_tool_block", {"block": "skills/installed"})
        assert unloaded["loadedBlocks"] == ["core", "skills/vsk"]
        hidden_again = {item["name"] for item in rpc(11, "tools/list", {})["tools"]}
        assert installed_tools.isdisjoint(hidden_again)
