from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from external_agent_connector_installer import (
    ConnectorInstallError,
    StdioBridgeSpec,
    connector_client_statuses,
    install_connector,
    run_stdio_mcp_handshake,
    uninstall_connector,
)


def make_source_root(tmp_path: Path) -> Path:
    root = tmp_path / "VRCForge"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "vrcforge_agent_mcp_stdio.py").write_text("# test bridge placeholder\n", encoding="utf-8")
    return root


def test_claude_code_install_preserves_existing_server_and_is_idempotent(tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    project = tmp_path / "Unity Project"
    project.mkdir()
    config = project / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node", "args": ["server.js"]}}}, indent=2),
        encoding="utf-8",
    )

    first = install_connector("claudeCode", root_dir=root, project_path=str(project), run_self_test=False)
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["vrcforge"]

    assert first["ok"] is True
    assert first["changed"] is True
    assert payload["mcpServers"]["existing"]["command"] == "node"
    assert server["command"] == sys.executable
    assert server["args"] == [str((root / "tools" / "vrcforge_agent_mcp_stdio.py").resolve())]
    assert server["env"] == {}

    second = install_connector("claudeCode", root_dir=root, project_path=str(project), run_self_test=False)

    assert second["ok"] is True
    assert second["changed"] is False
    assert second["backupPath"] == ""


def test_claude_cowork_install_missing_config_then_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    appdata = tmp_path / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))

    installed = install_connector("claudeCowork", root_dir=root, run_self_test=False)
    config = appdata / "Claude" / "claude_desktop_config.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["vrcforge"]

    assert installed["ok"] is True
    assert installed["changed"] is True
    assert installed["restartRequired"] is True
    assert server["type"] == "sdk"
    assert server["command"] == sys.executable

    removed = uninstall_connector("claudeCowork")
    payload_after = json.loads(config.read_text(encoding="utf-8"))

    assert removed["ok"] is True
    assert removed["removed"] is True
    assert "vrcforge" not in payload_after["mcpServers"]


def test_invalid_claude_json_is_rejected_without_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    appdata = tmp_path / "AppData" / "Roaming"
    config = appdata / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    with pytest.raises(ConnectorInstallError) as error:
        install_connector("claudeCowork", root_dir=root, run_self_test=False)

    assert error.value.stage == "parse_config"
    assert config.read_text(encoding="utf-8") == "{not json"
    assert not list(config.parent.glob("*.vrcforge-backup-*"))


def test_codex_app_and_cli_share_safe_toml_install_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text(
        "\n".join(
            [
                'model = "gpt-5"',
                "",
                "[mcp_servers.other]",
                'command = "node"',
                'args = ["server.js"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    app_install = install_connector("codexApp", root_dir=root, run_self_test=False)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    server = parsed["mcp_servers"]["vrcforge"]

    assert app_install["ok"] is True
    assert app_install["changed"] is True
    assert parsed["mcp_servers"]["other"]["command"] == "node"
    assert server["command"] == sys.executable
    assert server["args"] == [str((root / "tools" / "vrcforge_agent_mcp_stdio.py").resolve())]
    assert server["cwd"] == str(root.resolve())

    cli_install = install_connector("codexCli", root_dir=root, run_self_test=False)
    assert cli_install["ok"] is True
    assert cli_install["changed"] is False

    removed = uninstall_connector("codexCli")
    parsed_after = tomllib.loads(config.read_text(encoding="utf-8"))

    assert removed["ok"] is True
    assert removed["removed"] is True
    assert "vrcforge" not in parsed_after["mcp_servers"]
    assert parsed_after["mcp_servers"]["other"]["command"] == "node"


def test_invalid_codex_toml_is_rejected_without_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text("[mcp_servers.vrcforge\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    with pytest.raises(ConnectorInstallError) as error:
        install_connector("codexApp", root_dir=root, run_self_test=False)

    assert error.value.stage == "parse_config"
    assert config.read_text(encoding="utf-8") == "[mcp_servers.vrcforge\n"
    assert not list(codex_home.glob("*.vrcforge-backup-*"))


def test_stdio_mcp_handshake_runs_initialize_and_tools_list(tmp_path: Path) -> None:
    server = tmp_path / "mock_mcp.py"
    server.write_text(
        "\n".join(
            [
                "import json, sys",
                "for line in sys.stdin:",
                "    msg = json.loads(line)",
                "    if msg.get('method') == 'initialize':",
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'mock','version':'0'}}}), flush=True)",
                "    elif msg.get('method') == 'tools/list':",
                "        tools = [{'name':'vrcforge_bridge_preflight'}, {'name':'vrcforge_request_apply'}]",
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'tools':tools}}), flush=True)",
            ]
        ),
        encoding="utf-8",
    )
    bridge = StdioBridgeSpec(command=sys.executable, args=[str(server)], cwd=str(tmp_path), packaged=False, source="test")

    result = run_stdio_mcp_handshake(bridge, timeout_seconds=5)

    assert result["ok"] is True
    assert result["connected"] is True
    assert result["ready"] is True
    assert result["toolCount"] == 2


def test_stdio_mcp_handshake_reports_missing_mcp_dependency(tmp_path: Path) -> None:
    server = tmp_path / "broken_bridge.py"
    server.write_text("import sys\nsys.stderr.write(\"ModuleNotFoundError: No module named 'mcp'\\n\")\nsys.exit(1)\n", encoding="utf-8")
    bridge = StdioBridgeSpec(command=sys.executable, args=[str(server)], cwd=str(tmp_path), packaged=False, source="test")

    result = run_stdio_mcp_handshake(bridge, timeout_seconds=5)

    assert result["ok"] is False
    assert "No module named 'mcp'" in result["error"]
    assert "packaged VRCForge" in result["suggestion"]


def test_connector_statuses_are_split_by_app_and_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    project = tmp_path / "Unity Project"
    project.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    statuses = connector_client_statuses(root_dir=root, project_path=str(project))

    assert set(statuses) == {"codexApp", "codexCli", "claudeCode", "claudeCowork"}
    assert statuses["codexApp"]["label"] == "Codex App"
    assert statuses["codexCli"]["label"] == "Codex CLI"
    assert statuses["claudeCode"]["scope"] == "project"
    assert statuses["claudeCowork"]["scope"] == "user"
