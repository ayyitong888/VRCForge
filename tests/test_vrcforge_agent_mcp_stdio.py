from __future__ import annotations

import importlib
from pathlib import Path

from agent_mcp_2026 import PROTOCOL_VERSION


def test_find_vrcforge_executable_prefers_current_packaged_root(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    current_root = tmp_path / "Current VRCForge"
    backend = current_root / "backend" / "vrcforge_backend.exe"
    desktop = current_root / "VRCForge.exe"
    old_program_files = tmp_path / "Program Files"
    old_desktop = old_program_files / "VRCForge" / "VRCForge.exe"
    backend.parent.mkdir(parents=True)
    old_desktop.parent.mkdir(parents=True)
    backend.write_text("backend", encoding="utf-8")
    desktop.write_text("desktop", encoding="utf-8")
    old_desktop.write_text("old desktop", encoding="utf-8")

    monkeypatch.delenv("VRCFORGE_EXE", raising=False)
    monkeypatch.setenv("ProgramFiles", str(old_program_files))
    monkeypatch.setenv("ProgramFiles(x86)", "")
    monkeypatch.setattr(module.sys, "executable", str(backend))
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)

    assert module.find_vrcforge_executable() == desktop.resolve()


def test_preflight_does_not_launch_runtime_when_start_disabled(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"token":"test-token","enabled":true,"allow_write_requests":true}', encoding="utf-8")
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )

    monkeypatch.setattr(bridge, "runtime_port_open", lambda: False)

    def fail_launch() -> dict[str, object]:
        raise AssertionError("try_launch_runtime should not be called when start_runtime is false")

    def offline_request(*args, **kwargs) -> dict[str, object]:
        raise RuntimeError("runtime offline")

    monkeypatch.setattr(bridge, "try_launch_runtime", fail_launch)
    monkeypatch.setattr(bridge, "request_json", offline_request)

    report = bridge.preflight()

    assert report["ok"] is False
    assert "launch" not in report
    assert report["error"] == "runtime offline"


def test_stdio_bridge_start_runtime_is_explicit_opt_in() -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    assert module.parse_args([]).start_runtime is False
    assert module.parse_args(["--start-runtime"]).start_runtime is True
    parsed = module.parse_args(["--start-runtime", "--no-start"])
    assert parsed.start_runtime is True
    assert parsed.no_start is True


def test_stdio_bridge_protocol_profile_and_exposure_are_explicit() -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    defaults = module.parse_args([])
    assert defaults.protocol_profile == "auto"
    assert defaults.exposure_layer == "planning"

    standard = module.parse_args(["--protocol-profile", "mcp-1x", "--exposure-layer", "planning"])
    assert standard.protocol_profile == "mcp-1x"
    assert standard.exposure_layer == "planning"


def test_stdio_bridge_exposes_writes_only_in_execution_layer(monkeypatch) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    class Bridge:
        calls = []

        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, exposure_layer="planning"):
            read_tool = {
                "name": "vrcforge_read_status",
                "description": "Read status",
                "inputSchema": {"type": "object"},
            }
            tools = [read_tool]
            if exposure_layer == "execution":
                tools.append(
                    {
                        "name": "vrcforge_request_apply",
                        "description": "Request an approved write",
                        "inputSchema": {"type": "object"},
                    }
                )
            return {"tools": tools}

        def call_tool(self, tool_name, arguments, **_kwargs):
            self.calls.append((tool_name, arguments))
            return {"ok": True, "queued": tool_name}

    captured = {}
    monkeypatch.setattr(module, "run_stdio_loop", lambda router: captured.setdefault("router", router))
    module.run_stdio_server(Bridge(), protocol_profile="vrcforge-2026")
    router = captured["router"]
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "stdio-test", "version": "1.4.0"},
    }

    planning, planning_status = router.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}
    )
    execution, execution_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": meta, "exposureLayer": "execution"},
        }
    )

    assert planning_status == 200
    assert execution_status == 200
    planning_names = {tool["name"] for tool in planning["result"]["tools"]}
    execution_names = {tool["name"] for tool in execution["result"]["tools"]}
    assert "vrcforge_request_apply" not in planning_names
    assert "vrcforge_request_apply" in execution_names
    for tool in execution["result"]["tools"]:
        assert "When to use:" in tool["description"]
        assert "When NOT to use:" in tool["description"]
        assert "Negative example:" in tool["description"]

    called, called_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_request_apply",
                "arguments": {"target_tool": "vrcforge_create_gameobject"},
            },
        }
    )
    assert called_status == 200
    assert called["result"]["structuredContent"]["ok"] is True
    assert Bridge.calls == [
        ("vrcforge_request_apply", {"target_tool": "vrcforge_create_gameobject"})
    ]
