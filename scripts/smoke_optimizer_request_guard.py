from __future__ import annotations

import argparse
import json
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
    smoke = OptimizerRequestGuardSmoke(args)
    report = smoke.run()
    path = smoke.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test optimizer apply-request guard without applying optimizer writes.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--app-token-file", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--avatar-path", required=True)
    parser.add_argument("--tool", default="optimization.lac.apply-request")
    parser.add_argument("--target-profile", default="pc_conservative")
    parser.add_argument("--mode", action="append", default=[], help="Execution mode to test. Defaults to approval and auto.")
    parser.add_argument("--option", action="append", default=[], help="Optimizer option as key=value. JSON values are accepted.")
    parser.add_argument("--material", action="append", default=[], help="Append a TexTransTool atlas target material path under Assets/.")
    parser.add_argument("--renderer-path", default="", help="Meshia renderer GameObject path for conservative simplify setup.")
    parser.add_argument("--relative-vertex-count", type=float, default=None, help="Meshia relative vertex target in the stable 0.75..1.0 range.")
    parser.add_argument("--install-missing-dependencies", action="store_true")
    parser.add_argument("--include-prerelease", action="store_true")
    parser.add_argument("--expect-packaged", action="store_true")
    parser.add_argument("--timeout", type=float, default=90.0)
    return parser.parse_args()


class OptimizerRequestGuardSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_url = str(args.base_url).rstrip("/")
        self.app_token_path = resolve_app_token_path(args.app_token_file)
        self.app_token = read_text_file(self.app_token_path).strip()
        self.project_root = Path(args.project_root).expanduser().resolve() if args.project_root else None
        self.previous_permission = ""
        self.steps: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.optimizer_request_guard_smoke.v1",
            "startedAt": utc_now(),
            "baseUrl": self.base_url,
            "appTokenFile": str(self.app_token_path) if self.app_token_path else "",
            "appTokenConfigured": bool(self.app_token),
            "projectRoot": str(self.project_root) if self.project_root else "",
            "avatarPath": self.args.avatar_path,
            "tool": self.args.tool,
            "steps": self.steps,
            "summary": {},
        }
        try:
            self.step("runtime.bootstrap", self.bootstrap())
            modes = self.args.mode or ["approval", "auto"]
            for mode in modes:
                self.run_mode(mode)
            report["ok"] = all(bool(step.get("ok")) for step in self.steps)
        except Exception as exc:  # noqa: BLE001 - smoke should always emit evidence.
            self.step("smoke.error", {"ok": False, "error": str(exc)})
            report["ok"] = False
        finally:
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
        permission = ensure_dict(payload.get("permission"))
        self.previous_permission = str(permission.get("executionMode") or "")
        if self.project_root is None:
            selected = str(ensure_dict(ensure_dict(payload.get("health")).get("state")).get("selected_project_path") or "")
            if not selected:
                selected = str(ensure_dict(payload.get("projects")).get("selectedProjectPath") or "")
            if not selected:
                raise RuntimeError("--project-root is required when VRCForge has no selected Unity project.")
            self.project_root = Path(selected).expanduser().resolve()
        health = ensure_dict(payload.get("health"))
        paths = ensure_dict(health.get("paths"))
        program_dir = str(paths.get("programDir") or health.get("programDir") or "")
        packaged_ok = "dist" in program_dir.replace("\\", "/").lower() and "vrcforge_windows_x64" in program_dir.replace("\\", "/").lower()
        return {
            "ok": bool(payload.get("ok")) and (packaged_ok or not self.args.expect_packaged),
            "version": payload.get("version"),
            "projectRoot": str(self.project_root),
            "programDir": program_dir,
            "expectPackaged": bool(self.args.expect_packaged),
            "packagedDetected": packaged_ok,
            "previousPermission": self.previous_permission,
        }

    def run_mode(self, mode: str) -> None:
        mode = str(mode or "").strip()
        if mode not in {"approval", "auto", "roslyn_full_auto"}:
            raise RuntimeError(f"Unsupported execution mode for smoke: {mode}")
        updated = self.request_app_json("POST", "/api/app/permission", {"execution_mode": mode})
        permission = ensure_dict(updated.get("permission"))
        self.step(
            f"permission.set_{mode}",
            {
                "ok": permission.get("executionMode") == mode,
                "executionMode": permission.get("executionMode"),
                "autoApprove": permission.get("autoApprove"),
            },
        )

        request_payload = self.request_app_json("POST", "/api/app/optimization/apply-request", self.apply_request_payload())
        approval = ensure_dict(request_payload.get("approval"))
        approval_id = str(approval.get("id") or "")
        self.step(
            f"optimizer.request_{mode}",
            {
                "ok": bool(approval_id)
                and request_payload.get("status") == "pending"
                and approval.get("status") == "pending"
                and approval.get("requiresExplicitApproval") is True
                and approval.get("autoApprovalBlocked") is True
                and request_payload.get("autoApproved") is not True,
                "approvalId": approval_id,
                "requestStatus": request_payload.get("status"),
                "approvalStatus": approval.get("status"),
                "targetTool": approval.get("targetTool"),
                "requiresExplicitApproval": approval.get("requiresExplicitApproval"),
                "autoApprovalBlocked": approval.get("autoApprovalBlocked"),
                "autoApproved": request_payload.get("autoApproved"),
                "explicitApprovalReason": approval.get("explicitApprovalReason"),
                "error": request_payload.get("error"),
            },
        )
        if approval_id:
            rejected = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/reject", {})
            rejected_approval = ensure_dict(rejected.get("approval"))
            self.step(
                f"optimizer.reject_{mode}",
                {
                    "ok": bool(rejected.get("ok")) and rejected_approval.get("status") == "rejected",
                    "approvalId": approval_id,
                    "status": rejected_approval.get("status"),
                },
            )

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

    def restore_permission_mode(self) -> None:
        if not self.previous_permission:
            return
        try:
            restored = self.request_app_json("POST", "/api/app/permission", {"execution_mode": self.previous_permission})
            permission = ensure_dict(restored.get("permission"))
            self.step("permission.restore", {"ok": permission.get("executionMode") == self.previous_permission, "restoredPermission": permission.get("executionMode")})
        except Exception as exc:  # noqa: BLE001
            self.step("permission.restore", {"ok": False, "targetPermission": self.previous_permission, "error": str(exc)})

    def request_app_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.app_token:
            raise RuntimeError("App session token was not found.")
        return request_json(self.base_url, method, path, self.app_token, payload, False, self.args.timeout)

    def step(self, name: str, payload: dict[str, Any]) -> None:
        self.steps.append({"name": name, **redact_evidence(payload)})

    def write_report(self, report: dict[str, Any]) -> Path:
        root = Path.cwd() / "artifacts" / "optimizer-request-guard-smoke"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"optimizer-request-guard-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(redact_evidence(report), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def build_summary(self, ok: bool) -> dict[str, Any]:
        return {
            "status": "passed" if ok else "failed",
            "tool": self.args.tool,
            "testedModes": self.args.mode or ["approval", "auto"],
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
