from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from external_agent_connector_installer import install_connector


@pytest.mark.parametrize("preflight", [False, True])
def test_packaged_entry_accepts_generated_profile_args_and_forwards_to_bridge(tmp_path, monkeypatch, preflight):
    import dashboard_server
    from tools import vrcforge_agent_mcp_stdio as stdio

    root = tmp_path / "app"
    executable = root / "backend" / "vrcforge_backend.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"entry fixture")
    profile = tmp_path / "profile with spaces" / "agent_gateway.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({"token": "active-profile-fixture"}), encoding="utf-8")
    target = tmp_path / "client.json"
    install_connector("generic", root_dir=root, config_path=str(target),
                      gateway_config_path=profile, run_self_test=False)
    config = json.loads(target.read_text())["mcpServers"]["vrcforge"]
    argv = [config["command"], *config["args"]]
    if preflight:
        argv.append("--preflight")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("VRCFORGE_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("VRCFORGE_AGENT_GATEWAY_CONFIG", str(tmp_path / "wrong-profile.json"))
    monkeypatch.setattr(stdio, "configure_utf8_stdio", lambda: None)
    observed = []

    def check_bridge(bridge, **_kwargs):
        assert bridge.resolve_config_path() == profile.resolve()
        assert bridge.require_token() == "active-profile-fixture"
        assert bridge.start_runtime is False
        observed.append(True)
        return {"ok": True}

    monkeypatch.setattr(stdio, "run_stdio_server", check_bridge)
    monkeypatch.setattr(stdio.VRCForgeBridge, "preflight", check_bridge)
    # Exercise the real packaged entry's parser and dispatch, not the inner CLI.
    assert dashboard_server.main() == 0
    assert observed == [True]


def test_packaged_entry_preserves_config_environment_default(tmp_path, monkeypatch):
    import dashboard_server

    profile = tmp_path / "gateway.json"
    monkeypatch.setenv("VRCFORGE_AGENT_GATEWAY_CONFIG", str(profile))
    assert dashboard_server.parse_args(["--agent-mcp-stdio"]).config == str(profile)
