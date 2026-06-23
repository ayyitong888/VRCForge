from __future__ import annotations

import importlib.util
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
