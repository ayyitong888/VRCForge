"""STDIO bridge startup must not load the HTTP Gateway Resource registry."""

from pathlib import Path
import json
import os
import subprocess
import sys

import agent_gateway


def test_stdio_gateway_skips_resource_registry_construction(tmp_path, monkeypatch):
    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("STDIO startup must not construct McpResourceRegistry")

    monkeypatch.setattr(agent_gateway, "McpResourceRegistry", fail_if_constructed)

    gateway = agent_gateway.AgentGateway(
        Path(tmp_path) / "config.json",
        Path(tmp_path) / "audit",
        load_mcp_resources=False,
    )

    assert gateway._mcp_resources is None


def test_stdio_preflight_entrypoint_skips_registry_load(tmp_path):
    # This child belongs to the test (30 seconds), with only temporary config
    # and no credential; preflight must stop before any HTTP or Unity call.
    config = tmp_path / "gateway.json"
    config.write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env.pop("VRCFORGE_AGENT_TOKEN", None)
    env.update(
        VRCFORGE_AGENT_GATEWAY_CONFIG=str(config),
        VRCFORGE_USER_DATA_DIR=str(tmp_path / "user-data"),
        VRCFORGE_CONFIG_DIR=str(tmp_path / "config"),
    )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "dashboard_server.py"),
            "--agent-mcp-stdio",
            "--preflight",
            "--no-start",
            "--protocol-profile",
            "mcp-1x",
            "--exposure-layer",
            "execution",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["status"] == "gateway_token_missing"
    assert report["commitState"] == "not_started"
    assert "Resource registry is busy or unavailable" not in result.stderr
