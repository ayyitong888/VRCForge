from __future__ import annotations

import importlib.util
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
