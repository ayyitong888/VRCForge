"""Project-bound Core configuration and per-request bearer authentication.

Reads only the selected project's descriptor; no endpoint fallback or token cache.
Diagnostics may inspect older protocols without granting execution access.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from unity_mcp_tool_contract import CORE_IDENTITY, HANDSHAKE_PROTOCOL

TRANSPORT_SCHEMA = "vrcforge.mcp.transport.v2"
MODERN_PROTOCOL_VERSION = "2026-07-28"
MINIMUM_CORE_PROTOCOL_VERSION = "2026-07-28"


class UnityMcpCoreError(RuntimeError):
    """A deliberately non-sensitive Core discovery or transport failure."""

    cause_code = "unity_core_contract_invalid"
    retryable = False


class UnityMcpCoreConnectionError(UnityMcpCoreError):
    """A transient project-scoped Core connection failure."""

    cause_code = "unity_core_unavailable"
    retryable = True


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
    root, descriptor = _read_core_descriptor(project_root)
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

    raw_project_path = _validate_project_binding(
        root, descriptor, mismatch_message="Unity MCP Core belongs to a different project.",
    )
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


def load_core_diagnostic_binding(
    project_root: str | Path,
) -> tuple[Path, dict[str, Any], int, str, str, str]:
    """Validate local credentials and project binding even for an older Core."""
    root, descriptor = _read_core_descriptor(project_root)
    raw_project_path = _validate_project_binding(root, descriptor)
    if _require_string(descriptor, "host", "Core descriptor") != "127.0.0.1":
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.")
    port = _require_port(descriptor, "port", "Core descriptor")
    token = _require_base64_token(descriptor, "authToken", "Core descriptor", 32)
    instance_id = _require_string(descriptor, "instanceId", "Core descriptor")
    project_id = hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest()
    return root, descriptor, port, token, instance_id, project_id


def authenticated_core_request(token: str, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build the shared authenticated envelope; callers own socket lifetime."""
    return {
        "schema": TRANSPORT_SCHEMA,
        "authorization": "Bearer " + token,
        "message": {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    }


def _read_core_descriptor(project_root: str | Path) -> tuple[Path, dict[str, Any]]:
    root = _resolve_project_root(project_root)
    return root, _read_json_object(root / "Library" / "VRCForge" / "mcp-core.json", "Core descriptor")


def _validate_project_binding(
    root: Path, descriptor: dict[str, Any], *,
    mismatch_message: str = "Unity MCP Core project binding is invalid.",
) -> str:
    raw_path = _require_string(descriptor, "projectPath", "Core descriptor")
    try:
        descriptor_root = Path(raw_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnityMcpCoreError("Unity MCP Core project binding is invalid.") from None
    if descriptor_root != root:
        raise UnityMcpCoreError(mismatch_message)
    return raw_path


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
