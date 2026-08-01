"""Narrow client for the project-scoped VRCForge Unity MCP Core transport."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TRANSPORT_SCHEMA = "vrcforge.mcp.transport.v1"
PROTOCOL_VERSION = "2025-11-25"
MAX_FRAME_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 5.0


class UnityMcpCoreError(RuntimeError):
    """A deliberately non-sensitive Core discovery or transport failure."""


@dataclass(frozen=True)
class UnityMcpCoreConnection:
    project_root: Path
    host: str
    port: int
    discovery_token: str
    instance_id: str
    process_id: int
    project_hash: str


def load_unity_mcp_core_connection(
    project_root: str | Path,
) -> UnityMcpCoreConnection:
    root = _resolve_project_root(project_root)
    descriptor = _read_json_object(root / "Library" / "VRCForge" / "mcp-core.json", "Core descriptor")
    _require_string(descriptor, "schema", "Core descriptor")
    if descriptor["schema"] != TRANSPORT_SCHEMA:
        raise UnityMcpCoreError("Unity MCP Core descriptor is not recognized.")
    if _require_string(descriptor, "transport", "Core descriptor") != "tcp-length-prefixed-jsonrpc":
        raise UnityMcpCoreError("Unity MCP Core transport is not supported.")
    if _require_string(descriptor, "protocolVersion", "Core descriptor") != PROTOCOL_VERSION:
        raise UnityMcpCoreError("Unity MCP Core protocol version is not supported.")
    if _require_string(descriptor, "authMode", "Core descriptor") != "bearer":
        raise UnityMcpCoreError("Unity MCP Core authentication mode is not supported.")
    if _require_string(descriptor, "executionPolicy", "Core descriptor") != "read-only-direct-writes-rejected":
        raise UnityMcpCoreError("Unity MCP Core execution policy is invalid.")
    if _require_string(descriptor, "host", "Core descriptor") != "127.0.0.1":
        raise UnityMcpCoreError("Unity MCP Core must use the loopback endpoint.")

    raw_project_path = _require_string(descriptor, "projectPath", "Core descriptor")
    try:
        descriptor_root = Path(raw_project_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.") from None
    if descriptor_root != root:
        raise UnityMcpCoreError("Unity MCP Core belongs to a different project.")

    project_hash = _require_string(descriptor, "projectHash", "Core descriptor")
    expected_hash = hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest()
    if project_hash != expected_hash:
        raise UnityMcpCoreError("Unity MCP Core project hash is invalid.")
    port = _require_port(descriptor, "port", "Core descriptor")
    discovery_token = _require_base64_token(descriptor, "authToken", "Core descriptor", 32)
    instance_id = _require_string(descriptor, "instanceId", "Core descriptor")
    process_id = _require_positive_int(descriptor, "processId", "Core descriptor")

    return UnityMcpCoreConnection(
        project_root=root,
        host="127.0.0.1",
        port=port,
        discovery_token=discovery_token,
        instance_id=instance_id,
        process_id=process_id,
        project_hash=project_hash,
    )


class UnityMcpCoreClient:
    def __init__(
        self,
        project_root: str | Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 0 and 600 seconds.")
        self._connection = load_unity_mcp_core_connection(project_root)
        self._timeout_seconds = float(timeout_seconds)

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            raise UnityMcpCoreError("Unity MCP Core returned an invalid tools list.")
        return tools

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise ValueError("tool name is required.")
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object.")
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise UnityMcpCoreError("Unity MCP Core returned an invalid tool result.")
        return result

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        try:
            with socket.create_connection((self._connection.host, self._connection.port), self._timeout_seconds) as connection:
                connection.settimeout(self._timeout_seconds)
                self._send_envelope(connection, 1, "initialize", {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "VRCForge FastAPI", "version": "1"},
                }, authorization="Bearer " + self._connection.discovery_token)
                initialize = self._receive_response(connection, 1)
                if not isinstance(initialize, dict) or initialize.get("protocolVersion") != PROTOCOL_VERSION:
                    raise UnityMcpCoreError("Unity MCP Core initialization failed.")
                self._send_notification(connection, "notifications/initialized", {})
                self._send_envelope(
                    connection,
                    2,
                    method,
                    params,
                )
                return self._receive_response(connection, 2)
        except UnityMcpCoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, struct.error):
            raise UnityMcpCoreError("Unity MCP Core connection failed.") from None

    def _send_notification(self, connection: socket.socket, method: str, params: dict[str, Any]) -> None:
        self._write_frame(connection, {"schema": TRANSPORT_SCHEMA, "message": {
            "jsonrpc": "2.0", "method": method, "params": params,
        }})

    def _send_envelope(
        self,
        connection: socket.socket,
        request_id: int,
        method: str,
        params: dict[str, Any],
        *,
        authorization: str | None = None,
    ) -> None:
        envelope: dict[str, Any] = {
            "schema": TRANSPORT_SCHEMA,
            "message": {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        }
        if authorization is not None:
            envelope["authorization"] = authorization
        self._write_frame(connection, envelope)

    def _receive_response(self, connection: socket.socket, expected_id: int) -> Any:
        envelope = self._read_frame(connection)
        if not isinstance(envelope, dict) or envelope.get("schema") != TRANSPORT_SCHEMA:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid transport response.")
        message = envelope.get("message")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or message.get("id") != expected_id:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid JSON-RPC response.")
        if "error" in message:
            raise UnityMcpCoreError("Unity MCP Core rejected the request.")
        if "result" not in message:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid JSON-RPC response.")
        return message["result"]

    @staticmethod
    def _write_frame(connection: socket.socket, payload: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise UnityMcpCoreError("Unity MCP Core request is invalid.") from None
        if not 0 < len(encoded) <= MAX_FRAME_BYTES:
            raise UnityMcpCoreError("Unity MCP Core request is too large.")
        connection.sendall(struct.pack(">I", len(encoded)) + encoded)

    @staticmethod
    def _read_frame(connection: socket.socket) -> dict[str, Any]:
        size = struct.unpack(">I", _read_exactly(connection, 4))[0]
        if not 0 < size <= MAX_FRAME_BYTES:
            raise UnityMcpCoreError("Unity MCP Core response is invalid.")
        try:
            value = json.loads(_read_exactly(connection, size).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise UnityMcpCoreError("Unity MCP Core response is invalid.") from None
        if not isinstance(value, dict):
            raise UnityMcpCoreError("Unity MCP Core response is invalid.")
        return value


def _read_exactly(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise UnityMcpCoreError("Unity MCP Core connection closed unexpectedly.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _resolve_project_root(project_root: str | Path) -> Path:
    try:
        root = Path(project_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        raise UnityMcpCoreError("Unity project root is invalid.") from None
    if not root.is_dir():
        raise UnityMcpCoreError("Unity project root is invalid.")
    return root


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise UnityMcpCoreError(label + " is unavailable or invalid.") from None
    if not isinstance(value, dict):
        raise UnityMcpCoreError(label + " is invalid.")
    return value


def _require_string(document: dict[str, Any], key: str, label: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise UnityMcpCoreError(label + " is invalid.")
    return value


def _require_positive_int(document: dict[str, Any], key: str, label: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise UnityMcpCoreError(label + " is invalid.")
    return value


def _require_port(document: dict[str, Any], key: str, label: str) -> int:
    port = _require_positive_int(document, key, label)
    if port > 65535:
        raise UnityMcpCoreError(label + " is invalid.")
    return port


def _require_base64_token(document: dict[str, Any], key: str, label: str, expected_length: int) -> str:
    value = _require_string(document, key, label)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise UnityMcpCoreError(label + " is invalid.") from None
    if len(decoded) != expected_length:
        raise UnityMcpCoreError(label + " is invalid.")
    return value
