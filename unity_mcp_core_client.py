"""Bounded client for the project-scoped VRCForge Unity MCP Core transport."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import socket
import struct
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from unity_mcp_tool_contract import (
    CORE_IDENTITY,
    EXPECTED_TOOL_NAMES,
    HANDSHAKE_PROTOCOL,
    PLANNING_TOOL_NAMES,
    PREVIOUS_CORE_TOOL_CONTRACT_VERSION,
    PREVIOUS_CORE_TOOL_NAMES,
    PRODUCT_VERSION,
    READ_ONLY_TOOL_NAMES,
    TOOL_CONTRACT_VERSION,
)


TRANSPORT_SCHEMA = "vrcforge.mcp.transport.v2"
MODERN_PROTOCOL_VERSION = "2026-07-28"
MINIMUM_CORE_PROTOCOL_VERSION = "2026-07-28"
APP_SETUP_OUTFIT_POLL_LANE = "app_setup_outfit_poll"
APP_UNITYPACKAGE_IMPORT_POLL_LANE = "app_unitypackage_import_poll"
APP_BUILD_TEST_POLL_LANE = "app_build_test_poll"
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_PROTOCOL_VERSION,)
MAX_FRAME_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 5.0
PROJECT_CORE_MAX_CONCURRENT = 3
PROJECT_CORE_BUSY_WAIT_SECONDS = 0.25
_ACTIVE_CALL_AUDIT_CAPTURE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "vrcforge_unity_mcp_call_audit_capture",
    default=None,
)


class UnityMcpCoreError(RuntimeError):
    """A deliberately non-sensitive Core discovery or transport failure."""

    cause_code = "unity_core_contract_invalid"
    retryable = False


class UnityMcpCoreConnectionError(UnityMcpCoreError):
    """A transient project-scoped Core connection failure."""

    cause_code = "unity_core_unavailable"
    retryable = True


class UnityMcpCoreBusyError(UnityMcpCoreError):
    """The exact project has reached its bounded Core connection capacity."""

    cause_code = "unity_core_project_busy"
    retryable = True

    def __init__(self, project_root: Path, *, wait_seconds: float, active: int) -> None:
        self.details = {
            "kind": "project_connection_limit",
            "projectPath": str(project_root),
            "maxConcurrent": PROJECT_CORE_MAX_CONCURRENT,
            "active": active,
            "waitSeconds": wait_seconds,
        }
        super().__init__(
            f"Unity MCP Core is busy for project '{project_root}' "
            f"({PROJECT_CORE_MAX_CONCURRENT} concurrent connections already active)."
        )


_PROJECT_CORE_CONDITION = threading.Condition()
_PROJECT_CORE_ACTIVE: dict[str, int] = {}


@contextmanager
def _project_core_slot(project_root: str | Path, *, wait_seconds: float = PROJECT_CORE_BUSY_WAIT_SECONDS) -> Iterator[None]:
    """Bound Core work per canonical project without queueing or replaying calls."""

    root = _resolve_project_root(project_root)
    key = str(root).casefold() if os.name == "nt" else str(root)
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    with _PROJECT_CORE_CONDITION:
        while _PROJECT_CORE_ACTIVE.get(key, 0) >= PROJECT_CORE_MAX_CONCURRENT:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UnityMcpCoreBusyError(
                    root,
                    wait_seconds=max(0.0, float(wait_seconds)),
                    active=_PROJECT_CORE_ACTIVE.get(key, 0),
                )
            _PROJECT_CORE_CONDITION.wait(remaining)
        _PROJECT_CORE_ACTIVE[key] = _PROJECT_CORE_ACTIVE.get(key, 0) + 1
    try:
        yield
    finally:
        with _PROJECT_CORE_CONDITION:
            active = _PROJECT_CORE_ACTIVE.get(key, 0) - 1
            if active > 0:
                _PROJECT_CORE_ACTIVE[key] = active
            else:
                _PROJECT_CORE_ACTIVE.pop(key, None)
            _PROJECT_CORE_CONDITION.notify_all()


def probe_unity_mcp_core_diagnostics(
    project_root: str | Path,
    *,
    timeout_seconds: float = 3.0,
    max_errors: int = 30,
) -> dict[str, Any]:
    """Read pre-handshake Core identity and compile state, including legacy Core."""

    root = _resolve_project_root(project_root)
    descriptor = _read_json_object(root / "Library" / "VRCForge" / "mcp-core.json", "Core descriptor")
    raw_project_path = _require_string(descriptor, "projectPath", "Core descriptor")
    try:
        descriptor_root = Path(raw_project_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.") from None
    if descriptor_root != root or _require_string(descriptor, "host", "Core descriptor") != "127.0.0.1":
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.")
    port = _require_port(descriptor, "port", "Core descriptor")
    token = _require_base64_token(descriptor, "authToken", "Core descriptor", 32)
    instance_id = _require_string(descriptor, "instanceId", "Core descriptor")
    project_id = hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest()
    descriptor_protocol = descriptor.get("protocolVersion")
    protocol_version = descriptor_protocol if _is_protocol_version(descriptor_protocol) else MODERN_PROTOCOL_VERSION
    minimum = descriptor.get("minimumProtocolVersion")
    maximum = descriptor.get("maximumProtocolVersion")
    minimum = minimum if _is_protocol_version(minimum) else protocol_version
    maximum = maximum if _is_protocol_version(maximum) else protocol_version
    if minimum > maximum:
        minimum = protocol_version
        maximum = protocol_version
    metadata = {
        "io.modelcontextprotocol/protocolVersion": protocol_version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "VRCForge FastAPI", "version": PRODUCT_VERSION},
        "io.vrcforge/protocolRange": {
            "minimum": MINIMUM_CORE_PROTOCOL_VERSION,
            "maximum": MODERN_PROTOCOL_VERSION,
        },
        "io.vrcforge/projectBinding": {
            "projectId": project_id,
            "instanceId": instance_id,
        },
    }
    result: dict[str, Any] = {
        "schema": "vrcforge.core_diagnostics.v1",
        "projectPath": str(root),
        "descriptor": {
            "protocolVersion": descriptor_protocol,
            "productVersion": descriptor.get("productVersion"),
            "toolContractVersion": descriptor.get("toolContractVersion"),
            "instanceId": instance_id,
            "projectId": descriptor.get("projectId") or descriptor.get("projectHash") or project_id,
            "projectIdSource": descriptor.get("projectIdSource") or "legacy_path_sha256",
        },
        "coreInfo": None,
        "compileResult": None,
        "transportError": "",
        "transportErrorCauseCode": "",
        "transportErrorRetryable": False,
        "transportErrorDetails": None,
        "handshakeError": None,
    }
    try:
        with _project_core_slot(root):
            with socket.create_connection(("127.0.0.1", port), timeout_seconds) as connection:
                connection.settimeout(timeout_seconds)
                core_info_response = _probe_core_request(connection, token, 0, "server/core-info", {})
                result["coreInfo"] = _probe_result_or_core_info(core_info_response)
                if isinstance(result["coreInfo"], dict) and isinstance(result["coreInfo"].get("compileSnapshot"), dict):
                    result["compileResult"] = {
                        "structuredContent": {"data": result["coreInfo"]["compileSnapshot"]}
                    }

                discover_response = _probe_core_request(
                    connection,
                    token,
                    1,
                    "server/discover",
                    {"_meta": metadata},
                )
                discover_message = discover_response.get("message") if isinstance(discover_response, dict) else None
                discover_error = discover_message.get("error") if isinstance(discover_message, dict) else None
                if isinstance(discover_error, dict):
                    result["handshakeError"] = discover_error
                    if result["coreInfo"] is None:
                        data = discover_error.get("data")
                        if isinstance(data, dict) and isinstance(data.get("coreInfo"), dict):
                            result["coreInfo"] = data["coreInfo"]
                    return result

                compile_response = _probe_core_request(
                    connection,
                    token,
                    2,
                    "tools/call",
                    {
                        "name": "vrc_get_compile_errors",
                        "arguments": {
                            "maxErrors": max(1, min(int(max_errors), 200)),
                            "includeConsoleFallback": True,
                        },
                        "_meta": metadata,
                    },
                )
                compile_message = compile_response.get("message") if isinstance(compile_response, dict) else None
                if isinstance(compile_message, dict):
                    result["compileResult"] = compile_message.get("result")
    except (OSError, UnicodeError, json.JSONDecodeError, UnityMcpCoreError) as exc:
        result["transportError"] = str(exc)
        result["transportErrorCauseCode"] = str(
            getattr(exc, "cause_code", "unity_core_diagnostics_failed")
        )
        result["transportErrorRetryable"] = bool(getattr(exc, "retryable", False))
        details = getattr(exc, "details", None)
        result["transportErrorDetails"] = dict(details) if isinstance(details, dict) else None
    return result


def _probe_core_request(
    connection: socket.socket,
    token: str,
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": TRANSPORT_SCHEMA,
        "authorization": "Bearer " + token,
        "message": {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="strict")
    if len(encoded) + 1 > MAX_FRAME_BYTES:
        raise UnityMcpCoreError("Unity MCP Core diagnostics request is too large.")
    connection.sendall(encoded + b"\n")
    data = bytearray()
    while len(data) <= MAX_FRAME_BYTES:
        value = connection.recv(1)
        if not value:
            raise UnityMcpCoreConnectionError("Unity MCP Core diagnostics connection closed unexpectedly.")
        if value == b"\n":
            decoded = json.loads(bytes(data).decode("utf-8", errors="strict"))
            if not isinstance(decoded, dict):
                raise UnityMcpCoreError("Unity MCP Core diagnostics response is invalid.")
            return decoded
        data.extend(value)
    raise UnityMcpCoreError("Unity MCP Core diagnostics response is too large.")


def _probe_result_or_core_info(response: dict[str, Any]) -> dict[str, Any] | None:
    message = response.get("message") if isinstance(response, dict) else None
    if not isinstance(message, dict):
        return None
    result = message.get("result")
    if isinstance(result, dict) and result.get("schema") == "vrcforge.core_info.v1":
        return result
    error = message.get("error")
    data = error.get("data") if isinstance(error, dict) else None
    core_info = data.get("coreInfo") if isinstance(data, dict) else None
    return core_info if isinstance(core_info, dict) else None


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
    project_id: str
    supported_protocol_versions: tuple[str, ...]
    minimum_protocol_version: str
    maximum_protocol_version: str
    negotiated_protocol_version: str
    transport: str
    tool_count: int
    core_identity: str
    handshake_protocol: str
    product_version: str
    tool_contract_version: str


def load_unity_mcp_core_connection(
    project_root: str | Path,
    *,
    allow_previous_contract: bool = False,
) -> UnityMcpCoreConnection:
    root = _resolve_project_root(project_root)
    descriptor = _read_json_object(root / "Library" / "VRCForge" / "mcp-core.json", "Core descriptor")
    if _require_string(descriptor, "schema", "Core descriptor") != TRANSPORT_SCHEMA:
        raise UnityMcpCoreError("Unity MCP Core descriptor is not recognized.")
    descriptor_protocol_version = _require_protocol_version(descriptor, "protocolVersion", "Core descriptor")
    if _require_string(descriptor, "coreIdentity", "Core descriptor") != CORE_IDENTITY:
        raise UnityMcpCoreError("Unity MCP Core identity is not supported.")
    if _require_string(descriptor, "handshakeProtocol", "Core descriptor") != HANDSHAKE_PROTOCOL:
        raise UnityMcpCoreError("Unity MCP Core handshake protocol is not supported.")
    product_version = _require_string(descriptor, "productVersion", "Core descriptor")
    tool_contract_version = _require_string(descriptor, "toolContractVersion", "Core descriptor")
    # Tool-contract revisions are diagnostic/discovery identities, not a
    # second handshake protocol. Compatibility is decided by the negotiated
    # protocol range below; each routed tool remains validated from discovery.
    del allow_previous_contract
    if _require_string(descriptor, "authMode", "Core descriptor") != "bearer-per-request":
        raise UnityMcpCoreError("Unity MCP Core authentication mode is not supported.")
    if _require_string(descriptor, "executionPolicy", "Core descriptor") != "read-direct-app-process-approved-writes":
        raise UnityMcpCoreError("Unity MCP Core execution policy is invalid.")
    if _require_string(descriptor, "host", "Core descriptor") != "127.0.0.1":
        raise UnityMcpCoreError("Unity MCP Core must use the loopback endpoint.")
    minimum_protocol_version = _require_protocol_version(descriptor, "minimumProtocolVersion", "Core descriptor")
    maximum_protocol_version = _require_protocol_version(descriptor, "maximumProtocolVersion", "Core descriptor")
    if minimum_protocol_version > maximum_protocol_version \
            or not minimum_protocol_version <= descriptor_protocol_version <= maximum_protocol_version:
        raise UnityMcpCoreError("Unity MCP Core protocol range is invalid.")
    negotiated_protocol_version = min(MODERN_PROTOCOL_VERSION, maximum_protocol_version)
    if negotiated_protocol_version < max(MINIMUM_CORE_PROTOCOL_VERSION, minimum_protocol_version):
        raise UnityMcpCoreError("Unity MCP Core has no compatible protocol version.")
    supported_versions = _require_supported_versions(descriptor)
    if descriptor_protocol_version not in supported_versions:
        raise UnityMcpCoreError("Unity MCP Core supported protocol list is invalid.")
    _require_string(descriptor, "transport", "Core descriptor")
    tool_count = _require_positive_int(descriptor, "toolCount", "Core descriptor")

    raw_project_path = _require_string(descriptor, "projectPath", "Core descriptor")
    try:
        descriptor_root = Path(raw_project_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.") from None
    if descriptor_root != root:
        raise UnityMcpCoreError("Unity MCP Core belongs to a different project.")
    project_id = _require_string(descriptor, "projectId", "Core descriptor")
    if project_id != hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest():
        raise UnityMcpCoreError("Unity MCP Core path-derived project ID is invalid.")
    if _require_string(descriptor, "projectIdSource", "Core descriptor") != "normalized_project_path_sha256":
        raise UnityMcpCoreError("Unity MCP Core project ID source is invalid.")

    return UnityMcpCoreConnection(
        project_root=root,
        host="127.0.0.1",
        port=_require_port(descriptor, "port", "Core descriptor"),
        discovery_token=_require_base64_token(descriptor, "authToken", "Core descriptor", 32),
        instance_id=_require_string(descriptor, "instanceId", "Core descriptor"),
        process_id=_require_positive_int(descriptor, "processId", "Core descriptor"),
        project_id=project_id,
        supported_protocol_versions=supported_versions,
        minimum_protocol_version=minimum_protocol_version,
        maximum_protocol_version=maximum_protocol_version,
        negotiated_protocol_version=negotiated_protocol_version,
        transport=_require_string(descriptor, "transport", "Core descriptor"),
        tool_count=tool_count,
        core_identity=CORE_IDENTITY,
        handshake_protocol=HANDSHAKE_PROTOCOL,
        product_version=product_version,
        tool_contract_version=tool_contract_version,
    )


class UnityMcpCoreClient:
    """Client for the sole VRCForge MCP 2026-07-28 Core contract."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        allow_previous_contract: bool = False,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 0 and 600 seconds.")
        self._connection = load_unity_mcp_core_connection(
            project_root,
            allow_previous_contract=allow_previous_contract,
        )
        if self._connection.transport != "tcp-newline-jsonrpc":
            raise UnityMcpCoreError("Unity MCP Core transport is not supported.")
        self._expected_tool_names = (
            PREVIOUS_CORE_TOOL_NAMES
            if self._connection.tool_contract_version == PREVIOUS_CORE_TOOL_CONTRACT_VERSION
            else EXPECTED_TOOL_NAMES
        )
        self._timeout_seconds = float(timeout_seconds)

    @property
    def protocol_version(self) -> str:
        return MODERN_PROTOCOL_VERSION

    @property
    def uses_previous_contract(self) -> bool:
        return self._connection.tool_contract_version == PREVIOUS_CORE_TOOL_CONTRACT_VERSION

    def list_tools(self, *, exposure_layer: str = "planning") -> list[dict[str, Any]]:
        if exposure_layer not in {"planning", "execution"}:
            raise ValueError("exposure_layer must be planning or execution.")
        result = self._request("tools/list", {"exposureLayer": exposure_layer})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            raise UnityMcpCoreError("Unity MCP Core returned an invalid tools list.")
        names = [tool.get("name") for tool in tools]
        expected_names = PLANNING_TOOL_NAMES if exposure_layer == "planning" else self._expected_tool_names
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
        if isinstance(execution_context, dict) and execution_context.get("lane") == APP_BUILD_TEST_POLL_LANE:
            if name != "vrc_build_test_avatar" or not _is_strict_build_test_job_poll(arguments):
                raise ValueError("Build & Test job polling requires exact jobId arguments.")
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
            with _project_core_slot(self._connection.project_root):
                with self._open_connection() as connection:
                    self._send_modern(connection, 1, "server/discover", {})
                    discovery = self._receive_modern_response(connection, 1)
                    supported = discovery.get("supportedVersions") if isinstance(discovery, dict) else None
                    if not isinstance(supported, list) or not all(isinstance(version, str) for version in supported) \
                            or self._connection.negotiated_protocol_version not in supported \
                            or discovery.get("coreIdentity") != self._connection.core_identity \
                            or discovery.get("handshakeProtocol") != self._connection.handshake_protocol \
                            or discovery.get("productVersion") != self._connection.product_version \
                            or discovery.get("toolContractVersion") != self._connection.tool_contract_version \
                            or discovery.get("instanceId") != self._connection.instance_id \
                            or discovery.get("projectId") != self._connection.project_id \
                            or discovery.get("protocolRange") != {
                                "minimum": self._connection.minimum_protocol_version,
                                "maximum": self._connection.maximum_protocol_version,
                            }:
                        raise UnityMcpCoreError("Unity MCP Core modern discovery failed.")
                    call_id = request_id if request_id is not None else 2
                    self._send_modern(connection, call_id, method, params, execution_context=execution_context)
                    return self._receive_modern_response(connection, call_id)
        except UnityMcpCoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failure = UnityMcpCoreConnectionError(_bounded_error_message("Unity MCP Core connection failed.", exc))
            failure.cause_code = "unity_core_timeout" if isinstance(exc, TimeoutError) else "unity_core_connection_failed"
            failure.details = _bounded_cause(failure.cause_code, exc)
            raise failure from exc

    def _open_connection(self) -> socket.socket:
        connection = socket.create_connection((self._connection.host, self._connection.port), self._timeout_seconds)
        connection.settimeout(self._timeout_seconds)
        return connection

    def _send_modern(self, connection: socket.socket, request_id: int, method: str, params: dict[str, Any],
                     *, execution_context: dict[str, Any] | None = None) -> None:
        request_params = dict(params)
        metadata: dict[str, Any] = {
            "io.modelcontextprotocol/protocolVersion": self._connection.negotiated_protocol_version,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "VRCForge FastAPI", "version": PRODUCT_VERSION},
            "io.vrcforge/protocolRange": {
                "minimum": MINIMUM_CORE_PROTOCOL_VERSION,
                "maximum": MODERN_PROTOCOL_VERSION,
            },
            "io.vrcforge/projectBinding": {
                "projectId": self._connection.project_id,
                "instanceId": self._connection.instance_id,
            },
        }
        if execution_context is not None:
            approved_execution = _json_object_copy(execution_context)
            approved_execution["clientProcessId"] = os.getpid()
            approved_execution["projectId"] = self._connection.project_id
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
            error = message.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            error_message = error.get("message") if isinstance(error, dict) else None
            data = error.get("data") if isinstance(error, dict) else None
            core_info = data.get("coreInfo") if isinstance(data, dict) else None
            summary = ""
            if isinstance(core_info, dict):
                summary = (
                    f" core={str(core_info.get('coreIdentity', ''))[:100]}"
                    f" version={str(core_info.get('coreVersion', ''))[:80]}"
                    f" protocol={str(core_info.get('protocolRange', ''))[:120]}"
                    f" instance={str(core_info.get('instanceId', ''))[:100]}"
                )
            failure = UnityMcpCoreError(
                f"Unity MCP Core rejected the request (code={code}): "
                f"{str(error_message or 'request rejected')[:240]}.{summary}"
            )
            failure.cause_code = "unity_core_jsonrpc_error"
            failure.details = {
                "kind": "json_rpc_error",
                "code": code,
                "message": str(error_message or "")[:240],
                "data": _bounded_json_value(data),
            }
            raise failure
        if "result" not in message:
            failure = UnityMcpCoreError("Unity MCP Core returned an invalid JSON-RPC response.")
            failure.cause_code = "unity_core_jsonrpc_missing_result"
            failure.details = {"kind": "invalid_json_rpc", "missing": "result"}
            raise failure
        result = message["result"]
        if require_complete and (not isinstance(result, dict) or result.get("resultType") != "complete"):
            failure = UnityMcpCoreError("Unity MCP Core returned an incomplete modern result.")
            failure.cause_code = "unity_core_invalid_result_shape"
            failure.details = {
                "kind": "invalid_result_shape",
                "resultType": str(result.get("resultType") or "")[:80] if isinstance(result, dict) else None,
                "actualType": type(result).__name__[:80],
            }
            raise failure
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
                raise UnityMcpCoreConnectionError("Unity MCP Core connection closed unexpectedly.")
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


def _is_strict_build_test_job_poll(arguments: Any) -> bool:
    return _is_strict_setup_outfit_job_poll(arguments)


def _encode_payload(payload: dict[str, Any], direction: str) -> bytes:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        failure = UnityMcpCoreError("Unity MCP Core " + direction + " is invalid.")
        failure.cause_code = "unity_core_serialization_failed"
        failure.details = _bounded_cause(failure.cause_code, exc)
        raise failure from exc
    if not 0 < len(encoded) <= MAX_FRAME_BYTES:
        failure = UnityMcpCoreError("Unity MCP Core " + direction + " is too large.")
        failure.cause_code = "unity_core_frame_too_large"
        failure.details = {"kind": "frame_too_large", "maxBytes": MAX_FRAME_BYTES}
        raise failure
    return encoded


def canonical_arguments_sha256(arguments: dict[str, Any]) -> str:
    """Bind an approved Core execution to the exact JSON tool arguments."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object.")
    try:
        encoded = _canonical_argument_token(arguments)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("arguments must be JSON-compatible.") from None
    return hashlib.sha256(encoded).hexdigest()


def _canonical_argument_token(value: Any) -> bytes:
    """Cross-runtime JSON value encoding shared with the Unity Core.

    JSON object order is normalized, and floats are bound by their IEEE-754
    binary64 bits instead of runtime-specific decimal rendering.
    """

    if value is None:
        return b"n;"
    if isinstance(value, bool):
        return b"b1;" if value else b"b0;"
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b";"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        # Unity's bundled Newtonsoft parser normalizes JSON -0.0 to +0.0
        # before the Core can validate the managed-execution hash. Bind the
        # value the Core can actually observe so Quaternion/Vector payloads
        # containing a negative zero are not rejected before routing.
        if value == 0.0:
            value = 0.0
        return b"f" + struct.pack(">d", value).hex().encode("ascii") + b";"
    if isinstance(value, str):
        return b"s" + base64.b64encode(value.encode("utf-8")) + b";"
    if isinstance(value, list):
        return b"a[" + b"".join(_canonical_argument_token(item) for item in value) + b"];"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return b"o{" + b"".join(
            _canonical_argument_token(key) + _canonical_argument_token(value[key])
            for key in sorted(value, key=lambda item: base64.b64encode(item.encode("utf-8")))
        ) + b"};"
    raise TypeError("unsupported JSON value")


def _new_request_id() -> int:
    return 2 + int.from_bytes(os.urandom(8), "big") % (2**63 - 3)


def _decode_payload(data: bytes, direction: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        failure = UnityMcpCoreError("Unity MCP Core " + direction + " is invalid.")
        failure.cause_code = "unity_core_deserialization_failed"
        failure.details = _bounded_cause(failure.cause_code, exc)
        raise failure from exc
    if not isinstance(value, dict):
        raise UnityMcpCoreError("Unity MCP Core " + direction + " is invalid.")
    return value


def _bounded_error_message(prefix: str, error: BaseException) -> str:
    detail = str(error).replace("\r", " ").replace("\n", " ").strip()[:180]
    return f"{prefix}: {detail}" if detail else prefix


def _bounded_cause(kind: str, error: BaseException) -> dict[str, str]:
    return {
        "kind": kind,
        "exceptionType": type(error).__name__[:80],
        "message": str(error).replace("\r", " ").replace("\n", " ").strip()[:240],
    }


def _bounded_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_bounded_json_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded_json_value(item) for key, item in list(value.items())[:30]}
    return type(value).__name__[:80]


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
    if any(not _is_protocol_version(version) for version in value):
        raise UnityMcpCoreError("Core descriptor is invalid.")
    return tuple(value)


def _is_protocol_version(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        time.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _require_protocol_version(document: dict[str, Any], key: str, label: str) -> str:
    value = document.get(key)
    if not _is_protocol_version(value):
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
