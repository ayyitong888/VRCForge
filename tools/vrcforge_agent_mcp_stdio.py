from __future__ import annotations

import argparse
import json
import os
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
from avatar_composition_workflow_skills import (
    AVATAR_COMPOSITION_WORKFLOW_SKILL_NAMES,
    AVATAR_COMPOSITION_WORKFLOW_SKILLS,
)
from external_tool_result_contract import build_external_tool_error
from agent_tool_result_contract import normalize_agent_tool_result


DEFAULT_BASE_URL = "http://127.0.0.1:8757"
DEFAULT_SERVER_NAME = "VRCForge Agent Bridge"
DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 360.0
HIDDEN_EXTERNAL_TOOLS = {
    "vrcforge_agent_message",
    "vrcforge_apply_approved",
    "vrcforge_execute_approved_shell",
    "vrcforge_execute_shell",
    "vrcforge_request_apply",
}
EXTERNAL_TOOL_BLOCK_DESCRIPTIONS = {
    "core": "Connection and basic Unity reads.",
    "project": "Unity projects and package-manager backends.",
    "avatar": "Avatar hierarchy, animation, parameters, and menus.",
    "assets": "Prefab and asset import or inspection.",
    "materials": "Materials, shaders, and textures.",
    "integrations": "Installed Unity plugin adapters; expand this branch before loading a family.",
    "integrations/modular-avatar": "Modular Avatar inspection, Setup Outfit, and atomic components.",
    "integrations/vrcfury": "VRCFury inspection and public-API-backed features.",
    "integrations/gesture-manager": "Gesture Manager Play Mode status and atomic runtime parameter control.",
    "optimization": "Avatar optimization.",
    "checkpoint": "Checkpoints and approved recovery.",
    "diagnostics": "Logs and validation.",
    "encryption": "Private avatar-encryption integration.",
    "skills": "Installed Skill workflows and local VSK package management; expand this branch before loading one family.",
    "skills/vsk": "Read-only preflight and atomic import/export for local .vsk Skill packages.",
    "skills/installed": "Read-only discovery and on-demand instructions for enabled installed user Skills.",
}
EXTERNAL_TOOL_BLOCK_DO_NOT_USE = {
    "core": "Do not use for project creation, asset edits, or recovery.",
    "project": "Do not use for avatar hierarchy, materials, or visual tuning.",
    "avatar": "Do not use for package installation, project registration, or log review.",
    "assets": "Do not use for material tuning, parameter budgets, or checkpoint restore.",
    "materials": "Do not use for hierarchy edits, package management, or recovery.",
    "integrations": "Do not load every plugin family when only one installed integration is relevant.",
    "integrations/modular-avatar": "Do not use for VRCFury features or plain Unity components.",
    "integrations/vrcfury": "Do not use when VRCFury is absent or for Modular Avatar components.",
    "integrations/gesture-manager": "Do not use outside Gesture Manager runtime inspection or parameter testing.",
    "optimization": "Do not use for ordinary edits before a concrete budget or performance need exists.",
    "checkpoint": "Do not use checkpoint restore as an automatic response to a failed tool call.",
    "diagnostics": "Do not use diagnostic tools to mutate the avatar or clear evidence.",
    "encryption": "Do not use unless the private encryption integration is explicitly in scope.",
    "skills": "Do not load unrelated Skill families or treat reading a workflow as approval to execute it.",
    "skills/vsk": "Do not use preflight as installation proof or overwrite an existing export path.",
    "skills/installed": "Do not import packages, read arbitrary host files, execute a Skill, or bypass write approval.",
}
EXTERNAL_TOOL_BLOCK_INDEXES = {
    "1": "core",
    "2": "project",
    "3": "avatar",
    "4": "assets",
    "5": "materials",
    "6": "integrations",
    "7": "optimization",
    "8": "checkpoint",
    "9": "diagnostics",
    "10": "encryption",
    "11": "skills",
}
EXTERNAL_TOOL_BLOCK_BRANCHES = {
    "integrations": (
        "integrations/modular-avatar",
        "integrations/vrcfury",
        "integrations/gesture-manager",
    ),
    "skills": ("skills/vsk", "skills/installed"),
}
EXTERNAL_TOOL_BLOCK_LEAF_INDEXES = {
    "6.1": "integrations/modular-avatar",
    "6.2": "integrations/vrcfury",
    "6.3": "integrations/gesture-manager",
    "11.1": "skills/vsk",
    "11.2": "skills/installed",
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
            report["ok"] = (
                bool(config.get("enabled"))
                and bool(config.get("allow_write_requests", True))
                and bool(actual_write_names)
                and actual_write_names.isdisjoint(planning_names)
                and "vrcforge_request_apply" not in tool_names
                and not bool(HIDDEN_EXTERNAL_TOOLS & tool_names)
            )
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
    ) -> dict[str, Any]:
        if exposure_layer not in {"planning", "execution"}:
            raise ValueError("exposure_layer must be planning or execution")
        token = self.require_token()
        params: dict[str, Any] = {"exposureLayer": exposure_layer}
        if tool_blocks is not None:
            params["toolBlocks"] = list(tool_blocks)
        return self._mcp_request(
            "tools/list",
            params,
            token=token,
        )

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
                "version": "1.7.10",
            },
        }
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {"_meta": meta, **params},
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
    tool_list_revision = 0

    def bridge_tool_block(item: Mapping[str, Any]) -> str:
        meta = item.get("_meta") if isinstance(item.get("_meta"), Mapping) else {}
        return str(meta.get("toolBlock") or "").strip().lower()

    def resolve_block_selector(value: Any) -> str:
        selector = str(value or "").strip().lower()
        if selector in EXTERNAL_TOOL_BLOCK_DESCRIPTIONS:
            return selector
        return EXTERNAL_TOOL_BLOCK_INDEXES.get(
            selector,
            EXTERNAL_TOOL_BLOCK_LEAF_INDEXES.get(selector, ""),
        )

    def block_controls() -> list[dict[str, Any]]:
        block_enum = [
            *EXTERNAL_TOOL_BLOCK_INDEXES,
            *EXTERNAL_TOOL_BLOCK_LEAF_INDEXES,
            *EXTERNAL_TOOL_BLOCK_NAME_INDEXES,
        ]
        return [
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
                    "When to use: Load one needed block.\n"
                    "When NOT to use: Load unrelated blocks or approve writes.\n"
                    "Negative example: Loading optimization to read compile errors."
                ),
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block"],
                    "properties": {"block": {"type": "string", "enum": block_enum}},
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
        counts = {
            block: {"planningToolCount": 0, "executionToolCount": 0}
            for block in EXTERNAL_TOOL_BLOCK_DESCRIPTIONS
        }
        execution_tools: list[Mapping[str, Any]] = []
        for layer, count_key in (
            ("planning", "planningToolCount"),
            ("execution", "executionToolCount"),
        ):
            try:
                manifest = bridge.manifest(layer, ["*"])
            except Exception:
                continue
            manifest_tools = manifest.get("tools", []) if isinstance(manifest, dict) else []
            if layer == "execution":
                execution_tools = [item for item in manifest_tools if isinstance(item, Mapping)]
            for item in manifest_tools:
                if not isinstance(item, Mapping):
                    continue
                block = bridge_tool_block(item)
                if block in counts:
                    counts[block][count_key] += 1
        for branch, descendants in EXTERNAL_TOOL_BLOCK_BRANCHES.items():
            for count_key in ("planningToolCount", "executionToolCount"):
                counts[branch][count_key] = sum(
                    counts[child][count_key] for child in descendants
                )

        tool_leaves: dict[str, list[dict[str, str]]] = {
            block: [] for block in EXTERNAL_TOOL_BLOCK_DESCRIPTIONS
        }
        indexed_blocks = list(EXTERNAL_TOOL_BLOCK_DESCRIPTIONS)
        for indexed_block in indexed_blocks:
            selected_tools = sorted(
                (
                    item
                    for item in execution_tools
                    if bridge_tool_block(item) == indexed_block
                ),
                key=lambda item: str(item.get("name") or ""),
            )
            block_index = EXTERNAL_TOOL_BLOCK_NAME_INDEXES[indexed_block]
            for ordinal, item in enumerate(selected_tools, start=1):
                meta = item.get("_meta") if isinstance(item.get("_meta"), Mapping) else {}
                is_write = bool(item.get("write")) or str(meta.get("permission") or "").strip().lower() == "write"
                tool_leaves[indexed_block].append(
                    {
                        "index": f"{block_index}.{ordinal}",
                        "name": str(item.get("name") or ""),
                        "mode": "write" if is_write else "read",
                    }
                )

        def block_node(block: str, *, expanded: bool) -> dict[str, Any]:
            descendants = EXTERNAL_TOOL_BLOCK_BRANCHES.get(block, ())
            node: dict[str, Any] = {
                "index": EXTERNAL_TOOL_BLOCK_NAME_INDEXES[block],
                "name": block,
                "whenToUse": EXTERNAL_TOOL_BLOCK_DESCRIPTIONS[block],
                "whenNotToUse": EXTERNAL_TOOL_BLOCK_DO_NOT_USE[block],
                "loaded": (
                    all(child in loaded_blocks for child in descendants)
                    if descendants
                    else block in loaded_blocks
                ),
                **counts[block],
            }
            if descendants:
                node["expandable"] = True
                node["children"] = [
                    block_node(child, expanded=False) for child in descendants
                ]
            elif expanded:
                node["children"] = tool_leaves[block]
            else:
                node["toolNames"] = [leaf["name"] for leaf in tool_leaves[block]]
            if block == "avatar":
                node["workflowSkillNames"] = list(AVATAR_COMPOSITION_WORKFLOW_SKILL_NAMES)
                if expanded:
                    tool_lookup = {
                        leaf["name"]: {"block": owner, **leaf}
                        for owner, leaves in tool_leaves.items()
                        for leaf in leaves
                    }
                    workflow_skills: list[dict[str, Any]] = []
                    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
                        steps: list[dict[str, Any]] = []
                        referenced_names: list[str] = []
                        for ordinal, step in enumerate(skill.get("steps") or (), start=1):
                            names = [str(name) for name in step.get("tools") or ()]
                            referenced_names.extend(names)
                            steps.append(
                                {
                                    "order": ordinal,
                                    "goal": str(step.get("goal") or ""),
                                    "toolRefs": [tool_lookup[name] for name in names if name in tool_lookup],
                                }
                            )
                        missing_names = sorted(set(referenced_names) - set(tool_lookup))
                        workflow_skills.append(
                            {
                                "schema": "vrcforge.skill.v1",
                                "name": skill["name"],
                                "title": skill["title"],
                                "description": skill["description"],
                                "whenToUse": skill["whenToUse"],
                                "whenNotToUse": skill["whenNotToUse"],
                                "backupRestore": skill["backupRestore"],
                                "toolBlocks": list(skill["toolBlocks"]),
                                "problemBreakdown": list(skill["problemBreakdown"]),
                                "steps": steps,
                                "acceptance": list(skill["acceptance"]),
                                "pitfalls": list(skill["pitfalls"]),
                                "missingToolNames": missing_names,
                            }
                        )
                    node["workflowSkills"] = workflow_skills
            return node

        ordered_blocks = list(EXTERNAL_TOOL_BLOCK_INDEXES.values())
        visible_blocks = [selected_block] if selected_block else ordered_blocks
        # Keep the index cheap but self-describing: leaf names and read/write
        # modes are small discovery metadata, while full descriptions and
        # schemas remain unloaded until the Agent selects a block.
        nodes = [
            block_node(block, expanded=bool(selected_block))
            for block in visible_blocks
        ]
        return {
            "ok": True,
            "schema": "vrcforge.external_tool_blocks.v2",
            "loadedBlocks": sorted(loaded_blocks),
            "selectedBlock": selected_block,
            "selectionHint": (
                "Load only the block whose whenToUse matches the task; full tool descriptions and schemas appear after loading."
            ),
            "tree": {"index": "0", "name": "unity", "children": nodes},
        }

    def list_tools(params: Mapping[str, Any]) -> list[dict[str, Any]]:
        requested_exposure = str(params.get("exposureLayer") or exposure_layer)
        if requested_exposure not in {"planning", "execution"}:
            raise ValueError("exposureLayer must be planning or execution")
        tools: list[dict[str, Any]] = [
            {
                "name": "vrcforge_bridge_preflight",
                "description": "Check whether the local VRCForge App gateway is authenticated and ready.",
                "inputSchema": {"type": "object", "additionalProperties": False},
            }
        ]
        tools.extend(block_controls())
        if not bridge.preflight().get("runtimeOnline"):
            return tools
        try:
            manifest = bridge.manifest(requested_exposure, sorted(loaded_blocks))
        except Exception:
            return tools
        manifest_tools = manifest.get("tools") if isinstance(manifest, dict) else []
        for item in manifest_tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            block = bridge_tool_block(item)
            if (
                not name
                or name in HIDDEN_EXTERNAL_TOOLS
                or not block
                or block not in loaded_blocks
            ):
                continue
            tools.append(
                {
                    **item,
                    "name": name,
                    "description": str(item.get("description") or name),
                    "inputSchema": item.get("inputSchema")
                    if isinstance(item.get("inputSchema"), dict)
                    else {"type": "object", "additionalProperties": True},
                }
            )
        return tools

    def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal tool_list_revision
        if tool_name == "vrcforge_bridge_preflight":
            return bridge.preflight()
        if tool_name == "vrcforge_list_tool_blocks":
            return block_inventory(arguments.get("block"))
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
            # Preserve historical `skills` package-management loading. Installed
            # workflow instructions stay hidden until their leaf is requested.
            targets = (
                ("skills/vsk",)
                if block == "skills"
                else EXTERNAL_TOOL_BLOCK_BRANCHES.get(block, (block,))
            )
            if tool_name == "vrcforge_load_tool_block":
                changed = any(target not in loaded_blocks for target in targets)
                loaded_blocks.update(targets)
            else:
                if block == "core":
                    return external_rejection(
                        status="core_block_required",
                        error="The core external MCP block cannot be unloaded.",
                        error_code="external_core_block_required",
                        failure_layer="external_tool_discovery",
                        failure_phase="block_unload",
                        operation_kind="discovery",
                        loadedBlocks=sorted(loaded_blocks),
                    )
                changed = any(target in loaded_blocks for target in targets)
                loaded_blocks.difference_update(targets)
            if changed:
                tool_list_revision += 1
            return {
                "ok": True,
                "status": "loaded" if tool_name == "vrcforge_load_tool_block" else "unloaded",
                "block": block,
                "blockIndex": EXTERNAL_TOOL_BLOCK_NAME_INDEXES[block],
                "changed": changed,
                "toolListChanged": changed,
                "toolListRevision": tool_list_revision,
                "loadedBlocks": sorted(loaded_blocks),
            }
        return bridge.call_tool(tool_name, arguments, agent_name="external-stdio-agent")

    router_standard = McpStandardRouter(
        lambda: list_tools({"exposureLayer": exposure_layer}),
        call_tool,
        server_name=DEFAULT_SERVER_NAME,
        server_version="1.7.10",
        tool_list_revision=lambda: tool_list_revision,
    )
    if protocol_profile == "mcp-1x":
        run_standard_stdio_loop(router_standard)
        return

    router_2026 = Mcp2026Router(
        list_tools,
        call_tool,
        server_name=DEFAULT_SERVER_NAME,
        server_version="1.7.10",
        tool_list_revision=lambda: tool_list_revision,
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
