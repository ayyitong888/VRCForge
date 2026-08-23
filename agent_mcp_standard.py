"""Explicit standard MCP 1.x stdio adapter for external agent clients.

The first-party VRCForge MCP boundary remains in :mod:`agent_mcp_2026`.
This adapter exists only at the App-side external-agent edge for clients such
as DeepSeek Harness that use the published MCP 1.x lifecycle.
"""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any, Awaitable, Callable, Mapping, Sequence, TextIO

from agent_mcp_2026 import (
    PROTOCOL_VERSION as VRCFORGE_2026_PROTOCOL_VERSION,
    Mcp2026Router,
    _normalise_tool,
    _strict_json_clone,
    _strict_json_loads,
    _tool_result_content_text,
)
from external_tool_result_contract import build_external_tool_error
from agent_tool_result_contract import normalize_agent_tool_result


LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
    "2024-10-07",
)
DEFAULT_MAX_STDIO_LINE_BYTES = 1_048_576

JsonObject = dict[str, Any]
ToolListCallback = Callable[[], Sequence[Mapping[str, Any]] | Awaitable[Sequence[Mapping[str, Any]]]]
ToolCallCallback = Callable[[str, Mapping[str, Any]], Any | Awaitable[Any]]
ToolListRevisionCallback = Callable[[], Any]


class McpStandardError(Exception):
    def __init__(self, code: int, message: str, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = dict(data) if data else None


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _error(
    request_id: Any,
    code: int,
    message: str,
    data: Mapping[str, Any] | None = None,
    *,
    failure_phase: str = "protocol_request_validation",
    tool_routing_started: bool | None = False,
    mutation_started: bool | None = False,
    committed: bool | None = False,
    exception: BaseException | None = None,
) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    context = dict(data) if isinstance(data, Mapping) else {}
    context.setdefault("protocolNamespace", "initialize.params.protocolVersion")
    context.setdefault("protocolProfile", "mcp-1x")
    error_object = build_external_tool_error(
        error=message,
        error_code=f"mcp_jsonrpc_{code}",
        failure_layer="external_mcp_protocol",
        failure_phase=failure_phase,
        operation_kind="protocol",
        tool_routing_started=tool_routing_started,
        mutation_started=mutation_started,
        committed=committed,
        exception=exception,
        retryable=False,
        checkpoint_recovery_required=False,
        temporary_cleanup_required=False,
        details=context,
    )
    # Preserve protocol-specific facts at their original data keys while the
    # same object also carries the canonical VRCForge rejection contract.
    error["data"] = {**error_object, **context}
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _success(request_id: Any, result: Mapping[str, Any]) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class McpStandardRouter:
    """Stateful JSON-RPC router for the MCP 1.x initialize/tools lifecycle."""

    def __init__(
        self,
        tool_list: ToolListCallback,
        tool_call: ToolCallCallback,
        *,
        server_name: str = "VRCForge",
        server_version: str = "1.5.1",
        tool_list_revision: ToolListRevisionCallback | None = None,
    ) -> None:
        if not _is_nonempty_string(server_name) or not _is_nonempty_string(server_version):
            raise ValueError("server_name and server_version must be non-empty strings")
        self._tool_list = tool_list
        self._tool_call = tool_call
        self.server_name = server_name
        self.server_version = server_version
        self._tool_list_revision = tool_list_revision
        self._pending_notifications: list[JsonObject] = []
        self._initialized = False

    def drain_notifications(self) -> list[JsonObject]:
        notifications = list(self._pending_notifications)
        self._pending_notifications.clear()
        return notifications

    def _request(self, message: Any) -> tuple[Any, str, JsonObject, bool]:
        if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
            raise McpStandardError(-32600, "JSON-RPC request must be a 2.0 object")
        notification = "id" not in message
        request_id = message.get("id")
        if not notification and type(request_id) not in {str, int}:
            raise McpStandardError(-32600, "JSON-RPC request id must be a string or integer")
        method = message.get("method")
        if not _is_nonempty_string(method):
            raise McpStandardError(-32600, "JSON-RPC method must be a non-empty string")
        params = message.get("params", {})
        if not isinstance(params, Mapping):
            raise McpStandardError(-32602, "MCP request params must be an object")
        return request_id, str(method), dict(params), notification

    async def handle_async(self, message: Any) -> JsonObject | None:
        request_id = message.get("id") if isinstance(message, Mapping) else None
        try:
            request_id, method, params, notification = self._request(message)
            if method == "initialize":
                if notification:
                    raise McpStandardError(-32600, "initialize must be a request")
                protocol_version = params.get("protocolVersion")
                client_info = params.get("clientInfo")
                if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
                    raise McpStandardError(
                        -32602,
                        "Unsupported MCP protocol version",
                        {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": protocol_version},
                    )
                if not isinstance(params.get("capabilities"), Mapping):
                    raise McpStandardError(-32602, "initialize capabilities must be an object")
                if not isinstance(client_info, Mapping) or not _is_nonempty_string(client_info.get("name")) or not _is_nonempty_string(client_info.get("version")):
                    raise McpStandardError(-32602, "initialize clientInfo must contain non-empty name and version")
                self._initialized = True
                return _success(
                    request_id,
                    {
                        "protocolVersion": protocol_version,
                        "capabilities": {
                            "tools": (
                                {"listChanged": True}
                                if self._tool_list_revision is not None
                                else {}
                            )
                        },
                        "serverInfo": {"name": self.server_name, "version": self.server_version},
                        "instructions": (
                            "Use VRCForge tools for supervised avatar work. Project writes remain "
                            "subject to VRCForge approval and checkpoint policy."
                        ),
                    },
                )
            if method == "notifications/initialized":
                if not notification or not self._initialized:
                    raise McpStandardError(-32600, "notifications/initialized requires a completed initialize request")
                return None
            if not self._initialized:
                raise McpStandardError(-32002, "MCP server is not initialized")
            if method == "ping":
                return None if notification else _success(request_id, {})
            if method == "tools/list":
                if notification:
                    return None
                supplied = await _resolve(self._tool_list())
                if not isinstance(supplied, Sequence) or isinstance(supplied, (str, bytes, bytearray)):
                    raise McpStandardError(-32603, "Tool catalogue must return a sequence")
                tools = [_normalise_tool(tool) for tool in supplied if isinstance(tool, Mapping)]
                if len(tools) != len(supplied):
                    raise McpStandardError(-32603, "Tool catalogue must contain only objects")
                tools.sort(key=lambda item: item["name"])
                if len({item["name"] for item in tools}) != len(tools):
                    raise McpStandardError(-32603, "Tool catalogue contains duplicate names")
                return _success(request_id, {"tools": tools})
            if method == "tools/call":
                if notification:
                    return None
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not _is_nonempty_string(name) or not isinstance(arguments, Mapping):
                    raise McpStandardError(-32602, "tools/call requires a non-empty name and object arguments")
                supplied = await _resolve(self._tool_list())
                allowed = {str(tool.get("name")) for tool in supplied if isinstance(tool, Mapping) and _is_nonempty_string(tool.get("name"))}
                if name not in allowed:
                    raise McpStandardError(-32602, "Tool is not exposed by this MCP server")
                revision_before = (
                    self._tool_list_revision()
                    if self._tool_list_revision is not None
                    else None
                )
                result = _strict_json_clone(await _resolve(self._tool_call(str(name), dict(arguments))))
                revision_after = (
                    self._tool_list_revision()
                    if self._tool_list_revision is not None
                    else None
                )
                if self._tool_list_revision is not None and revision_after != revision_before:
                    self._pending_notifications.append(
                        {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
                    )
                if not isinstance(result, Mapping):
                    result = {"ok": True, "value": result}
                structured = dict(result)
                outcome = normalize_agent_tool_result(
                    structured,
                    fallback_summary=f"{name} completed.",
                    write=bool(structured.get("write")),
                )
                structured["outcome"] = outcome
                return _success(
                    request_id,
                    {
                        "content": [{"type": "text", "text": _tool_result_content_text(structured)}],
                        "structuredContent": structured,
                        "isError": outcome.get("status") == "failed",
                    },
                )
            raise McpStandardError(-32601, "Method not found")
        except McpStandardError as exc:
            return _error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            return _error(
                request_id,
                -32603,
                "Internal MCP server error",
                failure_phase="protocol_router_internal",
                tool_routing_started=None,
                mutation_started=None,
                committed=None,
                exception=exc,
            )

    def handle(self, message: Any) -> JsonObject | None:
        import asyncio

        return asyncio.run(self.handle_async(message))


def run_standard_stdio_loop(
    router: McpStandardRouter,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    max_line_bytes: int = DEFAULT_MAX_STDIO_LINE_BYTES,
) -> int:
    source = input_stream or sys.stdin
    sink = output_stream or sys.stdout
    for raw_line in source:
        request_id: Any = None
        try:
            if len(raw_line.encode("utf-8")) > max_line_bytes:
                response = _error(None, -32700, "JSON-RPC message exceeds the supported size")
            else:
                message = _strict_json_loads(raw_line)
                request_id = message.get("id") if isinstance(message, Mapping) else None
                response = router.handle(message)
        except Exception:
            response = _error(request_id, -32700, "Invalid JSON")
        if response is None:
            continue
        sink.write(json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        for notification in router.drain_notifications():
            sink.write(
                json.dumps(notification, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                + "\n"
            )
        sink.flush()
    return 0


def run_negotiated_stdio_loop(
    router_2026: Mcp2026Router,
    router_standard: McpStandardRouter,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    diagnostic_stream: TextIO | None = None,
    max_line_bytes: int = DEFAULT_MAX_STDIO_LINE_BYTES,
) -> int:
    """Select one protocol from the first valid frame, then freeze it.

    VRCForge 2026 clients advertise their protocol in request metadata. MCP
    1.x clients begin with ``initialize``. No mid-connection profile switch is
    allowed, so a downgrade cannot alter a catalogue after discovery.
    """

    source = input_stream or sys.stdin
    sink = output_stream or sys.stdout
    diagnostics = diagnostic_stream or sys.stderr
    selected_profile: str | None = None
    for raw_line in source:
        request_id: Any = None
        try:
            if len(raw_line.encode("utf-8")) > max_line_bytes:
                response = _error(None, -32700, "JSON-RPC message exceeds the supported size")
            else:
                message = _strict_json_loads(raw_line)
                request_id = message.get("id") if isinstance(message, Mapping) else None
                if selected_profile is None:
                    method = message.get("method") if isinstance(message, Mapping) else None
                    params = message.get("params") if isinstance(message, Mapping) else None
                    meta = params.get("_meta") if isinstance(params, Mapping) else None
                    if isinstance(meta, Mapping) and meta.get("io.modelcontextprotocol/protocolVersion") == VRCFORGE_2026_PROTOCOL_VERSION:
                        selected_profile = "vrcforge-2026"
                    elif method == "initialize":
                        selected_profile = "mcp-1x"
                    else:
                        response = _error(
                            request_id,
                            -32022,
                            "MCP protocol profile could not be negotiated",
                            {
                                "preferred": "vrcforge-2026",
                                "fallback": "mcp-1x",
                                "protocolProfile": "unnegotiated",
                                "protocolNamespace": "io.modelcontextprotocol/protocolVersion",
                                "protocolMetadataLocation": "params._meta",
                                "protocolVersion": VRCFORGE_2026_PROTOCOL_VERSION,
                                "fallbackProtocolNamespace": "initialize.params.protocolVersion",
                                "receivedMethod": method,
                                "receivedProtocolVersion": (
                                    meta.get("io.modelcontextprotocol/protocolVersion")
                                    if isinstance(meta, Mapping)
                                    else None
                                ),
                            },
                        )
                        sink.write(json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                        sink.flush()
                        continue
                    diagnostics.write(
                        json.dumps(
                            {
                                "schema": "vrcforge.mcp.profile_selection.v1",
                                "preferredProfile": "vrcforge-2026",
                                "selectedProfile": selected_profile,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    diagnostics.flush()
                if selected_profile == "vrcforge-2026":
                    response, _status = router_2026.handle(message)
                else:
                    response = router_standard.handle(message)
        except Exception:
            response = _error(request_id, -32700, "Invalid JSON")
        if response is None:
            continue
        sink.write(json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        active_router = router_2026 if selected_profile == "vrcforge-2026" else router_standard
        for notification in active_router.drain_notifications():
            sink.write(
                json.dumps(notification, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                + "\n"
            )
        sink.flush()
    return 0


__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "McpStandardRouter",
    "run_negotiated_stdio_loop",
    "run_standard_stdio_loop",
]
