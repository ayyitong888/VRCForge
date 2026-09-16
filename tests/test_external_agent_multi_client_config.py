from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import connector_action_status
from connector_action_status import ConnectorActionStatusStore


@pytest.fixture
def controller(tmp_path: Path, monkeypatch):
    import dashboard_server as app
    import external_agent_connector_installer as installer
    from agent_gateway import AgentGateway

    root = tmp_path / "app"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "vrcforge_agent_mcp_stdio.py").write_text("# fixture", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    generic = tmp_path / "generic.json"
    generic.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}), encoding="utf-8")
    codex = tmp_path / "config.toml"
    codex.write_text('[mcp_servers.other]\ncommand = "keep"\n', encoding="utf-8")
    monkeypatch.setattr(app.runtime_paths, "ROOT_DIR", root)
    monkeypatch.setattr(app, "AGENT_GATEWAY", AgentGateway(tmp_path / "profile" / "config.json", tmp_path / "audit"))
    monkeypatch.setattr(app, "CONNECTOR_ACTION_STATUS", ConnectorActionStatusStore())
    monkeypatch.setattr(app, "safe_agent_health", lambda: {})
    monkeypatch.setattr(app, "safe_agent_manifest", lambda: {})
    monkeypatch.setattr(app, "summarize_external_agent_audit", lambda: [])
    monkeypatch.setattr(app, "emit_log", lambda *_args: None)
    monkeypatch.setattr(installer, "codex_config_path", lambda: codex)
    monkeypatch.setattr(installer, "claude_cowork_config_path", lambda: tmp_path / "cowork.json")
    monkeypatch.setattr(installer, "deepseek_harness_config_path", lambda: tmp_path / "cordis.patch.yml")
    monkeypatch.setattr(installer, "_probe_windows_app", lambda *_args: {})
    monkeypatch.setattr(installer, "_probe_codex_cli", lambda: {})
    monkeypatch.setattr(installer, "_probe_command", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(installer, "run_stdio_mcp_handshake", lambda *_args: {
        "ok": True, "ready": True, "preflightOk": True, "preflightRuntimeOnline": True,
        "toolCount": 7, "stderrTail": ["private authentication data"],
        "transcriptTail": [{"token": "private authentication data"}],
    })

    def action(client, verb="install", path=None, selected_project=None):
        request = app.ExternalAgentConnectorActionRequest(
            client=client, projectPath=str(selected_project or project),
            configPath=str(path) if path else None,
        )
        handler = app.app_install_external_agent_connector if verb == "install" else app.app_uninstall_external_agent_connector
        return handler(request)

    return app, action, project, generic, codex


def test_public_controller_installs_two_clients_and_removes_only_one(controller):
    app, action, project, generic, codex = controller
    first = action("codexApp")
    assert first["clients"]["codexApp"]["installed"]
    second = action("generic", path=generic)
    assert second["clients"]["codexApp"]["installed"]
    assert second["clients"]["generic"]["installed"]
    assert second["connectorActions"]["codexApp"]["handshake"]["ready"]
    assert second["connectorActions"]["generic"]["handshake"]["ready"]
    assert "private authentication data" not in json.dumps(second)
    assert '[mcp_servers.other]' in codex.read_text()
    assert set(tomllib.loads(codex.read_text())["mcp_servers"]) == {"other", "vrcforge"}
    assert json.loads(generic.read_text())["mcpServers"]["other"] == {"command": "keep"}
    codex_before = codex.read_bytes()
    removed = action("generic", "uninstall", generic)
    assert codex.read_bytes() == codex_before
    assert removed["clients"]["codexApp"]["installed"]
    assert not removed["clients"]["generic"]["installed"]
    assert "handshake" not in removed["connectorActions"]["generic"]


def test_failed_second_client_preserves_first_config_and_proof(controller, tmp_path):
    app, action, project, generic, codex = controller
    action("codexCli")
    before = codex.read_bytes()
    broken = tmp_path / "broken.json"
    broken.write_text('{"mcpServers":', encoding="utf-8")
    failed = action("generic", path=broken)
    assert not failed["lastConnectorAction"]["ok"]
    assert "json" in failed["lastConnectorAction"]["error"].lower()
    assert failed["lastConnectorAction"].get("suggestion")
    assert codex.read_bytes() == before
    assert failed["clients"]["codexCli"]["installed"]
    assert failed["connectorActions"]["codexCli"]["handshake"]["ready"]
    assert broken.read_text() == '{"mcpServers":'


def test_alias_uninstall_invalidates_app_and_cli_proof(controller):
    app, action, project, generic, codex = controller
    action("codexApp")
    removed = action("codexCli", "uninstall")
    for name in ("codexApp", "codexCli"):
        assert not removed["clients"][name]["installed"]
        assert removed["connectorActions"][name]["action"] == "uninstall"
        assert "handshake" not in removed["connectorActions"][name]


def test_actions_are_scoped_to_project_config_and_profile_and_file_hash(controller, tmp_path):
    app, action, project, generic, codex = controller
    action("generic", path=generic)
    other = tmp_path / "other.json"
    assert "generic" not in app.external_agent_status_sync(str(project), str(other))["connectorActions"]
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    assert app.external_agent_status_sync(str(other_project), str(generic))["connectorActions"] == {}
    previous = app.AGENT_GATEWAY.config_path
    app.AGENT_GATEWAY.config_path = tmp_path / "other-profile.json"
    assert app.external_agent_status_sync(str(project), str(generic))["connectorActions"] == {}
    app.AGENT_GATEWAY.config_path = previous
    generic.write_text(generic.read_text() + "\n", encoding="utf-8")
    assert "generic" not in app.external_agent_status_sync(str(project), str(generic))["connectorActions"]


def test_selftest_expires_and_restart_has_no_proof(controller, monkeypatch):
    app, action, project, generic, codex = controller
    installed = action("codexApp")
    expiry = installed["connectorActions"]["codexApp"]["verificationExpiresAt"]
    monkeypatch.setattr(connector_action_status.time, "time", lambda: expiry + 1)
    status = app.external_agent_status_sync(str(project))
    assert status["clients"]["codexApp"]["installed"]
    assert "handshake" not in status["connectorActions"]["codexApp"]
    app.CONNECTOR_ACTION_STATUS = ConnectorActionStatusStore()
    assert app.external_agent_status_sync(str(project))["connectorActions"] == {}


@pytest.fixture
def profile_auth_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            accepted = self.headers.get("Authorization") == "Bearer active-profile-fixture"
            self.send_response(200 if accepted else 401)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": accepted}).encode())

        def log_message(self, *_args):
            pass

    # Test-owned read-only loopback listener, fixture-token auth; closed below.
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.parametrize("packaged", [False, True])
def test_client_configs_pin_active_gateway_profile_without_credentials(controller, packaged, tmp_path, profile_auth_server):
    import hashlib
    import os
    import subprocess
    import sys

    app, action, project, generic, codex = controller
    if packaged:
        backend = app.runtime_paths.ROOT_DIR / "backend" / "vrcforge_backend.exe"
        backend.parent.mkdir()
        backend.write_bytes(b"fixture executable")
    active = app.AGENT_GATEWAY.config_path.resolve()
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps({"token": "active-profile-fixture"}), encoding="utf-8")
    default = tmp_path / "default-local-app-data" / "VRCForge" / "agentic-app" / "config" / "agent_gateway.json"
    default.parent.mkdir(parents=True)
    default.write_text(json.dumps({"token": "wrong-profile-fixture"}), encoding="utf-8")
    action("codexApp")
    action("generic", path=generic)
    configs = [tomllib.loads(codex.read_text())["mcp_servers"]["vrcforge"],
               json.loads(generic.read_text())["mcpServers"]["vrcforge"]]
    bundle = app.connector_bundle_sync()["clientConfigs"]
    configs.extend(bundle[name]["config"]["mcpServers"]["vrcforge"]
                   for name in ("claudeCodeStdio", "claudeCowork", "generic"))
    configs.append(bundle["codexStdio"]["config"]["mcp_servers"]["vrcforge"])
    configs.append(bundle["deepseekHarness"]["config"][0]["insert"][0]["config"])
    for config in configs:
        assert config["args"][1:4] == ["--no-start", "--config", str(active)]
        assert "active-profile-fixture" not in json.dumps(config)
        assert not config.get("env")

    # Fresh process uses the real bridge parser/config reader without inheriting
    # App profile/token variables. Only non-secret config paths cross the boundary.
    env = {key: value for key, value in os.environ.items() if not key.startswith("VRCFORGE_")}
    env["LOCALAPPDATA"] = str(default.parents[3])
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    probe = """
import hashlib, json
from pathlib import Path
from tools.vrcforge_agent_mcp_stdio import parse_args, VRCForgeBridge
args = parse_args()
bridge = VRCForgeBridge(base_url=args.base_url, config_path=Path(args.config) if args.config else None,
                        timeout_seconds=2, start_runtime=False)
print(json.dumps({'configPath': str(bridge.resolve_config_path()),
                  'identityHash': hashlib.sha256(bridge.require_token().encode()).hexdigest(),
                  'auth': bridge.request_json('GET', '/profile-probe', token=bridge.require_token())}))
"""
    for config in configs[:2]:
        command = [sys.executable, "-c", probe, *config["args"][1:], "--base-url", profile_auth_server]
        result = subprocess.run(command,
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=True)
        identity = json.loads(result.stdout)
        assert identity["configPath"] == str(active)
        assert identity["identityHash"] == hashlib.sha256(b"active-profile-fixture").hexdigest()
        assert identity["auth"]["ok"]
        # Removing only the fix reproduces the fresh-client 401 using the same
        # bridge and endpoint, despite an existing default-profile credential.
        config_index = command.index("--config")
        del command[config_index:config_index + 2]
        previous = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                                  text=True, timeout=15, check=True)
        assert json.loads(previous.stdout)["auth"]["status"] == 401
