from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

import dashboard_server
from agent_gateway import AgentGatewayConfig
from agent_mcp_2026 import PROTOCOL_VERSION


@contextmanager
def isolated_external_vsk_gateway() -> Iterator[Path]:
    gateway = dashboard_server.AGENT_GATEWAY
    original_config_path = gateway.config_path
    original_audit_dir = gateway.audit_dir
    with TemporaryDirectory(prefix="vrcforge-external-vsk-test-") as temp_dir:
        root = Path(temp_dir)
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
            yield root
        finally:
            gateway.configure_paths(original_config_path, original_audit_dir)


def write_test_skill(root: Path, name: str = "external-vsk-roundtrip") -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "title: External VSK Roundtrip",
                "description: Exercise the external VSK package boundary.",
                "permission-mode: read_only",
                "risk-level: low",
                "allowed-tools:",
                "  - vrcforge_health",
                "entrypoint-tool: vrcforge_health",
                "---",
                "",
                "Use this temporary Skill only for the isolated VSK roundtrip test.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_dir


def tool_map(layer: str) -> dict[str, dict[str, object]]:
    return {
        str(tool["name"]): tool
        for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            layer,
            tool_blocks=["skills/vsk"],
        )
    }


def test_external_vsk_block_lazily_exposes_preview_write_import_and_state_tools() -> None:
    with isolated_external_vsk_gateway():
        planning = tool_map("planning")
        execution = tool_map("execution")
        assert set(planning) == {
            "vrcforge_preflight_skill_package",
            "vrcforge_preview_path_to_skill",
        }
        assert set(execution) == {
            "vrcforge_preflight_skill_package",
            "vrcforge_preview_path_to_skill",
            "vrcforge_import_skill_package",
            "vrcforge_export_skill_package",
            "vrcforge_set_skill_package_enabled",
            "vrcforge_write_path_to_skill",
        }

        export_schema = execution["vrcforge_export_skill_package"]["inputSchema"]
        assert export_schema["additionalProperties"] is False
        assert "privateKeyPath" in export_schema["properties"]
        assert "privateKeyPem" not in export_schema["properties"]
        state_schema = execution["vrcforge_set_skill_package_enabled"]["inputSchema"]
        assert set(state_schema["required"]) == {"skillPackageId", "enabled"}
        assert "syncProjectedSkill" not in state_schema["properties"]

        index = dashboard_server.AGENT_GATEWAY.external_mcp_tool_block_index(
            {"block": "skills/vsk"}
        )
        assert index["selectedBlock"] == "skills/vsk"
        assert index["children"][0]["block"] == "skills"
        leaf = index["children"][0]["children"][0]
        assert leaf["block"] == "skills/vsk"
        assert set(leaf["toolNames"]) == set(execution)


def test_external_vsk_export_rejects_inline_keys_and_existing_outputs_without_writing() -> None:
    with isolated_external_vsk_gateway() as root:
        write_test_skill(root)
        output = root / "blocked.vsk"
        inline = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_export_skill_package",
            {
                "skillName": "external-vsk-roundtrip",
                "outputPath": str(output),
                "release": True,
                "privateKeyPem": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----",
            },
        )
        assert inline["ok"] is False
        assert inline["errorDetails"]["errorCode"] == "vsk_inline_private_key_rejected"
        assert inline["errorDetails"]["mutationStarted"] is False
        assert inline["errorDetails"]["commitState"] == "not_started"
        assert not output.exists()

        output.write_text("keep", encoding="utf-8")
        existing = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_export_skill_package",
            {
                "skillName": "external-vsk-roundtrip",
                "outputPath": str(output),
            },
        )
        assert existing["ok"] is False
        assert existing["errorDetails"]["errorCode"] == "vsk_output_exists"
        assert existing["errorDetails"]["mutationStarted"] is False
        assert existing["errorDetails"]["commitState"] == "not_started"
        assert output.read_text(encoding="utf-8") == "keep"

        release_output = root / "release.vsk"
        key_path = root / "release-key.pem"
        key_path.write_bytes(
            dashboard_server.skill_package_service().generate_signing_keypair().private_key_pem
        )
        released = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_export_skill_package",
            {
                "skillName": "external-vsk-roundtrip",
                "outputPath": str(release_output),
                "release": True,
                "privateKeyPath": str(key_path),
            },
        )
        assert released["ok"] is True
        assert released["result"]["exported"]["signature_status"] == "signed"
        assert release_output.is_file()


def test_external_vsk_export_missing_user_skill_is_truthful_pre_route_rejection() -> None:
    with isolated_external_vsk_gateway() as root:
        output = root / "missing-user-skill.vsk"

        rejected = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_export_skill_package",
            {
                "skillName": "missing-user-skill",
                "outputPath": str(output),
            },
        )

        assert rejected["ok"] is False
        assert rejected["error"] == "User skill was not found: missing-user-skill"
        assert rejected["result"] is None
        assert rejected["errorDetails"]["errorCode"] == "vsk_user_skill_not_found"
        assert rejected["errorDetails"]["failureLayer"] == "pre_route"
        assert rejected["errorDetails"]["failurePhase"] == "user_skill_lookup"
        assert rejected["errorDetails"]["toolRoutingStarted"] is False
        assert rejected["errorDetails"]["mutationStarted"] is False
        assert rejected["errorDetails"]["committed"] is False
        assert rejected["errorDetails"]["commitState"] == "not_started"
        assert rejected["writeFailure"]["mutationStarted"] is False
        assert rejected["writeFailure"]["committed"] is False
        assert rejected["writeFailure"]["commitState"] == "not_started"
        assert not output.exists()


def test_external_vsk_dev_export_preflight_import_roundtrip_preserves_handler_results() -> None:
    with isolated_external_vsk_gateway() as root:
        write_test_skill(root)
        output = root / "roundtrip.vsk"

        exported = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_export_skill_package",
            {
                "skillName": "external-vsk-roundtrip",
                "outputPath": str(output),
            },
        )
        assert exported["ok"] is True
        assert exported["result"]["ok"] is True
        assert Path(exported["result"]["exported"]["package_path"]) == output
        assert output.is_file()

        preflight = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_preflight_skill_package",
            {"packagePath": str(output)},
        )
        assert preflight["ok"] is True
        assert preflight["result"]["ok"] is True
        package_id = preflight["result"]["preview"]["manifest"]["id"]

        imported = dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(
            "vrcforge_import_skill_package",
            {
                "packagePath": str(output),
                "source": "external-vsk-blackbox-roundtrip",
                "projectToUserSkills": False,
            },
        )
        assert imported["ok"] is True
        assert imported["result"]["ok"] is True
        assert imported["result"]["projectedSkill"] is None
        installed_path = Path(imported["result"]["imported"]["installed_path"])
        assert installed_path.is_dir()
        assert installed_path.joinpath("SKILL.md").is_file()

        registry_path = root / "skill-packages" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry["skills"][package_id]["id"] == package_id


def test_external_vsk_roundtrip_crosses_stdio_and_reads_back_an_isolated_import(monkeypatch) -> None:
    from tools import vrcforge_agent_mcp_stdio as stdio

    gateway = dashboard_server.AGENT_GATEWAY

    class IsolatedBridge:
        def preflight(self) -> dict[str, object]:
            return {"runtimeOnline": True}

        def manifest(self, layer: str, tool_blocks: list[str]) -> dict[str, object]:
            return {
                "tools": gateway.build_external_mcp_tools(
                    layer,
                    tool_blocks=tool_blocks,
                )
            }

        def call_tool(
            self,
            tool_name: str,
            arguments: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            return gateway.call_external_mcp_tool(tool_name, arguments)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        stdio,
        "run_stdio_loop",
        lambda router: captured.setdefault("router", router),
    )

    with isolated_external_vsk_gateway() as root:
        write_test_skill(root)
        package_path = root / "external-roundtrip.vsk"
        stdio.run_stdio_server(
            IsolatedBridge(),
            protocol_profile="vrcforge-2026",
            exposure_layer="execution",
        )
        router = captured["router"]
        meta = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "external-vsk-blackbox-test",
                "version": "1",
            },
        }

        def call(request_id: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
            response, status = router.handle(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"_meta": meta, "name": name, "arguments": arguments},
                }
            )
            assert status == 200
            return response["result"]["structuredContent"]

        loaded = call(1, "vrcforge_load_tool_block", {"block": "skills"})
        assert loaded["loadedBlocks"] == ["core", "skills/vsk"]

        exported = call(
            2,
            "vrcforge_export_skill_package",
            {
                "skillName": "external-vsk-roundtrip",
                "outputPath": str(package_path),
            },
        )
        assert exported["ok"] is True
        assert package_path.is_file()

        importer_root = root / "isolated-importer"
        gateway.configure_paths(
            importer_root / "config" / "agent_gateway.json",
            importer_root / "audit",
        )
        gateway.save_config(
            AgentGatewayConfig(
                enabled=True,
                require_token=False,
                allow_write_requests=True,
                execution_mode="full",
            )
        )

        preflight = call(
            3,
            "vrcforge_preflight_skill_package",
            {"packagePath": str(package_path)},
        )
        assert preflight["ok"] is True
        package_id = preflight["result"]["preview"]["manifest"]["id"]

        imported = call(
            4,
            "vrcforge_import_skill_package",
            {
                "packagePath": str(package_path),
                "source": "external-vsk-blackbox-roundtrip",
                "projectToUserSkills": False,
            },
        )
        assert imported["ok"] is True
        installed_path = Path(imported["result"]["imported"]["installed_path"])
        assert installed_path.is_relative_to(importer_root)
        assert installed_path.joinpath("SKILL.md").is_file()

        registry_path = importer_root / "skill-packages" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry["skills"][package_id]["id"] == package_id
