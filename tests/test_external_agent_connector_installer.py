from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from external_agent_connector_installer import (
    ConnectorInstallError,
    StdioBridgeSpec,
    _probe_appx_package,
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
    assert server["args"] == [str((root / "tools" / "vrcforge_agent_mcp_stdio.py").resolve()), "--no-start"]
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


def test_generic_install_preserves_existing_server_and_uninstalls(tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    config_dir = tmp_path / ".cursor"
    config_dir.mkdir()
    config = config_dir / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node", "args": ["server.js"]}}}, indent=2),
        encoding="utf-8",
    )

    first = install_connector("generic", root_dir=root, config_path=str(config), run_self_test=False)
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["vrcforge"]

    assert first["ok"] is True
    assert first["changed"] is True
    assert first["configPath"] == str(config.resolve())
    assert payload["mcpServers"]["existing"]["command"] == "node"
    assert server["command"] == sys.executable
    assert server["args"] == [str((root / "tools" / "vrcforge_agent_mcp_stdio.py").resolve()), "--no-start"]
    assert server["env"] == {}
    assert "type" not in server

    second = install_connector("generic", root_dir=root, config_path=str(config), run_self_test=False)
    assert second["ok"] is True
    assert second["changed"] is False

    removed = uninstall_connector("generic", config_path=str(config))
    payload_after = json.loads(config.read_text(encoding="utf-8"))
    assert removed["ok"] is True
    assert removed["removed"] is True
    assert "vrcforge" not in payload_after["mcpServers"]
    assert payload_after["mcpServers"]["existing"]["command"] == "node"


@pytest.mark.parametrize(
    "config_path",
    [
        None,
        "",
        "   ",
        "relative-or-not\nmulti-line.json",
    ],
)
def test_generic_install_rejects_missing_or_multiline_path(tmp_path: Path, config_path: str | None) -> None:
    root = make_source_root(tmp_path)

    with pytest.raises(ConnectorInstallError) as error:
        install_connector("generic", root_dir=root, config_path=config_path, run_self_test=False)

    assert error.value.stage == "resolve_config_path"


def test_generic_install_rejects_non_json_and_missing_parent(tmp_path: Path) -> None:
    root = make_source_root(tmp_path)

    with pytest.raises(ConnectorInstallError) as toml_error:
        install_connector("generic", root_dir=root, config_path=str(tmp_path / "config.toml"), run_self_test=False)
    assert toml_error.value.stage == "resolve_config_path"
    assert "JSON" in str(toml_error.value)

    with pytest.raises(ConnectorInstallError) as parent_error:
        install_connector("generic", root_dir=root, config_path=str(tmp_path / "missing-dir" / "mcp.json"), run_self_test=False)
    assert parent_error.value.stage == "resolve_config_path"
    assert "parent directory" in str(parent_error.value)


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
    assert server["args"] == [str((root / "tools" / "vrcforge_agent_mcp_stdio.py").resolve()), "--no-start"]
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

    assert set(statuses) == {"codexApp", "codexCli", "claudeCode", "claudeCowork", "generic"}
    assert statuses["codexApp"]["label"] == "Codex App"
    assert statuses["codexCli"]["label"] == "Codex CLI"
    assert statuses["claudeCode"]["scope"] == "project"
    assert statuses["claudeCowork"]["scope"] == "user"
    assert statuses["generic"]["scope"] == "custom"
    assert statuses["generic"]["installable"] is True
    assert statuses["generic"]["requiresConfigPath"] is True


def test_connector_status_does_not_treat_broken_path_shim_as_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    shim = tmp_path / "WindowsApps" / "codex.exe"
    shim.parent.mkdir(parents=True)
    shim.write_text("", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

    def fake_which(command: str) -> str | None:
        if command == "codex":
            return str(shim)
        if command == "claude":
            return None
        return None

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("Access is denied")

    monkeypatch.setattr("external_agent_connector_installer.shutil.which", fake_which)
    monkeypatch.setattr("external_agent_connector_installer.subprocess.run", fake_run)

    statuses = connector_client_statuses(root_dir=root, project_path=str(tmp_path))

    assert statuses["codexCli"]["cliDetected"] is False
    assert "Access is denied" in statuses["codexCli"]["cliError"]
    assert statuses["claudeCode"]["cliDetected"] is False
    assert "claude was not found" in statuses["claudeCode"]["cliError"]


def test_connector_status_prefers_configured_codex_cli_over_broken_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_source_root(tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    configured_cli = tmp_path / "OpenAI" / "Codex" / "bin" / "real" / "codex.exe"
    configured_cli.parent.mkdir(parents=True)
    configured_cli.write_text("", encoding="utf-8")
    (codex_home / "config.toml").write_text(f"CODEX_CLI_PATH = '{configured_cli}'\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

    def fake_which(command: str) -> str | None:
        if command == "codex":
            return str(tmp_path / "WindowsApps" / "codex.exe")
        return None

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == str(configured_cli):
            return subprocess.CompletedProcess(command, 0, stdout="codex-cli test", stderr="")
        return subprocess.CompletedProcess(command, 5, stdout="", stderr="Access is denied")

    monkeypatch.setattr("external_agent_connector_installer.shutil.which", fake_which)
    monkeypatch.setattr("external_agent_connector_installer.subprocess.run", fake_run)

    statuses = connector_client_statuses(root_dir=root, project_path=str(tmp_path))

    assert statuses["codexCli"]["cliDetected"] is True
    assert statuses["codexCli"]["cliPath"] == str(configured_cli)
    assert statuses["codexCli"]["cliSource"].startswith("config:")


def test_appx_package_probe_does_not_shell_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(command: str) -> str | None:
        raise AssertionError(f"unexpected command lookup: {command}")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"unexpected subprocess: {command}")

    monkeypatch.setattr("external_agent_connector_installer.shutil.which", fake_which)
    monkeypatch.setattr("external_agent_connector_installer.subprocess.run", fake_run)

    probe = _probe_appx_package("Claude")

    assert probe["ok"] is False
    assert probe["matches"] == []
    assert "WindowsApps" in probe["error"]
