from __future__ import annotations

import json
import re
from dataclasses import dataclass
from os import PathLike
from typing import Any
from urllib.parse import urlparse, urlunparse

DEFAULT_SERVER_NAME = "vrcforge"
DEFAULT_MCP_URL = "http://127.0.0.1:8757/mcp"
DEFAULT_TOKEN_ENV_VAR = "VRCFORGE_AGENT_TOKEN"
DEFAULT_SKILLS_PROJECTION_DIR = "%LOCALAPPDATA%\\VRCForge\\agentic-app\\skills"

_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class ExternalAgentConnectorOptions:
    server_name: str = DEFAULT_SERVER_NAME
    mcp_url: str = DEFAULT_MCP_URL
    token_env_var: str = DEFAULT_TOKEN_ENV_VAR
    skills_projection_dir: str | PathLike[str] = DEFAULT_SKILLS_PROJECTION_DIR

    def __post_init__(self) -> None:
        object.__setattr__(self, "server_name", _validate_server_name(self.server_name))
        object.__setattr__(self, "mcp_url", _normalize_mcp_url(self.mcp_url))
        object.__setattr__(self, "token_env_var", _validate_env_var_name(self.token_env_var))
        object.__setattr__(self, "skills_projection_dir", _validate_path_text(self.skills_projection_dir))


def build_connector_bundle(options: ExternalAgentConnectorOptions | None = None) -> dict[str, Any]:
    opts = options or ExternalAgentConnectorOptions()
    return {
        "schema": "vrcforge.external_agent_connectors.v1",
        "mcp": {
            "serverName": opts.server_name,
            "transport": "streamable_http",
            "url": opts.mcp_url,
            "loopbackOnly": True,
        },
        "auth": {
            "type": "bearer",
            "header": "Authorization",
            "tokenEnvVar": opts.token_env_var,
            "headerTemplate": _shell_bearer_template(opts.token_env_var),
            "storesPlaintextToken": False,
        },
        "skillsProjection": build_skills_projection(options=opts),
        "clientConfigs": {
            "codex": {
                "format": "toml",
                "config": build_codex_style_config(opts),
                "text": render_codex_toml(opts),
            },
            "claudeCode": {
                "format": "json",
                "config": build_claude_code_style_config(opts),
                "text": render_claude_code_json(opts),
            },
        },
    }


def build_skills_projection(
    *,
    options: ExternalAgentConnectorOptions | None = None,
    skills_projection_dir: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    opts = options or ExternalAgentConnectorOptions(
        skills_projection_dir=skills_projection_dir or DEFAULT_SKILLS_PROJECTION_DIR
    )
    directory = _validate_path_text(skills_projection_dir) if skills_projection_dir is not None else opts.skills_projection_dir
    return {
        "recommendedDirectory": directory,
        "layout": "skills/<skill-name>/SKILL.md",
        "projectionMode": "copy-or-symlink-public-skill-packages",
        "secretPolicy": "Do not place gateway tokens, approval tokens, API keys, or paid avatar assets in this directory.",
    }


def build_codex_style_config(options: ExternalAgentConnectorOptions | None = None) -> dict[str, Any]:
    opts = options or ExternalAgentConnectorOptions()
    return {
        "mcp_servers": {
            opts.server_name: {
                "transport": "streamable_http",
                "url": opts.mcp_url,
                "headers": {
                    "Authorization": _env_bearer_template(opts.token_env_var),
                },
            }
        }
    }


def build_claude_code_style_config(options: ExternalAgentConnectorOptions | None = None) -> dict[str, Any]:
    opts = options or ExternalAgentConnectorOptions()
    return {
        "mcpServers": {
            opts.server_name: {
                "type": "http",
                "url": opts.mcp_url,
                "headers": {
                    "Authorization": _shell_bearer_template(opts.token_env_var),
                },
            }
        }
    }


def render_connector_bundle_json(options: ExternalAgentConnectorOptions | None = None) -> str:
    return _json_text(build_connector_bundle(options))


def render_claude_code_json(options: ExternalAgentConnectorOptions | None = None) -> str:
    return _json_text(build_claude_code_style_config(options))


def render_codex_toml(options: ExternalAgentConnectorOptions | None = None) -> str:
    opts = options or ExternalAgentConnectorOptions()
    table = f"mcp_servers.{opts.server_name}"
    headers = f'{{ Authorization = {_toml_string(_env_bearer_template(opts.token_env_var))} }}'
    return "\n".join(
        [
            f"[{table}]",
            'transport = "streamable_http"',
            f"url = {_toml_string(opts.mcp_url)}",
            f"headers = {headers}",
            "",
        ]
    )


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _env_bearer_template(env_var: str) -> str:
    return f"Bearer ${{env:{env_var}}}"


def _shell_bearer_template(env_var: str) -> str:
    return f"Bearer ${{{env_var}}}"


def _validate_server_name(server_name: str) -> str:
    value = str(server_name).strip()
    if not _SERVER_NAME_RE.fullmatch(value):
        raise ValueError("server_name must start with a letter and contain only letters, numbers, '_' or '-'.")
    return value


def _validate_env_var_name(env_var: str) -> str:
    value = str(env_var).strip()
    if not _ENV_VAR_RE.fullmatch(value):
        raise ValueError("token_env_var must be an environment variable name, not a token value.")
    return value


def _validate_path_text(path: str | PathLike[str]) -> str:
    value = str(path).strip()
    if not value:
        raise ValueError("skills_projection_dir must not be empty.")
    if any(char in value for char in "\r\n\0"):
        raise ValueError("skills_projection_dir must be a single path string.")
    return value


def _normalize_mcp_url(url: str) -> str:
    value = str(url).strip()
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise ValueError("mcp_url must use http because the gateway is loopback-only.")
    if not parsed.hostname or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("mcp_url must point at a loopback host.")
    if parsed.query or parsed.fragment:
        raise ValueError("mcp_url must not include query strings or fragments.")
    path = parsed.path.rstrip("/") or "/mcp"
    if path != "/mcp":
        raise ValueError("mcp_url must use the /mcp endpoint.")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


__all__ = [
    "DEFAULT_MCP_URL",
    "DEFAULT_SERVER_NAME",
    "DEFAULT_SKILLS_PROJECTION_DIR",
    "DEFAULT_TOKEN_ENV_VAR",
    "ExternalAgentConnectorOptions",
    "build_claude_code_style_config",
    "build_codex_style_config",
    "build_connector_bundle",
    "build_skills_projection",
    "render_claude_code_json",
    "render_codex_toml",
    "render_connector_bundle_json",
]
