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
    smoke = OutfitImportRollbackSmoke(args)
    report = smoke.run()
    path = smoke.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test VRCForge UnityPackage import through approval, checkpoint, validation, and rollback.")
    parser.add_argument("package_path")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--app-token-file", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--target-folder", default="Assets/VRCForge/ImportedOutfits/Smoke")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-expected-assets", type=int, default=120)
    return parser.parse_args()


class OutfitImportRollbackSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_url = str(args.base_url).rstrip("/")
        self.app_token_path = resolve_app_token_path(args.app_token_file)
        self.app_token = read_text_file(self.app_token_path).strip()
        self.package_path = Path(args.package_path).expanduser().resolve()
        self.project_root = Path(args.project_root).expanduser().resolve() if args.project_root else None
        self.steps: list[dict[str, Any]] = []
        self.checkpoint_id = ""
        self.rollback_done = False
        self.expected_assets: list[str] = []
        self.asset_state_before: dict[str, bool] = {}
        self.previous_permission = ""

    def run(self) -> dict[str, Any]:
        started = utc_now()
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.outfit_import_rollback_smoke.v1",
            "startedAt": started,
            "baseUrl": self.base_url,
            "appTokenFile": str(self.app_token_path) if self.app_token_path else "",
            "appTokenConfigured": bool(self.app_token),
            "packagePath": str(self.package_path),
            "projectRoot": str(self.project_root) if self.project_root else "",
            "steps": self.steps,
            "summary": {},
        }
        try:
            self.step("runtime.bootstrap", self.bootstrap())
            self.step("permission.force_approval", self.force_approval_mode())
            self.step("input.package", self.check_package())
            inspect_payload = self.request_app_json("POST", "/api/app/outfit-packages/inspect", {"packagePath": str(self.package_path)})
            self.step("package.inspect", {"ok": bool(inspect_payload.get("ok")), "schema": inspect_payload.get("schema"), "summary": inspect_payload.get("summary")})
            plan_payload = self.request_app_json(
                "POST",
                "/api/app/outfit-imports/plan",
                {
                    "packagePath": str(self.package_path),
                    "projectPath": str(self.project_root),
                    "targetFolder": str(self.args.target_folder),
                },
            )
            plan = ensure_dict(plan_payload.get("plan"))
            dependency_preflight = ensure_dict(plan.get("dependencyPreflight") or plan_payload.get("dependencyPreflight"))
            package_order = ensure_dict(dependency_preflight.get("packageOrder"))
            import_queue = ensure_list(package_order.get("importQueue") or ensure_dict(plan.get("source")).get("importQueue"))
            skipped_installed_support = [
                item
                for item in ensure_list(package_order.get("skippedInstalledSupportPackages") or package_order.get("skippedPackages"))
                if isinstance(item, dict) and item.get("skippedBecause") == "installed_dependency"
            ]
            self.expected_assets = [str(item) for item in ensure_list(plan.get("expectedAssetPaths"))[: max(1, int(self.args.max_expected_assets))]]
            self.asset_state_before = self.read_asset_state(self.expected_assets)
            self.step(
                "import.plan",
                {
                    "ok": bool(plan_payload.get("ok")) and bool(plan.get("readyToApply")),
                    "kind": plan.get("kind"),
                    "readyToApply": plan.get("readyToApply"),
                    "requiresApproval": plan.get("requiresApproval"),
                    "requiresCheckpoint": plan.get("requiresCheckpoint"),
                    "rollbackProofRequired": plan.get("rollbackProofRequired"),
                    "importQueue": [
                        {
                            "order": item.get("order"),
                            "role": item.get("role"),
                            "path": item.get("path") or item.get("actualPackagePath"),
                            "sourceType": item.get("sourceType"),
                        }
                        for item in import_queue
                        if isinstance(item, dict)
                    ],
                    "skippedInstalledSupportPackages": [
                        {
                            "dependencyId": item.get("dependencyId"),
                            "dependencyLabel": item.get("dependencyLabel"),
                            "path": item.get("path") or item.get("actualPackagePath"),
                            "reason": item.get("skipReason") or item.get("reason"),
                        }
                        for item in skipped_installed_support
                        if isinstance(item, dict)
                    ],
                    "expectedAssetCount": len(self.expected_assets),
                },
            )
            request_payload = self.request_app_json(
                "POST",
                "/api/app/outfit-imports/request",
                {
                    "packagePath": str(self.package_path),
                    "projectPath": str(self.project_root),
                    "targetFolder": str(self.args.target_folder),
                },
            )
            approval = ensure_dict(request_payload.get("approval"))
            approval_id = str(approval.get("id") or "")
            self.step("import.request", {"ok": bool(approval_id) and approval.get("status") == "pending", "approvalId": approval_id, "targetTool": approval.get("targetTool"), "status": approval.get("status")})
            if not approval_id:
                raise RuntimeError("Import request did not create a pending approval.")

            applied = self.request_app_json("POST", f"/api/app/agent/approvals/{approval_id}/approve", {})
            execution = ensure_dict(applied.get("execution"))
            checkpoint = ensure_dict(execution.get("checkpoint") or applied.get("checkpoint") or ensure_dict(applied.get("approval")).get("checkpoint"))
            checkpoint_id = str(checkpoint.get("id") or "")
            checkpoint_usable = bool(checkpoint.get("ok")) and str(checkpoint.get("status") or "") == "ready" and bool(checkpoint_id)
            if checkpoint_usable:
                self.checkpoint_id = checkpoint_id
            self.step(
                "import.approve_apply_checkpoint",
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
                raise RuntimeError("Approved import did not create a usable checkpoint.")

            changed = self.request_app_json("POST", f"/api/app/checkpoints/{self.checkpoint_id}/preview", {})
            changed_files = ensure_list(changed.get("changedFiles"))
            state_after_import = self.read_asset_state(self.expected_assets)
            self.step(
                "import.verify_changed",
                {
                    "ok": bool(changed.get("ok")) and (bool(changed_files) or any(state_after_import.get(path) != self.asset_state_before.get(path) for path in self.expected_assets)),
                    "changedFileCount": len(changed_files),
                    "expectedAssetStateChanges": sum(1 for path in self.expected_assets if state_after_import.get(path) != self.asset_state_before.get(path)),
                },
            )

            validation_after = self.request_app_json("POST", "/api/app/validation/report", {"projectPath": str(self.project_root), "includeQuest": False, "maxErrors": 20})
            self.step("validation.after_import", validation_summary(validation_after))

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

            self.step("rollback.verify_asset_state", self.verify_asset_state_restored())
            preview_after_restore = self.request_app_json("POST", f"/api/app/checkpoints/{self.checkpoint_id}/preview", {})
            self.step("rollback.verify_checkpoint_clean", {"ok": bool(preview_after_restore.get("ok")) and not bool(preview_after_restore.get("changedFiles")), "changedFileCount": len(ensure_list(preview_after_restore.get("changedFiles")))})
            validation_restore = self.request_app_json("POST", "/api/app/validation/report", {"projectPath": str(self.project_root), "includeQuest": False, "maxErrors": 20})
            self.step("validation.after_rollback", validation_summary(validation_restore))
            report["ok"] = all(bool(step.get("ok")) for step in self.steps)
        except Exception as exc:  # noqa: BLE001 - always emit evidence and try rollback below.
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
        return {
            "ok": bool(payload.get("ok")),
            "version": payload.get("version"),
            "projectRoot": str(self.project_root),
        }

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

    def check_package(self) -> dict[str, Any]:
        return {"ok": self.package_path.is_file(), "name": self.package_path.name, "bytes": self.package_path.stat().st_size if self.package_path.is_file() else 0}

    def read_asset_state(self, asset_paths: list[str]) -> dict[str, bool]:
        if self.project_root is None:
            return {}
        state: dict[str, bool] = {}
        root = self.project_root.resolve()
        for asset_path in asset_paths:
            normalized = asset_path.replace("\\", "/").strip("/")
            try:
                target = (root / normalized).resolve()
                if not target.is_relative_to(root):
                    continue
            except (OSError, ValueError):
                continue
            state[normalized] = target.exists()
        return state

    def verify_asset_state_restored(self) -> dict[str, Any]:
        after = self.read_asset_state(self.expected_assets)
        mismatches = [
            path
            for path in sorted(set(self.asset_state_before) | set(after))
            if bool(self.asset_state_before.get(path)) != bool(after.get(path))
        ]
        return {
            "ok": not mismatches,
            "checkedAssetCount": len(after),
            "mismatchCount": len(mismatches),
            "mismatches": mismatches[:20],
        }

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
        root = Path.cwd() / "artifacts" / "outfit-import-smoke"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"outfit-import-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(redact_evidence(report), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def build_summary(self, ok: bool) -> dict[str, Any]:
        return {
            "status": "passed" if ok else "failed",
            "checkpointId": self.checkpoint_id,
            "rollbackDone": self.rollback_done,
            "expectedAssetCount": len(self.expected_assets),
            "failedSteps": [step["name"] for step in self.steps if not step.get("ok")],
        }


def validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = ensure_dict(payload.get("summary"))
    return {
        "ok": payload.get("schema") == "vrcforge.validation.v1",
        "reportOk": bool(payload.get("ok", True)),
        "schema": payload.get("schema"),
        "severityCounts": summary.get("severityCounts"),
        "findingCount": summary.get("findingCount"),
        "gateStatus": summary.get("gateStatus"),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
