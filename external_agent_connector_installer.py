from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback for source users.
    import tomli as tomllib  # type: ignore[no-redef]

from external_agent_connectors import (
    DEFAULT_SERVER_NAME,
    ExternalAgentConnectorOptions,
    build_claude_code_stdio_config,
    build_connector_bundle,
    render_codex_stdio_toml,
)

ConnectorClient = Literal["codex", "codexApp", "codexCli", "claudeCode", "claudeCowork"]


class ConnectorInstallError(RuntimeError):
    def __init__(self, message: str, *, stage: str, suggestion: str = "") -> None:
        super().__init__(message)
        self.stage = stage
        self.suggestion = suggestion

    def as_result(self, *, client: str, action: str) -> dict[str, Any]:
        return {
            "ok": False,
            "client": client,
            "action": action,
            "stage": self.stage,
            "error": str(self),
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class StdioBridgeSpec:
    command: str
    args: list[str]
    cwd: str
    packaged: bool
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "packaged": self.packaged,
            "source": self.source,
        }

    def to_options(self) -> ExternalAgentConnectorOptions:
        stdio_arg = self.args[0] if self.args else ""
        return ExternalAgentConnectorOptions(
            stdio_command=self.command,
            stdio_script=stdio_arg,
            stdio_cwd=self.cwd,
        )


def resolve_stdio_bridge(root_dir: Path) -> StdioBridgeSpec:
    root = root_dir.expanduser().resolve()
    packaged_candidates = [
        root / "backend" / "vrcforge_backend.exe",
        root / "backend" / "vrcforge_backend",
    ]
    for candidate in packaged_candidates:
        if candidate.is_file():
            return StdioBridgeSpec(
                command=str(candidate.resolve()),
                args=["--agent-mcp-stdio"],
                cwd=str(root),
                packaged=True,
                source="packaged-backend",
            )

    script = root / "tools" / "vrcforge_agent_mcp_stdio.py"
    if not script.is_file():
        raise ConnectorInstallError(
            "VRCForge stdio bridge was not found.",
            stage="resolve_stdio_bridge",
            suggestion="Reinstall VRCForge or run from a complete source checkout that contains tools/vrcforge_agent_mcp_stdio.py.",
        )
    executable = Path(sys.executable)
    command = str(executable.resolve()) if executable.is_file() else "python"
    return StdioBridgeSpec(
        command=command,
        args=[str(script.resolve())],
        cwd=str(root),
        packaged=False,
        source="source-python",
    )


def install_connector(
    client: ConnectorClient,
    *,
    root_dir: Path,
    project_path: str | None = None,
    run_self_test: bool = True,
) -> dict[str, Any]:
    bridge = resolve_stdio_bridge(root_dir)
    options = bridge.to_options()
    if client == "claudeCode":
        result = _install_claude_code(project_path=project_path, options=options)
    elif client == "claudeCowork":
        result = _install_claude_cowork(options=options)
    elif client in {"codex", "codexApp", "codexCli"}:
        result = _install_codex(options=options)
    else:  # pragma: no cover - Literal request validation should stop this.
        raise ConnectorInstallError(f"Unsupported connector client: {client}", stage="validate_client")

    result.update(
        {
            "ok": True,
            "client": client,
            "action": "install",
            "bridge": bridge.as_dict(),
            "restartRequired": client == "claudeCowork",
            "restartInstruction": restart_instruction(client),
        }
    )
    if run_self_test:
        handshake = run_stdio_mcp_handshake(bridge)
        result["handshake"] = handshake
        if not handshake.get("ok"):
            result["ok"] = False
            result["stage"] = "stdio_handshake"
            result["error"] = handshake.get("error") or "MCP stdio handshake failed."
            result["suggestion"] = handshake.get("suggestion") or handshake_suggestion(client)
    return result


def uninstall_connector(
    client: ConnectorClient,
    *,
    project_path: str | None = None,
) -> dict[str, Any]:
    if client == "claudeCode":
        path = claude_code_config_path(project_path)
        result = _update_json_mcp_server(path, DEFAULT_SERVER_NAME, None)
    elif client == "claudeCowork":
        path = claude_cowork_config_path()
        result = _update_json_mcp_server(path, DEFAULT_SERVER_NAME, None)
    elif client in {"codex", "codexApp", "codexCli"}:
        path = codex_config_path()
        result = _update_codex_toml_server(path, DEFAULT_SERVER_NAME, None)
    else:  # pragma: no cover - Literal request validation should stop this.
        raise ConnectorInstallError(f"Unsupported connector client: {client}", stage="validate_client")
    return {
        "ok": True,
        "client": client,
        "action": "uninstall",
        "configPath": str(path),
        **result,
    }


def connector_client_statuses(*, root_dir: Path, project_path: str | None = None) -> dict[str, Any]:
    bridge: dict[str, Any]
    try:
        bridge = resolve_stdio_bridge(root_dir).as_dict()
    except ConnectorInstallError as exc:
        bridge = {"ok": False, "error": str(exc), "suggestion": exc.suggestion}
    return {
        "codexApp": _codex_status("codexApp", bridge),
        "codexCli": _codex_status("codexCli", bridge),
        "claudeCode": _claude_code_status(project_path, bridge),
        "claudeCowork": _claude_cowork_status(bridge),
    }


def build_runtime_connector_bundle(root_dir: Path) -> dict[str, Any]:
    bridge = resolve_stdio_bridge(root_dir)
    return build_connector_bundle(bridge.to_options())


def restart_instruction(client: str) -> str:
    if client == "claudeCowork":
        return "Fully quit and reopen the Claude desktop app, then start a new Cowork session."
    if client == "claudeCode":
        return "Start a new Claude Code session in the selected project."
    if client == "codexApp":
        return "Start a new Codex App session so it reloads the MCP server list."
    if client == "codex":
        return "Start a new Codex App or CLI session so it reloads the MCP server list."
    if client == "codexCli":
        return "Start a new Codex CLI session so it reloads ~/.codex/config.toml."
    return "Start a new client session so it reloads MCP servers."


def handshake_suggestion(client: str) -> str:
    label = CLIENT_LABELS.get(client, client)
    return (
        f"{label} config was written, but VRCForge could not prove the stdio MCP bridge. "
        "Open VRCForge, enable Agent Gateway if needed, then run Doctor and retry."
    )


def _install_claude_code(*, project_path: str | None, options: ExternalAgentConnectorOptions) -> dict[str, Any]:
    path = claude_code_config_path(project_path)
    block = build_claude_code_stdio_config(options)["mcpServers"][DEFAULT_SERVER_NAME]
    return {"configPath": str(path), **_update_json_mcp_server(path, DEFAULT_SERVER_NAME, block)}


def _install_claude_cowork(*, options: ExternalAgentConnectorOptions) -> dict[str, Any]:
    path = claude_cowork_config_path()
    block = dict(build_claude_code_stdio_config(options)["mcpServers"][DEFAULT_SERVER_NAME])
    block["type"] = "sdk"
    return {"configPath": str(path), **_update_json_mcp_server(path, DEFAULT_SERVER_NAME, block)}


def _install_codex(*, options: ExternalAgentConnectorOptions) -> dict[str, Any]:
    path = codex_config_path()
    text = render_codex_stdio_toml(options)
    return {"configPath": str(path), **_update_codex_toml_server(path, DEFAULT_SERVER_NAME, text)}


def claude_code_config_path(project_path: str | None) -> Path:
    if not project_path or not str(project_path).strip():
        raise ConnectorInstallError(
            "Claude Code project config needs a selected project.",
            stage="resolve_config_path",
            suggestion="Select a Unity project in VRCForge, then install the Claude Code connector again.",
        )
    root = Path(project_path).expanduser().resolve()
    if not root.exists():
        raise ConnectorInstallError(
            "Selected project path does not exist.",
            stage="resolve_config_path",
            suggestion="Select an existing Unity project before installing the project-level .mcp.json connector.",
        )
    return root / ".mcp.json"


def claude_cowork_config_path() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise ConnectorInstallError(
            "APPDATA is not available, so the Claude desktop config path cannot be resolved.",
            stage="resolve_config_path",
            suggestion="Run VRCForge as a normal Windows user session and retry.",
        )
    return Path(appdata).expanduser() / "Claude" / "claude_desktop_config.json"


def codex_config_path() -> Path:
    home = os.environ.get("CODEX_HOME", "").strip()
    if home:
        return Path(home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def run_stdio_mcp_handshake(bridge: StdioBridgeSpec, *, timeout_seconds: float = 20.0) -> dict[str, Any]:
    started_at = time.monotonic()
    command = [bridge.command, *bridge.args]
    try:
        process = subprocess.Popen(
            command,
            cwd=bridge.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except OSError as exc:
        return {
            "ok": False,
            "stage": "spawn",
            "command": command,
            "cwd": bridge.cwd,
            "error": str(exc),
            "suggestion": "The configured VRCForge backend path could not be started. Reinstall VRCForge, then retry the connector install.",
        }

    stdout_queue: queue.Queue[str] = queue.Queue()
    stderr_queue: queue.Queue[str] = queue.Queue()
    stdout_thread = threading.Thread(target=_read_stream_lines, args=(process.stdout, stdout_queue), daemon=True)
    stderr_thread = threading.Thread(target=_read_stream_lines, args=(process.stderr, stderr_queue), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    transcript: list[dict[str, Any]] = []
    stderr_lines: list[str] = []
    try:
        _send_json_rpc(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "vrcforge-connector-self-test", "version": "0"},
                },
            },
        )
        initialize = _read_json_rpc_response(process, stdout_queue, stderr_queue, 1, timeout_seconds, transcript, stderr_lines)
        _send_json_rpc(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send_json_rpc(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _read_json_rpc_response(process, stdout_queue, stderr_queue, 2, timeout_seconds, transcript, stderr_lines)
        tools = listed.get("result", {}).get("tools", []) if isinstance(listed.get("result"), dict) else []
        tool_names = [str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)]
        connected = "vrcforge_bridge_preflight" in tool_names
        ready = "vrcforge_request_apply" in tool_names
        ok = bool(initialize.get("result")) and connected
        return {
            "ok": ok,
            "connected": connected,
            "ready": ready,
            "stage": "tools/list",
            "command": command,
            "cwd": bridge.cwd,
            "durationSeconds": round(time.monotonic() - started_at, 3),
            "toolCount": len(tool_names),
            "toolsSample": tool_names[:12],
            "hasBridgePreflight": connected,
            "hasRequestApply": ready,
            "stderrTail": _tail(stderr_lines),
            "transcriptTail": transcript[-4:],
            "error": "" if ok else "MCP tools/list did not expose the VRCForge bridge preflight tool.",
            "warning": "" if ready else "Connector is visible, but Gateway/token readiness may be incomplete. Run bridge preflight from the client or open VRCForge Doctor.",
            "suggestion": "" if ok else "Open VRCForge, run Doctor, then retry connector install.",
        }
    except Exception as exc:  # noqa: BLE001 - user-facing diagnostic path.
        stderr_tail = _tail(stderr_lines)
        error_text = str(exc)
        return {
            "ok": False,
            "stage": "stdio_handshake",
            "command": command,
            "cwd": bridge.cwd,
            "durationSeconds": round(time.monotonic() - started_at, 3),
            "stderrTail": stderr_tail,
            "transcriptTail": transcript[-4:],
            "error": error_text,
            "suggestion": _stdio_failure_suggestion(error_text, stderr_tail),
        }
    finally:
        _terminate_process(process)


def _read_stream_lines(stream: Any, output: queue.Queue[str]) -> None:
    if stream is None:
        return
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            output.put(line.rstrip("\r\n"))
    except Exception as exc:  # noqa: BLE001 - diagnostic reader must not crash the install action.
        output.put(f"<stream read failed: {exc}>")


def _send_json_rpc(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise RuntimeError("stdio process did not expose stdin.")
    process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _read_json_rpc_response(
    process: subprocess.Popen[str],
    stdout_queue: queue.Queue[str],
    stderr_queue: queue.Queue[str],
    expected_id: int,
    timeout_seconds: float,
    transcript: list[dict[str, Any]],
    stderr_lines: list[str],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _drain_queue(stderr_queue, stderr_lines)
        if process.poll() is not None and stdout_queue.empty():
            _drain_queue(stderr_queue, stderr_lines)
            raise RuntimeError(f"stdio bridge exited with code {process.returncode}. stderr: {' | '.join(_tail(stderr_lines, 6))}")
        try:
            line = stdout_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            transcript.append({"raw": line[:500]})
            continue
        transcript.append({"id": payload.get("id"), "method": payload.get("method"), "hasResult": "result" in payload, "hasError": "error" in payload})
        if payload.get("id") != expected_id:
            continue
        if "error" in payload:
            raise RuntimeError(f"MCP response {expected_id} returned error: {payload['error']}")
        return payload if isinstance(payload, dict) else {}
    _drain_queue(stderr_queue, stderr_lines)
    raise TimeoutError(f"Timed out waiting for MCP response id={expected_id}. stderr: {' | '.join(_tail(stderr_lines, 6))}")


def _drain_queue(source: queue.Queue[str], target: list[str]) -> None:
    while True:
        try:
            target.append(source.get_nowait())
        except queue.Empty:
            break


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdin:
            process.stdin.close()
    except OSError:
        pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _tail(lines: list[str], count: int = 12) -> list[str]:
    return [line[-1000:] for line in lines[-count:]]


def _stdio_failure_suggestion(error: str, stderr_tail: list[str]) -> str:
    combined = "\n".join([error, *stderr_tail]).lower()
    if "no module named 'mcp'" in combined or 'no module named "mcp"' in combined:
        return "The source-checkout bridge is using a Python environment without the mcp package. Use the packaged VRCForge build or install the source Python dependencies, then retry."
    if "gateway token was not found" in combined or "token was not found" in combined:
        return "Open VRCForge once so it creates the local Agent Gateway token, then retry the connector install."
    if "connection refused" in combined or "did not open its loopback port" in combined:
        return "Start VRCForge Desktop, run Doctor, and retry after the runtime is online."
    return "Open VRCForge, run Doctor, confirm the Agent Gateway token exists, then retry connector install."


def _update_json_mcp_server(path: Path, server_name: str, server_block: dict[str, Any] | None) -> dict[str, Any]:
    payload = _load_json_object(path)
    servers = payload.get("mcpServers")
    if servers is None:
        servers = {}
        payload["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ConnectorInstallError(
            "mcpServers must be a JSON object.",
            stage="parse_config",
            suggestion=f"Repair {path} so mcpServers is an object, then retry.",
        )

    previous = servers.get(server_name)
    if server_block is None:
        changed = server_name in servers
        servers.pop(server_name, None)
    else:
        changed = previous != server_block
        servers[server_name] = server_block

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    backup_path = _write_config_atomically(path, text, _validate_json_object) if changed or not path.exists() else ""
    return {
        "changed": changed,
        "installed": server_block is not None,
        "removed": server_block is None and changed,
        "backupPath": str(backup_path) if backup_path else "",
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConnectorInstallError(
            f"Could not read config file: {exc}",
            stage="read_config",
            suggestion=f"Close apps that may be locking {path}, then retry.",
        ) from exc
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ConnectorInstallError(
            f"Config file is not valid JSON: {exc}",
            stage="parse_config",
            suggestion=f"Fix or rename {path}; VRCForge did not write partial changes.",
        ) from exc
    if not isinstance(payload, dict):
        raise ConnectorInstallError(
            "Config file root must be a JSON object.",
            stage="parse_config",
            suggestion=f"Fix {path} so the root value is an object, then retry.",
        )
    return payload


def _update_codex_toml_server(path: Path, server_name: str, server_text: str | None) -> dict[str, Any]:
    original = ""
    if path.exists():
        try:
            original = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ConnectorInstallError(
                f"Could not read config file: {exc}",
                stage="read_config",
                suggestion=f"Close apps that may be locking {path}, then retry.",
            ) from exc
    if original.strip():
        _validate_toml_object(original)
    without_server = _remove_codex_server_block(original, server_name)
    if server_text is None:
        changed = without_server != original
        new_text = _normalize_terminal_newline(without_server)
    else:
        insertion = _normalize_terminal_newline(server_text)
        new_text = _normalize_terminal_newline(without_server)
        if new_text.strip():
            new_text = new_text.rstrip() + "\n\n" + insertion
        else:
            new_text = insertion
        changed = new_text != original
    backup_path = _write_config_atomically(path, new_text, _validate_toml_object) if changed or (server_text is not None and not path.exists()) else ""
    return {
        "changed": changed,
        "installed": server_text is not None,
        "removed": server_text is None and changed,
        "backupPath": str(backup_path) if backup_path else "",
    }


def _remove_codex_server_block(text: str, server_name: str) -> str:
    target = f"mcp_servers.{server_name}"
    output: list[str] = []
    skipping = False
    for line in text.splitlines():
        table = _toml_table_name(line)
        if table:
            normalized = _normalize_toml_table_name(table)
            skipping = normalized == target or normalized.startswith(f"{target}.")
        if not skipping:
            output.append(line)
    return "\n".join(output).strip() + ("\n" if output else "")


def _toml_table_name(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        return ""
    if stripped.startswith("[["):
        return stripped.strip("[]").strip()
    return stripped[1:-1].strip()


def _normalize_toml_table_name(name: str) -> str:
    return name.replace('"', "").replace("'", "").strip()


def _write_config_atomically(path: Path, text: str, validator: Any) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if path.exists():
        backup_path = path.with_name(f"{path.name}.vrcforge-backup-{_timestamp()}-{uuid.uuid4().hex[:8]}")
        shutil.copy2(path, backup_path)
    validator(text)
    temp_path = path.with_name(f".{path.name}.vrcforge-{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        validator(temp_path.read_text(encoding="utf-8"))
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return backup_path


def _validate_json_object(text: str) -> None:
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ConnectorInstallError(
            "Rendered config root is not a JSON object.",
            stage="validate_config",
            suggestion="Retry the install. If it repeats, export a support bundle.",
        )


def _validate_toml_object(text: str) -> None:
    try:
        payload = tomllib.loads(text or "")
    except Exception as exc:  # noqa: BLE001 - tomllib/tomli expose different exception classes.
        raise ConnectorInstallError(
            f"Config file is not valid TOML: {exc}",
            stage="parse_config",
            suggestion="Fix or rename the TOML config; VRCForge did not write partial changes.",
        ) from exc
    if not isinstance(payload, dict):
        raise ConnectorInstallError(
            "TOML config root is not an object.",
            stage="validate_config",
            suggestion="Retry the install. If it repeats, export a support bundle.",
        )


def _normalize_terminal_newline(text: str) -> str:
    stripped = text.rstrip()
    return f"{stripped}\n" if stripped else ""


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


CLIENT_LABELS = {
    "codex": "Codex",
    "codexApp": "Codex App",
    "codexCli": "Codex CLI",
    "claudeCode": "Claude Code CLI",
    "claudeCowork": "Claude Cowork App",
}


def _codex_status(client: Literal["codexApp", "codexCli"], bridge: dict[str, Any]) -> dict[str, Any]:
    path = codex_config_path()
    app_probe = _probe_windows_app("OpenAI.Codex") if client == "codexApp" else {}
    cli_probe = _probe_codex_cli() if client == "codexCli" else {}
    return {
        "label": CLIENT_LABELS[client],
        "scope": "user",
        "configPath": str(path),
        "installed": _codex_server_installed(path),
        "installable": True,
        "sharedConfigGroup": "codex",
        "cliDetected": bool(cli_probe.get("ok")) if client == "codexCli" else None,
        "cliPath": cli_probe.get("path", "") if cli_probe else "",
        "cliSource": cli_probe.get("source", "") if cli_probe else "",
        "cliError": _probe_error(cli_probe) if cli_probe and not cli_probe.get("ok") else "",
        "appDetected": bool(app_probe.get("ok")) if app_probe else None,
        "appMatches": app_probe.get("matches", []) if app_probe else [],
        "appError": _probe_error(app_probe) if app_probe and not app_probe.get("ok") else "",
        "bridge": bridge,
        "restartInstruction": restart_instruction(client),
    }


def _claude_code_status(project_path: str | None, bridge: dict[str, Any]) -> dict[str, Any]:
    cli_probe = _probe_command(["claude", "--version"], source="PATH")
    installable = bool(project_path and str(project_path).strip())
    path = ""
    installed = False
    last_error = ""
    if installable:
        try:
            resolved = claude_code_config_path(project_path)
            path = str(resolved)
            installed = _json_server_installed(resolved, DEFAULT_SERVER_NAME)
        except ConnectorInstallError as exc:
            last_error = str(exc)
    return {
        "label": CLIENT_LABELS["claudeCode"],
        "scope": "project",
        "configPath": path,
        "installed": installed,
        "installable": installable,
        "lastError": last_error,
        "cliDetected": bool(cli_probe.get("ok")),
        "cliPath": cli_probe.get("path", ""),
        "cliSource": cli_probe.get("source", ""),
        "cliError": _probe_error(cli_probe) if not cli_probe.get("ok") else "",
        "bridge": bridge,
        "restartInstruction": restart_instruction("claudeCode"),
    }


def _claude_cowork_status(bridge: dict[str, Any]) -> dict[str, Any]:
    app_probe = _probe_windows_app("Claude")
    path = ""
    installed = False
    last_error = ""
    try:
        resolved = claude_cowork_config_path()
        path = str(resolved)
        installed = _json_server_installed(resolved, DEFAULT_SERVER_NAME)
    except ConnectorInstallError as exc:
        last_error = str(exc)
    return {
        "label": CLIENT_LABELS["claudeCowork"],
        "scope": "user",
        "configPath": path,
        "installed": installed,
        "installable": bool(path),
        "lastError": last_error,
        "cliDetected": False,
        "appDetected": bool(app_probe.get("ok")),
        "appMatches": app_probe.get("matches", []),
        "appError": _probe_error(app_probe) if not app_probe.get("ok") else "",
        "bridge": bridge,
        "restartInstruction": restart_instruction("claudeCowork"),
    }


def _probe_codex_cli() -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    seen: set[str] = set()
    env_cli = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_cli:
        _append_cli_probe_attempt(attempts, seen, env_cli, "env:CODEX_CLI_PATH")
    config_cli = _read_codex_cli_path_from_config(codex_config_path())
    if config_cli:
        _append_cli_probe_attempt(attempts, seen, config_cli, f"config:{codex_config_path()}")
    path_cli = shutil.which("codex")
    if path_cli:
        _append_cli_probe_attempt(attempts, seen, path_cli, "PATH")
    elif not attempts:
        attempts.append(_probe_command(["codex", "--version"], source="PATH"))
    ok_attempt = next((attempt for attempt in attempts if attempt.get("ok")), None)
    best = ok_attempt or next((attempt for attempt in attempts if attempt.get("found")), attempts[-1] if attempts else {})
    result = dict(best)
    result["attempts"] = attempts
    return result


def _append_cli_probe_attempt(attempts: list[dict[str, Any]], seen: set[str], cli_path: str, source: str) -> None:
    normalized = str(Path(cli_path).expanduser())
    key = normalized.lower() if os.name == "nt" else normalized
    if key in seen:
        return
    seen.add(key)
    attempts.append(_probe_command([normalized, "--version"], source=source))


def _read_codex_cli_path_from_config(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for line in text.splitlines():
        match = re.match(r"\s*CODEX_CLI_PATH\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$", line)
        if match:
            return match.group(2).strip()
    return ""


def _probe_command(command: list[str], *, source: str, timeout_seconds: float = 5.0) -> dict[str, Any]:
    exe = _resolve_command_exe(command[0])
    payload: dict[str, Any] = {
        "found": bool(exe),
        "path": exe or "",
        "source": source,
        "ok": False,
        "stdout": "",
        "stderr": "",
        "error": "",
    }
    if not exe:
        payload["error"] = f"{command[0]} was not found."
        return payload
    try:
        completed = subprocess.run(
            [exe, *command[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception as exc:  # noqa: BLE001 - user-facing status detail.
        payload["error"] = str(exc)
        return payload
    payload.update(
        {
            "ok": completed.returncode == 0,
            "exitCode": completed.returncode,
            "stdout": (completed.stdout or "").strip()[:500],
            "stderr": (completed.stderr or "").strip()[:500],
        }
    )
    return payload


def _resolve_command_exe(command_name: str) -> str:
    command_path = Path(command_name).expanduser()
    if command_path.is_file():
        return str(command_path)
    return shutil.which(command_name) or ""


def _probe_windows_app(name_fragment: str) -> dict[str, Any]:
    if os.name != "nt":
        return {"found": False, "ok": False, "reason": "not-windows", "matches": []}
    appx_probe = _probe_appx_package(name_fragment)
    roots = [
        Path(os.environ.get("ProgramFiles", "")) / "WindowsApps",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
    ]
    matches: list[str] = []
    path_matches: list[str] = []
    for path_item in os.environ.get("PATH", "").split(os.pathsep):
        if name_fragment.lower() in path_item.lower():
            path_matches.append(path_item)
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for item in root.iterdir():
                if name_fragment.lower() in item.name.lower():
                    matches.append(str(item))
        except OSError:
            continue
    all_matches = []
    seen: set[str] = set()
    for candidate in [*matches, *path_matches, *appx_probe.get("matches", [])]:
        key = candidate.lower() if os.name == "nt" else candidate
        if key in seen:
            continue
        seen.add(key)
        all_matches.append(candidate)
    error = ""
    if not all_matches:
        error = _probe_error(appx_probe) or f"No installed app matching {name_fragment} was found."
    return {"found": bool(all_matches), "ok": bool(all_matches), "matches": all_matches[:10], "error": error}


def _probe_appx_package(name_fragment: str) -> dict[str, Any]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return {"found": False, "ok": False, "matches": [], "error": "PowerShell was not found."}
    escaped = name_fragment.replace("'", "''")
    command = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"Get-AppxPackage -Name '*{escaped}*' | "
        "ForEach-Object { $_.InstallLocation } | "
        "Where-Object { $_ } | "
        "Select-Object -First 10"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception as exc:  # noqa: BLE001 - user-facing diagnostic detail.
        return {"found": False, "ok": False, "matches": [], "error": str(exc)}
    matches = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    error = "" if completed.returncode == 0 else (completed.stderr or completed.stdout or "").strip()
    return {"found": bool(matches), "ok": bool(matches), "matches": matches[:10], "error": error}


def _probe_error(probe: dict[str, Any]) -> str:
    return str(probe.get("error") or probe.get("stderr") or probe.get("stdout") or "").strip()


def _json_server_installed(path: Path, server_name: str) -> bool:
    try:
        payload = _load_json_object(path)
    except ConnectorInstallError:
        return False
    servers = payload.get("mcpServers")
    return isinstance(servers, dict) and isinstance(servers.get(server_name), dict)


def _codex_server_installed(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8-sig") or "")
    except Exception:  # noqa: BLE001
        return False
    servers = parsed.get("mcp_servers")
    return isinstance(servers, dict) and isinstance(servers.get(DEFAULT_SERVER_NAME), dict)
