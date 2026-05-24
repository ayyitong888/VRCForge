from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
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


@dataclass
class UserConstraintsSnapshot:
    path: Path
    content: str = ""
    status: str = "ok"
    message: str = "No user constraints configured."
    error: str = ""


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
        self._runtime_sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def configure_paths(self, config_path: Path, audit_dir: Path) -> None:
        with self._lock:
            self.config_path = config_path
            self.audit_dir = audit_dir
            self._approvals.clear()
            self._runtime_sessions.clear()

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
        user_constraints = self.read_user_constraints()
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
            "userConstraints": self._serialize_user_constraints(user_constraints),
        }

    def build_health(self) -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        pending = [item for item in self.list_approvals(include_expired=False) if item.get("status") == "pending"]
        return {
            "ok": True,
            "runtimeAlive": True,
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
            "userConstraints": self._serialize_user_constraints(user_constraints, include_error=True),
            "shellExecutor": {
                "status": "ok",
                "shell": "powershell",
                "timeoutSeconds": 120,
            },
            "deterministicPlanner": {
                "status": "ok",
                "available": True,
            },
            "runtimeSessions": len(self._runtime_sessions),
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
        user_constraints = self.read_user_constraints()
        tool_params = self._inject_user_constraints(params, tool, user_constraints)
        try:
            result = tool.handler(tool_params)
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

    def runtime_message(
        self,
        params: dict[str, Any] | None = None,
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        params = params or {}
        message = str(params.get("message") or "").strip()
        if not message:
            raise AgentGatewayError("message is required.")

        now = utc_now_iso()
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        if not session_id:
            session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        turn_id = f"turn_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        observe = self.runtime_observe(session_id=session_id)
        plan = self._plan_agent_turn(message, params, observe)

        shell_payload: dict[str, Any] | None = None
        command = str(params.get("shell_command") or params.get("shellCommand") or plan.get("shellCommand") or "").strip()
        if command:
            shell_payload = self.execute_shell(
                {
                    "command": command,
                    "cwd": params.get("cwd"),
                    "workspace_root": params.get("workspace_root") or params.get("workspaceRoot"),
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "reason": plan.get("summary") or "Agent shell step",
                },
                agent_name=agent_name,
            )

        turn = {
            "id": turn_id,
            "createdAt": now,
            "message": message,
            "observe": summarize_params(observe),
            "plan": plan,
        }
        if shell_payload is not None:
            turn["shell"] = shell_payload

        with self._lock:
            session = self._runtime_sessions.setdefault(
                session_id,
                {
                    "id": session_id,
                    "createdAt": now,
                    "updatedAt": now,
                    "turns": [],
                },
            )
            session["updatedAt"] = utc_now_iso()
            session["turns"].append(turn)

        self.append_audit(
            {
                "event": "agent_runtime_turn",
                "agent": agent_name,
                "sessionId": session_id,
                "turnId": turn_id,
                "messageSummary": summarize_text(message),
                "plan": plan,
                "shellStatus": shell_payload.get("status") if shell_payload else "none",
            }
        )

        payload = {
            "ok": True,
            "session_id": session_id,
            "sessionId": session_id,
            "turn_id": turn_id,
            "turnId": turn_id,
            "observe": observe,
            "plan": plan,
        }
        if shell_payload is not None:
            payload["shell"] = shell_payload
            if shell_payload.get("approval_id"):
                payload["approval_id"] = shell_payload["approval_id"]
                payload["approvalId"] = shell_payload["approval_id"]
            if shell_payload.get("result"):
                payload["result"] = shell_payload["result"]
        return payload

    def runtime_observe(self, session_id: str | None = None) -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        pending = [item for item in self.list_approvals(include_expired=False) if item.get("status") == "pending"]
        session = self._runtime_sessions.get(session_id or "")
        return {
            "ok": True,
            "runtime": {
                "alive": True,
                "executionMode": normalize_execution_mode(config.execution_mode),
                "gatewayEnabled": config.enabled,
            },
            "workspaceRoot": str(self.default_workspace_root),
            "userConstraints": self._serialize_user_constraints(user_constraints, include_error=True),
            "approvalQueue": {
                "pendingCount": len(pending),
            },
            "shellExecutor": {
                "available": True,
                "shell": "powershell",
                "timeoutSeconds": 120,
            },
            "deterministicPlanner": {
                "available": True,
            },
            "tools": {
                "count": len(self.build_manifest().get("tools", [])),
            },
            "session": {
                "id": session_id or "",
                "turnCount": len(session.get("turns", [])) if isinstance(session, dict) else 0,
            },
        }

    def get_runtime_session(self, session_id: str) -> dict[str, Any]:
        session = self._runtime_sessions.get(session_id)
        if not session:
            raise AgentGatewayError(f"Runtime session was not found: {session_id}", status_code=404)
        return {"ok": True, "session": session}

    def classify_shell(self, params: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(params, str):
            params = {"command": params}
        command = str(params.get("command") or "").strip()
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        reasons: list[str] = []

        if not command:
            return self._shell_classification(command, cwd, workspace_root, "reject", ["Command is empty."])
        if len(command) > 4000:
            return self._shell_classification(command, cwd, workspace_root, "reject", ["Command is too long."])

        if not is_path_within(cwd, workspace_root):
            reasons.append("cwd is outside the workspace root.")

        lowered = command.lower()
        if "\n" in command or "\r" in command:
            reasons.append("Command contains multiple lines.")
        if re.search(r"&&|\|\||[;|]|(?:^|\s)(?:\d?>|\*>|>>)", command):
            reasons.append("Command contains chaining, pipeline, or redirection syntax.")
        if "$(" in command or "{" in command or "}" in command or '@"' in command or "@'" in command:
            reasons.append("Command contains advanced PowerShell syntax.")
        if re.search(r"(^|\s|['\"])(?:\\\\|[a-zA-Z]:\\)", command):
            outside_paths = [
                token
                for token in tokenize_command(command)
                if looks_like_absolute_path(strip_quotes(token)) and not is_path_within(Path(strip_quotes(token)), workspace_root)
            ]
            if outside_paths:
                reasons.append("Command references an absolute path outside the workspace root.")
        if ".." in [part for token in tokenize_command(command) for part in re.split(r"[\\/]+", strip_quotes(token))]:
            reasons.append("Command contains parent path traversal.")
        if re.search(r"\.(ps1|bat|cmd|exe)(?:\s|$)", lowered):
            reasons.append("Command executes a script or executable directly.")

        tokens = tokenize_command(command)
        if not tokens:
            return self._shell_classification(command, cwd, workspace_root, "reject", ["Command could not be parsed."])

        if reasons:
            return self._shell_classification(command, cwd, workspace_root, "high", reasons)

        command_name = strip_quotes(tokens[0]).lower()
        args = [strip_quotes(token) for token in tokens[1:]]
        low_reasons = self._low_risk_reasons(command_name, args, workspace_root)
        if low_reasons:
            return self._shell_classification(command, cwd, workspace_root, "low", low_reasons)

        return self._shell_classification(command, cwd, workspace_root, "high", ["Command is not in the low-risk allowlist."])

    def execute_shell(
        self,
        params: dict[str, Any],
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        classification = self.classify_shell(params)
        command = classification["command"]
        if classification["risk"] == "reject":
            self.append_audit({"event": "shell_rejected", "classification": classification, "agent": agent_name})
            return {"ok": False, "status": "rejected", "classification": classification, "error": "; ".join(classification["reasons"])}

        if classification["risk"] == "high":
            approval = self._create_shell_approval(params, classification, agent_name)
            return {
                "ok": True,
                "status": "pending_approval",
                "classification": classification,
                "approval": approval,
                "approval_id": approval["id"],
                "approvalId": approval["id"],
            }

        result = self._run_shell_command(command, Path(classification["cwd"]), timeout_seconds=int(params.get("timeout_seconds") or 120))
        self.append_audit(
            {
                "event": "shell_executed",
                "agent": agent_name,
                "classification": classification,
                "result": summarize_shell_result(result),
            }
        )
        return {"ok": result["ok"], "status": "executed", "classification": classification, "result": result}

    def execute_approved_shell(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approval_id") or params.get("approvalId") or "").strip()
        if not approval_id:
            raise AgentGatewayError("approval_id is required.")
        approval = self._approvals.get(approval_id) or self._load_approval_from_audit(approval_id)
        if not approval:
            raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
        if approval.get("targetTool") != "vrcforge_shell_execute":
            raise AgentGatewayError("Approval is not a shell execution approval.", status_code=400)
        return self.apply_approved({"approval_id": approval_id})

    def execute_shell_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        command = str(params.get("command") or "").strip()
        expected_hash = str(params.get("command_hash") or params.get("commandHash") or "")
        if expected_hash and expected_hash != command_hash(command):
            raise AgentGatewayError("Stored shell approval command hash does not match.")
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        result = self._run_shell_command(command, cwd, timeout_seconds=int(params.get("timeout_seconds") or 120))
        self.append_audit(
            {
                "event": "shell_approved_executed",
                "sessionId": params.get("session_id") or params.get("sessionId") or "",
                "turnId": params.get("turn_id") or params.get("turnId") or "",
                "commandHash": command_hash(command),
                "cwd": str(cwd),
                "workspaceRoot": str(workspace_root),
                "result": summarize_shell_result(result),
            }
        )
        return result

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
        user_constraints = self.read_user_constraints()
        arguments = self._inject_user_constraints_for_apply(arguments, user_constraints)
        preview = params.get("preview")
        if user_constraints.content and isinstance(preview, dict):
            preview = {
                **preview,
                "userConstraintsApplied": True,
                "userConstraintsPath": str(user_constraints.path),
            }
        approval = self._new_approval(
            agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent"),
            target_tool=target_tool,
            arguments=arguments,
            reason=str(params.get("reason") or ""),
            preview=preview,
            risk_level=write_handler.risk_level,
            user_constraints=user_constraints,
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
            user_constraints = self.read_user_constraints()
            arguments = self._inject_user_constraints_for_apply(
                ensure_dict(approval.get("arguments") or {}),
                user_constraints,
            )
            result = write_handler.handler(arguments)
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

    def read_user_constraints(self) -> UserConstraintsSnapshot:
        path = self.user_constraints_path
        try:
            if not path.exists():
                return UserConstraintsSnapshot(
                    path=path,
                    content="",
                    status="ok",
                    message="User AGENTS.md is not configured.",
                )
            content = path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            return UserConstraintsSnapshot(
                path=path,
                content="",
                status="warning",
                message="User AGENTS.md could not be read.",
                error=str(exc),
            )
        return UserConstraintsSnapshot(
            path=path,
            content=content,
            status="ok",
            message="User constraints are active." if content else "User AGENTS.md is empty.",
        )

    @property
    def audit_log_path(self) -> Path:
        return self.audit_dir / "approvals.jsonl"

    @property
    def user_constraints_path(self) -> Path:
        if self.config_path.parent.name.lower() == "config":
            return self.config_path.parent.parent / "AGENTS.md"
        user_data_dir = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data_dir:
            return Path(user_data_dir) / "AGENTS.md"
        return self.config_path.parent / "AGENTS.md"

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

    def _serialize_user_constraints(
        self,
        snapshot: UserConstraintsSnapshot,
        include_error: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": snapshot.status,
            "path": str(snapshot.path),
            "enabled": bool(snapshot.content),
            "message": snapshot.message,
            "characterCount": len(snapshot.content),
        }
        if include_error and snapshot.error:
            payload["error"] = snapshot.error
        return payload

    def _inject_user_constraints(
        self,
        params: dict[str, Any],
        tool: AgentTool,
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        if not snapshot.content:
            return dict(params)
        if tool.category not in {"read/debug", "plan/preview", "supervised-write", "advanced"}:
            return dict(params)
        return self._with_user_constraints(params, snapshot)

    def _inject_user_constraints_for_apply(
        self,
        params: dict[str, Any],
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        if not snapshot.content:
            return dict(params)
        return self._with_user_constraints(params, snapshot)

    def _with_user_constraints(
        self,
        params: dict[str, Any],
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        enriched = dict(params)
        enriched["_vrcforge_user_constraints"] = {
            "source": "user_agents_md",
            "path": str(snapshot.path),
            "content": snapshot.content,
        }
        enriched.setdefault("user_constraints", snapshot.content)
        enriched.setdefault("userConstraints", snapshot.content)
        instruction = enriched.get("instruction")
        constraints_block = (
            "\n\nUser constraints from %LOCALAPPDATA%\\VRCForge\\agentic-app\\AGENTS.md:\n"
            f"{snapshot.content}"
        )
        if isinstance(instruction, str) and instruction.strip():
            if snapshot.content not in instruction:
                enriched["instruction"] = instruction.rstrip() + constraints_block
        elif "instruction" in enriched or any(
            key in enriched for key in ("avatar", "avatar_path", "avatarPath", "inventory", "changes", "adjustments")
        ):
            enriched["instruction"] = "Follow the user constraints below." + constraints_block
        return enriched

    @property
    def default_workspace_root(self) -> Path:
        app_dir = os.environ.get("VRCFORGE_APP_DIR", "").strip()
        if app_dir:
            return Path(app_dir).resolve()
        return Path.cwd().resolve()

    def _resolve_workspace_root(self, params: dict[str, Any]) -> Path:
        raw = str(params.get("workspace_root") or params.get("workspaceRoot") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return self.default_workspace_root

    def _resolve_cwd(self, params: dict[str, Any], workspace_root: Path) -> Path:
        raw = str(params.get("cwd") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return workspace_root

    def _shell_classification(
        self,
        command: str,
        cwd: Path,
        workspace_root: Path,
        risk: str,
        reasons: list[str],
    ) -> dict[str, Any]:
        return {
            "ok": risk != "reject",
            "command": command,
            "commandHash": command_hash(command),
            "risk": risk,
            "reasons": reasons,
            "cwd": str(cwd),
            "workspaceRoot": str(workspace_root),
        }

    def _low_risk_reasons(self, command_name: str, args: list[str], workspace_root: Path) -> list[str]:
        read_only = {"get-childitem", "dir", "ls", "get-content", "type", "rg", "findstr"}
        if command_name in read_only:
            if self._args_stay_in_workspace(args, workspace_root):
                return ["Read-only workspace inspection command."]
            return []

        if command_name in {"python", "node", "npm", "uv"} and args in (["--version"], ["-v"]):
            return ["Read-only environment version probe."]

        if command_name == "where" and len(args) == 1 and re.fullmatch(r"[a-zA-Z0-9_.-]+", args[0] or ""):
            return ["Read-only executable lookup."]

        if command_name == "git":
            return self._git_low_risk_reasons(args, workspace_root)

        return []

    def _args_stay_in_workspace(self, args: list[str], workspace_root: Path) -> bool:
        for arg in args:
            if not arg or arg.startswith("-"):
                continue
            cleaned = strip_quotes(arg)
            if cleaned in {".", "*"}:
                continue
            if ".." in re.split(r"[\\/]+", cleaned):
                return False
            if looks_like_absolute_path(cleaned) and not is_path_within(Path(cleaned), workspace_root):
                return False
        return True

    def _git_low_risk_reasons(self, args: list[str], workspace_root: Path) -> list[str]:
        if not args:
            return []
        if "-c" in args or any(arg.startswith("--config") for arg in args):
            return []
        if args[0] == "--no-pager":
            args = args[1:]
        if not args:
            return []

        verb = args[0]
        rest = args[1:]
        if verb == "status" and all(arg in {"--short", "-s", "--porcelain", "--branch", "-b"} for arg in rest):
            return ["Read-only git status command."]
        if verb == "log" and self._git_log_args_are_low_risk(rest):
            return ["Read-only git log command."]
        if verb == "diff" and self._git_diff_args_are_low_risk(rest, workspace_root):
            return ["Read-only git diff command."]
        if verb == "show" and "--stat" in rest and "--ext-diff" not in rest:
            return ["Read-only git show stat command."]
        return []

    def _git_log_args_are_low_risk(self, args: list[str]) -> bool:
        allowed_flags = {"--oneline", "--decorate", "--no-decorate"}
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in allowed_flags:
                index += 1
                continue
            if arg == "-n" and index + 1 < len(args) and args[index + 1].isdigit():
                index += 2
                continue
            if re.fullmatch(r"-\d{1,3}", arg):
                index += 1
                continue
            return False
        return True

    def _git_diff_args_are_low_risk(self, args: list[str], workspace_root: Path) -> bool:
        if "--ext-diff" in args or "--cached" in args:
            return False
        if args == ["--stat"] or not args:
            return True
        if "--" in args:
            path_args = args[args.index("--") + 1 :]
            return self._args_stay_in_workspace(path_args, workspace_root)
        return all(arg in {"--stat", "--name-only", "--name-status"} for arg in args)

    def _create_shell_approval(
        self,
        params: dict[str, Any],
        classification: dict[str, Any],
        agent_name: str,
    ) -> dict[str, Any]:
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        turn_id = str(params.get("turn_id") or params.get("turnId") or "").strip()
        with self._lock:
            for approval in self._approvals.values():
                if (
                    approval.get("targetTool") == "vrcforge_shell_execute"
                    and approval.get("status") == "pending"
                    and approval.get("sessionId") == session_id
                    and approval.get("turnId") == turn_id
                    and turn_id
                ):
                    return redact_sensitive(dict(approval))

        arguments = {
            "command": classification["command"],
            "command_hash": classification["commandHash"],
            "cwd": classification["cwd"],
            "workspace_root": classification["workspaceRoot"],
            "session_id": session_id,
            "turn_id": turn_id,
            "timeout_seconds": int(params.get("timeout_seconds") or 120),
        }
        approval = self._new_approval(
            agent_name=agent_name,
            target_tool="vrcforge_shell_execute",
            arguments=arguments,
            reason=str(params.get("reason") or "High-risk shell command requires approval."),
            preview={
                "command": classification["command"],
                "cwd": classification["cwd"],
                "workspaceRoot": classification["workspaceRoot"],
                "riskReasons": classification["reasons"],
            },
            risk_level="high",
            user_constraints=self.read_user_constraints(),
        )
        with self._lock:
            stored = self._approvals.get(approval["id"])
            if stored is not None:
                stored["sessionId"] = session_id
                stored["turnId"] = turn_id
                stored["commandHash"] = classification["commandHash"]
        self.append_audit(
            {
                "event": "shell_approval_requested",
                "agent": agent_name,
                "approvalId": approval["id"],
                "classification": classification,
            }
        )
        return approval

    def _run_shell_command(self, command: str, cwd: Path, timeout_seconds: int = 120) -> dict[str, Any]:
        started = time.monotonic()
        started_at = utc_now_iso()
        env = os.environ.copy()
        env["GIT_PAGER"] = "cat"
        env["GIT_EXTERNAL_DIFF"] = ""
        process_args = [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        process = subprocess.Popen(  # noqa: S603 - shell execution is the supervised capability under test.
            process_args,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=max(1, min(timeout_seconds, 600)))
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(process)
            stdout, stderr = process.communicate()

        duration = time.monotonic() - started
        exit_code = process.returncode if process.returncode is not None else -1
        return {
            "ok": exit_code == 0 and not timed_out,
            "command": command,
            "cwd": str(cwd),
            "exitCode": exit_code,
            "timedOut": timed_out,
            "startedAt": started_at,
            "finishedAt": utc_now_iso(),
            "durationSeconds": round(duration, 3),
            "stdout": truncate_text(stdout),
            "stderr": truncate_text(stderr),
            "stdoutTruncated": len(stdout or "") > 12000,
            "stderrTruncated": len(stderr or "") > 12000,
        }

    def _plan_agent_turn(self, message: str, params: dict[str, Any], observe: dict[str, Any]) -> dict[str, Any]:
        command = extract_shell_command_candidate(message, params)
        summary = "Observed runtime state and prepared the next action."
        if command:
            summary = "Prepared a shell step for the requested task."
        elif "health" in message.lower() or "健康" in message:
            summary = "Observed runtime health. No shell step is required."
        return {
            "summary": summary,
            "planner": "deterministic-local",
            "userConstraintsApplied": bool(observe.get("userConstraints", {}).get("enabled")),
            "shellNeeded": bool(command),
            "shellCommand": command,
            "expectedResult": "Shell output will be returned inline." if command else "Runtime observation is available.",
            "nextStep": "classify_shell" if command else "await_user_instruction",
        }

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
        user_constraints: UserConstraintsSnapshot | None = None,
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
        if user_constraints and user_constraints.content:
            approval["userConstraintsApplied"] = True
            approval["userConstraintsPath"] = str(user_constraints.path)
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
        "vrcforge_agent_observe",
        "vrcforge_agent_message",
        "vrcforge_classify_shell",
        "vrcforge_execute_shell",
        "vrcforge_execute_approved_shell",
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


def tokenize_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return []


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def looks_like_absolute_path(value: str) -> bool:
    return bool(re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\)", value))


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


def summarize_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def truncate_text(text: str, limit: int = 12000) -> str:
    if len(text or "") <= limit:
        return text or ""
    return (text or "")[:limit] + "\n[truncated]"


def summarize_shell_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": result.get("ok"),
        "exitCode": result.get("exitCode"),
        "timedOut": result.get("timedOut"),
        "durationSeconds": result.get("durationSeconds"),
        "stdoutSummary": summarize_text(str(result.get("stdout") or "")),
        "stderrSummary": summarize_text(str(result.get("stderr") or "")),
    }


def kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            check=False,
        )
        return
    process.kill()


def extract_shell_command_candidate(message: str, params: dict[str, Any]) -> str:
    explicit = str(params.get("shell_command") or params.get("shellCommand") or "").strip()
    if explicit:
        return explicit
    stripped = message.strip()
    lowered = stripped.lower()
    if lowered.startswith("/shell "):
        return stripped[7:].strip()
    if lowered.startswith("shell:"):
        return stripped[6:].strip()
    fenced = re.search(r"```(?:powershell|pwsh|shell|bash|cmd)?\s*([\s\S]+?)```", stripped, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    inline = re.search(r"`([^`\n]+)`", stripped)
    if inline:
        return inline.group(1).strip()
    if "git status" in lowered or "工作树" in stripped or "仓库状态" in stripped:
        return "git --no-pager status --short"
    if "git log" in lowered or "最近提交" in stripped:
        return "git --no-pager log --oneline -n 10"
    if "列目录" in stripped or "文件列表" in stripped or lowered in {"ls", "dir"}:
        return "Get-ChildItem"
    return ""


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
            if str(key).lower()
            not in {
                "token",
                "authorization",
                "api_key",
                "apikey",
                "secret",
                "user_constraints",
                "userconstraints",
                "_vrcforge_user_constraints",
            }
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
            if lowered in {
                "token",
                "authorization",
                "api_key",
                "apikey",
                "secret",
                "user_constraints",
                "userconstraints",
                "_vrcforge_user_constraints",
            }:
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
