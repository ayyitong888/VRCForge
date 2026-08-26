"""VRCForge-owned MCP 2026-07-28 request boundary.

This module deliberately implements only the small server surface needed by
the desktop agent gateway.  It does not import a third-party MCP runtime and
it does not provide a compatibility mode for older lifecycle negotiation.
Callers supply the tool catalogue and invocation callbacks, so permission and
approval policy remain owned by the gateway that mounts this transport.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence, TextIO
from urllib.parse import urlsplit

from external_tool_result_contract import build_external_tool_error
from agent_tool_result_contract import normalize_agent_tool_result


PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
DEFAULT_MAX_BODY_BYTES = 1_048_576
DEFAULT_MAX_STDIO_LINE_BYTES = 1_048_576

HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

JsonObject = dict[str, Any]
ToolListCallback = Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | Awaitable[Sequence[Mapping[str, Any]]]]
ToolCallCallback = Callable[[str, Mapping[str, Any]], Any | Awaitable[Any]]
BearerValidator = Callable[[str], bool | Awaitable[bool]]
ToolListRevisionCallback = Callable[[], Any]


@dataclass(frozen=True)
class Mcp2026Error(Exception):
    """A shaped protocol error that can safely cross an HTTP or stdio boundary."""

    code: int
    message: str
    http_status: int = 400
    data: Mapping[str, Any] | None = None


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
    body: JsonObject = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    context = dict(data) if isinstance(data, Mapping) else {}
    context.setdefault("protocolNamespace", PROTOCOL_VERSION_META_KEY)
    context.setdefault("protocolVersion", PROTOCOL_VERSION)
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
    body["error"]["data"] = {**error_object, **context}
    return body


def _server_info(name: str, version: str) -> JsonObject:
    return {"name": name, "version": version}


def _success(request_id: Any, result: Mapping[str, Any], *, server_name: str, server_version: str) -> JsonObject:
    completed = dict(result)
    completed["resultType"] = "complete"
    result_meta = completed.get("_meta")
    if not isinstance(result_meta, Mapping):
        result_meta = {}
    completed["_meta"] = {
        **dict(result_meta),
        SERVER_INFO_META_KEY: _server_info(server_name, server_version),
    }
    return {"jsonrpc": "2.0", "id": request_id, "result": completed}


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _tool_result_content_text(structured: Mapping[str, Any]) -> str:
    """Keep discovery text compact; structuredContent remains lossless."""

    if structured.get("schema") == "vrcforge.external_tool_blocks.v2":
        tree = structured.get("tree") if isinstance(structured.get("tree"), Mapping) else {}
        children = tree.get("children") if isinstance(tree.get("children"), Sequence) else []
        blocks = []
        for item in children:
            if not isinstance(item, Mapping):
                continue
            tool_names = item.get("toolNames")
            child_tools = item.get("children")
            tool_count = (
                len(tool_names)
                if isinstance(tool_names, Sequence) and not isinstance(tool_names, (str, bytes))
                else len(child_tools)
                if isinstance(child_tools, Sequence) and not isinstance(child_tools, (str, bytes))
                else 0
            )
            blocks.append(
                {
                    "index": item.get("index"),
                    "name": item.get("name"),
                    "loaded": item.get("loaded"),
                    "whenToUse": item.get("whenToUse"),
                    "whenNotToUse": item.get("whenNotToUse"),
                    "toolCount": tool_count,
                }
            )
        compact = {
            "ok": structured.get("ok"),
            "schema": structured.get("schema"),
            "selectedBlock": structured.get("selectedBlock"),
            "loadedBlocks": structured.get("loadedBlocks"),
            "blocks": blocks,
            "note": "Full tool-name index is in structuredContent; full descriptions and schemas load only for selected blocks.",
        }
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return json.dumps(structured, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _normalise_tool(tool: Mapping[str, Any]) -> JsonObject:
    name = tool.get("name")
    if not _is_nonempty_string(name):
        raise Mcp2026Error(-32603, "Tool catalogue returned a tool without a name", 500)
    schema = tool.get("inputSchema") or tool.get("inputsSchema")
    if not isinstance(schema, Mapping):
        schema = {"type": "object", "additionalProperties": True}
    elif schema.get("type") != "object":
        raise Mcp2026Error(-32603, "Tool catalogue inputSchema must be an object schema", 500)
    supplied_meta = tool.get("_meta") if isinstance(tool.get("_meta"), Mapping) else {}
    is_write = bool(
        tool.get("write")
        or tool.get("requiresApproval")
        or str(supplied_meta.get("permission") or "").strip().casefold() == "write"
    )
    summary = str(tool.get("description") or name).strip()
    if not all(section in summary for section in ("When to use:", "When NOT to use:", "Negative example:")):
        when_not = (
            "Do not use while planning, for hypothetical or quoted requests, or without an explicit "
            "project change request. High-risk or destructive calls require an external user confirmation "
            "round trip before the tool can mutate anything."
            if is_write
            else "Do not use for general questions, quoted examples, hypothetical requests, or when the user forbids inspection."
        )
        negative = (
            f"Explain {name} conceptually, but do not modify the project."
            if is_write
            else f"Mention {name} without inspecting the current project."
        )
        summary = f"When to use: {summary}\nWhen NOT to use: {when_not}\nNegative example: {negative}"
    normalised: JsonObject = {
        "name": str(name),
        "description": summary,
        "inputSchema": dict(schema),
    }
    title = tool.get("title")
    if _is_nonempty_string(title):
        normalised["title"] = str(title)
    output_schema = tool.get("outputSchema") or tool.get("outputsSchema")
    if isinstance(output_schema, Mapping):
        normalised["outputSchema"] = dict(output_schema)
    normalised["annotations"] = {
        "readOnlyHint": not is_write,
        "destructiveHint": False,
        "openWorldHint": False,
    }
    normalised["_meta"] = {
        **dict(supplied_meta),
        "permission": str(supplied_meta.get("permission") or ("Write" if is_write else "ReadOnly")),
    }
    return normalised


def _strict_json_clone(value: Any, *, _seen: set[int] | None = None, _depth: int = 0) -> Any:
    """Return a JSON-only copy without repr fallbacks or non-finite numbers."""
    if _depth > 64:
        raise ValueError("JSON nesting exceeds the supported limit")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON numbers are unsupported")
        return value
    if isinstance(value, Mapping):
        seen = _seen if _seen is not None else set()
        marker = id(value)
        if marker in seen:
            raise ValueError("Cyclic JSON objects are unsupported")
        seen.add(marker)
        try:
            if any(not isinstance(key, str) for key in value):
                raise ValueError("JSON object keys must be strings")
            return {
                key: _strict_json_clone(item, _seen=seen, _depth=_depth + 1)
                for key, item in value.items()
            }
        finally:
            seen.remove(marker)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        seen = _seen if _seen is not None else set()
        marker = id(value)
        if marker in seen:
            raise ValueError("Cyclic JSON arrays are unsupported")
        seen.add(marker)
        try:
            return [_strict_json_clone(item, _seen=seen, _depth=_depth + 1) for item in value]
        finally:
            seen.remove(marker)
    raise ValueError("Value is not JSON serializable")


def _strict_json_loads(text: str | bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON number is unsupported: {value}")

    return json.loads(text, parse_constant=reject_constant)


def _validate_request(message: Any) -> tuple[Any, str, JsonObject]:
    if not isinstance(message, Mapping):
        raise Mcp2026Error(-32600, "JSON-RPC request must be an object")
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0":
        raise Mcp2026Error(-32600, "JSON-RPC version must be 2.0")
    if type(request_id) not in {str, int}:
        raise Mcp2026Error(-32600, "JSON-RPC request id must be a string or integer")
    method = message.get("method")
    if not _is_nonempty_string(method):
        raise Mcp2026Error(-32600, "JSON-RPC method must be a non-empty string")
    params = message.get("params")
    if not isinstance(params, Mapping):
        raise Mcp2026Error(-32020, "MCP request params must be an object")
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        raise Mcp2026Error(-32020, "MCP request params._meta must be an object")
    requested_version = meta.get(PROTOCOL_VERSION_META_KEY)
    if requested_version != PROTOCOL_VERSION:
        raise Mcp2026Error(
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported MCP protocol version",
            data={"supported": [PROTOCOL_VERSION], "requested": requested_version},
        )
    if not isinstance(meta.get(CLIENT_CAPABILITIES_META_KEY), Mapping):
        raise Mcp2026Error(
            MISSING_REQUIRED_CLIENT_CAPABILITY,
            "MCP request client capabilities must be an object",
            data={"requiredCapabilities": []},
        )
    if CLIENT_INFO_META_KEY in meta:
        client_info = meta[CLIENT_INFO_META_KEY]
        if not isinstance(client_info, Mapping) or not _is_nonempty_string(client_info.get("name")) or not _is_nonempty_string(client_info.get("version")):
            raise Mcp2026Error(-32602, "MCP clientInfo must contain non-empty name and version")
    if "exposureLayer" in params:
        exposure_layer = params.get("exposureLayer")
        if not isinstance(exposure_layer, str) or exposure_layer not in {"planning", "execution"}:
            raise Mcp2026Error(-32602, "exposureLayer must be planning or execution")
    if "toolBlocks" in params:
        tool_blocks = params.get("toolBlocks")
        if (
            not isinstance(tool_blocks, Sequence)
            or isinstance(tool_blocks, (str, bytes, bytearray))
            or not all(_is_nonempty_string(item) for item in tool_blocks)
        ):
            raise Mcp2026Error(-32602, "toolBlocks must be an array of non-empty strings")
    return request_id, method, dict(params)


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class Mcp2026Router:
    """Strict, callback-backed MCP 2026-07-28 JSON-RPC router."""

    def __init__(
        self,
        tool_list: ToolListCallback,
        tool_call: ToolCallCallback,
        *,
        server_name: str = "VRCForge",
        server_version: str = "1.7.10",
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

    def drain_notifications(self) -> list[JsonObject]:
        notifications = list(self._pending_notifications)
        self._pending_notifications.clear()
        return notifications

    async def handle_async(self, message: Any) -> tuple[JsonObject, int]:
        """Return a JSON-RPC response and its transport-appropriate status."""
        request_id = message.get("id") if isinstance(message, Mapping) else None
        try:
            request_id, method, params = _validate_request(message)
            if method == "server/discover":
                return _success(
                    request_id,
                    {
                        "supportedVersions": [PROTOCOL_VERSION],
                        "capabilities": {
                            "tools": (
                                {"listChanged": True}
                                if self._tool_list_revision is not None
                                else {}
                            )
                        },
                        "instructions": (
                            "Use VRCForge as a tool server for avatar work; the external client owns "
                            "its Agent loop. Low/medium writes execute through checkpoint safety, while "
                            "high-risk or destructive calls first return user_confirmation_required."
                        ),
                    },
                    server_name=self.server_name,
                    server_version=self.server_version,
                ), 200
            if method == "tools/list":
                supplied_tools = await _resolve(self._tool_list(params))
                if not isinstance(supplied_tools, Sequence) or isinstance(supplied_tools, (str, bytes, bytearray)):
                    raise Mcp2026Error(-32603, "Tool catalogue must return a sequence", 500)
                tools = [_normalise_tool(tool) for tool in supplied_tools if isinstance(tool, Mapping)]
                if len(tools) != len(supplied_tools):
                    raise Mcp2026Error(-32603, "Tool catalogue must contain only objects", 500)
                tools.sort(key=lambda item: item["name"])
                if len({item["name"] for item in tools}) != len(tools):
                    raise Mcp2026Error(-32603, "Tool catalogue contains duplicate names", 500)
                return _success(
                    request_id,
                    {"tools": tools},
                    server_name=self.server_name,
                    server_version=self.server_version,
                ), 200
            if method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                if not _is_nonempty_string(tool_name) or not isinstance(arguments, Mapping):
                    raise Mcp2026Error(-32602, "tools/call requires a non-empty name and object arguments")
                # Tool visibility is a discovery concern. Standard MCP clients do
                # not repeat a tools/list exposure hint on tools/call, so validate
                # the call against the explicit execution catalogue by default;
                # the callback still enforces auth, permissions and approval.
                catalogue_params = dict(params)
                catalogue_params.setdefault("exposureLayer", "execution")
                # Block selection limits discovery/context only. Permission and
                # approval remain enforced by the independent external
                # catalogue, so a client that already knows an exact atomic
                # tool may call it without replaying tools/list block hints.
                catalogue_params.setdefault("toolBlocks", ["*"])
                supplied_tools = await _resolve(self._tool_list(catalogue_params))
                if not isinstance(supplied_tools, Sequence) or isinstance(supplied_tools, (str, bytes, bytearray)):
                    raise Mcp2026Error(-32603, "Tool catalogue must return a sequence", 500)
                allowed_names = {
                    str(item.get("name"))
                    for item in supplied_tools
                    if isinstance(item, Mapping) and _is_nonempty_string(item.get("name"))
                }
                if tool_name not in allowed_names:
                    raise Mcp2026Error(-32602, "Unknown or unavailable tool")
                revision_before = (
                    self._tool_list_revision()
                    if self._tool_list_revision is not None
                    else None
                )
                callback_result = await _resolve(self._tool_call(tool_name, dict(arguments)))
                revision_after = (
                    self._tool_list_revision()
                    if self._tool_list_revision is not None
                    else None
                )
                if self._tool_list_revision is not None and revision_after != revision_before:
                    self._pending_notifications.append(
                        {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
                    )
                raw_structured = dict(callback_result) if isinstance(callback_result, Mapping) else {"value": callback_result}
                outcome = normalize_agent_tool_result(
                    raw_structured,
                    fallback_summary=f"{tool_name} completed.",
                    write=bool(raw_structured.get("write")),
                )
                raw_structured.setdefault("outcome", outcome)
                structured = _strict_json_clone(raw_structured)
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": _tool_result_content_text(structured),
                        }
                    ],
                    "structuredContent": structured,
                    "isError": structured.get("outcome", {}).get("status") == "failed",
                }
                return _success(request_id, result, server_name=self.server_name, server_version=self.server_version), 200
            raise Mcp2026Error(-32601, "MCP method not found", 404)
        except Mcp2026Error as exc:
            return _error(request_id, exc.code, exc.message, exc.data), exc.http_status
        except Exception as exc:
            # Preserve a bounded exact cause chain; raw secrets remain redacted.
            return _error(
                request_id,
                -32603,
                "Internal MCP server error",
                failure_phase="protocol_router_internal",
                tool_routing_started=None,
                mutation_started=None,
                committed=None,
                exception=exc,
            ), 500

    def handle(self, message: Any) -> tuple[JsonObject, int]:
        """Synchronous helper for ordinary callback implementations and tests."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.handle_async(message))
        raise RuntimeError("Use await router.handle_async() from an active event loop")


def _origin_is_loopback(value: str) -> bool:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _header_has_media_type(value: str, expected: str) -> bool:
    return any(part.strip().split(";", 1)[0].strip().lower() == expected for part in value.split(","))


def _request_header_values(request: Any, name: str) -> list[str]:
    expected = name.encode("ascii").lower()
    return [
        raw_value.decode("latin-1")
        for raw_name, raw_value in request.scope.get("headers", [])
        if raw_name.lower() == expected
    ]


def _single_header(request: Any, name: str, *, required: bool = True) -> str | None:
    values = _request_header_values(request, name)
    if len(values) != 1 or not values[0].strip():
        if not required and not values:
            return None
        raise Mcp2026Error(HEADER_MISMATCH, f"{name} must appear exactly once", 400)
    return values[0]


def _host_is_loopback(value: str) -> bool:
    try:
        parsed = urlsplit(f"//{value}")
        return (
            parsed.hostname in {"localhost", "127.0.0.1", "::1", "testserver", "test"}
            and parsed.username is None
            and parsed.password is None
            and parsed.path == ""
        )
    except ValueError:
        return False


def _transport_error(message: str, *, request_id: Any = None) -> tuple[JsonObject, int]:
    return _error(request_id, HEADER_MISMATCH, message), 400


def create_asgi_app(
    router: Mcp2026Router,
    *,
    bearer_validator: BearerValidator | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    route_path: str = "/",
):
    """Create a POST-only, loopback-origin-checked Starlette endpoint.

    When a bearer validator is supplied, the endpoint requires a single
    ``Authorization: Bearer <token>`` credential and delegates validation to
    it.  The caller owns token issuance, permissions, and lifecycle.
    """
    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    if not route_path.startswith("/") or (len(route_path) > 1 and route_path.endswith("/")):
        raise ValueError("route_path must be an absolute path without a trailing slash")
    try:
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.applications import Starlette
    except ImportError as exc:  # pragma: no cover - package environment error
        raise RuntimeError("Starlette is required for the MCP HTTP transport") from exc

    async def endpoint(request: Request):
        if request.method != "POST":
            return JSONResponse(_error(None, -32601, "Only POST is supported"), status_code=405, headers={"Allow": "POST"})
        try:
            host = _single_header(request, "Host")
            origin = _single_header(request, "Origin", required=False)
            accept = ",".join(_request_header_values(request, "Accept"))
            protocol_version = _single_header(request, "MCP-Protocol-Version")
            content_type = _single_header(request, "Content-Type")
            method_header = _single_header(request, "Mcp-Method")
            content_length_values = _request_header_values(request, "Content-Length")
            if len(content_length_values) > 1:
                raise Mcp2026Error(HEADER_MISMATCH, "Content-Length must appear at most once", 400)
            content_length = content_length_values[0] if content_length_values else None
        except Mcp2026Error as exc:
            return JSONResponse(_error(None, exc.code, exc.message), status_code=exc.http_status)
        if not _host_is_loopback(str(host)):
            return JSONResponse(_error(None, -32600, "MCP endpoint host must be loopback"), status_code=403)
        if origin and not _origin_is_loopback(origin):
            return JSONResponse(_error(None, -32600, "Origin must be a loopback origin"), status_code=403)
        if not (_header_has_media_type(accept, "application/json") and _header_has_media_type(accept, "text/event-stream")):
            payload, status = _transport_error("Accept must include application/json and text/event-stream")
            return JSONResponse(payload, status_code=status)
        if protocol_version != PROTOCOL_VERSION:
            payload, status = _transport_error("MCP-Protocol-Version must be 2026-07-28")
            return JSONResponse(payload, status_code=status)
        if str(content_type).split(";", 1)[0].strip().lower() != "application/json":
            payload, status = _transport_error("Content-Type must be application/json")
            return JSONResponse(payload, status_code=status)
        if content_length:
            try:
                if int(content_length) > max_body_bytes:
                    payload, status = _transport_error("MCP request body exceeds the configured limit")
                    return JSONResponse(payload, status_code=status)
            except ValueError:
                payload, status = _transport_error("Content-Length must be an integer")
                return JSONResponse(payload, status_code=status)
        if bearer_validator is not None:
            authorization_values = _request_header_values(request, "Authorization")
            authorization = authorization_values[0] if len(authorization_values) == 1 else ""
            parts = authorization.split(" ")
            token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
            if not token or any(character.isspace() for character in token) or not await _resolve(bearer_validator(token)):
                payload = _error(None, -32000, "Bearer authentication failed")
                return JSONResponse(payload, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_body_bytes:
                payload, status = _transport_error("MCP request body exceeds the configured limit")
                return JSONResponse(payload, status_code=status)
            chunks.append(chunk)
        try:
            message = _strict_json_loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            return JSONResponse(_error(None, -32700, "Invalid JSON"), status_code=400)
        if isinstance(message, list):
            return JSONResponse(_error(None, -32600, "JSON-RPC batch requests are not supported"), status_code=400)
        if not isinstance(message, Mapping):
            return JSONResponse(_error(None, -32600, "JSON-RPC request must be an object"), status_code=400)
        method = message.get("method")
        if method_header != method:
            payload, status = _transport_error("Mcp-Method must match JSON-RPC method")
            return JSONResponse(payload, status_code=status)
        name_values = _request_header_values(request, "Mcp-Name")
        if method == "tools/call":
            params = message.get("params")
            body_name = params.get("name") if isinstance(params, Mapping) else None
            if len(name_values) != 1 or not name_values[0].strip() or name_values[0] != body_name:
                payload, status = _transport_error("Mcp-Name must appear exactly once and match tools/call name")
                return JSONResponse(payload, status_code=status)
        elif name_values:
            payload, status = _transport_error("Mcp-Name is only valid for tools/call")
            return JSONResponse(payload, status_code=status)
        payload, status = await router.handle_async(message)
        return JSONResponse(payload, status_code=status)

    # Let Starlette produce the strict ``Allow: POST`` response for every
    # non-POST method before the endpoint is entered.
    return Starlette(routes=[Route(route_path, endpoint, methods=["GET", "POST", "DELETE", "HEAD", "OPTIONS"])])


def create_agent_mcp_2026_asgi_app(
    router: Mcp2026Router,
    *,
    bearer_validator: BearerValidator,
    **kwargs: Any,
):
    """Explicitly named alias for composition sites that host the agent MCP."""
    if bearer_validator is None:
        raise ValueError("The production MCP HTTP endpoint requires bearer authentication")
    return create_asgi_app(router, bearer_validator=bearer_validator, **kwargs)


def run_stdio_loop(
    router: Mcp2026Router,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    max_line_bytes: int = DEFAULT_MAX_STDIO_LINE_BYTES,
) -> int:
    """Serve one UTF-8 JSON-RPC object per newline; stdout contains responses only."""
    source = input_stream if input_stream is not None else sys.stdin
    sink = output_stream if output_stream is not None else sys.stdout
    if max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be positive")
    while True:
        line = source.readline(max_line_bytes + 2)
        if line == "":
            break
        truncated = len(line) == max_line_bytes + 2 and not line.endswith("\n")
        if truncated:
            while True:
                remainder = source.readline(max_line_bytes + 2)
                if remainder == "" or remainder.endswith("\n"):
                    break
        try:
            encoded = line.encode("utf-8")
        except UnicodeEncodeError:
            encoded = b""
        if truncated or not encoded or len(encoded) > max_line_bytes:
            response = _error(None, -32700, "Invalid or oversized JSON line")
        else:
            try:
                message = _strict_json_loads(line)
            except (json.JSONDecodeError, ValueError, RecursionError):
                response = _error(None, -32700, "Invalid JSON")
            else:
                if isinstance(message, list):
                    response = _error(None, -32600, "JSON-RPC batch requests are not supported")
                else:
                    response, _ = router.handle(message)
        sink.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        for notification in router.drain_notifications():
            sink.write(json.dumps(notification, ensure_ascii=False, separators=(",", ":")) + "\n")
        sink.flush()
    return 0


serve_stdio = run_stdio_loop
