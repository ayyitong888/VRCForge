from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

import dashboard_server
from agent_gateway import AgentGatewayConfig, render_skill_markdown
from agent_mcp_2026 import PROTOCOL_VERSION
from bundled_skill_delivery import SKILL_NAME, bundled_source_dir, deliver_bundled_guide
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
            execution_mode="full",
        )
    )
    try:
        with dashboard_server.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
            delivered = deliver_bundled_guide(
                dashboard_server.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version="1.9.1"
            )
        assert delivered["status"] == "application-owned"
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
        def preflight(_exposure_layer=None) -> dict[str, object]:
            return {"runtimeOnline": True}

        @staticmethod
        def manifest(
            layer: str,
            tool_blocks: list[str],
            _tool_names: list[str] | None = None,
        ) -> dict[str, object]:
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
                    "params": {**params, "_meta": {**meta, **params.get("_meta", {})}},
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
        assert inventory["selectedBlock"] == "project_environment/files"
        assert inventory["tree"]["name"] == "project_environment/files"
        assert inventory["tree"]["loaded"] is False

        browsed = call(20, "vrcforge_load_tool_block", {"block": "skills"})
        assert browsed["loadedBlocks"] == ["core"]
        assert installed_tools.isdisjoint({item["name"] for item in rpc(21, "tools/list", {})["tools"]})
        legacy = call(3, "vrcforge_load_tool_block", {"block": "skills", "toolNames": sorted(installed_tools)})
        assert legacy["block"] == "project_environment/files"
        assert legacy["loadedBlocks"] == ["core", "project_environment/files"]
        assert legacy["activation"]["blocks"] == ["project_environment/files"]
        assert legacy["activation"]["exposureLayer"] == exposure_layer
        assert {item["name"] for item in legacy["selectedTools"]} == installed_tools
        assert {item["name"] for item in rpc(4, "tools/list", {})["tools"]} == before
        selected = rpc(5, "tools/list", {"_meta": {"io.vrcforge/toolNames": sorted(installed_tools)}})
        assert {item["name"] for item in selected["tools"]} == installed_tools

        invocations = {item["arguments"]["toolName"]: item for item in legacy["invocationTemplates"]}

        def invoke(request_id: int, tool: str, arguments: dict[str, object]) -> dict[str, object]:
            template = invocations[tool]
            return call(request_id, template["name"], {**template["arguments"], "arguments": arguments})

        listing = invoke(7, "vrcforge_list_installed_skills", {})
        assert listing["ok"] is True
        entries = {item["name"]: item for item in listing["result"]["skills"]}
        assert {"installed-stdio-skill", SKILL_NAME} <= entries.keys()
        assert entries[SKILL_NAME]["source"] == "builtin"
        assert "instructions" not in entries["installed-stdio-skill"]

        skill = invoke(8, "vrcforge_read_installed_skill", {"name": "installed-stdio-skill"})
        assert skill["result"]["instructions"] == "Inspect before requesting supervised writes."
        document = invoke(
            9,
            "vrcforge_read_installed_skill",
            {"name": "installed-stdio-skill", "file": "references/workflow.md"},
        )
        assert document["result"]["content"] == "Read this declared workflow only."
        reference = "references/zero-start.md"
        guide = invoke(12, "vrcforge_read_installed_skill", {"name": SKILL_NAME, "file": reference})
        assert guide["result"]["content"] == (bundled_source_dir() / reference).read_text(encoding="utf-8")

        unloaded = call(10, "vrcforge_unload_tool_block", {"block": "skills/installed"})
        assert unloaded["block"] == "project_environment/files"
        assert unloaded["loadedBlocks"] == ["core"]
        stale = invoke(11, "vrcforge_list_installed_skills", {})
        assert stale["status"] == "invalid_activation_handle"
