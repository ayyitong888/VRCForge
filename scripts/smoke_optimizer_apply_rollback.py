from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from smoke_external_agent_bridge import ensure_dict, ensure_list, read_text_file, redact_evidence, request_json, resolve_app_token_path  # noqa: E402


DEFAULT_BASE_URL = "http://127.0.0.1:8757"


def main() -> int:
    args = parse_args()
    smoke = OptimizerApplyRollbackSmoke(args)
    report = smoke.run()
    path = smoke.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test one VRCForge optimizer apply-request through approval, checkpoint, validation delta, and rollback.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--app-token-file", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--avatar-path", required=True)
    parser.add_argument("--tool", required=True, help="Optimizer request tool, for example optimization.lac.apply-request or vrcforge_optimization_lac_apply_request.")
    parser.add_argument("--target-profile", default="pc_conservative")
    parser.add_argument("--option", action="append", default=[], help="Optimizer option as key=value. JSON values are accepted.")
    parser.add_argument("--material", action="append", default=[], help="Append a TexTransTool atlas target material path under Assets/.")
    parser.add_argument("--renderer-path", default="", help="Meshia renderer GameObject path for conservative simplify setup.")
    parser.add_argument("--relative-vertex-count", type=float, default=None, help="Meshia relative vertex target in the stable 0.75..1.0 range.")
    parser.add_argument("--install-missing-dependencies", action="store_true")
    parser.add_argument("--include-prerelease", action="store_true")
    parser.add_argument("--capture-screenshots", action="store_true")
    parser.add_argument("--screenshot-width", type=int, default=960)
    parser.add_argument("--screenshot-height", type=int, default=960)
    parser.add_argument("--require-changed-files", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


class OptimizerApplyRollbackSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_url = str(args.base_url).rstrip("/")
        self.app_token_path = resolve_app_token_path(args.app_token_file)
        self.app_token = read_text_file(self.app_token_path).strip()
        self.project_root = Path(args.project_root).expanduser().resolve() if args.project_root else None
        self.run_id = f"optimizer-apply-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.artifact_root = Path.cwd() / "artifacts" / "optimizer-apply-smoke"
        self.run_dir = self.artifact_root / self.run_id
        self.screenshot_dir = self.run_dir / "screenshots"
        self.steps: list[dict[str, Any]] = []
        self.checkpoint_id = ""
        self.rollback_done = False
        self.previous_permission = ""
        self.before_validation: dict[str, Any] = {}
        self.after_validation: dict[str, Any] = {}
        self.rollback_validation: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.optimizer_apply_rollback_smoke.v1",
            "startedAt": utc_now(),
            "baseUrl": self.base_url,
            "appTokenFile": str(self.app_token_path) if self.app_token_path else "",
            "appTokenConfigured": bool(self.app_token),
            "projectRoot": str(self.project_root) if self.project_root else "",
            "avatarPath": self.args.avatar_path,
            "tool": self.args.tool,
            "artifactRunId": self.run_id,
            "artifactRunDir": str(self.run_dir),
            "screenshotDir": str(self.screenshot_dir) if self.args.capture_screenshots else "",
            "steps": self.steps,
            "summary": {},
        }
        try:
            self.step("runtime.bootstrap", self.bootstrap())
            self.step("permission.force_approval", self.force_approval_mode())
            self.before_validation = self.validation_report()
            self.step("validation.before", validation_summary(self.before_validation))
            self.capture_screenshot("screenshot.before")

            request_payload = self.request_app_json("POST", "/api/app/optimization/apply-request", self.apply_request_payload())
            approval = ensure_dict(request_payload.get("approval"))
            approval_id = str(approval.get("id") or "")
            self.step("optimizer.request", {"ok": bool(approval_id) and approval.get("status") == "pending", "approvalId": approval_id, "targetTool": approval.get("targetTool"), "status": approval.get("status")})
            if not approval_id:
                raise RuntimeError("Optimizer request did not create a pending approval.")

            applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
            execution = ensure_dict(applied.get("execution"))
            checkpoint = ensure_dict(execution.get("checkpoint") or applied.get("checkpoint") or ensure_dict(applied.get("approval")).get("checkpoint"))
            checkpoint_id = str(checkpoint.get("id") or "")
            checkpoint_usable = bool(checkpoint.get("ok")) and str(checkpoint.get("status") or "") == "ready" and bool(checkpoint_id)
            if checkpoint_usable:
                self.checkpoint_id = checkpoint_id
            self.step(
                "optimizer.approve_apply_checkpoint",
                {
                    "ok": bool(applied.get("ok")) and execution.get("status") == "applied" and checkpoint_usable,
                    "approvalId": approval_id,
                    "executionStatus": execution.get("status"),
                    "checkpointId": checkpoint_id,
                    "checkpointOk": checkpoint.get("ok"),
                    "checkpointStatus": checkpoint.get("status"),
                    "checkpointStrategy": checkpoint.get("strategy"),
                    "error": execution.get("error") or applied.get("error") or ensure_dict(applied.get("approval")).get("error"),
                },
            )
            if not checkpoint_usable:
                raise RuntimeError("Approved optimizer request did not create a usable checkpoint.")

            changed = self.request_app_json("POST", f"/api/app/checkpoints/{self.checkpoint_id}/preview", {})
            changed_files = ensure_list(changed.get("changedFiles"))
            self.step(
                "optimizer.verify_checkpoint_delta",
                {
                    "ok": bool(changed.get("ok")) and (bool(changed_files) or not bool(self.args.require_changed_files)),
                    "changedFileCount": len(changed_files),
                    "requireChangedFiles": bool(self.args.require_changed_files),
                },
            )

            self.after_validation = self.validation_report()
            self.step("validation.after_apply", validation_summary(self.after_validation))
            self.capture_screenshot("screenshot.after_apply")
            delta_after = self.validation_delta()
            self.step("validation.delta_after_apply", delta_summary(delta_after, require_rollback=False))

            restore_request = self.request_app_json("POST", f"/api/app/checkpoints/{self.checkpoint_id}/restore", {})
            restore_approval = ensure_dict(restore_request.get("approval"))
            restore_approval_id = str(restore_approval.get("id") or "")
            self.step("rollback.request", {"ok": bool(restore_approval_id) and restore_approval.get("status") == "pending", "approvalId": restore_approval_id, "targetTool": restore_approval.get("targetTool"), "status": restore_approval.get("status")})
            if not restore_approval_id:
                raise RuntimeError("Rollback request did not create a pending approval.")

            restored = self.request_app_json("POST", f"/api/app/agent/approvals/{restore_approval_id}/approve", {})
            restore_execution = ensure_dict(restored.get("execution"))
            self.rollback_done = bool(restored.get("ok")) and restore_execution.get("status") == "applied"
            self.step(
                "rollback.approve_apply",
                {
                    "ok": self.rollback_done,
                    "approvalId": restore_approval_id,
                    "executionStatus": restore_execution.get("status"),
                    "unityReloadOk": ensure_dict(ensure_dict(restore_execution.get("result")).get("unityReload")).get("ok"),
                },
            )

            preview_after_restore = self.request_app_json("POST", f"/api/app/checkpoints/{self.checkpoint_id}/preview", {})
            self.step("rollback.verify_checkpoint_clean", {"ok": bool(preview_after_restore.get("ok")) and not bool(preview_after_restore.get("changedFiles")), "changedFileCount": len(ensure_list(preview_after_restore.get("changedFiles")))})
            self.rollback_validation = self.validation_report()
            self.step("validation.after_rollback", validation_summary(self.rollback_validation))
            self.capture_screenshot("screenshot.after_rollback")
            delta_rollback = self.validation_delta(include_rollback=True)
            self.step("validation.delta_after_rollback", delta_summary(delta_rollback, require_rollback=True))
            report["ok"] = all(bool(step.get("ok")) for step in self.steps)
        except Exception as exc:  # noqa: BLE001 - always emit evidence and try rollback.
            self.step("smoke.error", {"ok": False, "error": str(exc)})
            report["ok"] = False
        finally:
            if self.checkpoint_id and not self.rollback_done:
                self.try_emergency_rollback()
            self.restore_permission_mode()
            report["ok"] = bool(report.get("ok")) and all(bool(step.get("ok")) for step in self.steps)
            report["finishedAt"] = utc_now()
            report["steps"] = self.steps
            report["summary"] = self.build_summary(report["ok"])
        return report

    def bootstrap(self) -> dict[str, Any]:
        if not self.app_token:
            raise RuntimeError("App session token was not found.")
        payload = self.request_app_json("GET", "/api/app/bootstrap")
        if self.project_root is None:
            selected = str(ensure_dict(ensure_dict(payload.get("health")).get("state")).get("selected_project_path") or "")
            if not selected:
                selected = str(ensure_dict(payload.get("projects")).get("selectedProjectPath") or "")
            if not selected:
                raise RuntimeError("--project-root is required when VRCForge has no selected Unity project.")
            self.project_root = Path(selected).expanduser().resolve()
        return {"ok": bool(payload.get("ok")), "version": payload.get("version"), "projectRoot": str(self.project_root)}

    def force_approval_mode(self) -> dict[str, Any]:
        state = self.request_app_json("GET", "/api/app/permission")
        self.previous_permission = str(ensure_dict(state.get("permission")).get("executionMode") or "")
        updated = self.request_app_json("POST", "/api/app/permission", {"execution_mode": "approval"})
        permission = ensure_dict(updated.get("permission"))
        return {
            "ok": permission.get("executionMode") == "approval" and bool(permission.get("perActionApproval")),
            "previousPermission": self.previous_permission,
            "currentPermission": permission.get("executionMode"),
            "perActionApproval": permission.get("perActionApproval"),
        }

    def apply_request_payload(self) -> dict[str, Any]:
        return {
            "tool": self.args.tool,
            "projectPath": str(self.project_root) if self.project_root else "",
            "avatarPath": self.args.avatar_path,
            "targetProfile": self.args.target_profile,
            "installMissingDependencies": bool(self.args.install_missing_dependencies),
            "includePrerelease": bool(self.args.include_prerelease),
            "options": build_options(self.args),
        }

    def validation_report(self) -> dict[str, Any]:
        return self.request_app_json(
            "POST",
            "/api/app/validation/report",
            {
                "projectPath": str(self.project_root) if self.project_root else "",
                "avatarPath": self.args.avatar_path,
                "includeQuest": True,
                "includeReadiness": True,
                "maxErrors": 50,
            },
        )

    def validation_delta(self, include_rollback: bool = False) -> dict[str, Any]:
        return self.request_app_json(
            "POST",
            "/api/app/optimization/validation-delta",
            {
                "optimizerTool": self.args.tool,
                "checkpointId": self.checkpoint_id,
                "beforeValidation": self.before_validation,
                "afterValidation": self.after_validation,
                "rollbackValidation": self.rollback_validation if include_rollback else {},
            },
        )

    def capture_screenshot(self, step_name: str) -> None:
        if not self.args.capture_screenshots:
            return
        try:
            payload = self.request_app_json(
                "POST",
                "/api/vision/capture",
                {
                    "projectPath": str(self.project_root) if self.project_root else "",
                    "avatarPath": self.args.avatar_path,
                    "width": int(self.args.screenshot_width),
                    "height": int(self.args.screenshot_height),
                    "require_play_mode": False,
                },
            )
            if bool(payload.get("ok")):
                artifact = self.persist_screenshot(step_name, str(payload.get("imagePath") or ""))
            else:
                artifact = {"ok": False, "error": "capture returned ok=false"}
            self.step(
                step_name,
                {
                    "ok": True,
                    "captureOk": bool(payload.get("ok")),
                    "artifactOk": bool(artifact.get("ok")),
                    "optional": True,
                    "sourceImagePath": payload.get("imagePath"),
                    "artifactImagePath": artifact.get("artifactImagePath"),
                    "imageUrl": payload.get("imageUrl"),
                    "warnings": payload.get("warnings"),
                    "artifactError": artifact.get("error"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - screenshots are optional evidence.
            self.step(step_name, {"ok": True, "captureOk": False, "optional": True, "error": str(exc)})

    def persist_screenshot(self, step_name: str, source_image_path: str) -> dict[str, Any]:
        if not source_image_path:
            return {"ok": False, "error": "capture did not return imagePath"}
        source = Path(source_image_path).expanduser()
        if not source.exists() or not source.is_file():
            return {"ok": False, "error": f"capture image does not exist: {source}"}
        stage = screenshot_stage_name(step_name)
        destination = self.screenshot_dir / f"{stage}.png"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {"ok": True, "artifactImagePath": str(destination)}

    def try_emergency_rollback(self) -> None:
        try:
            request = self.request_app_json("POST", f"/api/app/checkpoints/{self.checkpoint_id}/restore", {})
            approval_id = str(ensure_dict(request.get("approval")).get("id") or "")
            if approval_id:
                applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
                self.rollback_done = bool(applied.get("ok"))
                self.step("rollback.emergency", {"ok": self.rollback_done, "checkpointId": self.checkpoint_id})
        except Exception as exc:  # noqa: BLE001
            self.step("rollback.emergency", {"ok": False, "checkpointId": self.checkpoint_id, "error": str(exc)})

    def restore_permission_mode(self) -> None:
        if not self.previous_permission or self.previous_permission == "approval":
            return
        try:
            restored = self.request_app_json("POST", "/api/app/permission", {"execution_mode": self.previous_permission})
            permission = ensure_dict(restored.get("permission"))
            self.step("permission.restore", {"ok": permission.get("executionMode") == self.previous_permission, "restoredPermission": permission.get("executionMode")})
        except Exception as exc:  # noqa: BLE001
            self.step("permission.restore", {"ok": False, "targetPermission": self.previous_permission, "error": str(exc)})

    def request_app_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return request_json(self.base_url, method, path, self.app_token, payload, False, self.args.timeout)

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
            "tool": self.args.tool,
            "checkpointId": self.checkpoint_id,
            "rollbackDone": self.rollback_done,
            "screenshotDir": str(self.screenshot_dir) if self.args.capture_screenshots else "",
            "failedSteps": [step["name"] for step in self.steps if not step.get("ok")],
        }


def build_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for entry in args.option:
        key, value = parse_option(entry)
        options[key] = value
    if args.material:
        options["atlasTargetMaterials"] = [str(item).replace("\\", "/") for item in args.material]
    if args.renderer_path:
        options["rendererPath"] = str(args.renderer_path)
    if args.relative_vertex_count is not None:
        options["relativeVertexCount"] = float(args.relative_vertex_count)
    return options


def parse_option(entry: str) -> tuple[str, Any]:
    if "=" not in entry:
        raise ValueError(f"Option must be key=value: {entry}")
    key, raw = entry.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Option key is empty: {entry}")
    raw = raw.strip()
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        return key, raw


def screenshot_stage_name(step_name: str) -> str:
    stage = step_name.split(".", 1)[-1].strip().lower()
    safe = "".join(char if char.isalnum() else "_" for char in stage).strip("_")
    return safe or "screenshot"


def validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = ensure_dict(payload.get("summary"))
    return {
        "ok": payload.get("schema") == "vrcforge.validation.v1" and ensure_dict(payload.get("gate")).get("status") != "blocked",
        "reportOk": bool(payload.get("ok", True)),
        "schema": payload.get("schema"),
        "severityCounts": summary.get("severityCounts"),
        "findingCount": summary.get("findingCount"),
        "gateStatus": summary.get("gateStatus") or ensure_dict(payload.get("gate")).get("status"),
    }


def delta_summary(payload: dict[str, Any], *, require_rollback: bool) -> dict[str, Any]:
    rollback = ensure_dict(payload.get("rollbackProof"))
    rollback_ok = not require_rollback or bool(rollback.get("matchesBeforeSeverityAndGate"))
    return {
        "ok": bool(payload.get("ok")) and rollback_ok,
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "severityDelta": payload.get("severityDelta"),
        "findingDelta": {
            "addedCount": ensure_dict(payload.get("findingDelta")).get("addedCount"),
            "removedCount": ensure_dict(payload.get("findingDelta")).get("removedCount"),
        },
        "rollbackProof": rollback,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
