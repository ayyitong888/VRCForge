from __future__ import annotations

import importlib.util
import json
import subprocess
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any


def load_smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_external_agent_bridge.py"
    spec = importlib.util.spec_from_file_location("smoke_external_agent_bridge", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_codex_cli_path_from_config_accepts_quoted_value(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                "model = 'gpt-5'",
                "CODEX_CLI_PATH = 'C:\\\\Users\\\\xiao123\\\\AppData\\\\Local\\\\OpenAI\\\\Codex\\\\bin\\\\abc\\\\codex.exe'",
                "other = true",
            ]
        ),
        encoding="utf-8",
    )

    assert smoke.read_codex_cli_path_from_config(config) == "C:\\\\Users\\\\xiao123\\\\AppData\\\\Local\\\\OpenAI\\\\Codex\\\\bin\\\\abc\\\\codex.exe"


def test_probe_codex_cli_prefers_codex_config_path(monkeypatch: Any, tmp_path: Path) -> None:
    smoke = load_smoke_module()
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    configured_cli = tmp_path / "OpenAI" / "Codex" / "bin" / "real" / "codex.exe"
    configured_cli.parent.mkdir(parents=True)
    (codex_home / "config.toml").write_text(f"CODEX_CLI_PATH = '{configured_cli}'\n", encoding="utf-8")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "C:\\WindowsApps\\codex.exe" if name == "codex" else None)

    def fake_probe(command: list[str], source: str = "PATH") -> dict[str, Any]:
        path = command[0]
        ok = path == str(configured_cli)
        return {
            "found": True,
            "path": path,
            "source": source,
            "ok": ok,
            "stdout": "codex-cli 0.test" if ok else "",
            "stderr": "",
            "error": "" if ok else "Access is denied",
        }

    monkeypatch.setattr(smoke, "probe_command", fake_probe)

    result = smoke.probe_codex_cli()

    assert result["ok"] is True
    assert result["path"] == str(configured_cli)
    assert result["source"] == f"config:{codex_home / 'config.toml'}"
    assert result["preferredConfiguredCli"] is True
    assert [attempt["source"] for attempt in result["attempts"]] == [
        f"config:{codex_home / 'config.toml'}",
        "PATH",
    ]


def make_bridge_smoke(smoke: ModuleType, tmp_path: Path) -> Any:
    gateway_config = tmp_path / "agent_gateway.json"
    app_token = tmp_path / "app-session-token"
    gateway_config.write_text('{"token":"gateway-token"}', encoding="utf-8")
    app_token.write_text("app-token", encoding="utf-8")
    args = Namespace(
        base_url="http://127.0.0.1:8782",
        gateway_config=str(gateway_config),
        app_token_file=str(app_token),
        project_root="",
        live_write_rollback=False,
        optimizer_write_request=False,
        optimizer_tool="vrcforge_optimization_lac_apply_request",
        avatar_path="",
        target_profile="pc_conservative",
        execution_mode="approval",
        optimizer_option=[],
        material=[],
        renderer_path="",
        relative_vertex_count=None,
        install_missing_dependencies=False,
        include_prerelease=False,
        enable_gateway=False,
        timeout=30.0,
        agent_name="test-agent",
    )
    bridge = smoke.ExternalAgentBridgeSmoke(args)
    bridge.connector_payload = {
        "launcher": {
            "stdioBridge": {
                "command": "python",
                "args": ["tools/vrcforge_agent_mcp_stdio.py", "--no-start"],
                "cwd": str(tmp_path),
                "packaged": False,
            }
        }
    }
    return bridge


def test_stdio_preflight_uses_explicit_gateway_config_env(monkeypatch: Any, tmp_path: Path) -> None:
    smoke = load_smoke_module()
    bridge = make_bridge_smoke(smoke, tmp_path)
    seen_env: dict[str, str] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen_env.update(kwargs["env"])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok":true,"runtimeOnline":true,"gatewayEnabled":true,"allowWriteRequests":true,"manifestToolCount":1,"advertisesRequestApply":true}',
            stderr="",
        )

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    result = bridge.check_stdio_bridge_preflight()

    assert result["ok"] is True
    assert seen_env["VRCFORGE_AGENT_BASE_URL"] == "http://127.0.0.1:8782"
    assert seen_env["VRCFORGE_AGENT_GATEWAY_CONFIG"] == str(bridge.gateway_config_path)


def test_stdio_mcp_tools_uses_explicit_gateway_config_env(monkeypatch: Any, tmp_path: Path) -> None:
    smoke = load_smoke_module()
    bridge = make_bridge_smoke(smoke, tmp_path)
    seen_gateway_config = ""

    def fake_handshake(spec: Any, timeout_seconds: float) -> dict[str, Any]:
        nonlocal seen_gateway_config
        seen_gateway_config = smoke.os.environ.get("VRCFORGE_AGENT_GATEWAY_CONFIG", "")
        return {"ok": True, "hasRequestApply": True, "toolCount": 1}

    monkeypatch.setattr(smoke, "run_stdio_mcp_handshake", fake_handshake)

    result = bridge.check_stdio_mcp_tools()

    assert result["ok"] is True
    assert seen_gateway_config == str(bridge.gateway_config_path)


def test_smoke_run_uses_stdio_discovery_without_legacy_http_mcp_probe(monkeypatch: Any, tmp_path: Path) -> None:
    smoke = load_smoke_module()
    bridge = make_bridge_smoke(smoke, tmp_path)
    mcp_rpc_calls: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(bridge, "client_preflight", lambda: {})
    monkeypatch.setattr(bridge, "check_runtime_health", lambda: {"ok": True})
    monkeypatch.setattr(bridge, "check_connector_config", lambda: {"ok": True})
    monkeypatch.setattr(bridge, "check_stdio_bridge_preflight", lambda: {"ok": True})
    monkeypatch.setattr(bridge, "check_stdio_mcp_tools", lambda: {"ok": True, "hasRequestApply": True})
    monkeypatch.setattr(bridge, "check_manifest", lambda: {"ok": True})

    def fail_if_called(method: str, params: dict[str, Any]) -> dict[str, Any]:
        mcp_rpc_calls.append((method, params))
        raise AssertionError("default smoke must use the strict stdio discovery check, not the legacy HTTP MCP probe")

    monkeypatch.setattr(bridge, "mcp_rpc", fail_if_called)

    report = bridge.run()

    assert report["ok"] is True
    assert [step["name"] for step in report["steps"]] == [
        "runtime.health",
        "connector.config",
        "stdio.bridge_preflight",
        "stdio.mcp_tools_list",
        "gateway.manifest",
    ]
    assert mcp_rpc_calls == []


def test_live_mcp_call_uses_strict_2026_http_contract_and_direct_tool_arguments(monkeypatch: Any, tmp_path: Path) -> None:
    smoke = load_smoke_module()
    bridge = make_bridge_smoke(smoke, tmp_path)
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}'

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(smoke.urllib.request, "urlopen", fake_urlopen)

    result = bridge.mcp_call_tool("vrcforge_get_compile_errors", {"maxErrors": 20})

    assert result == {"ok": True}
    request = captured["request"]
    body = json.loads(request.data.decode("utf-8"))
    assert body["params"]["arguments"] == {"maxErrors": 20}
    assert body["params"]["_meta"] == {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test-agent", "version": "1.4.0-smoke"},
    }
    assert request.get_header("Mcp-protocol-version") == "2026-07-28"
    assert request.get_header("Mcp-method") == "tools/call"
    assert request.get_header("Mcp-name") == "vrcforge_get_compile_errors"
    assert request.get_header("Origin") == "http://127.0.0.1:8782"
