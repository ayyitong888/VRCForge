from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


ToolHandler = Callable[[dict[str, Any]], Any]


class AgentGatewayError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AgentGatewayConfig:
    enabled: bool = False
    require_token: bool = True
    token: str = ""
    approval_token: str = ""
    allow_write_requests: bool = True
    allow_roslyn_advanced: bool = False
    approval_timeout_seconds: int = 600
    execution_mode: str = "approval"
    roslyn_risk_acknowledged: bool = False


@dataclass
class AgentTool:
    name: str
    description: str
    category: str
    handler: ToolHandler
    write: bool = False
    advanced: bool = False


@dataclass
class AgentWriteHandler:
    name: str
    description: str
    risk_level: str
    handler: ToolHandler
    advanced: bool = False


class AgentGateway:
    def __init__(
        self,
        config_path: Path,
        audit_dir: Path,
        public_base_url: str = "http://127.0.0.1:8757",
    ) -> None:
        self.config_path = config_path
        self.audit_dir = audit_dir
        self.public_base_url = public_base_url.rstrip("/")
        self._tools: dict[str, AgentTool] = {}
        self._write_handlers: dict[str, AgentWriteHandler] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def configure_paths(self, config_path: Path, audit_dir: Path) -> None:
        with self._lock:
            self.config_path = config_path
            self.audit_dir = audit_dir
            self._approvals.clear()

    def register_tool(
        self,
        name: str,
        description: str,
        category: str,
        handler: ToolHandler,
        write: bool = False,
        advanced: bool = False,
    ) -> None:
        self._tools[name] = AgentTool(
            name=name,
            description=description,
            category=category,
            handler=handler,
            write=write,
            advanced=advanced,
        )

    def register_write_handler(
        self,
        name: str,
        description: str,
        risk_level: str,
        handler: ToolHandler,
        advanced: bool = False,
    ) -> None:
        self._write_handlers[name] = AgentWriteHandler(
            name=name,
            description=description,
            risk_level=risk_level,
            handler=handler,
            advanced=advanced,
        )

    def ensure_config(self) -> AgentGatewayConfig:
        with self._lock:
            raw = self._read_config_payload()
            changed = False

            if not raw.get("token"):
                raw["token"] = secrets.token_urlsafe(32)
                changed = True
            if not raw.get("approval_token"):
                raw["approval_token"] = secrets.token_urlsafe(32)
                changed = True

            defaults = {
                "enabled": False,
                "require_token": True,
                "allow_write_requests": True,
                "allow_roslyn_advanced": False,
                "approval_timeout_seconds": 600,
                "execution_mode": "approval",
                "roslyn_risk_acknowledged": False,
            }
            for key, value in defaults.items():
                if key not in raw:
                    raw[key] = value
                    changed = True

            config = AgentGatewayConfig(
                enabled=bool(raw.get("enabled")),
                require_token=bool(raw.get("require_token", True)),
                token=str(raw.get("token") or ""),
                approval_token=str(raw.get("approval_token") or ""),
                allow_write_requests=bool(raw.get("allow_write_requests", True)),
                allow_roslyn_advanced=bool(raw.get("allow_roslyn_advanced", False)),
                approval_timeout_seconds=int(raw.get("approval_timeout_seconds", 600)),
                execution_mode=normalize_execution_mode(raw.get("execution_mode")),
                roslyn_risk_acknowledged=bool(raw.get("roslyn_risk_acknowledged", False)),
            )
            if changed:
                self.save_config(config)
            return config

    def save_config(self, config: AgentGatewayConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": bool(config.enabled),
            "require_token": bool(config.require_token),
            "token": config.token or secrets.token_urlsafe(32),
            "approval_token": config.approval_token or secrets.token_urlsafe(32),
            "allow_write_requests": bool(config.allow_write_requests),
            "allow_roslyn_advanced": bool(config.allow_roslyn_advanced),
            "approval_timeout_seconds": int(config.approval_timeout_seconds),
            "execution_mode": normalize_execution_mode(config.execution_mode),
            "roslyn_risk_acknowledged": bool(config.roslyn_risk_acknowledged),
        }
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def authenticate(
        self,
        headers: dict[str, str],
        query_params: dict[str, str],
        client_host: str | None,
        allow_disabled: bool = False,
    ) -> AgentGatewayConfig:
        config = self.ensure_config()
        if client_host and client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise AgentGatewayError("Agent Gateway only accepts loopback clients.", status_code=403)

        if config.require_token:
            supplied = self._extract_token(headers, query_params)
            if not supplied or not hmac.compare_digest(supplied, config.token):
                raise AgentGatewayError("Agent Gateway token is missing or invalid.", status_code=401)

        if not config.enabled and not allow_disabled:
            raise AgentGatewayError("Agent Gateway is disabled in config/agent_gateway.json.", status_code=403)

        return config

    def authenticate_approval(
        self,
        headers: dict[str, str],
        query_params: dict[str, str],
        client_host: str | None,
    ) -> AgentGatewayConfig:
        config = self.authenticate(headers, query_params, client_host, allow_disabled=False)
        supplied = (
            headers.get("x-vrcforge-approval-token")
            or headers.get("X-VRCForge-Approval-Token")
            or query_params.get("approval_token")
            or ""
        )
        if not supplied or not hmac.compare_digest(supplied, config.approval_token):
            raise AgentGatewayError("Approval token is missing or invalid.", status_code=401)
        return config

    def build_manifest(self) -> dict[str, Any]:
        config = self.ensure_config()
        tools = [
            self._serialize_tool(tool, config)
            for tool in self._tools.values()
            if self._tool_visible(tool, config)
        ]
        return {
            "ok": True,
            "name": "VRCForge Agent Gateway",
            "version": "0.1",
            "enabled": config.enabled,
            "mcpUrl": f"{self.public_base_url}/mcp",
            "restUrl": f"{self.public_base_url}/api/agent",
            "requiresToken": config.require_token,
            "allowWriteRequests": config.allow_write_requests,
            "allowRoslynAdvanced": self.roslyn_available(config),
            "executionMode": normalize_execution_mode(config.execution_mode),
            "roslynFullAuto": normalize_execution_mode(config.execution_mode) == "roslyn_full_auto",
            "roslynRiskAcknowledged": config.roslyn_risk_acknowledged,
            "approvalTimeoutSeconds": config.approval_timeout_seconds,
            "tools": tools,
            "toolCount": len(tools),
            "writeTargets": self.visible_write_targets(config),
        }

    def build_health(self) -> dict[str, Any]:
        config = self.ensure_config()
        pending = [item for item in self.list_approvals(include_expired=False) if item.get("status") == "pending"]
        return {
            "ok": config.enabled,
            "enabled": config.enabled,
            "requiresToken": config.require_token,
            "configPath": str(self.config_path),
            "auditLogPath": str(self.audit_log_path),
            "mcpUrl": f"{self.public_base_url}/mcp",
            "restUrl": f"{self.public_base_url}/api/agent",
            "pendingApprovalCount": len(pending),
            "allowWriteRequests": config.allow_write_requests,
            "allowRoslynAdvanced": self.roslyn_available(config),
            "permission": self.permission_state(config),
        }

    def permission_state(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        return {
            "executionMode": mode,
            "perActionApproval": mode == "approval",
            "roslynFullAuto": mode == "roslyn_full_auto",
            "roslynRiskAcknowledged": bool(config.roslyn_risk_acknowledged),
            "allowWriteRequests": bool(config.allow_write_requests),
            "allowRoslynAdvanced": self.roslyn_available(config),
            "roslynEnvEnabled": os.environ.get("VRCFORGE_ENABLE_ROSLYN", "").strip().lower()
            in {"1", "true", "yes", "on"},
        }

    def update_permission_state(
        self,
        execution_mode: str,
        acknowledge_roslyn_risk: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            config = self.ensure_config()
            mode = normalize_execution_mode(execution_mode)
            entering_roslyn = mode == "roslyn_full_auto"
            if entering_roslyn and not config.roslyn_risk_acknowledged and not acknowledge_roslyn_risk:
                raise AgentGatewayError(
                    "Roslyn full-auto requires one-time risk acknowledgement.",
                    status_code=409,
                )

            previous = self.permission_state(config)
            config.execution_mode = mode
            if acknowledge_roslyn_risk and entering_roslyn:
                config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = entering_roslyn
            self.save_config(config)
            updated = self.permission_state(config)
            self.append_audit(
                {
                    "event": "permission_mode_updated",
                    "previous": previous,
                    "updated": updated,
                }
            )
            return {"ok": True, "permission": updated}

    def call_tool(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        agent_name: str = "external-agent",
    ) -> dict[str, Any]:
        config = self.ensure_config()
        if not config.enabled:
            raise AgentGatewayError("Agent Gateway is disabled in config/agent_gateway.json.", status_code=403)

        tool = self._tools.get(name)
        if not tool or not self._tool_visible(tool, config):
            raise AgentGatewayError(f"Unknown or unavailable agent tool: {name}", status_code=404)

        params = params or {}
        try:
            result = tool.handler(params)
            self.append_audit(
                {
                    "event": "tool_call",
                    "tool": name,
                    "agent": agent_name,
                    "paramsSummary": summarize_params(params),
                    "status": "ok",
                }
            )
            return {
                "ok": True,
                "tool": name,
                "agent": agent_name,
                "result": result,
            }
        except Exception as exc:  # noqa: BLE001 - tool errors must be returned to external agents.
            self.append_audit(
                {
                    "event": "tool_call",
                    "tool": name,
                    "agent": agent_name,
                    "paramsSummary": summarize_params(params),
                    "status": "error",
                    "error": str(exc),
                }
            )
            return {
                "ok": False,
                "tool": name,
                "agent": agent_name,
                "error": str(exc),
            }

    def create_apply_request(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self.ensure_config()
        if not config.allow_write_requests:
            raise AgentGatewayError("Agent Gateway write requests are disabled.", status_code=403)

        target_tool = str(params.get("target_tool") or params.get("targetTool") or "").strip()
        if not target_tool:
            raise AgentGatewayError("target_tool is required.")

        write_handler = self._write_handlers.get(target_tool)
        if not write_handler or not self._write_handler_visible(write_handler, config):
            raise AgentGatewayError(f"Unknown or unavailable write target: {target_tool}", status_code=404)

        arguments = ensure_dict(params.get("arguments") or params.get("params") or {})
        approval = self._new_approval(
            agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent"),
            target_tool=target_tool,
            arguments=arguments,
            reason=str(params.get("reason") or ""),
            preview=params.get("preview"),
            risk_level=write_handler.risk_level,
        )
        return {
            "ok": True,
            "status": "pending",
            "approval": approval,
            "message": "Apply request is waiting for user approval.",
        }

    def apply_approved(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approval_id") or params.get("approvalId") or "").strip()
        if not approval_id:
            raise AgentGatewayError("approval_id is required.")

        with self._lock:
            approval = self._approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)

            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") != "approved":
                return {
                    "ok": False,
                    "status": approval.get("status"),
                    "approval": approval,
                    "message": "Approval is not approved yet.",
                }

            target_tool = str(approval.get("targetTool") or "")
            write_handler = self._write_handlers.get(target_tool)
            if not write_handler:
                raise AgentGatewayError(f"Write target is no longer available: {target_tool}", status_code=404)

            approval["status"] = "applying"
            self._approvals[approval_id] = approval
            self.append_audit({"event": "approval_applying", "approval": approval})

        try:
            result = write_handler.handler(ensure_dict(approval.get("arguments") or {}))
            with self._lock:
                approval["status"] = "applied"
                approval["appliedAt"] = utc_now_iso()
                approval["resultSummary"] = summarize_params(result if isinstance(result, dict) else {"result": result})
                self._approvals[approval_id] = approval
                self.append_audit({"event": "approval_applied", "approval": approval})
            return {"ok": True, "status": "applied", "approval": approval, "result": result}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                approval["status"] = "failed"
                approval["failedAt"] = utc_now_iso()
                approval["error"] = str(exc)
                self._approvals[approval_id] = approval
                self.append_audit({"event": "approval_failed", "approval": approval})
            return {"ok": False, "status": "failed", "approval": approval, "error": str(exc)}

    def list_approvals(self, include_expired: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            approvals = [self._refresh_approval_expiry(dict(item)) for item in self._approvals.values()]
            if include_expired:
                return [
                    redact_sensitive(item)
                    for item in sorted(approvals, key=lambda item: str(item.get("createdAt") or ""), reverse=True)
                ]
            filtered = [
                item
                for item in sorted(approvals, key=lambda approval: str(approval.get("createdAt") or ""), reverse=True)
                if item.get("status") != "expired"
            ]
            return [redact_sensitive(item) for item in filtered]

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self._set_approval_status(approval_id, "approved")

    def reject(self, approval_id: str) -> dict[str, Any]:
        return self._set_approval_status(approval_id, "rejected")

    def recent_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.audit_log_path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @property
    def audit_log_path(self) -> Path:
        return self.audit_dir / "approvals.jsonl"

    def visible_write_targets(self, config: AgentGatewayConfig | None = None) -> list[dict[str, Any]]:
        config = config or self.ensure_config()
        return [
            {
                "name": handler.name,
                "description": handler.description,
                "riskLevel": handler.risk_level,
                "advanced": handler.advanced,
            }
            for handler in self._write_handlers.values()
            if self._write_handler_visible(handler, config)
        ]

    def roslyn_available(self, config: AgentGatewayConfig | None = None) -> bool:
        config = config or self.ensure_config()
        if not config.allow_roslyn_advanced:
            return False
        return os.environ.get("VRCFORGE_ENABLE_ROSLYN", "").strip().lower() in {"1", "true", "yes", "on"}

    def append_audit(self, entry: dict[str, Any]) -> None:
        safe_entry = redact_sensitive({
            "timestamp": utc_now_iso(),
            **entry,
        })
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_config_payload(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _extract_token(self, headers: dict[str, str], query_params: dict[str, str]) -> str:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return str(query_params.get("token") or "")

    def _serialize_tool(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "write": tool.write,
            "advanced": tool.advanced,
            "available": self._tool_visible(tool, config),
        }

    def _tool_visible(self, tool: AgentTool, config: AgentGatewayConfig) -> bool:
        if tool.advanced and not self.roslyn_available(config):
            return False
        if tool.write and not config.allow_write_requests:
            return False
        return True

    def _write_handler_visible(self, handler: AgentWriteHandler, config: AgentGatewayConfig) -> bool:
        if handler.advanced and not self.roslyn_available(config):
            return False
        return True

    def _new_approval(
        self,
        agent_name: str,
        target_tool: str,
        arguments: dict[str, Any],
        reason: str,
        preview: Any,
        risk_level: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        config = self.ensure_config()
        approval = {
            "id": f"appr_{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}",
            "createdAt": now.isoformat(),
            "expiresAt": (now + timedelta(seconds=config.approval_timeout_seconds)).isoformat(),
            "agentName": agent_name,
            "targetTool": target_tool,
            "reason": reason,
            "riskLevel": risk_level,
            "status": "pending",
            "arguments": arguments,
            "paramsSummary": summarize_params(arguments),
            "preview": preview if preview is not None else summarize_params(arguments),
        }
        with self._lock:
            self._approvals[approval["id"]] = approval
            self.append_audit({"event": "approval_requested", "approval": approval})
        return redact_sensitive(dict(approval))

    def _set_approval_status(self, approval_id: str, status: str) -> dict[str, Any]:
        with self._lock:
            approval = self._approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") not in {"pending", "approved"} and status == "approved":
                return {"ok": False, "approval": approval, "message": f"Approval is {approval.get('status')}."}
            if approval.get("status") == "expired":
                return {"ok": False, "approval": approval, "message": "Approval has expired."}
            approval["status"] = status
            approval[f"{status}At"] = utc_now_iso()
            self._approvals[approval_id] = approval
            self.append_audit({"event": f"approval_{status}", "approval": approval})
            return {"ok": True, "approval": redact_sensitive(dict(approval))}

    def _refresh_approval_expiry(self, approval: dict[str, Any]) -> dict[str, Any]:
        if approval.get("status") != "pending":
            return approval
        expires_at = parse_iso_datetime(str(approval.get("expiresAt") or ""))
        if expires_at and expires_at < datetime.now(timezone.utc):
            approval["status"] = "expired"
            self._approvals[str(approval.get("id"))] = approval
        return approval

    def _load_approval_from_audit(self, approval_id: str) -> dict[str, Any] | None:
        for entry in reversed(self.recent_audit_logs(limit=500)):
            approval = entry.get("approval")
            if isinstance(approval, dict) and approval.get("id") == approval_id:
                return approval
        return None


def create_agent_mcp_app(gateway: AgentGateway):
    mcp = FastMCP(
        "VRCForge Agent Gateway",
        instructions=(
            "Use VRCForge tools for supervised VRChat avatar debugging. "
            "Read, plan, and preview tools run directly. Writes require an approval request."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    def register(name: str):
        async def tool(params: dict[str, Any] | None = None, agent_name: str = "mcp-agent") -> dict[str, Any]:
            return gateway.call_tool(name, params or {}, agent_name=agent_name)

        mcp.tool(name=name)(tool)

    for tool_name in [
        "vrcforge_health",
        "vrcforge_unity_status",
        "vrcforge_unity_tools",
        "vrcforge_list_avatars",
        "vrcforge_scan_blendshapes",
        "vrcforge_scan_materials",
        "vrcforge_capture_status",
        "vrcforge_capture_screenshot",
        "vrcforge_vision_audit",
        "vrcforge_read_recent_logs",
        "vrcforge_plan_face_tuning",
        "vrcforge_plan_shader_tuning",
        "vrcforge_preview_blendshape_apply",
        "vrcforge_preview_shader_apply",
        "vrcforge_request_apply",
        "vrcforge_apply_approved",
        "vrcforge_restore_last_backup",
        "vrcforge_request_roslyn_advanced",
    ]:
        register(tool_name)

    app = mcp.streamable_http_app()
    app.state.fastmcp_server = mcp
    return app


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_execution_mode(value: Any) -> str:
    mode = str(value or "approval").strip().lower().replace("-", "_")
    if mode in {"roslyn_full_auto", "full_auto", "roslyn_auto", "advanced"}:
        return "roslyn_full_auto"
    return "approval"


def summarize_params(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            str(key): summarize_value(key, item)
            for key, item in value.items()
            if str(key).lower() not in {"token", "authorization", "api_key", "apikey", "secret"}
        }
    return {"value": summarize_value("value", value)}


def summarize_value(key: Any, value: Any) -> Any:
    key_text = str(key).lower()
    if key_text in {"token", "authorization", "api_key", "apikey", "secret"}:
        return "<redacted>"
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(str(item) for item in value.keys())[:20], "keyCount": len(value)}
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, str):
        if len(value) > 140:
            return value[:137] + "..."
        if "\\" in value or "/" in value:
            return Path(value).name or "<path>"
        return value
    return value


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"token", "authorization", "api_key", "apikey", "secret"}:
                result[str(key)] = "<redacted>"
            elif lowered in {"arguments"}:
                result[str(key)] = summarize_params(item)
            else:
                result[str(key)] = redact_sensitive(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
