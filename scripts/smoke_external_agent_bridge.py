from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from external_agent_connector_installer import StdioBridgeSpec, run_stdio_mcp_handshake


DEFAULT_BASE_URL = "http://127.0.0.1:8757"
HIDDEN_EXTERNAL_TOOLS = {"vrcforge_apply_approved", "vrcforge_execute_approved_shell"}


def main() -> int:
    args = parse_args()
    smoke = ExternalAgentBridgeSmoke(args)
    report = smoke.run()
    output_path = smoke.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(output_path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test VRCForge external-agent connectors and supervised write rollback.")
    parser.add_argument("--base-url", default=os.environ.get("VRCFORGE_AGENT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--gateway-config", default=os.environ.get("VRCFORGE_AGENT_GATEWAY_CONFIG", ""))
    parser.add_argument("--app-token-file", default=os.environ.get("VRCFORGE_APP_TOKEN_FILE", ""))
    parser.add_argument("--project-root", default=os.environ.get("VRCFORGE_SMOKE_PROJECT_ROOT", ""))
    parser.add_argument("--live-write-rollback", action="store_true")
    parser.add_argument("--enable-gateway", action="store_true", help="Temporarily enable the Agent Gateway for the smoke.")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--agent-name", default="vrcforge-external-smoke")
    return parser.parse_args()


class ExternalAgentBridgeSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_url = str(args.base_url).rstrip("/")
        self.gateway_config_path = resolve_gateway_config_path(args.gateway_config)
        self.app_token_path = resolve_app_token_path(args.app_token_file)
        self.gateway_config = read_json_file(self.gateway_config_path)
        self.gateway_token = str(os.environ.get("VRCFORGE_AGENT_TOKEN") or self.gateway_config.get("token") or "")
        self.app_token = read_text_file(self.app_token_path).strip()
        self.steps: list[dict[str, Any]] = []
        self.previous_gateway: dict[str, Any] | None = None
        self.previous_permission: str = ""
        self.checkpoint_id: str = ""
        self.created_object_path: str = ""
        self.rollback_done = False
        self.connector_payload: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        started = utc_now()
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.external_agent_bridge_smoke.v1",
            "startedAt": started,
            "baseUrl": self.base_url,
            "gatewayConfigPath": str(self.gateway_config_path) if self.gateway_config_path else "",
            "appTokenFile": str(self.app_token_path) if self.app_token_path else "",
            "tokenConfigured": bool(self.gateway_token),
            "appTokenConfigured": bool(self.app_token),
            "clientPreflight": self.client_preflight(),
            "steps": self.steps,
            "summary": "",
        }
        try:
            self.step("runtime.health", self.check_runtime_health())
            self.step("connector.config", self.check_connector_config())
            if self.args.enable_gateway:
                self.step("gateway.enable", self.enable_gateway())
            self.step("stdio.bridge_preflight", self.check_stdio_bridge_preflight())
            self.step("stdio.mcp_tools_list", self.check_stdio_mcp_tools())
            self.step("gateway.manifest", self.check_manifest())
            self.step("mcp.tools_list", self.check_mcp_tools())
            if self.args.live_write_rollback:
                self.live_write_rollback()
            report["ok"] = all(bool(step.get("ok")) for step in self.steps)
        except Exception as exc:  # noqa: BLE001 - smoke should always produce an evidence report.
            self.step("smoke.error", {"ok": False, "error": str(exc)})
            report["ok"] = False
        finally:
            if self.args.live_write_rollback and self.checkpoint_id and not self.rollback_done:
                self.try_emergency_rollback()
                if self.rollback_done:
                    self.verify_no_residue("rollback.verify_no_residue_after_emergency")
            self.restore_previous_state()
            report["finishedAt"] = utc_now()
            report["steps"] = self.steps
            report["summary"] = self.build_summary(report["ok"])
        return report

    def client_preflight(self) -> dict[str, Any]:
        return {
            "codexCli": probe_codex_cli(),
            "claudeCli": probe_command(["claude", "--version"]),
            "codexApp": probe_windows_app("OpenAI.Codex"),
            "claudeCoworkApp": probe_windows_app("Claude"),
        }

    def check_runtime_health(self) -> dict[str, Any]:
        if not self.gateway_token:
            return {"ok": False, "error": "Gateway token was not found."}
        payload = self.request_json("GET", "/api/agent/health", token=self.gateway_token, allow_http_error=False)
        return {
            "ok": bool(payload.get("runtimeAlive")),
            "runtimeAlive": bool(payload.get("runtimeAlive")),
            "enabled": bool(payload.get("enabled")),
            "allowWriteRequests": bool(payload.get("allowWriteRequests")),
            "executionMode": ensure_dict(payload.get("permission")).get("executionMode"),
            "pendingApprovalCount": payload.get("pendingApprovalCount"),
        }

    def check_connector_config(self) -> dict[str, Any]:
        payload = self.request_json("GET", "/api/agent/external-agent/connectors", token=self.gateway_token)
        if payload.get("ok") is False and payload.get("status"):
            return {
                "ok": False,
                "status": payload.get("status"),
                "error": payload.get("error"),
                "hint": "The running VRCForge backend does not expose the external-agent connector bundle. Install or start a backend built from this commit.",
            }
        self.connector_payload = payload
        rendered = json.dumps(payload, ensure_ascii=False)
        return {
            "ok": bool(payload.get("ok")) and self.gateway_token not in rendered,
            "schema": payload.get("schema"),
            "hasCodexHttp": "codex" in ensure_dict(payload.get("clientConfigs")),
            "hasCodexStdio": "codexStdio" in ensure_dict(payload.get("clientConfigs")),
            "hasClaudeHttp": "claudeCode" in ensure_dict(payload.get("clientConfigs")),
            "hasClaudeStdio": "claudeCodeStdio" in ensure_dict(payload.get("clientConfigs")),
            "hasCowork": "claudeCowork" in ensure_dict(payload.get("clientConfigs")),
            "storesPlaintextToken": ensure_dict(payload.get("auth")).get("storesPlaintextToken"),
            "tokenLeaked": self.gateway_token in rendered,
        }

    def enable_gateway(self) -> dict[str, Any]:
        if not self.app_token:
            return {"ok": False, "error": "App session token is required to toggle gateway state."}
        status = self.request_app_json("GET", "/api/app/external-agent/connectors")
        self.previous_gateway = ensure_dict(status.get("gateway"))
        enabled = self.request_app_json(
            "POST",
            "/api/app/external-agent/gateway",
            {"enabled": True, "allowWriteRequests": True},
        )
        permission = self.request_app_json("GET", "/api/app/permission")
        self.previous_permission = str(ensure_dict(permission.get("permission")).get("executionMode") or "")
        set_approval = self.request_app_json("POST", "/api/app/permission", {"execution_mode": "approval"})
        return {
            "ok": bool(enabled.get("gateway", {}).get("enabled")) and bool(set_approval.get("permission", {}).get("perActionApproval")),
            "previousEnabled": self.previous_gateway.get("enabled"),
            "previousAllowWriteRequests": self.previous_gateway.get("allowWriteRequests"),
            "previousPermission": self.previous_permission,
            "currentPermission": ensure_dict(set_approval.get("permission")).get("executionMode"),
        }

    def check_stdio_bridge_preflight(self) -> dict[str, Any]:
        launcher = ensure_dict(ensure_dict(self.connector_payload.get("launcher")).get("stdioBridge"))
        command_text = str(launcher.get("command") or sys.executable).strip()
        bridge_args = [str(item) for item in ensure_list(launcher.get("args"))]
        cwd = str(launcher.get("cwd") or Path(__file__).resolve().parents[1])
        command = [
            command_text,
            *bridge_args,
            "--preflight",
            "--json",
        ]
        env = os.environ.copy()
        env["VRCFORGE_AGENT_BASE_URL"] = self.base_url
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(max(self.args.timeout, 15.0), 60.0),
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        payload = try_parse_json(completed.stdout.strip())
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "exitCode": completed.returncode,
                "stdout": completed.stdout.strip()[:500],
                "stderr": completed.stderr.strip()[:500],
                "error": "stdio bridge preflight did not return JSON.",
            }
        return {
            "ok": completed.returncode == 0 and bool(payload.get("ok")),
            "exitCode": completed.returncode,
            "command": command_text,
            "args": bridge_args,
            "cwd": cwd,
            "runtimeOnline": bool(payload.get("runtimeOnline")),
            "gatewayEnabled": bool(payload.get("gatewayEnabled")),
            "allowWriteRequests": bool(payload.get("allowWriteRequests")),
            "manifestToolCount": payload.get("manifestToolCount"),
            "advertisesRequestApply": bool(payload.get("advertisesRequestApply")),
            "advertisesDirectApply": bool(payload.get("advertisesDirectApply")),
            "error": payload.get("error") or completed.stderr.strip()[:500],
        }

    def check_stdio_mcp_tools(self) -> dict[str, Any]:
        launcher = ensure_dict(ensure_dict(self.connector_payload.get("launcher")).get("stdioBridge"))
        command_text = str(launcher.get("command") or sys.executable).strip()
        bridge_args = [str(item) for item in ensure_list(launcher.get("args"))]
        cwd = str(launcher.get("cwd") or Path(__file__).resolve().parents[1])
        env = os.environ.copy()
        env["VRCFORGE_AGENT_BASE_URL"] = self.base_url
        env.setdefault("PYTHONIOENCODING", "utf-8")
        previous_env = os.environ.copy()
        try:
            os.environ.update(env)
            result = run_stdio_mcp_handshake(
                StdioBridgeSpec(
                    command=command_text,
                    args=bridge_args,
                    cwd=cwd,
                    packaged=bool(launcher.get("packaged")),
                    source="smoke-connector-payload",
                ),
                timeout_seconds=min(max(self.args.timeout, 15.0), 60.0),
            )
        finally:
            os.environ.clear()
            os.environ.update(previous_env)
        return {
            **result,
            "ok": bool(result.get("ok")) and bool(result.get("hasRequestApply")),
            "requestApplyListed": bool(result.get("hasRequestApply")),
        }

    def check_manifest(self) -> dict[str, Any]:
        payload = self.request_json("GET", "/api/agent/manifest", token=self.gateway_token, allow_http_error=False)
        tools = ensure_list(payload.get("tools"))
        tool_names = {str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)}
        write_targets = {str(item.get("name") or "") for item in ensure_list(payload.get("writeTargets")) if isinstance(item, dict)}
        return {
            "ok": bool(payload.get("enabled"))
            and "vrcforge_request_apply" in tool_names
            and "vrcforge_create_gameobject" in write_targets
            and not bool(HIDDEN_EXTERNAL_TOOLS & tool_names),
            "enabled": bool(payload.get("enabled")),
            "toolCount": len(tool_names),
            "writeTargetCount": len(write_targets),
            "requestApplyAdvertised": "vrcforge_request_apply" in tool_names,
            "directApplyAdvertised": sorted(HIDDEN_EXTERNAL_TOOLS & tool_names),
            "createGameObjectTarget": "vrcforge_create_gameobject" in write_targets,
        }

    def check_mcp_tools(self) -> dict[str, Any]:
        try:
            initialize = self.mcp_rpc("initialize", {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": self.args.agent_name, "version": "0"},
            })
            listed = self.mcp_rpc("tools/list", {})
        except Exception as exc:  # noqa: BLE001 - smoke evidence should keep moving after disabled gateway/protocol failures.
            return {
                "ok": False,
                "initialized": False,
                "toolCount": 0,
                "requestApplyListed": False,
                "directApplyListed": [],
                "error": str(exc),
            }
        tools = ensure_list(ensure_dict(listed.get("result")).get("tools"))
        tool_names = {str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)}
        return {
            "ok": "vrcforge_request_apply" in tool_names and not bool(HIDDEN_EXTERNAL_TOOLS & tool_names),
            "initialized": "result" in initialize,
            "toolCount": len(tool_names),
            "requestApplyListed": "vrcforge_request_apply" in tool_names,
            "directApplyListed": sorted(HIDDEN_EXTERNAL_TOOLS & tool_names),
        }

    def live_write_rollback(self) -> None:
        project_root = self.resolve_project_root()
        object_name = f"VRCForgeExternalAgentSmoke_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.created_object_path = object_name
        compile_before = self.mcp_call_tool("vrcforge_get_compile_errors", {"maxErrors": 20})
        self.step("unity.compile_before", compile_result_summary(compile_before))

        request = self.mcp_call_tool(
            "vrcforge_request_apply",
            {
                "target_tool": "vrcforge_create_gameobject",
                "arguments": {
                    "name": object_name,
                    "parentPath": "",
                    "projectRoot": project_root,
                },
                "reason": "External agent bridge smoke: create a temporary scene GameObject, then prove rollback.",
                "preview": {
                    "action": "create temporary scene GameObject",
                    "objectPath": object_name,
                    "rollbackRequired": True,
                },
            },
        )
        approval = ensure_dict(request.get("result", request).get("approval"))
        self.step(
            "write.request",
            {
                "ok": bool(approval.get("id")) and approval.get("status") == "pending",
                "approvalId": approval.get("id"),
                "targetTool": approval.get("targetTool"),
                "status": approval.get("status"),
            },
        )
        approval_id = str(approval.get("id") or "")
        if not approval_id:
            raise RuntimeError("Write request did not produce a pending approval.")

        applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
        execution = ensure_dict(applied.get("execution"))
        checkpoint = ensure_dict(execution.get("checkpoint"))
        self.checkpoint_id = str(checkpoint.get("id") or "")
        self.step(
            "write.approve_apply_checkpoint",
            {
                "ok": bool(applied.get("ok")) and execution.get("status") == "applied" and bool(self.checkpoint_id),
                "approvalId": approval_id,
                "executionStatus": execution.get("status"),
                "checkpointId": self.checkpoint_id,
                "checkpointStrategy": checkpoint.get("strategy"),
            },
        )
        if not self.checkpoint_id:
            raise RuntimeError("Approved write did not return a checkpoint id.")

        exists_after = self.mcp_call_tool("vrcforge_get_gameobject", {"gameObjectPath": object_name})
        self.step("write.verify_object_exists", {"ok": bool(exists_after.get("result", exists_after).get("ok")), "objectPath": object_name})

        self.record_validation_after_write(project_root)

        rollback_request = self.mcp_call_tool(
            "vrcforge_request_apply",
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": {"checkpointId": self.checkpoint_id, "confirmRestore": True},
                "reason": "External agent bridge smoke rollback proof.",
                "preview": {"checkpointId": self.checkpoint_id, "objectPath": object_name},
            },
        )
        rollback_approval = ensure_dict(rollback_request.get("result", rollback_request).get("approval"))
        rollback_approval_id = str(rollback_approval.get("id") or "")
        self.step(
            "rollback.request",
            {
                "ok": bool(rollback_approval_id) and rollback_approval.get("status") == "pending",
                "approvalId": rollback_approval_id,
                "targetTool": rollback_approval.get("targetTool"),
                "status": rollback_approval.get("status"),
            },
        )
        if not rollback_approval_id:
            raise RuntimeError("Rollback request did not produce a pending approval.")

        rollback = self.request_app_json("POST", f"/api/app/agent/approvals/{rollback_approval_id}/approve", {})
        rollback_execution = ensure_dict(rollback.get("execution"))
        self.rollback_done = bool(rollback.get("ok")) and rollback_execution.get("status") == "applied"
        self.step(
            "rollback.approve_apply",
            {
                "ok": self.rollback_done,
                "approvalId": rollback_approval_id,
                "executionStatus": rollback_execution.get("status"),
                "unityReloadOk": ensure_dict(ensure_dict(rollback_execution.get("result")).get("unityReload")).get("ok"),
            },
        )

        self.verify_no_residue("rollback.verify_no_residue")
        compile_after = self.mcp_call_tool("vrcforge_get_compile_errors", {"maxErrors": 20})
        self.step("unity.compile_after_rollback", compile_result_summary(compile_after))

    def record_validation_after_write(self, project_root: str) -> None:
        try:
            validation = self.mcp_call_tool("vrcforge_run_validation_report", {"projectPath": project_root, "maxErrors": 20})
            validation_payload = ensure_dict(validation.get("result", validation))
            self.step(
                "validation.after_write",
                {
                    "ok": validation_payload.get("schema") == "vrcforge.validation.v1",
                    "reportOk": bool(validation_payload.get("ok", True)),
                    "schema": validation_payload.get("schema"),
                    "errorCount": ensure_dict(validation_payload.get("summary")).get("errorCount"),
                    "warningCount": ensure_dict(validation_payload.get("summary")).get("warningCount"),
                    "findingCount": len(ensure_list(validation_payload.get("findings"))),
                },
            )
        except Exception as exc:  # noqa: BLE001 - rollback must still be attempted after validation trouble.
            self.step("validation.after_write", {"ok": False, "error": str(exc)})

    def verify_no_residue(self, step_name: str) -> None:
        if not self.created_object_path:
            self.step(step_name, {"ok": False, "error": "No created object path was recorded."})
            return
        try:
            exists_after_rollback = self.mcp_call_tool("vrcforge_get_gameobject", {"gameObjectPath": self.created_object_path})
            exists_payload = ensure_dict(exists_after_rollback.get("result", exists_after_rollback))
            self.step(
                step_name,
                {
                    "ok": not bool(exists_payload.get("ok")),
                    "objectPath": self.created_object_path,
                    "readOkAfterRollback": bool(exists_payload.get("ok")),
                    "readError": exists_payload.get("error"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.step(step_name, {"ok": False, "objectPath": self.created_object_path, "error": str(exc)})

    def try_emergency_rollback(self) -> None:
        try:
            request = self.request_app_json(
                "POST",
                f"/api/app/checkpoints/{self.checkpoint_id}/restore",
                {},
            )
            approval_id = str(ensure_dict(request.get("approval")).get("id") or request.get("approvalId") or "")
            if approval_id:
                applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
                self.rollback_done = bool(applied.get("ok"))
                self.step("rollback.emergency", {"ok": self.rollback_done, "checkpointId": self.checkpoint_id})
        except Exception as exc:  # noqa: BLE001
            self.step("rollback.emergency", {"ok": False, "checkpointId": self.checkpoint_id, "error": str(exc)})

    def resolve_project_root(self) -> str:
        if self.args.project_root:
            return str(Path(self.args.project_root).expanduser().resolve())
        if self.app_token:
            try:
                bootstrap = self.request_app_json("GET", "/api/app/bootstrap")
                selected = str(ensure_dict(ensure_dict(bootstrap.get("health")).get("state")).get("selected_project_path") or "")
                if selected:
                    return selected
            except Exception:
                pass
        settings = read_json_file(resolve_settings_path())
        dashboard = ensure_dict(settings.get("dashboard"))
        roots = ensure_list(dashboard.get("project_roots"))
        if roots:
            return str(roots[0])
        raise RuntimeError("Unity project root was not provided or selected in VRCForge.")

    def restore_previous_state(self) -> None:
        if self.app_token and self.previous_permission:
            try:
                self.request_app_json("POST", "/api/app/permission", {"execution_mode": self.previous_permission})
                self.step("cleanup.permission_restore", {"ok": True, "permission": self.previous_permission})
            except Exception as exc:  # noqa: BLE001
                self.step("cleanup.permission_restore", {"ok": False, "error": str(exc)})
        if self.app_token and self.previous_gateway is not None:
            try:
                payload = {
                    "enabled": bool(self.previous_gateway.get("enabled")),
                    "allowWriteRequests": bool(self.previous_gateway.get("allowWriteRequests")),
                }
                self.request_app_json("POST", "/api/app/external-agent/gateway", payload)
                self.step("cleanup.gateway_restore", {"ok": True, **payload})
            except Exception as exc:  # noqa: BLE001
                self.step("cleanup.gateway_restore", {"ok": False, "error": str(exc)})

    def mcp_call_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = self.mcp_rpc(
            "tools/call",
            {"name": name, "arguments": {"params": params, "agent_name": self.args.agent_name}},
        )
        if "error" in payload:
            return {"ok": False, "error": payload["error"]}
        result = ensure_dict(payload.get("result"))
        if "structuredContent" in result:
            return ensure_dict(result.get("structuredContent"))
        content = ensure_list(result.get("content"))
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
            parsed = try_parse_json(text)
            if isinstance(parsed, dict):
                return parsed
        return result

    def mcp_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 100000000,
            "method": method,
            "params": params,
        }
        request = urllib.request.Request(
            f"{self.base_url}/mcp",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.gateway_token}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.args.timeout) as response:  # noqa: S310 - loopback-only smoke.
            return json.loads(response.read().decode("utf-8", errors="replace") or "{}")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict[str, Any] | None = None,
        allow_http_error: bool = True,
    ) -> dict[str, Any]:
        return request_json(self.base_url, method, path, token, payload, allow_http_error, self.args.timeout)

    def request_app_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.app_token:
            raise RuntimeError("App session token was not found.")
        return request_json(self.base_url, method, path, self.app_token, payload, False, self.args.timeout)

    def step(self, name: str, payload: dict[str, Any]) -> None:
        self.steps.append({"name": name, **redact_evidence(payload)})

    def write_report(self, report: dict[str, Any]) -> Path:
        root = Path.cwd() / "artifacts" / "external-agent-smoke"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"external-agent-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(redact_evidence(report), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def build_summary(self, ok: bool) -> dict[str, Any]:
        return {
            "status": "passed" if ok else "failed",
            "liveWriteRollback": bool(self.args.live_write_rollback),
            "checkpointId": self.checkpoint_id,
            "createdObjectPath": self.created_object_path,
            "rollbackDone": self.rollback_done,
            "failedSteps": [step["name"] for step in self.steps if not step.get("ok")],
        }


def request_json(
    base_url: str,
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None,
    allow_http_error: bool,
    timeout: float,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback-only smoke.
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if not allow_http_error:
            raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc
        return {"ok": False, "status": exc.code, "error": text}
    return json.loads(text or "{}")


def probe_codex_cli() -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    seen: set[str] = set()

    env_cli = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_cli:
        append_codex_probe_attempt(attempts, seen, env_cli, "env:CODEX_CLI_PATH")

    config_path = resolve_codex_config_path()
    config_cli = read_codex_cli_path_from_config(config_path)
    if config_cli:
        append_codex_probe_attempt(attempts, seen, config_cli, f"config:{config_path}")

    path_cli = shutil.which("codex")
    if path_cli:
        append_codex_probe_attempt(attempts, seen, path_cli, "PATH")
    elif not attempts:
        attempts.append(probe_command(["codex", "--version"], source="PATH"))

    ok_attempt = next((attempt for attempt in attempts if attempt.get("ok")), None)
    best = ok_attempt or next((attempt for attempt in attempts if attempt.get("found")), attempts[-1] if attempts else {})
    result = dict(best)
    result["attempts"] = attempts
    if ok_attempt and ok_attempt.get("source") != "PATH":
        result["preferredConfiguredCli"] = True
    return result


def append_codex_probe_attempt(attempts: list[dict[str, Any]], seen: set[str], cli_path: str, source: str) -> None:
    normalized = str(Path(cli_path).expanduser())
    key = normalized.lower() if os.name == "nt" else normalized
    if key in seen:
        return
    seen.add(key)
    attempts.append(probe_command([normalized, "--version"], source=source))


def resolve_codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def read_codex_cli_path_from_config(path: Path) -> str:
    text = read_text_file(path)
    if not text:
        return ""
    for line in text.splitlines():
        match = re.match(r"\s*CODEX_CLI_PATH\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$", line)
        if match:
            return match.group(2).strip()
    return ""


def probe_command(command: list[str], source: str = "PATH") -> dict[str, Any]:
    exe = resolve_command_exe(command[0])
    payload: dict[str, Any] = {
        "found": bool(exe),
        "path": exe or "",
        "source": source,
        "ok": False,
        "stdout": "",
        "stderr": "",
        "error": "",
    }
    if not exe:
        payload["error"] = f"{command[0]} was not found."
        return payload
    try:
        completed = subprocess.run(
            [exe, *command[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        payload["error"] = str(exc)
        return payload
    payload.update(
        {
            "ok": completed.returncode == 0,
            "exitCode": completed.returncode,
            "stdout": (completed.stdout or "").strip()[:500],
            "stderr": (completed.stderr or "").strip()[:500],
        }
    )
    return payload


def resolve_command_exe(command_name: str) -> str:
    command_path = Path(command_name).expanduser()
    if command_path.is_file():
        return str(command_path)
    return shutil.which(command_name) or ""


def probe_windows_app(name_fragment: str) -> dict[str, Any]:
    if os.name != "nt":
        return {"found": False, "ok": False, "reason": "not-windows"}
    roots = [
        Path(os.environ.get("ProgramFiles", "")) / "WindowsApps",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
    ]
    matches: list[str] = []
    path_matches: list[str] = []
    for path_item in os.environ.get("PATH", "").split(os.pathsep):
        if name_fragment.lower() in path_item.lower():
            path_matches.append(path_item)
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for item in root.iterdir():
                if name_fragment.lower() in item.name.lower():
                    matches.append(str(item))
        except OSError:
            continue
    all_matches = matches + path_matches
    return {"found": bool(all_matches), "ok": bool(all_matches), "matches": all_matches[:10]}


def resolve_gateway_config_path(raw: str) -> Path | None:
    if raw:
        return Path(raw).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidate = Path(local_app_data) / "VRCForge" / "agentic-app" / "config" / "agent_gateway.json"
        if candidate.is_file():
            return candidate
    candidate = Path.cwd() / "agent_gateway.json"
    return candidate if candidate.is_file() else None


def resolve_app_token_path(raw: str) -> Path | None:
    if raw:
        return Path(raw).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidate = Path(local_app_data) / "VRCForge" / "agentic-app" / "config" / "app-session-token"
        if candidate.is_file():
            return candidate
    return None


def resolve_settings_path() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidate = Path(local_app_data) / "VRCForge" / "agentic-app" / "config" / "settings.json"
        if candidate.is_file():
            return candidate
    candidate = Path.cwd() / "config.json"
    return candidate if candidate.is_file() else None


def read_json_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_text_file(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def compile_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    result = ensure_dict(payload.get("result", payload))
    return {
        "ok": bool(result.get("ok", True)) and not bool(result.get("hasErrors")),
        "hasErrors": bool(result.get("hasErrors")),
        "errorCount": int(result.get("errorCount") or 0),
        "isCompiling": bool(result.get("isCompiling")),
    }


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def redact_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if "token" in lowered or lowered in {"authorization", "api_key", "apikey"}:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_evidence(item)
        return redacted
    if isinstance(value, list):
        return [redact_evidence(item) for item in value]
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
