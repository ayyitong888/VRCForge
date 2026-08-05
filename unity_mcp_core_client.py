"""Bounded client for the project-scoped VRCForge Unity MCP Core transport."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from unity_mcp_tool_contract import (
    EXPECTED_TOOL_COUNT,
    EXPECTED_TOOL_NAMES,
    PLANNING_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
)


TRANSPORT_SCHEMA = "vrcforge.mcp.transport.v2"
MODERN_PROTOCOL_VERSION = "2026-07-28"
APP_SETUP_OUTFIT_POLL_LANE = "app_setup_outfit_poll"
APP_UNITYPACKAGE_IMPORT_POLL_LANE = "app_unitypackage_import_poll"
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_PROTOCOL_VERSION,)
MAX_FRAME_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 5.0
_ACTIVE_CALL_AUDIT_CAPTURE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "vrcforge_unity_mcp_call_audit_capture",
    default=None,
)


class UnityMcpCoreError(RuntimeError):
    """A deliberately non-sensitive Core discovery or transport failure."""


@contextmanager
def capture_unity_mcp_core_call_audits() -> Iterator[list[dict[str, Any]]]:
    """Collect safe Core call-audit records made in the current request context."""
    captured: list[dict[str, Any]] = []
    token = _ACTIVE_CALL_AUDIT_CAPTURE.set(captured)
    try:
        yield captured
    finally:
        _ACTIVE_CALL_AUDIT_CAPTURE.reset(token)


def _record_unity_mcp_core_call_audit(audit: dict[str, Any]) -> None:
    captured = _ACTIVE_CALL_AUDIT_CAPTURE.get()
    if captured is not None:
        captured.append(dict(audit))


@dataclass(frozen=True)
class UnityMcpCoreConnection:
    project_root: Path
    host: str
    port: int
    discovery_token: str
    instance_id: str
    process_id: int
    project_hash: str
    supported_protocol_versions: tuple[str, ...]
    transport: str


def load_unity_mcp_core_connection(project_root: str | Path) -> UnityMcpCoreConnection:
    root = _resolve_project_root(project_root)
    descriptor = _read_json_object(root / "Library" / "VRCForge" / "mcp-core.json", "Core descriptor")
    if _require_string(descriptor, "schema", "Core descriptor") != TRANSPORT_SCHEMA:
        raise UnityMcpCoreError("Unity MCP Core descriptor is not recognized.")
    if _require_string(descriptor, "protocolVersion", "Core descriptor") != MODERN_PROTOCOL_VERSION:
        raise UnityMcpCoreError("Unity MCP Core protocol version is not supported.")
    if _require_string(descriptor, "authMode", "Core descriptor") != "bearer-per-request":
        raise UnityMcpCoreError("Unity MCP Core authentication mode is not supported.")
    if _require_string(descriptor, "executionPolicy", "Core descriptor") != "read-direct-app-process-approved-writes":
        raise UnityMcpCoreError("Unity MCP Core execution policy is invalid.")
    if _require_string(descriptor, "host", "Core descriptor") != "127.0.0.1":
        raise UnityMcpCoreError("Unity MCP Core must use the loopback endpoint.")
    supported_versions = _require_supported_versions(descriptor)
    _require_string(descriptor, "transport", "Core descriptor")
    if _require_positive_int(descriptor, "toolCount", "Core descriptor") != EXPECTED_TOOL_COUNT:
        raise UnityMcpCoreError("Unity MCP Core tool contract is invalid.")

    raw_project_path = _require_string(descriptor, "projectPath", "Core descriptor")
    try:
        descriptor_root = Path(raw_project_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.") from None
    if descriptor_root != root:
        raise UnityMcpCoreError("Unity MCP Core belongs to a different project.")
    project_hash = _require_string(descriptor, "projectHash", "Core descriptor")
    if project_hash != hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest():
        raise UnityMcpCoreError("Unity MCP Core project hash is invalid.")

    return UnityMcpCoreConnection(
        project_root=root,
        host="127.0.0.1",
        port=_require_port(descriptor, "port", "Core descriptor"),
        discovery_token=_require_base64_token(descriptor, "authToken", "Core descriptor", 32),
        instance_id=_require_string(descriptor, "instanceId", "Core descriptor"),
        process_id=_require_positive_int(descriptor, "processId", "Core descriptor"),
        project_hash=project_hash,
        supported_protocol_versions=supported_versions,
        transport=_require_string(descriptor, "transport", "Core descriptor"),
    )


class UnityMcpCoreClient:
    """Client for the sole VRCForge MCP 2026-07-28 Core contract."""

    def __init__(self, project_root: str | Path, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 0 and 600 seconds.")
        self._connection = load_unity_mcp_core_connection(project_root)
        if MODERN_PROTOCOL_VERSION not in self._connection.supported_protocol_versions:
            raise UnityMcpCoreError("Unity MCP Core does not support the requested protocol version.")
        if self._connection.transport != "tcp-newline-jsonrpc":
            raise UnityMcpCoreError("Unity MCP Core transport is not supported.")
        self._timeout_seconds = float(timeout_seconds)

    @property
    def protocol_version(self) -> str:
        return MODERN_PROTOCOL_VERSION

    def list_tools(self, *, exposure_layer: str = "planning") -> list[dict[str, Any]]:
        if exposure_layer not in {"planning", "execution"}:
            raise ValueError("exposure_layer must be planning or execution.")
        result = self._request("tools/list", {"exposureLayer": exposure_layer})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            raise UnityMcpCoreError("Unity MCP Core returned an invalid tools list.")
        names = [tool.get("name") for tool in tools]
        expected_names = PLANNING_TOOL_NAMES if exposure_layer == "planning" else EXPECTED_TOOL_NAMES
        if len(tools) != len(expected_names) or not all(isinstance(name, str) and name for name in names):
            raise UnityMcpCoreError("Unity MCP Core returned an invalid tools list.")
        if len(set(names)) != len(expected_names) or set(names) != expected_names:
            raise UnityMcpCoreError("Unity MCP Core tool contract does not match the packaged VRCForge tools.")
        for tool in tools:
            name = tool["name"]
            input_schema = tool.get("inputSchema")
            annotations = tool.get("annotations")
            metadata = tool.get("_meta")
            description = tool.get("description")
            if not isinstance(input_schema, dict) or input_schema.get("type") != "object" \
                    or not isinstance(annotations, dict) or not isinstance(metadata, dict):
                raise UnityMcpCoreError("Unity MCP Core tool metadata is invalid.")
            is_read_only = name in READ_ONLY_TOOL_NAMES
            if annotations.get("readOnlyHint") is not (True if is_read_only else None) \
                    or annotations.get("destructiveHint") is not (None if is_read_only else True) \
                    or metadata.get("permission") != ("ReadOnly" if is_read_only else "RequiresApproval") \
                    or not isinstance(metadata.get("whenToUse"), str) or not metadata["whenToUse"].strip() \
                    or not isinstance(metadata.get("doNotUse"), str) or not metadata["doNotUse"].strip() \
                    or not isinstance(metadata.get("negativeExample"), str) or not metadata["negativeExample"].strip() \
                    or metadata.get("exposureLayer") not in {"planning", "execution"} \
                    or not isinstance(description, str) \
                    or "When to use:" not in description \
                    or "When NOT to use:" not in description \
                    or "Negative example:" not in description:
                raise UnityMcpCoreError("Unity MCP Core tool permissions do not match the packaged VRCForge tools.")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  *, execution_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise ValueError("tool name is required.")
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object.")
        if execution_context is not None and not isinstance(execution_context, dict):
            raise ValueError("execution_context must be an object.")
        if isinstance(execution_context, dict) and execution_context.get("lane") == APP_SETUP_OUTFIT_POLL_LANE:
            if name != "vrc_setup_outfit" or not _is_strict_setup_outfit_job_poll(arguments):
                raise ValueError("Setup Outfit job polling requires exact jobId arguments.")
        if isinstance(execution_context, dict) and execution_context.get("lane") == APP_UNITYPACKAGE_IMPORT_POLL_LANE:
            if name != "vrc_import_unitypackage" or not _is_strict_unitypackage_import_job_poll(arguments):
                raise ValueError("UnityPackage import job polling requires exact jobId arguments.")
        call_arguments = arguments or {}
        request_id = _new_request_id()
        started_at = time.perf_counter()
        audit_base = {
            "requestId": request_id,
            "toolName": name,
            "argumentKeys": sorted(call_arguments),
            "inputSha256": canonical_arguments_sha256(call_arguments),
        }
        try:
            result = self._request(
                "tools/call",
                {"name": name, "arguments": call_arguments},
                execution_context=execution_context,
                request_id=request_id,
            )
            if not isinstance(result, dict):
                raise UnityMcpCoreError("Unity MCP Core returned an invalid tool result.")
            _validate_tool_result(result)
        except Exception as exc:
            _record_unity_mcp_core_call_audit(
                {
                    **audit_base,
                    "resultSummary": "error",
                    "durationMs": round((time.perf_counter() - started_at) * 1000, 3),
                    "errorClass": type(exc).__name__,
                }
            )
            raise
        audited = dict(result)
        metadata = dict(audited.get("_meta") or {}) if isinstance(audited.get("_meta"), dict) else {}
        structured = audited.get("structuredContent")
        status = "error" if audited.get("isError") is True else (
            "pending" if isinstance(structured, dict) and structured.get("_mcp_status") == "pending" else "complete"
        )
        call_audit = {
            **audit_base,
            "resultSummary": status,
            "durationMs": round((time.perf_counter() - started_at) * 1000, 3),
        }
        metadata["io.vrcforge/callAudit"] = call_audit
        _record_unity_mcp_core_call_audit(call_audit)
        audited["_meta"] = metadata
        return audited

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        execution_context: dict[str, Any] | None = None,
        request_id: int | None = None,
    ) -> Any:
        return self._modern_request(method, params, execution_context=execution_context, request_id=request_id)

    def _modern_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        execution_context: dict[str, Any] | None,
        request_id: int | None = None,
    ) -> Any:
        try:
            with self._open_connection() as connection:
                self._send_modern(connection, 1, "server/discover", {})
                discovery = self._receive_modern_response(connection, 1)
                supported = discovery.get("supportedVersions") if isinstance(discovery, dict) else None
                if not isinstance(supported, list) or not all(isinstance(version, str) for version in supported) \
                        or MODERN_PROTOCOL_VERSION not in supported:
                    raise UnityMcpCoreError("Unity MCP Core modern discovery failed.")
                call_id = request_id if request_id is not None else 2
                self._send_modern(connection, call_id, method, params, execution_context=execution_context)
                return self._receive_modern_response(connection, call_id)
        except UnityMcpCoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise UnityMcpCoreError("Unity MCP Core connection failed.") from None

    def _open_connection(self) -> socket.socket:
        connection = socket.create_connection((self._connection.host, self._connection.port), self._timeout_seconds)
        connection.settimeout(self._timeout_seconds)
        return connection

    def _send_modern(self, connection: socket.socket, request_id: int, method: str, params: dict[str, Any],
                     *, execution_context: dict[str, Any] | None = None) -> None:
        request_params = dict(params)
        metadata: dict[str, Any] = {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "VRCForge FastAPI", "version": "1"},
        }
        if execution_context is not None:
            approved_execution = _json_object_copy(execution_context)
            approved_execution["clientProcessId"] = os.getpid()
            approved_execution["projectHash"] = self._connection.project_hash
            approved_execution["instanceId"] = self._connection.instance_id
            metadata["io.vrcforge/approvedExecution"] = approved_execution
        request_params["_meta"] = metadata
        self._write_line(connection, {
            "schema": TRANSPORT_SCHEMA,
            "authorization": "Bearer " + self._connection.discovery_token,
            "message": {"jsonrpc": "2.0", "id": request_id, "method": method, "params": request_params},
        })

    def _receive_modern_response(self, connection: socket.socket, expected_id: int) -> Any:
        return self._validate_response(self._read_line(connection), expected_id, require_complete=True)

    @staticmethod
    def _validate_response(envelope: Any, expected_id: int, *, require_complete: bool) -> Any:
        if not isinstance(envelope, dict) or envelope.get("schema") != TRANSPORT_SCHEMA:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid transport response.")
        message = envelope.get("message")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or message.get("id") != expected_id:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid JSON-RPC response.")
        if "error" in message:
            raise UnityMcpCoreError("Unity MCP Core rejected the request.")
        if "result" not in message:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid JSON-RPC response.")
        result = message["result"]
        if require_complete and (not isinstance(result, dict) or result.get("resultType") != "complete"):
            raise UnityMcpCoreError("Unity MCP Core returned an incomplete modern result.")
        return result

    @staticmethod
    def _write_line(connection: socket.socket, payload: dict[str, Any]) -> None:
        encoded = _encode_payload(payload, "request")
        if b"\n" in encoded or len(encoded) + 1 > MAX_FRAME_BYTES:
            raise UnityMcpCoreError("Unity MCP Core request is too large.")
        connection.sendall(encoded + b"\n")

    @staticmethod
    def _read_line(connection: socket.socket) -> dict[str, Any]:
        data = bytearray()
        while len(data) <= MAX_FRAME_BYTES:
            byte = connection.recv(1)
            if not byte:
                raise UnityMcpCoreError("Unity MCP Core connection closed unexpectedly.")
            if byte == b"\n":
                if not data:
                    raise UnityMcpCoreError("Unity MCP Core response is invalid.")
                return _decode_payload(bytes(data), "response")
            data.extend(byte)
        raise UnityMcpCoreError("Unity MCP Core response is invalid.")




def _validate_tool_result(result: dict[str, Any]) -> None:
    content = result.get("content")
    if not isinstance(result.get("isError"), bool) \
            or not isinstance(content, list) or not content \
            or not all(
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and bool(block["text"].strip())
                for block in content
            ):
        raise UnityMcpCoreError("Unity MCP Core returned an invalid tool result.")

    structured = result.get("structuredContent")
    if result["isError"] is False:
        if not isinstance(structured, dict) or structured.get("success") is not True:
            raise UnityMcpCoreError("Unity MCP Core returned an invalid tool result.")
    elif structured is not None \
            and (not isinstance(structured, dict) or structured.get("success") is not False):
        raise UnityMcpCoreError("Unity MCP Core returned an invalid tool result.")


def _is_strict_setup_outfit_job_poll(arguments: Any) -> bool:
    if not isinstance(arguments, dict) or set(arguments) != {"jobId"}:
        return False
    job_id = arguments.get("jobId")
    if not isinstance(job_id, str) or len(job_id) != 32:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in job_id)


def _is_strict_unitypackage_import_job_poll(arguments: Any) -> bool:
    return _is_strict_setup_outfit_job_poll(arguments)


def _encode_payload(payload: dict[str, Any], direction: str) -> bytes:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise UnityMcpCoreError("Unity MCP Core " + direction + " is invalid.") from None
    if not 0 < len(encoded) <= MAX_FRAME_BYTES:
        raise UnityMcpCoreError("Unity MCP Core " + direction + " is too large.")
    return encoded


def canonical_arguments_sha256(arguments: dict[str, Any]) -> str:
    """Bind an approved Core execution to the exact JSON tool arguments."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object.")
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("arguments must be JSON-compatible.") from None
    return hashlib.sha256(encoded).hexdigest()


def _new_request_id() -> int:
    return 2 + int.from_bytes(os.urandom(8), "big") % (2**63 - 3)


def _decode_payload(data: bytes, direction: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise UnityMcpCoreError("Unity MCP Core " + direction + " is invalid.") from None
    if not isinstance(value, dict):
        raise UnityMcpCoreError("Unity MCP Core " + direction + " is invalid.")
    return value


def _json_object_copy(value: dict[str, Any]) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("execution_context must be JSON-compatible.") from None
    if not isinstance(copied, dict):
        raise ValueError("execution_context must be an object.")
    return copied


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


def _require_supported_versions(document: dict[str, Any]) -> tuple[str, ...]:
    value = document.get("supportedProtocolVersions")
    if not isinstance(value, list) or not value or not all(isinstance(version, str) for version in value):
        raise UnityMcpCoreError("Core descriptor is invalid.")
    if len(value) != len(set(value)):
        raise UnityMcpCoreError("Core descriptor is invalid.")
    if any(version not in SUPPORTED_PROTOCOL_VERSIONS for version in value):
        raise UnityMcpCoreError("Core descriptor is invalid.")
    return tuple(value)


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
