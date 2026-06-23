from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from smoke_external_agent_bridge import ensure_dict, ensure_list, read_json_file, read_text_file, redact_evidence, request_json, resolve_app_token_path  # noqa: E402


DEFAULT_BASE_URL = "http://127.0.0.1:8757"
DEFAULT_POIYOMI_REPOSITORY = "https://poiyomi.github.io/vpm/index.json"
DEFAULT_POIYOMI_PACKAGE_ID = "com.poiyomi.toon"
DEFAULT_POIYOMI_SHADER = ".poiyomi/Poiyomi Toon"
PREFERRED_SEMANTICS = ("smoothness", "rim_strength", "base_color", "shade_color")


def main() -> int:
    args = parse_args()
    smoke = ShaderAdapterApplyRollbackSmoke(args)
    report = smoke.run()
    path = smoke.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test shader adapter apply/rollback through VRCForge approval and checkpoints.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--app-token-file", default="")
    parser.add_argument("--gateway-config", default="")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--avatar-path", required=True)
    parser.add_argument("--source-family", default="lilToon")
    parser.add_argument("--target-family", default="Poiyomi")
    parser.add_argument("--target-shader", default=DEFAULT_POIYOMI_SHADER)
    parser.add_argument("--package-id", default=DEFAULT_POIYOMI_PACKAGE_ID)
    parser.add_argument("--repository", default=DEFAULT_POIYOMI_REPOSITORY)
    parser.add_argument("--renderer-path", default="")
    parser.add_argument("--slot-index", type=int, default=None)
    parser.add_argument("--semantic-property", default="")
    parser.add_argument("--capture-screenshots", action="store_true")
    parser.add_argument("--screenshot-width", type=int, default=960)
    parser.add_argument("--screenshot-height", type=int, default=960)
    parser.add_argument("--wait-after-package-seconds", type=float, default=20.0)
    parser.add_argument("--package-ready-timeout", type=float, default=180.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


class ShaderAdapterApplyRollbackSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_url = str(args.base_url).rstrip("/")
        self.app_token_path = resolve_app_token_path(args.app_token_file)
        self.app_token = read_text_file(self.app_token_path).strip()
        self.gateway_config_path = resolve_gateway_config_path(args.gateway_config)
        self.gateway_config = read_json_file(self.gateway_config_path)
        self.gateway_token = str(self.gateway_config.get("token") or "")
        self.project_root = str(Path(args.project_root).expanduser().resolve())
        self.run_id = f"shader-adapter-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.artifact_root = Path.cwd() / "artifacts" / "shader-adapter-smoke"
        self.run_dir = self.artifact_root / self.run_id
        self.screenshot_dir = self.run_dir / "screenshots"
        self.steps: list[dict[str, Any]] = []
        self.previous_permission = ""
        self.previous_gateway: dict[str, Any] | None = None
        self.package_checkpoint_id = ""
        self.package_installed_by_smoke = False
        self.shader_checkpoint_id = ""
        self.shader_rollback_done = False
        self.tuning_checkpoint_id = ""
        self.tuning_rollback_done = False
        self.package_rollback_done = False
        self.target_shader_asset_path = ""
        self.target_before: dict[str, Any] = {}
        self.target_after_switch: dict[str, Any] = {}
        self.tuning_change: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.shader_adapter_apply_rollback_smoke.v1",
            "startedAt": utc_now(),
            "baseUrl": self.base_url,
            "projectRoot": self.project_root,
            "avatarPath": self.args.avatar_path,
            "sourceFamily": self.args.source_family,
            "targetFamily": self.args.target_family,
            "targetShader": self.args.target_shader,
            "packageId": self.args.package_id,
            "repository": self.args.repository,
            "artifactRunId": self.run_id,
            "artifactRunDir": str(self.run_dir),
            "screenshotDir": str(self.screenshot_dir) if self.args.capture_screenshots else "",
            "steps": self.steps,
            "summary": {},
        }
        try:
            self.step("runtime.health", self.runtime_health())
            self.step("permission.force_approval", self.force_approval_mode())
            self.step("gateway.enable", self.enable_gateway())
            self.step("unity.tool_available", self.assert_unity_tool())
            self.ensure_package()
            self.target_shader_asset_path = self.resolve_shader_asset_path()
            self.step(
                "shader.resolve_target_asset",
                {
                    "ok": True,
                    "found": bool(self.target_shader_asset_path),
                    "shaderAssetPath": self.target_shader_asset_path,
                    "packageId": self.args.package_id,
                },
            )
            if is_default_poiyomi_target(self.args.package_id, self.args.target_shader) and not self.target_shader_asset_path:
                raise RuntimeError(f"Poiyomi shader asset was not found after package install: {self.args.target_shader}")
            before_scan = self.scan_materials()
            self.step("shader.scan_before", scan_summary(before_scan))
            self.target_before = self.select_source_target(before_scan)
            self.step("shader.select_source_target", {"ok": bool(self.target_before), "target": self.target_before})
            self.capture_screenshot("screenshot.before")
            self.apply_shader_switch()
            after_switch = self.scan_materials()
            self.target_after_switch = self.find_material(after_switch, self.target_before, self.args.target_family)
            self.step("shader.verify_target_family", {"ok": bool(self.target_after_switch), "target": self.target_after_switch, "summary": ensure_dict(after_switch.get("summary"))})
            if not self.target_after_switch:
                raise RuntimeError(f"Target material did not scan as {self.args.target_family} after shader switch.")
            self.apply_semantic_tuning()
            self.capture_screenshot("screenshot.after_apply")
            self.restore_tuning_checkpoint()
            self.restore_shader_checkpoint()
            final_scan = self.scan_materials()
            restored_target = self.find_material(final_scan, self.target_before, self.args.source_family)
            self.step("shader.verify_source_restored", {"ok": bool(restored_target), "target": restored_target, "summary": ensure_dict(final_scan.get("summary"))})
            self.capture_screenshot("screenshot.after_rollback")
            if self.package_installed_by_smoke:
                self.restore_package_checkpoint()
            report["ok"] = all(bool(step.get("ok")) for step in self.steps)
        except Exception as exc:  # noqa: BLE001 - always emit evidence and attempt rollback.
            self.step("smoke.error", {"ok": False, "error": str(exc)})
            report["ok"] = False
        finally:
            self.try_emergency_rollbacks()
            self.restore_previous_state()
            report["finishedAt"] = utc_now()
            report["steps"] = self.steps
            report["ok"] = bool(report.get("ok")) and all(bool(step.get("ok")) for step in self.steps)
            report["summary"] = self.build_summary(report["ok"])
            report["recipe"] = self.build_recipe(report["ok"])
        return report

    def runtime_health(self) -> dict[str, Any]:
        payload = self.request_app_json("GET", "/api/health")
        return {"ok": bool(payload.get("ok")), "version": payload.get("version"), "portableMode": payload.get("portableMode")}

    def force_approval_mode(self) -> dict[str, Any]:
        state = self.request_app_json("GET", "/api/app/permission")
        self.previous_permission = str(ensure_dict(state.get("permission")).get("executionMode") or "")
        updated = self.request_app_json("POST", "/api/app/permission", {"execution_mode": "approval"})
        permission = ensure_dict(updated.get("permission"))
        return {"ok": permission.get("executionMode") == "approval", "previousPermission": self.previous_permission, "currentPermission": permission.get("executionMode")}

    def enable_gateway(self) -> dict[str, Any]:
        if not self.gateway_token:
            raise RuntimeError("Gateway token was not found.")
        status = self.request_app_json("GET", "/api/app/external-agent/connectors")
        self.previous_gateway = ensure_dict(status.get("gateway"))
        updated = self.request_app_json("POST", "/api/app/external-agent/gateway", {"enabled": True, "allowWriteRequests": True})
        gateway = ensure_dict(updated.get("gateway"))
        return {"ok": bool(gateway.get("enabled")) and bool(gateway.get("allowWriteRequests")), "previous": self.previous_gateway, "current": gateway}

    def assert_unity_tool(self) -> dict[str, Any]:
        payload = self.request_app_json("POST", "/api/unity/tools", {"projectPath": self.project_root})
        names = {str(name) for name in ensure_list(payload.get("toolNames"))}
        return {"ok": "vrc_set_material_shader" in names, "toolCount": payload.get("totalTools"), "hasFixtureTool": "vrc_set_material_shader" in names}

    def ensure_package(self) -> None:
        if not self.args.package_id:
            self.step("package.skip", {"ok": True, "reason": "No package id was requested."})
            return
        plan = self.request_app_json("POST", "/api/app/package-install/plan", self.package_payload())
        installed = bool(ensure_dict(plan.get("packageState")).get("installed"))
        self.step("package.plan", {"ok": bool(plan.get("ok")), "installedBefore": installed, "canExecuteCommandInstall": plan.get("canExecuteCommandInstall"), "strategy": plan.get("strategy")})
        if installed:
            return
        request = self.request_app_json("POST", "/api/app/package-install/request", self.package_payload())
        approval = ensure_dict(request.get("approval"))
        approval_id = str(approval.get("id") or "")
        self.step("package.request", {"ok": bool(approval_id) and approval.get("status") == "pending", "approvalId": approval_id, "targetTool": approval.get("targetTool")})
        if not approval_id:
            raise RuntimeError("Package install request did not create a pending approval.")
        applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
        execution = ensure_dict(applied.get("execution"))
        checkpoint = ensure_dict(execution.get("checkpoint"))
        result = ensure_dict(execution.get("result"))
        self.package_checkpoint_id = str(checkpoint.get("id") or "")
        self.package_installed_by_smoke = bool(result.get("ok"))
        self.step(
            "package.approve_install",
            {
                "ok": bool(applied.get("ok")) and execution.get("status") == "applied" and bool(result.get("ok")) and bool(checkpoint.get("ok")) and bool(self.package_checkpoint_id),
                "approvalId": approval_id,
                "checkpointId": self.package_checkpoint_id,
                "checkpointOk": checkpoint.get("ok"),
                "resultOk": result.get("ok"),
                "exitCode": result.get("exitCode"),
                "preflightCount": len(ensure_list(result.get("preflightResults"))),
                "preflightResults": ensure_list(result.get("preflightResults")),
                "stdoutSummary": result.get("stdoutSummary"),
                "stderrSummary": result.get("stderrSummary"),
                "resultError": result.get("error"),
                "unityRefresh": result.get("unityRefresh"),
                "error": execution.get("error") or checkpoint.get("error"),
            },
        )
        if not checkpoint.get("ok") or not result.get("ok"):
            raise RuntimeError(
                f"Package install failed: {checkpoint.get('error') or result.get('error') or result.get('stderrSummary') or result.get('stdoutSummary') or execution.get('error')}"
            )
        self.recover_unity_after_package_install(ensure_dict(result.get("unityRefresh")))
        if self.args.wait_after_package_seconds > 0:
            time.sleep(float(self.args.wait_after_package_seconds))
        self.wait_for_material_scan_ready()

    def recover_unity_after_package_install(self, unity_refresh: dict[str, Any]) -> None:
        tools = self.try_read_unity_tools()
        refresh_ok = bool(unity_refresh.get("ok", True))
        if refresh_ok and tools.get("ok") and int(tools.get("totalTools") or 0) > 0:
            self.step(
                "package.unity_ready_after_install",
                {"ok": True, "toolCount": tools.get("totalTools"), "unityRefresh": unity_refresh},
            )
            return
        repair = self.request_app_json(
            "POST",
            "/api/app/doctor/unity-mcp/repair",
            {
                "projectPath": self.project_root,
                "allowUnityRelaunch": True,
                "waitSeconds": max(90, min(int(self.args.package_ready_timeout), 360)),
                "closeTimeoutSeconds": 60,
            },
        )
        self.step(
            "package.repair_unity_after_install",
            {
                "ok": bool(repair.get("ok")),
                "status": repair.get("status"),
                "unityRefresh": unity_refresh,
                "initialTools": tools,
                "phaseCount": len(ensure_list(repair.get("phases"))),
            },
        )
        self.wait_for_unity_tools_after_package()

    def try_read_unity_tools(self) -> dict[str, Any]:
        try:
            return self.request_app_json("POST", "/api/unity/tools", {"projectPath": self.project_root})
        except Exception as exc:  # noqa: BLE001 - package resolve can temporarily drop the bridge.
            return {"ok": False, "error": str(exc)}

    def wait_for_unity_tools_after_package(self) -> None:
        deadline = time.monotonic() + max(30.0, float(self.args.package_ready_timeout))
        attempts = 0
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            attempts += 1
            last = self.try_read_unity_tools()
            names = {str(name) for name in ensure_list(last.get("toolNames"))}
            if last.get("ok") and "vrc_set_material_shader" in names:
                self.step(
                    "package.wait_unity_tools_after_install",
                    {"ok": True, "attempts": attempts, "toolCount": last.get("totalTools"), "vrcForgeToolsCount": last.get("vrcForgeToolsCount")},
                )
                return
            time.sleep(15)
        self.step("package.wait_unity_tools_after_install", {"ok": False, "attempts": attempts, "last": last})
        raise RuntimeError("Unity MCP tools did not recover after package install.")

    def wait_for_material_scan_ready(self) -> None:
        deadline = time.monotonic() + max(1.0, float(self.args.package_ready_timeout))
        last_error = ""
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            try:
                scan = self.scan_materials()
                summary = scan_summary(scan)
                if summary.get("ok"):
                    self.step("package.wait_material_scan_ready", {"ok": True, "attempts": attempts, "summary": summary})
                    return
                last_error = json.dumps(summary, ensure_ascii=True)
            except Exception as exc:  # noqa: BLE001 - Unity may be compiling/importing packages.
                last_error = str(exc)
            time.sleep(10)
        self.step("package.wait_material_scan_ready", {"ok": False, "attempts": attempts, "error": last_error})
        raise RuntimeError(f"Material scan did not become ready after package install: {last_error}")

    def apply_shader_switch(self) -> None:
        shader_arguments = {
            "rendererPath": self.target_before.get("renderer_path"),
            "slotIndex": int(self.target_before.get("slot_index") or 0),
            "shaderName": self.args.target_shader,
            "preview": False,
            "saveAssets": True,
        }
        if self.target_shader_asset_path:
            shader_arguments["shaderAssetPath"] = self.target_shader_asset_path
        request = self.request_apply(
            {
                "target_tool": "vrcforge_unity_mcp_write",
                "arguments": {
                    "projectPath": self.project_root,
                    "toolName": "vrc_set_material_shader",
                    "arguments": shader_arguments,
                },
                "reason": f"Shader adapter proof: switch one {self.args.source_family} material to {self.args.target_family}.",
                "preview": {
                    "rendererPath": self.target_before.get("renderer_path"),
                    "slotIndex": self.target_before.get("slot_index"),
                    "beforeShader": self.target_before.get("shader_name"),
                    "afterShader": self.args.target_shader,
                    "shaderAssetPath": self.target_shader_asset_path,
                    "rollbackRequired": True,
                },
            }
        )
        approval = ensure_dict(request.get("result", request).get("approval"))
        approval_id = str(approval.get("id") or "")
        self.step("shader.request_switch", {"ok": bool(approval_id) and approval.get("status") == "pending", "approvalId": approval_id, "targetTool": approval.get("targetTool")})
        if not approval_id:
            raise RuntimeError("Shader switch request did not create a pending approval.")
        applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
        execution = ensure_dict(applied.get("execution"))
        checkpoint = ensure_dict(execution.get("checkpoint"))
        result = ensure_dict(execution.get("result"))
        self.shader_checkpoint_id = str(checkpoint.get("id") or "")
        self.step(
            "shader.approve_switch",
            {
                "ok": bool(applied.get("ok")) and execution.get("status") == "applied" and bool(result.get("ok")) and bool(checkpoint.get("ok")) and bool(self.shader_checkpoint_id),
                "approvalId": approval_id,
                "checkpointId": self.shader_checkpoint_id,
                "checkpointOk": checkpoint.get("ok"),
                "resultOk": result.get("ok"),
                "error": execution.get("error") or result.get("error") or checkpoint.get("error"),
            },
        )
        if not self.shader_checkpoint_id or not checkpoint.get("ok") or not result.get("ok"):
            raise RuntimeError("Shader switch did not apply cleanly.")

    def apply_semantic_tuning(self) -> None:
        self.tuning_change = self.build_tuning_change(self.target_after_switch)
        self.step("shader.select_semantic_change", {"ok": bool(self.tuning_change), "change": self.tuning_change})
        if not self.tuning_change:
            raise RuntimeError("No writable semantic property was available on the target material.")
        request = self.request_apply(
            {
                "target_tool": "vrcforge_apply_shader_tuning",
                "arguments": {
                    "projectPath": self.project_root,
                    "avatar_path": self.args.avatar_path,
                    "source_mode": "unity_live_export",
                    "mock_execute": False,
                    "changes": [self.tuning_change],
                },
                "reason": f"Shader adapter proof: apply one semantic {self.args.target_family} material tuning change.",
                "preview": {"targetFamily": self.args.target_family, "change": self.tuning_change, "rollbackRequired": True},
            }
        )
        approval = ensure_dict(request.get("result", request).get("approval"))
        approval_id = str(approval.get("id") or "")
        self.step("shader.request_semantic_apply", {"ok": bool(approval_id) and approval.get("status") == "pending", "approvalId": approval_id, "targetTool": approval.get("targetTool")})
        if not approval_id:
            raise RuntimeError("Shader tuning request did not create a pending approval.")
        applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
        execution = ensure_dict(applied.get("execution"))
        checkpoint = ensure_dict(execution.get("checkpoint"))
        result = ensure_dict(execution.get("result"))
        self.tuning_checkpoint_id = str(checkpoint.get("id") or "")
        self.step(
            "shader.approve_semantic_apply",
            {
                "ok": bool(applied.get("ok")) and execution.get("status") == "applied" and bool(result.get("ok")) and bool(ensure_list(result.get("appliedChanges"))) and bool(checkpoint.get("ok")) and bool(self.tuning_checkpoint_id),
                "approvalId": approval_id,
                "checkpointId": self.tuning_checkpoint_id,
                "checkpointOk": checkpoint.get("ok"),
                "resultOk": result.get("ok"),
                "appliedCount": len(ensure_list(result.get("appliedChanges"))),
                "skippedCount": len(ensure_list(result.get("skippedChanges"))),
                "error": execution.get("error") or result.get("error") or checkpoint.get("error"),
            },
        )
        if not self.tuning_checkpoint_id or not checkpoint.get("ok") or not result.get("ok"):
            raise RuntimeError("Shader semantic tuning did not apply cleanly.")

    def restore_tuning_checkpoint(self) -> None:
        self.tuning_rollback_done = self.restore_checkpoint(self.tuning_checkpoint_id, "shader.restore_semantic_checkpoint")

    def restore_shader_checkpoint(self) -> None:
        self.shader_rollback_done = self.restore_checkpoint(self.shader_checkpoint_id, "shader.restore_switch_checkpoint")

    def restore_package_checkpoint(self) -> None:
        self.package_rollback_done = self.restore_checkpoint(self.package_checkpoint_id, "package.restore_checkpoint")

    def restore_checkpoint(self, checkpoint_id: str, step_prefix: str) -> bool:
        if not checkpoint_id:
            self.step(step_prefix, {"ok": False, "error": "checkpoint id is empty"})
            return False
        restore_request = self.request_app_json("POST", f"/api/app/checkpoints/{checkpoint_id}/restore", {})
        approval = ensure_dict(restore_request.get("approval"))
        approval_id = str(approval.get("id") or "")
        self.step(f"{step_prefix}.request", {"ok": bool(approval_id) and approval.get("status") == "pending", "approvalId": approval_id, "targetTool": approval.get("targetTool")})
        if not approval_id:
            return False
        restored = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
        execution = ensure_dict(restored.get("execution"))
        ok = bool(restored.get("ok")) and execution.get("status") == "applied"
        self.step(f"{step_prefix}.approve", {"ok": ok, "approvalId": approval_id, "executionStatus": execution.get("status"), "error": execution.get("error")})
        preview = self.request_app_json("POST", f"/api/app/checkpoints/{checkpoint_id}/preview", {})
        self.step(f"{step_prefix}.verify_clean", {"ok": bool(preview.get("ok")) and not bool(preview.get("changedFiles")), "changedFileCount": len(ensure_list(preview.get("changedFiles")))})
        return ok

    def scan_materials(self) -> dict[str, Any]:
        return self.request_app_json("POST", "/api/shader/materials/scan", {"projectPath": self.project_root, "avatar_path": self.args.avatar_path})

    def resolve_shader_asset_path(self) -> str:
        target_shader = str(self.args.target_shader or "").strip()
        if not target_shader:
            return ""
        package_root = Path(self.project_root) / "Packages" / str(self.args.package_id or "")
        search_roots = [package_root] if package_root.is_dir() else []
        if not search_roots:
            search_roots = [Path(self.project_root) / "Packages", Path(self.project_root) / "Assets"]
        leaf_name = target_shader.rsplit("/", 1)[-1].lower()
        for root in search_roots:
            if not root.is_dir():
                continue
            for shader_file in root.rglob("*.shader"):
                try:
                    with shader_file.open("r", encoding="utf-8", errors="ignore") as handle:
                        head = handle.read(8192)
                except OSError:
                    continue
                if f'Shader "{target_shader}"' in head or shader_file.stem.lower() == leaf_name:
                    return unity_asset_path(self.project_root, shader_file)
        return ""

    def select_source_target(self, scan: dict[str, Any]) -> dict[str, Any]:
        materials = [item for item in ensure_list(scan.get("materials")) if isinstance(item, dict)]
        if self.args.renderer_path:
            for item in materials:
                if str(item.get("renderer_path") or "") == self.args.renderer_path and (self.args.slot_index is None or int(item.get("slot_index") or 0) == self.args.slot_index):
                    return item
            raise RuntimeError(f"Requested material target was not found: {self.args.renderer_path} slot {self.args.slot_index}")
        source_family = self.args.source_family.lower()
        for item in materials:
            category = str(item.get("category") or "")
            family = str(item.get("shader_family") or "").lower()
            if family == source_family and category in {"clothes", "accessory"}:
                return item
        raise RuntimeError(f"No low-risk {self.args.source_family} clothes/accessory material was found.")

    def find_material(self, scan: dict[str, Any], target: dict[str, Any], family: str) -> dict[str, Any]:
        renderer_path = str(target.get("renderer_path") or "")
        slot_index = int(target.get("slot_index") or 0)
        for item in ensure_list(scan.get("materials")):
            if not isinstance(item, dict):
                continue
            if str(item.get("renderer_path") or "") == renderer_path and int(item.get("slot_index") or 0) == slot_index and str(item.get("shader_family") or "").lower() == family.lower():
                return item
        return {}

    def build_tuning_change(self, material: dict[str, Any]) -> dict[str, Any]:
        supported = ensure_dict(material.get("supported_properties"))
        semantic = self.args.semantic_property.strip()
        if semantic:
            candidates = [semantic]
        else:
            candidates = list(PREFERRED_SEMANTICS) + sorted(key for key in supported if key not in PREFERRED_SEMANTICS)
        for key in candidates:
            value = ensure_dict(supported.get(key))
            if not value or not value.get("writable", True):
                continue
            after = next_value(value)
            if after is None:
                continue
            return {
                "material_id": material.get("material_id"),
                "material_name": material.get("material_name"),
                "semantic_property": key,
                "before": value.get("value"),
                "after": after,
                "reason": f"{self.args.target_family} adapter live proof.",
            }
        return {}

    def capture_screenshot(self, step_name: str) -> None:
        if not self.args.capture_screenshots:
            return
        try:
            payload = self.request_app_json(
                "POST",
                "/api/vision/capture",
                {
                    "projectPath": self.project_root,
                    "avatar_path": self.args.avatar_path,
                    "width": int(self.args.screenshot_width),
                    "height": int(self.args.screenshot_height),
                    "require_play_mode": False,
                },
            )
            artifact = self.persist_screenshot(step_name, str(payload.get("imagePath") or "")) if payload.get("ok") else {"ok": False, "error": "capture returned ok=false"}
            self.step(step_name, {"ok": True, "captureOk": bool(payload.get("ok")), "artifactOk": bool(artifact.get("ok")), "optional": True, "artifactImagePath": artifact.get("artifactImagePath"), "error": artifact.get("error")})
        except Exception as exc:  # noqa: BLE001 - screenshot evidence is optional.
            self.step(step_name, {"ok": True, "captureOk": False, "optional": True, "error": str(exc)})

    def persist_screenshot(self, step_name: str, source_image_path: str) -> dict[str, Any]:
        if not source_image_path:
            return {"ok": False, "error": "capture did not return imagePath"}
        source = Path(source_image_path).expanduser()
        if not source.exists() or not source.is_file():
            return {"ok": False, "error": f"capture image does not exist: {source}"}
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        destination = self.screenshot_dir / f"{screenshot_stage_name(step_name)}.png"
        shutil.copy2(source, destination)
        return {"ok": True, "artifactImagePath": str(destination)}

    def try_emergency_rollbacks(self) -> None:
        if self.tuning_checkpoint_id and not self.tuning_rollback_done:
            try:
                self.tuning_rollback_done = self.restore_checkpoint(self.tuning_checkpoint_id, "rollback.emergency_tuning")
            except Exception as exc:  # noqa: BLE001
                self.step("rollback.emergency_tuning", {"ok": False, "error": str(exc)})
        if self.shader_checkpoint_id and not self.shader_rollback_done:
            try:
                self.shader_rollback_done = self.restore_checkpoint(self.shader_checkpoint_id, "rollback.emergency_shader")
            except Exception as exc:  # noqa: BLE001
                self.step("rollback.emergency_shader", {"ok": False, "error": str(exc)})
        if self.package_installed_by_smoke and self.package_checkpoint_id and not self.package_rollback_done:
            try:
                self.package_rollback_done = self.restore_checkpoint(self.package_checkpoint_id, "rollback.emergency_package")
            except Exception as exc:  # noqa: BLE001
                self.step("rollback.emergency_package", {"ok": False, "error": str(exc)})

    def restore_previous_state(self) -> None:
        if self.previous_permission and self.previous_permission != "approval":
            try:
                restored = self.request_app_json("POST", "/api/app/permission", {"execution_mode": self.previous_permission})
                self.step("cleanup.permission_restore", {"ok": ensure_dict(restored.get("permission")).get("executionMode") == self.previous_permission, "permission": self.previous_permission})
            except Exception as exc:  # noqa: BLE001
                self.step("cleanup.permission_restore", {"ok": False, "error": str(exc)})
        if self.previous_gateway is not None:
            try:
                payload = {"enabled": bool(self.previous_gateway.get("enabled")), "allowWriteRequests": bool(self.previous_gateway.get("allowWriteRequests"))}
                self.request_app_json("POST", "/api/app/external-agent/gateway", payload)
                self.step("cleanup.gateway_restore", {"ok": True, **payload})
            except Exception as exc:  # noqa: BLE001
                self.step("cleanup.gateway_restore", {"ok": False, "error": str(exc)})

    def request_apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_agent_json("POST", "/api/agent/tool/vrcforge_request_apply", {"agent_name": "shader-adapter-smoke", "params": payload})

    def package_payload(self) -> dict[str, Any]:
        return {"projectPath": self.project_root, "packageId": self.args.package_id, "repository": self.args.repository, "includePrerelease": False}

    def request_app_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return request_json(self.base_url, method, path, self.app_token, payload, False, self.args.timeout)

    def request_agent_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return request_json(self.base_url, method, path, self.gateway_token, payload, False, self.args.timeout)

    def step(self, name: str, payload: dict[str, Any]) -> None:
        self.steps.append({"name": name, **redact_evidence(payload)})

    def write_report(self, report: dict[str, Any]) -> Path:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        path = self.artifact_root / f"{self.run_id}.json"
        path.write_text(json.dumps(redact_evidence(report), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def build_summary(self, ok: bool) -> dict[str, Any]:
        return {
            "status": "passed" if ok else "failed",
            "packageCheckpointId": self.package_checkpoint_id,
            "shaderCheckpointId": self.shader_checkpoint_id,
            "tuningCheckpointId": self.tuning_checkpoint_id,
            "shaderRollbackDone": self.shader_rollback_done,
            "tuningRollbackDone": self.tuning_rollback_done,
            "packageRollbackDone": self.package_rollback_done,
            "targetShaderAssetPath": self.target_shader_asset_path,
            "target": {
                "rendererPath": self.target_before.get("renderer_path"),
                "slotIndex": self.target_before.get("slot_index"),
                "materialName": self.target_before.get("material_name"),
            },
            "semanticProperty": self.tuning_change.get("semantic_property"),
            "failedSteps": [step["name"] for step in self.steps if not step.get("ok")],
        }

    def build_recipe(self, ok: bool) -> dict[str, Any]:
        return {
            "schema": "vrcforge.path_to_skill.v1",
            "source": "shader-adapter-smoke",
            "proofPassed": bool(ok),
            "workflow": "shader_adapter_semantic_tuning",
            "variables": {
                "projectPath": "{{projectPath}}",
                "avatarPath": "{{avatarPath}}",
                "rendererPath": self.target_before.get("renderer_path") or "{{rendererPath}}",
                "slotIndex": self.target_before.get("slot_index"),
            },
            "requirements": {
                "packageId": self.args.package_id,
                "repository": self.args.repository,
                "targetShader": self.args.target_shader,
                "targetShaderAssetPath": self.target_shader_asset_path,
                "sourceFamily": self.args.source_family,
                "targetFamily": self.args.target_family,
            },
            "steps": [
                "scan_materials",
                "request_shader_switch",
                "verify_target_family",
                "request_semantic_tuning",
                "checkpoint_restore",
            ],
            "validation": {
                "requiresApproval": True,
                "requiresCheckpoint": True,
                "requiresRollback": True,
                "semanticProperty": self.tuning_change.get("semantic_property") or "",
            },
        }


def resolve_gateway_config_path(raw: str) -> Path | None:
    if raw:
        return Path(raw).expanduser().resolve()
    repo_candidate = Path.cwd() / "agent_gateway.json"
    if repo_candidate.is_file():
        return repo_candidate
    local_app_data = Path.home()
    if "LOCALAPPDATA" in __import__("os").environ:
        local_app_data = Path(__import__("os").environ["LOCALAPPDATA"])
    candidate = local_app_data / "VRCForge" / "agentic-app" / "config" / "agent_gateway.json"
    return candidate if candidate.is_file() else None


def is_default_poiyomi_target(package_id: str, shader_name: str) -> bool:
    return package_id == DEFAULT_POIYOMI_PACKAGE_ID and shader_name == DEFAULT_POIYOMI_SHADER


def unity_asset_path(project_root: str, file_path: Path) -> str:
    project = Path(project_root).expanduser().resolve()
    resolved = file_path.expanduser().resolve()
    try:
        return resolved.relative_to(project).as_posix()
    except ValueError:
        return resolved.as_posix()


def next_value(property_value: dict[str, Any]) -> Any:
    value_type = str(property_value.get("type") or "").lower()
    current = property_value.get("value")
    if value_type == "float":
        try:
            number = float(current)
        except (TypeError, ValueError):
            number = 0.0
        return 0.75 if number < 0.5 else 0.25
    if value_type == "color":
        text = str(current or "").strip().upper()
        return "#FFFFFF" if text != "#FFFFFF" else "#CCCCCC"
    return None


def scan_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = ensure_dict(payload.get("summary"))
    return {
        "ok": bool(payload.get("ok")) and bool(summary),
        "materialCount": summary.get("materialCount"),
        "lilToonCount": summary.get("lilToonCount"),
        "poiyomiCount": summary.get("poiyomiCount"),
        "genericCount": summary.get("genericCount"),
        "unsupportedCount": summary.get("unsupportedCount"),
    }


def screenshot_stage_name(step_name: str) -> str:
    stage = step_name.split(".", 1)[-1].strip().lower()
    safe = "".join(char if char.isalnum() else "_" for char in stage).strip("_")
    return safe or "screenshot"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
