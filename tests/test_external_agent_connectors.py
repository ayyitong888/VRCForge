from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from external_agent_connectors import (
    DEFAULT_MCP_URL,
    DEFAULT_SERVER_NAME,
    DEFAULT_SMOKE_SCRIPT,
    DEFAULT_SKILLS_PROJECTION_DIR,
    DEFAULT_STDIO_EXTRA_ARGS,
    DEFAULT_STDIO_SCRIPT,
    DEFAULT_TOKEN_ENV_VAR,
    ExternalAgentConnectorOptions,
    build_claude_code_stdio_config,
    build_claude_code_style_config,
    build_codex_stdio_config,
    build_connector_bundle,
    build_skills_projection,
    render_claude_code_stdio_json,
    render_claude_code_json,
    render_codex_stdio_toml,
    render_codex_toml,
    render_connector_bundle_json,
)


def test_connector_bundle_uses_loopback_endpoint_and_env_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "real-token-value-that-must-not-be-rendered"
    monkeypatch.setenv(DEFAULT_TOKEN_ENV_VAR, secret)

    bundle = build_connector_bundle()
    rendered = json.dumps(bundle, ensure_ascii=False)

    assert bundle["mcp"]["url"] == DEFAULT_MCP_URL
    assert bundle["mcp"]["loopbackOnly"] is True
    assert bundle["auth"]["type"] == "bearer"
    assert bundle["auth"]["storesPlaintextToken"] is False
    assert DEFAULT_TOKEN_ENV_VAR in rendered
    assert secret not in rendered


def test_codex_style_toml_is_parseable_and_uses_env_header() -> None:
    toml_text = render_codex_toml()

    parsed = tomllib.loads(toml_text)
    server = parsed["mcp_servers"][DEFAULT_SERVER_NAME]

    assert server["url"] == DEFAULT_MCP_URL
    assert server["bearer_token_env_var"] == DEFAULT_TOKEN_ENV_VAR
    assert server["startup_timeout_sec"] == 20
    assert DEFAULT_TOKEN_ENV_VAR in toml_text
    assert "Authorization" not in toml_text
    assert "real-token" not in toml_text


def test_codex_stdio_toml_is_parseable_and_uses_launcher() -> None:
    toml_text = render_codex_stdio_toml()

    parsed = tomllib.loads(toml_text)
    server = parsed["mcp_servers"][DEFAULT_SERVER_NAME]

    assert server["command"] == "python"
    assert server["args"] == [DEFAULT_STDIO_SCRIPT, *DEFAULT_STDIO_EXTRA_ARGS]
    assert server["cwd"] == "."
    assert build_codex_stdio_config()["mcp_servers"][DEFAULT_SERVER_NAME] == server


def test_claude_code_style_json_is_parseable_and_uses_env_header() -> None:
    json_text = render_claude_code_json()

    parsed = json.loads(json_text)
    server = parsed["mcpServers"][DEFAULT_SERVER_NAME]

    assert server["type"] == "http"
    assert server["url"] == DEFAULT_MCP_URL
    assert server["headers"]["Authorization"] == f"Bearer ${{{DEFAULT_TOKEN_ENV_VAR}}}"
    assert build_claude_code_style_config()["mcpServers"][DEFAULT_SERVER_NAME] == server


def test_claude_code_stdio_json_is_parseable_and_uses_launcher() -> None:
    json_text = render_claude_code_stdio_json()

    parsed = json.loads(json_text)
    server = parsed["mcpServers"][DEFAULT_SERVER_NAME]

    assert server["command"] == "python"
    assert server["args"] == [DEFAULT_STDIO_SCRIPT, *DEFAULT_STDIO_EXTRA_ARGS]
    assert server["env"] == {}
    assert build_claude_code_stdio_config()["mcpServers"][DEFAULT_SERVER_NAME] == server


def test_connector_bundle_includes_launcher_and_smoke_metadata() -> None:
    bundle = build_connector_bundle()

    assert bundle["launcher"]["stdioBridge"]["args"] == [DEFAULT_STDIO_SCRIPT, *DEFAULT_STDIO_EXTRA_ARGS]
    assert bundle["launcher"]["stdioBridge"]["startsOrReconnectsRuntime"] is False
    assert bundle["launcher"]["stdioBridge"]["requiresRuntimeAlreadyOnline"] is True
    assert bundle["launcher"]["stdioBridge"]["storesPlaintextToken"] is False
    assert bundle["launcher"]["smoke"]["args"] == [DEFAULT_SMOKE_SCRIPT]
    assert bundle["launcher"]["smoke"]["preflightArgs"] == ["--enable-gateway"]
    assert bundle["launcher"]["smoke"]["liveWriteRollbackArgs"] == ["--enable-gateway", "--live-write-rollback"]
    assert "codexStdio" in bundle["clientConfigs"]
    assert "claudeCodeStdio" in bundle["clientConfigs"]
    assert "claudeCowork" in bundle["clientConfigs"]


def test_skills_projection_suggests_user_data_skill_package_layout() -> None:
    projection = build_skills_projection()

    assert projection["recommendedDirectory"] == DEFAULT_SKILLS_PROJECTION_DIR
    assert projection["layout"] == "skills/<skill-name>/SKILL.md"
    assert "tokens" in projection["secretPolicy"]
    assert "API keys" in projection["secretPolicy"]


def test_custom_options_normalize_path_json_and_toml_outputs(tmp_path: Path) -> None:
    skills_dir = tmp_path / "Skill Projection"
    options = ExternalAgentConnectorOptions(
        server_name="vrcforge_local",
        mcp_url="http://localhost:8757",
        token_env_var="CUSTOM_VRCFORGE_TOKEN",
        skills_projection_dir=skills_dir,
    )

    assert options.mcp_url == "http://localhost:8757/mcp"

    bundle = json.loads(render_connector_bundle_json(options))
    assert bundle["skillsProjection"]["recommendedDirectory"] == str(skills_dir)
    assert bundle["auth"]["headerTemplate"] == "Bearer ${CUSTOM_VRCFORGE_TOKEN}"

    toml_server = tomllib.loads(render_codex_toml(options))["mcp_servers"]["vrcforge_local"]
    assert toml_server["url"] == "http://localhost:8757/mcp"
    assert toml_server["bearer_token_env_var"] == "CUSTOM_VRCFORGE_TOKEN"

    stdio_server = tomllib.loads(render_codex_stdio_toml(options))["mcp_servers"]["vrcforge_local"]
    assert stdio_server["args"] == [DEFAULT_STDIO_SCRIPT, *DEFAULT_STDIO_EXTRA_ARGS]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mcp_url": "https://127.0.0.1:8757/mcp"},
        {"mcp_url": "http://example.com:8757/mcp"},
        {"mcp_url": "http://127.0.0.1:8757/not-mcp"},
        {"token_env_var": "plain-token-value"},
        {"server_name": "vrcforge.local"},
        {"skills_projection_dir": "C:/bad\npath"},
    ],
)
def test_invalid_connector_inputs_are_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ExternalAgentConnectorOptions(**kwargs)
