from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent_mcp_2026 import PROTOCOL_VERSION, Mcp2026Router, run_stdio_loop
from agent_mcp_standard import McpStandardRouter, run_negotiated_stdio_loop, run_standard_stdio_loop
from mcp_tool_descriptor import standardize_tool_descriptor
from external_mcp_result_projection import project_tool
from external_tool_result_contract import build_external_tool_error
from agent_tool_result_contract import normalize_agent_tool_result
from internal_tool_blocks import (
    CANONICAL_TOOL_BLOCKS,
    CANONICAL_TOOL_BLOCK_ALIASES,
    CANONICAL_TOOL_LEAVES,
    canonical_block_routing,
    canonical_external_block,
    canonical_tool_owner,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8757"
DEFAULT_SERVER_NAME = "VRCForge Agent Bridge"
DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 360.0
DEFAULT_DISCOVERY_TOOLS = frozenset({
    "vrcforge_bridge_preflight", "vrcforge_list_tool_blocks", "vrcforge_load_tool_block",
    "vrcforge_unload_tool_block", "vrcforge_invoke_loaded_read_tool", "vrcforge_invoke_loaded_write_tool",
    "vrcforge_list_execution_targets", "vrcforge_bind_execution_target",
})
HIDDEN_EXTERNAL_TOOLS = {
    "vrcforge_agent_message",
    "vrcforge_apply_approved",
    "vrcforge_execute_approved_shell",
    "vrcforge_execute_shell",
    "vrcforge_request_apply",
}
EXTERNAL_TOOL_BLOCK_INDEXES = {
    str(position): block_id
    for position, block_id in enumerate(CANONICAL_TOOL_BLOCKS, start=1)
}
EXTERNAL_TOOL_BLOCK_LEAF_INDEXES = {
    f"{root_position}.{leaf_position}": f"{root_id}/{leaf_name}"
    for root_position, (root_id, root_spec) in enumerate(CANONICAL_TOOL_BLOCKS.items(), start=1)
    for leaf_position, leaf_name in enumerate(root_spec["children"], start=1)
}
EXTERNAL_TOOL_BLOCK_NAME_INDEXES = {
    name: index
    for index, name in {
        **EXTERNAL_TOOL_BLOCK_INDEXES,
        **EXTERNAL_TOOL_BLOCK_LEAF_INDEXES,
    }.items()
}


def configure_utf8_stdio() -> None:
    """Make the MCP stdio transport UTF-8 even on legacy Windows code pages."""
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=errors)
EXTERNAL_TOOL_BLOCK_CONTROL_NAMES = frozenset(
    {
        "vrcforge_list_tool_blocks",
        "vrcforge_load_tool_block",
        "vrcforge_unload_tool_block",
    }
)


class ExternalMcpBridgeError(RuntimeError):
    def __init__(self, message: str, *, raw_result: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.raw_result = dict(raw_result) if isinstance(raw_result, Mapping) else None
        response_error = (
            self.raw_result.get("error")
            if isinstance(self.raw_result, Mapping)
            and isinstance(self.raw_result.get("error"), Mapping)
            else {}
        )
        upstream_data = (
            response_error.get("data")
            if isinstance(response_error.get("data"), Mapping)
            else {}
        )
        self.external_error = build_external_tool_error(
            error=message,
            error_code=str(response_error.get("code") or "external_gateway_error"),
            failure_layer="external_stdio_gateway_bridge",
            failure_phase="gateway_response",
            operation_kind="tool",
            tool_routing_started=None,
            mutation_started=None,
            committed=None,
            raw_result=upstream_data,
            details={"jsonRpcErrorCode": response_error.get("code")},
        )


class ExternalHttpBridgeError(RuntimeError):
    def __init__(self, *, status_code: int, path: str, body: str) -> None:
        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError:
            parsed = {"body": body}
        raw_result = dict(parsed) if isinstance(parsed, Mapping) else {"body": body}
        message = str(raw_result.get("error") or raw_result.get("message") or f"HTTP {status_code} from {path}")
        super().__init__(message)
        self.status_code = int(status_code)
        self.raw_result = raw_result
        self.external_error = build_external_tool_error(
            error=message,
            error_code=str(raw_result.get("errorCode") or f"http_{status_code}"),
            failure_layer=str(raw_result.get("failureLayer") or "external_gateway_http"),
            failure_phase=str(raw_result.get("failurePhase") or "gateway_http_rejection"),
            operation_kind="transport",
            tool_routing_started=False,
            mutation_started=False,
            committed=False,
            raw_result=raw_result,
            details={"httpStatus": int(status_code), "path": path},
        )


def external_rejection(
    *,
    status: str,
    error: str,
    error_code: str,
    failure_layer: str,
    failure_phase: str,
    operation_kind: str,
    tool: str = "",
    tool_routing_started: bool | None = False,
    mutation_started: bool | None = False,
    committed: bool | None = False,
    retryable: bool | None = False,
    raw_result: Mapping[str, Any] | None = None,
    exception: BaseException | None = None,
    details: Mapping[str, Any] | None = None,
    **compatibility: Any,
) -> dict[str, Any]:
    error_object = build_external_tool_error(
        error=error,
        error_code=error_code,
        failure_layer=failure_layer,
        failure_phase=failure_phase,
        operation_kind=operation_kind,
        tool=tool,
        tool_routing_started=tool_routing_started,
        mutation_started=mutation_started,
        committed=committed,
        retryable=retryable,
        checkpoint_recovery_required=False if mutation_started is False else None,
        temporary_cleanup_required=False if mutation_started is False else None,
        raw_result=raw_result,
        exception=exception,
        details=details,
    )
    payload = {
        "ok": False,
        "status": status,
        "error": error_object["error"],
        "errorDetails": error_object,
        "failureLayer": error_object["failureLayer"],
        "errorCode": error_object["errorCode"],
        "mutationStarted": error_object["mutationStarted"],
        "committed": error_object["committed"],
        "commitState": error_object["commitState"],
        **compatibility,
    }
    payload["outcome"] = normalize_agent_tool_result(
        payload,
        fallback_summary=str(error_object.get("error") or error or status),
        write=operation_kind == "write",
    )
    return payload


def main() -> int:
    configure_utf8_stdio()
    args = parse_args()
    bridge = VRCForgeBridge(
        base_url=args.base_url.rstrip("/"),
        config_path=Path(args.config).expanduser().resolve() if args.config else None,
        timeout_seconds=args.timeout,
        tool_call_timeout_seconds=args.tool_timeout,
        start_runtime=args.start_runtime and not args.no_start,
    )
    if args.preflight:
        print(json.dumps(bridge.preflight(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    run_stdio_server(bridge, protocol_profile=args.protocol_profile, exposure_layer=args.exposure_layer)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the VRCForge external-agent stdio MCP bridge.")
    parser.add_argument("--base-url", default=os.environ.get("VRCFORGE_AGENT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--config", default=os.environ.get("VRCFORGE_AGENT_GATEWAY_CONFIG", ""))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("VRCFORGE_AGENT_TIMEOUT", "30")))
    parser.add_argument(
        "--tool-timeout",
        type=float,
        default=float(
            os.environ.get(
                "VRCFORGE_AGENT_TOOL_TIMEOUT",
                str(DEFAULT_TOOL_CALL_TIMEOUT_SECONDS),
            )
        ),
        help="Bounded timeout for tool calls that may wait on Unity domain reload/import work.",
    )
    parser.add_argument("--start-runtime", action="store_true", help="Launch VRCForge Desktop if the runtime is offline.")
    parser.add_argument("--no-start", action="store_true", help="Compatibility flag; runtime auto-launch is disabled by default.")
    parser.add_argument("--preflight", action="store_true", help="Print a JSON preflight report and exit.")
    parser.add_argument("--json", action="store_true", help="Compatibility flag; preflight already prints JSON.")
    parser.add_argument(
        "--protocol-profile",
        choices=("auto", "vrcforge-2026", "mcp-1x"),
        default=os.environ.get("VRCFORGE_MCP_PROTOCOL_PROFILE", "auto"),
        help="Prefer VRCForge 2026 and negotiate MCP 1.x only when the client initializes with it.",
    )
    parser.add_argument(
        "--exposure-layer",
        choices=("planning", "execution"),
        default=os.environ.get("VRCFORGE_MCP_EXPOSURE_LAYER", "planning"),
        help="Pin the tool catalogue exposed to this stdio client.",
    )
    return parser.parse_args(argv)


class VRCForgeBridge:
    def __init__(
        self,
        *,
        base_url: str,
        config_path: Path | None,
        timeout_seconds: float,
        start_runtime: bool,
        tool_call_timeout_seconds: float = DEFAULT_TOOL_CALL_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url
        self.config_path = config_path
        self.timeout_seconds = timeout_seconds
        self.tool_call_timeout_seconds = tool_call_timeout_seconds
        self.start_runtime = start_runtime
        # Notification revisions only; never used for authorization or identity.
        self._response_list_revisions: dict[str, Any] | None = None

    def preflight(self) -> dict[str, Any]:
        config_path = self.resolve_config_path()
        config = read_json_file(config_path)
        token = str(os.environ.get("VRCFORGE_AGENT_TOKEN") or config.get("token") or "")
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.external_agent_bridge.preflight.v1",
            "baseUrl": self.base_url,
            "configPath": str(config_path) if config_path else "",
            "configFound": bool(config_path and config_path.is_file()),
            "tokenSource": "env" if os.environ.get("VRCFORGE_AGENT_TOKEN") else "config" if token else "missing",
            "tokenConfigured": bool(token),
            "gatewayEnabled": bool(config.get("enabled")),
            "allowWriteRequests": bool(config.get("allow_write_requests", True)),
            "runtimeOnline": False,
            "manifestToolCount": 0,
            "advertisesRequestApply": False,
            "advertisesDirectApply": False,
            "error": "",
        }

        def reject(
            *,
            status: str,
            error: str,
            error_code: str,
            failure_layer: str,
            failure_phase: str,
            exception: BaseException | None = None,
            details: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            report.update(
                external_rejection(
                    status=status,
                    error=error,
                    error_code=error_code,
                    failure_layer=failure_layer,
                    failure_phase=failure_phase,
                    operation_kind="preflight",
                    tool="vrcforge_bridge_preflight",
                    tool_routing_started=False,
                    mutation_started=False,
                    committed=False,
                    retryable=False,
                    exception=exception,
                    details=details,
                )
            )
            return report

        if not token:
            return reject(
                status="gateway_token_missing",
                error="VRCForge Agent Gateway token was not found.",
                error_code="external_gateway_token_missing",
                failure_layer="external_stdio_authentication",
                failure_phase="token_resolution",
            )

        if self.start_runtime and not self.runtime_port_open():
            launch = self.try_launch_runtime()
            report["launch"] = launch

        try:
            planning_manifest = self._mcp_request(
                "tools/list",
                {"exposureLayer": "planning", "toolBlocks": ["*"]},
                token=token,
            )
            manifest = self._mcp_request(
                "tools/list",
                {"exposureLayer": "execution", "toolBlocks": ["*"]},
                token=token,
            )
            planning_tools = planning_manifest.get("tools") if isinstance(planning_manifest, dict) else []
            planning_names = {str(tool.get("name") or "") for tool in planning_tools if isinstance(tool, dict)}
            tools = manifest.get("tools") if isinstance(manifest, dict) else []
            tool_names = {str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)}
            actual_write_names = {
                str(tool.get("name") or "")
                for tool in tools
                if isinstance(tool, dict)
                and isinstance(tool.get("_meta"), dict)
                and str(tool["_meta"].get("permission") or "") == "Write"
            }
            report["runtimeOnline"] = True
            report["gatewayEnabled"] = bool(config.get("enabled"))
            report["allowWriteRequests"] = bool(config.get("allow_write_requests", True))
            report["manifestToolCount"] = len(tool_names)
            report["advertisesRequestApply"] = "vrcforge_request_apply" in tool_names
            report["advertisesDirectApply"] = bool(HIDDEN_EXTERNAL_TOOLS & tool_names)
            report["actualWriteToolCount"] = len(actual_write_names)
            planning_write_names = {
                str(tool.get("name") or "") for tool in planning_tools
                if isinstance(tool, dict) and isinstance(tool.get("_meta"), dict)
                and str(tool["_meta"].get("permission") or "") == "Write"
            }
            report["readReady"] = (
                bool(config.get("enabled"))
                and bool(planning_names)
                and not planning_write_names
                and actual_write_names.isdisjoint(planning_names)
                and not bool(HIDDEN_EXTERNAL_TOOLS & (tool_names | planning_names))
            )
            report["writeReady"] = bool(
                report["readReady"] and config.get("allow_write_requests", True)
                and actual_write_names
            )
            # Read-only/planning clients are fully connected without write
            # authority. Report write readiness separately; never demand a
            # hidden legacy write wrapper as proof of a working connection.
            report["ok"] = report["readReady"]
            if not report["ok"]:
                return reject(
                    status="external_tool_contract_not_ready",
                    error="Gateway is reachable, but the external MCP actual-tool contract is not ready.",
                    error_code="external_tool_contract_not_ready",
                    failure_layer="external_stdio_manifest_contract",
                    failure_phase="tool_manifest_validation",
                    details={
                        "planningToolCount": len(planning_names),
                        "executionToolCount": len(tool_names),
                        "actualWriteToolCount": len(actual_write_names),
                    },
                )
        except Exception as exc:  # noqa: BLE001 - preflight should report actionable failure instead of crashing.
            if isinstance(exc, ExternalHttpBridgeError):
                return reject(
                    status="gateway_http_rejection",
                    error=str(exc),
                    error_code="http_rejection",
                    failure_layer="external_gateway_http",
                    failure_phase="gateway_http_rejection",
                    exception=exc,
                    details={"baseUrl": self.base_url},
                )
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(
                getattr(exc, "reason", None), (TimeoutError, socket.timeout)
            )
            return reject(
                status="preflight_transport_error",
                error=str(exc),
                error_code="bridge_timeout" if timed_out else "bridge_connection_error",
                failure_layer="external_stdio_http_transport",
                failure_phase="preflight_manifest_request",
                exception=exc,
                details={"baseUrl": self.base_url},
            )
        return report

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None, agent_name: str = "external-stdio-agent") -> dict[str, Any]:
        if tool_name in HIDDEN_EXTERNAL_TOOLS:
            return external_rejection(
                status="tool_not_exposed",
                error=f"{tool_name} is internal to the VRCForge Agent loop.",
                error_code="external_tool_not_exposed",
                failure_layer="external_stdio_tool_visibility",
                failure_phase="before_gateway_call",
                operation_kind="tool",
                tool=tool_name,
            )
        try:
            token = self.require_token()
            result = self._mcp_request(
                "tools/call",
                {"name": tool_name, "arguments": params or {}},
                token=token,
            )
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(
                getattr(exc, "reason", None), (TimeoutError, socket.timeout)
            )
            return external_rejection(
                status="transport_error",
                error=str(exc),
                error_code="bridge_timeout" if timed_out else "bridge_connection_error",
                failure_layer="external_stdio_http_transport",
                failure_phase="gateway_request_or_response",
                operation_kind="tool",
                tool=tool_name,
                tool_routing_started=None,
                mutation_started=None,
                committed=None,
                retryable=False,
                exception=exc,
                requestMayHaveCommitted=True,
                safeToRetry=False,
                toolName=tool_name,
            )
        except Exception as exc:  # noqa: BLE001 - preserve upstream structured rejection facts.
            if isinstance(exc, ExternalHttpBridgeError):
                response_error = (
                    exc.raw_result.get("error")
                    if isinstance(exc.raw_result, Mapping)
                    and isinstance(exc.raw_result.get("error"), Mapping)
                    else {}
                )
                upstream_data = (
                    response_error.get("data")
                    if isinstance(response_error.get("data"), Mapping)
                    else {}
                )
                if (
                    upstream_data.get("errorCode") == "prompt_skill_provenance_mismatch"
                    and upstream_data.get("toolRoutingStarted") is False
                    and upstream_data.get("mutationStarted") is False
                    and upstream_data.get("committed") is False
                    and upstream_data.get("commitState") == "not_started"
                    and upstream_data.get("commitStateKnown") is True
                ):
                    return external_rejection(
                        status="gateway_http_rejection",
                        error=str(upstream_data.get("error") or exc),
                        error_code="prompt_skill_provenance_mismatch",
                        failure_layer=str(upstream_data.get("failureLayer") or "gateway_validation"),
                        failure_phase=str(upstream_data.get("failurePhase") or "prompt_skill_provenance_validation"),
                        operation_kind="tool",
                        tool=tool_name,
                        tool_routing_started=False,
                        mutation_started=False,
                        committed=False,
                        retryable=False,
                        details={"httpStatus": exc.status_code, "path": "/mcp"},
                    )
            raw_result = getattr(exc, "raw_result", None)
            return external_rejection(
                status="bridge_error",
                error=str(exc),
                error_code=str(getattr(exc, "cause_code", "") or "external_bridge_error"),
                failure_layer="external_stdio_gateway_bridge",
                failure_phase="gateway_request",
                operation_kind="tool",
                tool=tool_name,
                tool_routing_started=None,
                mutation_started=None,
                committed=None,
                retryable=False,
                raw_result=raw_result if isinstance(raw_result, Mapping) else None,
                exception=exc,
            )
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        if isinstance(structured, dict):
            payload = dict(structured)
            payload.setdefault(
                "outcome",
                normalize_agent_tool_result(
                    payload,
                    fallback_summary=f"{tool_name} completed.",
                    write=bool(payload.get("write")),
                ),
            )
            return payload
        return external_rejection(
            status="invalid_gateway_response",
            error="MCP tool response was not structured.",
            error_code="external_gateway_response_invalid",
            failure_layer="external_stdio_gateway_bridge",
            failure_phase="gateway_response_validation",
            operation_kind="tool",
            tool=tool_name,
            tool_routing_started=None,
            mutation_started=None,
            committed=None,
            raw_result=result if isinstance(result, Mapping) else None,
        )

    def manifest(
        self,
        exposure_layer: str = "planning",
        tool_blocks: list[str] | tuple[str, ...] | None = None,
        tool_names: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if exposure_layer not in {"planning", "execution"}:
            raise ValueError("exposure_layer must be planning or execution")
        token = self.require_token()
        params: dict[str, Any] = {"exposureLayer": exposure_layer}
        if tool_blocks is not None:
            params["toolBlocks"] = list(tool_blocks)
        if tool_names is not None:
            params["_meta"] = {"io.vrcforge/toolNames": list(tool_names)}
        return self._mcp_request(
            "tools/list",
            params,
            token=token,
        )

    def resources(self, *, cursor: str = "", page_size: int = 100) -> dict[str, Any]:
        token = self.require_token()
        params: dict[str, Any] = {"pageSize": page_size}
        if cursor:
            params["cursor"] = cursor
        return self._mcp_request("resources/list", params, token=token)

    def resource_templates(self) -> dict[str, Any]:
        return self._mcp_request("resources/templates/list", {}, token=self.require_token())

    def read_resource(self, uri: str) -> dict[str, Any]:
        return self._mcp_request("resources/read", {"uri": uri}, token=self.require_token())

    def resource_generation(self) -> int:
        if self._response_list_revisions is not None:
            return self._response_list_revisions["resources"]
        return int(self.resources(page_size=1).get("resourceGeneration") or 0)

    def prompts(self, *, cursor: str = "", page_size: int = 100) -> dict[str, Any]:
        token = self.require_token()
        params: dict[str, Any] = {"pageSize": page_size}
        if cursor:
            params["cursor"] = cursor
        return self._mcp_request("prompts/list", params, token=token)

    def get_prompt(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._mcp_request(
            "prompts/get",
            {"name": name, "arguments": dict(arguments or {})},
            token=self.require_token(),
        )

    def prompt_generation(self) -> str:
        if self._response_list_revisions is not None:
            return self._response_list_revisions["prompts"]
        return str(self.prompts(page_size=1).get("promptGeneration") or "")

    def _mcp_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        token: str,
    ) -> dict[str, Any]:
        request_id = f"stdio-{time.time_ns()}"
        meta = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "vrcforge-agent-stdio-bridge",
                "version": "1.8.4",
            },
        }
        if method in {"tools/call", "tools/list", "prompts/get"}:
            # This is an internal hop; only the outer MCP router chooses presentation.
            meta["io.vrcforge/resultMode"] = "full"
        if method == "tools/call":
            meta["io.vrcforge/includeListRevisions"] = True
            # A failed or older response must fall back to fresh revision reads.
            self._response_list_revisions = None
        request_params = dict(params)
        selection_meta = request_params.pop("_meta", {})
        if not isinstance(selection_meta, Mapping):
            raise ValueError("MCP metadata must be an object")
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {"_meta": {**selection_meta, **meta}, **request_params},
        }
        extra_headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if method == "tools/call":
            extra_headers["Mcp-Name"] = str(params.get("name") or "")
        response = self.request_json(
            "POST",
            "/mcp",
            token=token,
            payload=payload,
            allow_http_error=False,
            extra_headers=extra_headers,
            timeout_seconds=(
                self.tool_call_timeout_seconds
                if method == "tools/call"
                else self.timeout_seconds
            ),
        )
        if not isinstance(response, dict):
            raise RuntimeError("VRCForge MCP returned a non-object response.")
        if isinstance(response.get("error"), dict):
            error = response["error"]
            raise ExternalMcpBridgeError(
                str(error.get("message") or "VRCForge MCP request failed."),
                raw_result=response,
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("VRCForge MCP response did not contain a result object.")
        if method == "tools/call":
            result_meta = result.get("_meta")
            revisions = result_meta.get("io.vrcforge/listRevisions") if isinstance(result_meta, Mapping) else None
            if (isinstance(revisions, Mapping)
                and type(revisions.get("resources")) is int
                and revisions["resources"] >= 0
                and isinstance(revisions.get("prompts"), str)):
                self._response_list_revisions = dict(revisions)
        return dict(result)

    def require_token(self) -> str:
        config_path = self.resolve_config_path()
        config = read_json_file(config_path)
        token = str(os.environ.get("VRCFORGE_AGENT_TOKEN") or config.get("token") or "")
        if not token:
            raise RuntimeError("VRCForge Agent Gateway token was not found.")
        return token

    def resolve_config_path(self) -> Path | None:
        if self.config_path is not None:
            return self.config_path
        candidates: list[Path] = []
        user_data = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data:
            candidates.append(Path(user_data) / "config" / "agent_gateway.json")
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            candidates.append(Path(local_app_data) / "VRCForge" / "agentic-app" / "config" / "agent_gateway.json")
        candidates.append(Path.cwd() / "agent_gateway.json")
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return candidates[0].resolve() if candidates else None

    def runtime_port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", url_port(self.base_url)), timeout=0.35):
                return True
        except OSError:
            return False

    def try_launch_runtime(self) -> dict[str, Any]:
        exe = find_vrcforge_executable()
        if not exe:
            return external_rejection(
                status="runtime_executable_not_found",
                error="VRCForge.exe was not found. Start VRCForge Desktop, then retry.",
                error_code="external_runtime_executable_not_found",
                failure_layer="external_runtime_bootstrap",
                failure_phase="executable_resolution",
                operation_kind="runtime_bootstrap",
            )
        try:
            subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except OSError as exc:
            return external_rejection(
                status="runtime_launch_failed",
                error=str(exc),
                error_code="external_runtime_launch_failed",
                failure_layer="external_runtime_bootstrap",
                failure_phase="process_launch",
                operation_kind="runtime_bootstrap",
                exception=exc,
                path=str(exe),
            )
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if self.runtime_port_open():
                return {"ok": True, "path": str(exe)}
            time.sleep(0.25)
        return external_rejection(
            status="runtime_start_timeout",
            error="VRCForge runtime did not open its loopback port in time.",
            error_code="external_runtime_start_timeout",
            failure_layer="external_runtime_bootstrap",
            failure_phase="loopback_readiness",
            operation_kind="runtime_bootstrap",
            retryable=True,
            path=str(exe),
        )

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict[str, Any] | None = None,
        allow_http_error: bool = True,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                **dict(extra_headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
            ) as response:  # noqa: S310 - loopback-only URL.
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            if not allow_http_error:
                raise ExternalHttpBridgeError(
                    status_code=exc.code,
                    path=path,
                    body=text,
                ) from exc
            return {"ok": False, "status": exc.code, "error": text}
        return json.loads(text or "{}")


def run_stdio_server(
    bridge: VRCForgeBridge,
    *,
    protocol_profile: str = "vrcforge-2026",
    exposure_layer: str = "planning",
) -> None:
    if protocol_profile not in {"auto", "vrcforge-2026", "mcp-1x"}:
        raise ValueError("protocol_profile must be auto, vrcforge-2026 or mcp-1x")
    if exposure_layer not in {"planning", "execution"}:
        raise ValueError("exposure_layer must be planning or execution")

    loaded_blocks = {"core"}
    # None shows a whole leaf; sets are additive display selections, never permission grants.
    displayed_tools: dict[str, set[str] | None] = {}
    tool_list_revision = 0
    requested_layer = {"value": exposure_layer}
    # A failed fresh catalogue lookup makes the backend state unknown for this
    # stdio session.  Local discovery controls may still run, but revision
    # callbacks must not turn that local result into another remote request.
    catalogue_backend_unavailable = False
    # Opaque handles are scoped to this stdio server process, inherit the
    # authenticated transport, and expire when any covered block unloads.
    activation_handles: dict[str, dict[str, Any]] = {}

    def bridge_tool_block(item: Mapping[str, Any]) -> str:
        meta = item.get("_meta") if isinstance(item.get("_meta"), Mapping) else {}
        return str(meta.get("toolBlock") or "").strip().lower()

    def item_owner(item: Mapping[str, Any]) -> str:
        return canonical_tool_owner(
            bridge_tool_block(item),
            str(item.get("name") or "").strip(),
        )

    def resolve_block_selector(value: Any) -> str:
        selector = str(value or "").strip().lower()
        indexed = EXTERNAL_TOOL_BLOCK_INDEXES.get(
            selector, EXTERNAL_TOOL_BLOCK_LEAF_INDEXES.get(selector, "")
        )
        canonical = indexed or canonical_external_block(selector)
        if canonical in CANONICAL_TOOL_BLOCKS or canonical in CANONICAL_TOOL_LEAVES:
            return canonical
        return ""

    def block_controls() -> list[dict[str, Any]]:
        block_enum = [
            *EXTERNAL_TOOL_BLOCK_INDEXES,
            *EXTERNAL_TOOL_BLOCK_LEAF_INDEXES,
            *EXTERNAL_TOOL_BLOCK_NAME_INDEXES,
            *CANONICAL_TOOL_BLOCK_ALIASES,
        ]
        controls = [
            {
                "name": "vrcforge_list_tool_blocks",
                "description": (
                    "When to use: See available and loaded blocks.\n"
                    "When NOT to use: Inspect or change Unity.\n"
                    "Negative example: Treating visibility as write approval."
                ),
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"block": {"type": "string", "enum": block_enum}},
                },
            },
            {
                "name": "vrcforge_load_tool_block",
                "description": (
                    "When to use: Load one needed leaf, optionally choosing exact toolNames from its index. Selected full schemas are returned for hosts that do not refresh tools/list.\n"
                    "When NOT to use: Load unrelated blocks or approve writes.\n"
                    "Negative example: Loading optimization to read compile errors."
                ),
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block"],
                    "properties": {
                        "block": {"type": "string", "enum": block_enum},
                        "toolNames": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True,
                                      "items": {"type": "string", "minLength": 1},
                                      "description": "Exact names from this leaf's index. Adds displayed tools; omit to display the whole leaf. Unload first to narrow an already loaded selection. This does not change call permissions."},
                    },
                },
            },
            {
                "name": "vrcforge_unload_tool_block",
                "description": (
                    "When to use: Hide one finished block.\n"
                    "When NOT to use: Undo, restore, or delete Unity changes.\n"
                    "Negative example: Unloading avatar to revert an edit."
                ),
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block"],
                    "properties": {"block": {"type": "string", "enum": block_enum}},
                },
            },
        ]
        return [
            standardize_tool_descriptor(
                item,
                write=False,
                block="core",
                exposure_layer=requested_layer["value"],
                catalog_generation=tool_list_revision,
            )
            for item in controls
        ]

    def prompt_controls() -> list[dict[str, Any]]:
        """Expose the native prompt registry through bounded read controls."""
        return [
            standardize_tool_descriptor(
                {
                    "name": "vrcforge_list_prompts",
                    "description": (
                        "When to use: List the available VRCForge native .vsk prompts with bounded pagination.\n"
                        "When NOT to use: Execute a prompt, inspect Unity, or change project files.\n"
                        "Negative example: Guessing a prompt name instead of listing the prompt registry."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "cursor": {"type": "string", "maxLength": 512},
                            "pageSize": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                    },
                },
                write=False,
                block="core",
                exposure_layer=requested_layer["value"],
                catalog_generation=tool_list_revision,
            ),
            standardize_tool_descriptor(
                {
                    "name": "vrcforge_get_prompt",
                    "description": (
                        "When to use: Read one exact native .vsk prompt by name and supply its declared arguments.\n"
                        "When NOT to use: Use arbitrary methods, bypass prompt validation, or mutate Unity.\n"
                        "Negative example: Treating a prompt read as approval to execute its project writes."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": 240},
                            "arguments": {"type": "object", "additionalProperties": True},
                        },
                    },
                },
                write=False,
                block="core",
                exposure_layer=requested_layer["value"],
                catalog_generation=tool_list_revision,
            ),
        ]

    def activation_tools(requested_exposure: str) -> list[dict[str, Any]]:
        common_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["activationHandle", "toolName", "arguments"],
            "properties": {
                "activationHandle": {
                    "type": "string",
                    "minLength": 16,
                    "description": "Opaque handle returned by vrcforge_load_tool_block in this MCP session.",
                },
                "toolName": {"type": "string", "minLength": 1},
                "arguments": {"type": "object", "additionalProperties": True},
            },
        }
        tools = [
            standardize_tool_descriptor(
                {
                    "name": "vrcforge_invoke_loaded_read_tool",
                    "description": (
                        "When to use: Invoke one exact read Tool from a block already activated in this MCP session when the host has not refreshed tools/list.\n"
                        "When NOT to use: Do not invoke writes, unloaded blocks, guessed Tool names, or reuse a handle after unload/server restart.\n"
                        "Negative example: Calling a material write through the read bridge after loading only the avatar block."
                    ),
                    "inputSchema": common_schema,
                },
                write=False,
                block="core",
                exposure_layer=requested_exposure,
                catalog_generation=tool_list_revision,
            )
        ]
        if requested_exposure == "execution":
            tools.append(standardize_tool_descriptor(
                {
                    "name": "vrcforge_invoke_loaded_write_tool",
                    "description": (
                        "When to use: Invoke one exact supervised write Tool from a block already activated in this execution MCP session; normal approval, checkpoint, identity, and readback rules still apply.\n"
                        "When NOT to use: Do not use during planning, for reads, unloaded blocks, guessed Tool names, or to bypass approval.\n"
                        "Negative example: Treating an activation handle as user approval for a destructive write."
                    ),
                    "inputSchema": common_schema,
                },
                write=True,
                block="core",
                exposure_layer=requested_exposure,
                catalog_generation=tool_list_revision,
            ))
        return tools

    def block_inventory(selector: Any = "") -> dict[str, Any]:
        selected_block = resolve_block_selector(selector)
        if str(selector or "").strip() and not selected_block:
            return external_rejection(
                status="invalid_tool_block",
                error=f"Unknown external MCP tool block: {str(selector).strip()}",
                error_code="external_tool_block_unknown",
                failure_layer="external_tool_discovery",
                failure_phase="block_selection",
                operation_kind="discovery",
                details={"selector": str(selector).strip()},
                loadedBlocks=sorted(loaded_blocks),
            )
        manifests: dict[str, list[Mapping[str, Any]]] = {"planning": [], "execution": []}
        unavailable_layers: list[str] = []
        for layer in manifests:
            try:
                manifest = bridge.manifest(layer, ["*"])
            except Exception:
                unavailable_layers.append(layer)
                continue
            if not isinstance(manifest, Mapping) or not isinstance(manifest.get("tools"), list):
                unavailable_layers.append(layer)
                continue
            manifests[layer] = [
                item
                for item in (manifest.get("tools", []) if isinstance(manifest, Mapping) else [])
                if isinstance(item, Mapping)
                and str(item.get("name") or "").strip() not in HIDDEN_EXTERNAL_TOOLS
            ]

        counts = {
            leaf_id: {
                "planningToolCount": None if "planning" in unavailable_layers else sum(item_owner(item) == leaf_id for item in manifests["planning"]),
                "executionToolCount": None if "execution" in unavailable_layers else sum(item_owner(item) == leaf_id for item in manifests["execution"]),
            }
            for leaf_id in CANONICAL_TOOL_LEAVES
        }

        def routing_fields(block_id: str) -> dict[str, Any]:
            routing = canonical_block_routing(block_id)
            def entries(key: str) -> list[str]:
                value = routing.get(key, ())
                return [value] if isinstance(value, str) else list(value)
            return {
                "whenToUse": entries("useWhen"),
                "doNotUse": entries("doNotUse"),
                "provides": entries("provides"),
                "planningExposure": routing.get("planningExposure", ""),
                "executionExposure": routing.get("executionExposure", ""),
            }

        def leaf_node(leaf_id: str, *, expanded: bool) -> dict[str, Any]:
            tools = sorted(
                (item for item in manifests["execution"] if item_owner(item) == leaf_id),
                key=lambda item: str(item.get("name") or ""),
            )
            node: dict[str, Any] = {
                "index": EXTERNAL_TOOL_BLOCK_NAME_INDEXES[leaf_id],
                "name": leaf_id,
                "title": CANONICAL_TOOL_LEAVES[leaf_id]["title"],
                "loaded": leaf_id in loaded_blocks,
                "loadCall": {"name": "vrcforge_load_tool_block", "arguments": {"block": leaf_id}},
                **routing_fields(leaf_id),
                **counts[leaf_id],
            }
            if expanded:
                node["tools"] = [
                    {
                        "index": f"{node['index']}.{position}",
                        "name": str(item.get("name") or ""),
                        "mode": "write" if bool(item.get("write")) or str((item.get("_meta") or {}).get("permission") or "").casefold() == "write" else "read",
                    }
                    for position, item in enumerate(tools, start=1)
                ]
            return node

        def root_node(root_id: str, *, expanded: bool) -> dict[str, Any]:
            leaf_ids = [f"{root_id}/{leaf}" for leaf in CANONICAL_TOOL_BLOCKS[root_id]["children"]]
            return {
                "index": EXTERNAL_TOOL_BLOCK_NAME_INDEXES[root_id],
                "name": root_id,
                "title": CANONICAL_TOOL_BLOCKS[root_id]["title"],
                "loaded": any(leaf_id in loaded_blocks for leaf_id in leaf_ids),
                "fullyLoaded": all(leaf_id in loaded_blocks for leaf_id in leaf_ids),
                "expandable": True,
                **routing_fields(root_id),
                "children": [leaf_node(leaf_id, expanded=False) for leaf_id in leaf_ids] if expanded else [],
            }

        if selected_block in CANONICAL_TOOL_LEAVES:
            tree: dict[str, Any] = leaf_node(selected_block, expanded=True)
        elif selected_block in CANONICAL_TOOL_BLOCKS:
            tree = root_node(selected_block, expanded=True)
        else:
            tree = {
                "index": "0",
                "name": "capabilities",
                "children": [root_node(root_id, expanded=False) for root_id in CANONICAL_TOOL_BLOCKS],
            }
        return {
            "ok": True,
            "schema": "vrcforge.external_tool_blocks.v2",
            "catalogStatus": "unavailable" if len(unavailable_layers) == 2 else ("partial" if unavailable_layers else "complete"),
            "manifestComplete": not unavailable_layers,
            "unavailableLayers": unavailable_layers,
            "catalogMessage": (
                "Backend tool inventory is incomplete. Null counts mean unknown, not zero capabilities; "
                "the tree retains local navigation only. Run vrcforge_bridge_preflight to diagnose the connection."
                if unavailable_layers else "Both backend exposure inventories were read successfully."
            ),
            "loadedBlocks": sorted(loaded_blocks),
            "catalogGeneration": tool_list_revision,
            "selectedBlock": selected_block,
            "selectionHint": (
                "Default tools/list shows startup controls and displayed leaf tools. Select a matching leaf: tree.tools[].name gives exact names for load_tool_block toolNames. Omit toolNames to display the whole leaf. Selected full schemas and activationHandle support hosts without tools/list refresh. Existing direct calls retain their original permissions. tools/list _meta io.vrcforge/toolNames or resultMode=full also remain available."
            ),
            "tree": tree,
        }

    def list_tools(params: Mapping[str, Any]) -> list[dict[str, Any]]:
        nonlocal catalogue_backend_unavailable
        requested_exposure = str(params.get("exposureLayer") or exposure_layer)
        if requested_exposure not in {"planning", "execution"}:
            raise ValueError("exposureLayer must be planning or execution")
        if "exposureLayer" in params:
            requested_layer["value"] = requested_exposure
        tools: list[dict[str, Any]] = [
            standardize_tool_descriptor({
                "name": "vrcforge_bridge_preflight",
                "description": (
                    "When to use: Check whether the local VRCForge App gateway is authenticated and ready.\n"
                    "When NOT to use: Inspect or mutate Unity directly.\n"
                    "Negative example: Treating a reachable bridge as proof that a Unity target is bound."
                ),
                "inputSchema": {"type": "object", "additionalProperties": False},
            }, write=False, block="core", exposure_layer=requested_exposure, catalog_generation=tool_list_revision)
        ]
        tools.extend(block_controls())
        tools.extend(prompt_controls())
        tools.extend(activation_tools(requested_exposure))
        try:
            # This authenticated, fresh manifest already checks availability.
            # Full preflight remains an explicit tool, not two extra catalog reads.
            lookup_names = params.get("_lookupToolNames")
            manifest = bridge.manifest(requested_exposure, ["*"], lookup_names if isinstance(lookup_names, list) else None)
        except Exception:
            catalogue_backend_unavailable = True
            # A per-call catalogue lookup is an authorization precondition:
            # preserve backend availability failures so the standard router
            # cannot misreport them as a missing/excluded Tool.  Plain
            # tools/list remains usable offline and continues to expose only
            # local discovery controls.
            local_tool_names = {
                str(item.get("name") or "")
                for item in tools
                if isinstance(item, Mapping)
            }
            if (
                isinstance(lookup_names, list)
                and lookup_names
                and any(str(name) not in local_tool_names for name in lookup_names)
            ):
                raise
            return tools
        catalogue_backend_unavailable = False
        manifest_tools = manifest.get("tools") if isinstance(manifest, dict) else []
        for item in manifest_tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            block = item_owner(item)
            if (
                not name
                or name in HIDDEN_EXTERNAL_TOOLS
                or not block
                or (
                    bridge_tool_block(item) != "core"
                    and block not in loaded_blocks
                )
            ):
                continue
            metadata = dict(item.get("_meta") or {})
            selection = displayed_tools.get(block)
            visible = name in DEFAULT_DISCOVERY_TOOLS or (block in loaded_blocks and (selection is None or name in selection))
            if not visible:
                metadata["io.vrcforge/defaultVisible"] = False
            tools.append(standardize_tool_descriptor(
                {
                    **item,
                    "_meta": metadata,
                    "name": name,
                    "description": str(item.get("description") or name),
                    "inputSchema": item.get("inputSchema")
                    if isinstance(item.get("inputSchema"), dict)
                    else {"type": "object", "additionalProperties": True},
                },
                write=bool(item.get("write")) or str((item.get("_meta") or {}).get("permission") or "").casefold() == "write",
                block=block,
                exposure_layer=requested_exposure,
                catalog_generation=tool_list_revision,
            ))
        return tools

    def invoke_activated_tool(arguments: Mapping[str, Any], *, write: bool) -> dict[str, Any]:
        handle = str(arguments.get("activationHandle") or "").strip()
        delegated_name = str(arguments.get("toolName") or "").strip()
        delegated_arguments = arguments.get("arguments")
        activation = activation_handles.get(handle)
        if activation is None:
            return external_rejection(
                status="invalid_activation_handle",
                error="The activation handle is unknown or expired; load the required Tool block again.",
                error_code="external_tool_activation_handle_invalid",
                failure_layer="external_tool_discovery",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                details={"toolName": delegated_name},
                catalogGeneration=tool_list_revision,
                loadedBlocks=sorted(loaded_blocks),
            )
        if not isinstance(delegated_arguments, Mapping) or not delegated_name:
            return external_rejection(
                status="invalid_activated_tool_call",
                error="toolName must be non-empty and arguments must be an object.",
                error_code="external_tool_activation_call_invalid",
                failure_layer="external_tool_discovery",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                catalogGeneration=tool_list_revision,
                loadedBlocks=sorted(loaded_blocks),
            )
        covered_blocks = set(activation.get("blocks") or ())
        if write and activation.get("exposureLayer") != "execution":
            return external_rejection(
                status="planning_activation_cannot_write",
                error="An activation handle minted in planning mode cannot invoke write Tools.",
                error_code="external_tool_activation_planning_write_blocked",
                failure_layer="external_tool_permission",
                failure_phase="activated_tool_invocation",
                operation_kind="write",
                details={"toolName": delegated_name},
                catalogGeneration=tool_list_revision,
                loadedBlocks=sorted(loaded_blocks),
            )
        if not covered_blocks or not covered_blocks.issubset(loaded_blocks):
            activation_handles.pop(handle, None)
            return external_rejection(
                status="expired_activation_handle",
                error="A Tool block covered by the activation handle was unloaded; load it again.",
                error_code="external_tool_activation_handle_expired",
                failure_layer="external_tool_discovery",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                details={"toolName": delegated_name, "blocks": sorted(covered_blocks)},
                catalogGeneration=tool_list_revision,
                loadedBlocks=sorted(loaded_blocks),
            )
        manifest = bridge.manifest("execution" if write else requested_layer["value"], ["*"], [delegated_name])
        manifest_tools = manifest.get("tools") if isinstance(manifest, Mapping) else []
        descriptor = next(
            (
                item
                for item in manifest_tools
                if isinstance(item, Mapping)
                and str(item.get("name") or "").strip() == delegated_name
                and item_owner(item) in covered_blocks
            ),
            None,
        )
        if descriptor is None:
            return external_rejection(
                status="tool_not_activated",
                error="The requested Tool is not in the block covered by this activation handle.",
                error_code="external_tool_not_activated",
                failure_layer="external_tool_discovery",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                details={"toolName": delegated_name, "blocks": sorted(covered_blocks)},
                catalogGeneration=tool_list_revision,
                loadedBlocks=sorted(loaded_blocks),
            )
        meta = descriptor.get("_meta") if isinstance(descriptor.get("_meta"), Mapping) else {}
        descriptor_write = bool(descriptor.get("write")) or str(meta.get("permission") or "").strip().casefold() == "write"
        if descriptor_write != write:
            expected = "write" if write else "read"
            return external_rejection(
                status="activated_tool_effect_mismatch",
                error=f"The {expected} activation bridge cannot invoke this Tool effect.",
                error_code="external_tool_activation_effect_mismatch",
                failure_layer="external_tool_discovery",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                details={"toolName": delegated_name, "requestedWrite": write},
                catalogGeneration=tool_list_revision,
                loadedBlocks=sorted(loaded_blocks),
            )
        # Hosts may attach prompt/skill provenance beside the nested delegated
        # arguments. Preserve the older nested form, while refusing to choose
        # silently when both locations disagree.
        forwarded_arguments = dict(delegated_arguments)
        outer_has_provenance = "promptSkillProvenance" in arguments
        nested_has_provenance = "promptSkillProvenance" in delegated_arguments
        outer_provenance = arguments.get("promptSkillProvenance")
        nested_provenance = delegated_arguments.get("promptSkillProvenance")
        if outer_has_provenance and not isinstance(outer_provenance, Mapping):
            return external_rejection(
                status="invalid_activated_tool_provenance",
                error="promptSkillProvenance must be an object when supplied at the activated dispatcher level.",
                error_code="external_tool_provenance_invalid",
                failure_layer="external_tool_provenance",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                tool=delegated_name,
            )
        if outer_has_provenance and nested_has_provenance and outer_provenance != nested_provenance:
            return external_rejection(
                status="activated_tool_provenance_conflict",
                error="promptSkillProvenance was supplied in both dispatcher and delegated arguments with conflicting values.",
                error_code="external_tool_provenance_conflict",
                failure_layer="external_tool_provenance",
                failure_phase="activated_tool_invocation",
                operation_kind="discovery",
                tool=delegated_name,
                details={"sources": ["dispatcher", "delegatedArguments"]},
            )
        if outer_has_provenance and not nested_has_provenance:
            forwarded_arguments["promptSkillProvenance"] = outer_provenance
        result = bridge.call_tool(delegated_name, forwarded_arguments, agent_name="external-stdio-agent")
        if not isinstance(result, Mapping):
            return {"ok": True, "value": result, "delegatedToolName": delegated_name}
        return {
            **dict(result),
            "delegatedToolName": delegated_name,
            "activationCatalogGeneration": activation.get("catalogGeneration"),
            "catalogGeneration": tool_list_revision,
        }

    def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal tool_list_revision
        if tool_name == "vrcforge_bridge_preflight":
            return bridge.preflight()
        if tool_name == "vrcforge_list_prompts":
            cursor = arguments.get("cursor", "")
            page_size = arguments.get("pageSize", 100)
            if not isinstance(cursor, str) or len(cursor) > 512:
                raise ValueError("cursor must be a string no longer than 512 characters")
            if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
                raise ValueError("pageSize must be an integer between 1 and 100")
            return list_prompts({"cursor": cursor, "pageSize": page_size})
        if tool_name == "vrcforge_get_prompt":
            name = arguments.get("name")
            prompt_arguments = arguments.get("arguments", {})
            if not isinstance(name, str) or not name.strip() or len(name) > 240:
                raise ValueError("name must be a non-empty string no longer than 240 characters")
            if not isinstance(prompt_arguments, Mapping):
                raise ValueError("arguments must be an object")
            return get_prompt(name.strip(), prompt_arguments)
        if tool_name == "vrcforge_list_tool_blocks":
            return block_inventory(arguments.get("block"))
        if tool_name == "vrcforge_invoke_loaded_read_tool":
            return invoke_activated_tool(arguments, write=False)
        if tool_name == "vrcforge_invoke_loaded_write_tool":
            if requested_layer["value"] != "execution":
                return external_rejection(
                    status="write_tool_unavailable_in_planning",
                    error="The activated write bridge is unavailable in planning mode.",
                    error_code="external_write_tool_planning_blocked",
                    failure_layer="external_tool_permission",
                    failure_phase="activated_tool_invocation",
                    operation_kind="write",
                    catalogGeneration=tool_list_revision,
                )
            return invoke_activated_tool(arguments, write=True)
        if tool_name in {"vrcforge_load_tool_block", "vrcforge_unload_tool_block"}:
            selector = str(arguments.get("block") or "").strip().lower()
            block = resolve_block_selector(selector)
            if not block:
                return external_rejection(
                    status="invalid_tool_block",
                    error=f"Unknown external MCP tool block: {selector or 'missing'}",
                    error_code="external_tool_block_unknown",
                    failure_layer="external_tool_discovery",
                    failure_phase="block_selection",
                    operation_kind="discovery",
                    details={"selector": selector},
                    loadedBlocks=sorted(loaded_blocks),
                )
            if block in CANONICAL_TOOL_BLOCKS:
                return external_rejection(
                    status="tool_leaf_required",
                    error="A first-level category is routing-only; select and load one second-level Tool leaf.",
                    error_code="external_tool_leaf_required",
                    failure_layer="external_tool_discovery",
                    failure_phase="block_selection",
                    operation_kind="discovery",
                    details={"selector": selector, "category": block},
                    loadedBlocks=sorted(loaded_blocks),
                )
            targets = (block,)
            selected_names = None
            if tool_name == "vrcforge_load_tool_block" and "toolNames" in arguments:
                selected_names = arguments["toolNames"]
                valid = (
                    isinstance(selected_names, list) and 1 <= len(selected_names) <= 32
                    and all(isinstance(name, str) and name and name == name.strip() for name in selected_names)
                    and len(set(selected_names)) == len(selected_names)
                )
                if valid:
                    try:
                        manifest = bridge.manifest(requested_layer["value"], ["*"], selected_names)
                    except (ExternalMcpBridgeError, ExternalHttpBridgeError) as exc:
                        if isinstance(exc, ExternalHttpBridgeError) and exc.status_code != 400:
                            raise
                        upstream_error = (exc.raw_result or {}).get("error")
                        if not isinstance(upstream_error, Mapping) or upstream_error.get("code") != -32602:
                            raise
                        return external_rejection(
                            status="invalid_tool_selection",
                            error=str(upstream_error.get("message") or exc),
                            error_code="external_tool_selection_invalid",
                            failure_layer="external_tool_discovery",
                            failure_phase="block_selection", operation_kind="discovery",
                            loadedBlocks=sorted(loaded_blocks),
                        )
                    available = {str(item.get("name") or "") for item in manifest.get("tools", [])
                                 if isinstance(item, Mapping) and item_owner(item) == block
                                 and str(item.get("name") or "") not in HIDDEN_EXTERNAL_TOOLS}
                    valid = set(selected_names).issubset(available)
                if not valid:
                    return external_rejection(
                        status="invalid_tool_selection", error="toolNames must contain 1..32 unique exact names available in this leaf and exposure layer.",
                        error_code="external_tool_selection_invalid", failure_layer="external_tool_discovery",
                        failure_phase="block_selection", operation_kind="discovery", loadedBlocks=sorted(loaded_blocks),
                    )
            if tool_name == "vrcforge_load_tool_block":
                was_loaded = block in loaded_blocks
                previous = displayed_tools.get(block)
                selection = (set(selected_names) if not was_loaded else None if previous is None else previous | set(selected_names)) if selected_names is not None else None
                changed = not was_loaded or selection != previous
                loaded_blocks.update(targets)
                displayed_tools[block] = selection
            else:
                changed = any(target in loaded_blocks for target in targets)
                loaded_blocks.difference_update(targets)
                displayed_tools.pop(block, None)
                for handle, activation in list(activation_handles.items()):
                    if set(activation.get("blocks") or ()).intersection(targets):
                        activation_handles.pop(handle, None)
            if changed:
                tool_list_revision += 1
            response = {
                "ok": True,
                "status": "loaded" if tool_name == "vrcforge_load_tool_block" else "unloaded",
                "block": block,
                "blockIndex": EXTERNAL_TOOL_BLOCK_NAME_INDEXES[block],
                "changed": changed,
                "toolListChanged": changed,
                "toolListRevision": tool_list_revision,
                "catalogGeneration": tool_list_revision,
                "loadedBlocks": sorted(loaded_blocks),
            }
            if tool_name == "vrcforge_load_tool_block":
                handle = "vrcforge-act-" + secrets.token_urlsafe(24)
                activation_handles[handle] = {
                    "blocks": tuple(targets),
                    "catalogGeneration": tool_list_revision,
                    "exposureLayer": requested_layer["value"],
                }
                while len(activation_handles) > 64:
                    activation_handles.pop(next(iter(activation_handles)))
                response["activationHandle"] = handle
                response["activation"] = {
                    "handle": handle,
                    "blocks": list(targets),
                    "catalogGeneration": tool_list_revision,
                    "exposureLayer": requested_layer["value"],
                    "expiresWhen": "covered_block_unloaded_or_stdio_server_exits",
                }
                if selected_names is not None:
                    response["selectedTools"] = [project_tool(item, mode="compact") for item in list_tools({"exposureLayer": requested_layer["value"]}) if item["name"] in selected_names]
            return response
        return bridge.call_tool(tool_name, arguments, agent_name="external-stdio-agent")

    def list_resources(params: Mapping[str, Any]) -> dict[str, Any]:
        callback = getattr(bridge, "resources", None)
        if not callable(callback):
            return {"resources": [], "resourceGeneration": 0}
        return callback(
            cursor=str(params.get("cursor") or ""),
            page_size=int(params.get("pageSize") or 100),
        )

    def list_resource_templates(_params: Mapping[str, Any]) -> dict[str, Any]:
        callback = getattr(bridge, "resource_templates", None)
        return callback() if callable(callback) else {"resourceTemplates": []}

    def read_resource(uri: str) -> dict[str, Any]:
        callback = getattr(bridge, "read_resource", None)
        if not callable(callback):
            raise ValueError("Resource registry is unavailable")
        return callback(uri)

    def resource_generation() -> int | None:
        if catalogue_backend_unavailable:
            return None
        callback = getattr(bridge, "resource_generation", None)
        return int(callback()) if callable(callback) else 0

    def list_prompts(params: Mapping[str, Any]) -> dict[str, Any]:
        callback = getattr(bridge, "prompts", None)
        if not callable(callback):
            return {"prompts": [], "promptGeneration": ""}
        return callback(
            cursor=str(params.get("cursor") or ""),
            page_size=int(params.get("pageSize") or 100),
        )

    def get_prompt(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        callback = getattr(bridge, "get_prompt", None)
        if not callable(callback):
            raise ValueError("Prompt registry is unavailable")
        result = callback(name, arguments)
        if not isinstance(result, Mapping):
            raise ValueError("Prompt registry returned an invalid response")
        projected = dict(result)
        structured = projected.get("structuredContent")
        native_meta = projected.get("_meta")
        provenance = dict(native_meta) if isinstance(native_meta, Mapping) else {}
        if not provenance and isinstance(structured, Mapping) and isinstance(structured.get("provenance"), Mapping):
            provenance = dict(structured["provenance"])
        if provenance:
            provenance["status"] = "available"
            projected["promptSkillProvenance"] = provenance
            if isinstance(structured, Mapping):
                structured_copy = dict(structured)
                structured_copy["promptSkillProvenance"] = dict(provenance)
                projected["structuredContent"] = structured_copy
        return projected

    def prompt_generation() -> str | None:
        if catalogue_backend_unavailable:
            return None
        callback = getattr(bridge, "prompt_generation", None)
        return str(callback()) if callable(callback) else ""

    router_standard = McpStandardRouter(
        lambda: list_tools({"exposureLayer": requested_layer["value"]}),
        call_tool,
        server_name=DEFAULT_SERVER_NAME,
        server_version="1.8.4",
        tool_list_revision=lambda: tool_list_revision,
        tool_call_catalogue=lambda: list_tools({"exposureLayer": requested_layer["value"]}),
        tool_call_catalogue_for_call=lambda name, params: list_tools({
            "exposureLayer": requested_layer["value"],
            "_lookupToolNames": [name],
        }),
        resource_list=list_resources,
        resource_templates=list_resource_templates,
        resource_read=read_resource,
        resource_list_revision=resource_generation,
        prompt_list=list_prompts,
        prompt_get=get_prompt,
        prompt_list_revision=prompt_generation,
    )
    if protocol_profile == "mcp-1x":
        run_standard_stdio_loop(router_standard)
        return

    router_2026 = Mcp2026Router(
        list_tools,
        call_tool,
        server_name=DEFAULT_SERVER_NAME,
        server_version="1.8.4",
        tool_list_revision=lambda: tool_list_revision,
        tool_call_catalogue=lambda params: list_tools({"exposureLayer": requested_layer["value"], "_lookupToolNames": [str(params.get("name") or "")]}) if isinstance(params, Mapping) and params.get("name") else list_tools({"exposureLayer": requested_layer["value"]}),
        resource_list=list_resources,
        resource_templates=list_resource_templates,
        resource_read=read_resource,
        resource_list_revision=resource_generation,
        prompt_list=list_prompts,
        prompt_get=get_prompt,
        prompt_list_revision=prompt_generation,
    )
    if protocol_profile == "vrcforge-2026":
        run_stdio_loop(router_2026)
        return
    run_negotiated_stdio_loop(router_2026, router_standard)


def read_json_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def url_port(base_url: str) -> int:
    marker = "://"
    host_part = base_url.split(marker, 1)[1] if marker in base_url else base_url
    host_part = host_part.split("/", 1)[0]
    if ":" in host_part:
        return int(host_part.rsplit(":", 1)[1])
    return 80


def find_vrcforge_executable() -> Path | None:
    env_path = os.environ.get("VRCFORGE_EXE", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False) or executable.name.lower().startswith("vrcforge_backend"):
        candidates.extend(
            [
                executable.parent.parent / "VRCForge.exe",
                executable.parent / "VRCForge.exe",
            ]
        )
    root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            root / "VRCForge.exe",
            root / "dist" / "VRCForge_Windows_x64" / "VRCForge.exe",
        ]
    )
    program_files = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
    for base in program_files:
        if base:
            candidates.append(Path(base) / "VRCForge" / "VRCForge.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
