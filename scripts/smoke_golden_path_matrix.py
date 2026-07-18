from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from smoke_external_agent_bridge import ensure_dict, ensure_list, read_text_file, redact_evidence, request_json, resolve_app_token_path  # noqa: E402


DEFAULT_BASE_URL = "http://127.0.0.1:8757"
SCHEMA = "vrcforge.golden_path_matrix.v1"


RequestFunc = Callable[[str, str, str, str, dict[str, Any] | None, bool, float], dict[str, Any]]
RunCommandFunc = Callable[..., subprocess.CompletedProcess[str]]
ListenerQueryFunc = Callable[[int], list[dict[str, Any]]]


def main() -> int:
    args = parse_args()
    smoke = GoldenPathMatrixSmoke(args)
    report = smoke.run()
    path = smoke.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the VRCForge 0.9 golden path smoke matrix.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--app-token-file", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--avatar-path", default="")
    parser.add_argument("--target-profile", default="pc_conservative")
    parser.add_argument("--include-quest", action="store_true", default=True)
    parser.add_argument("--no-quest", dest="include_quest", action="store_false")
    parser.add_argument("--outfit-package", default="")
    parser.add_argument("--outfit-target-folder", default="Assets/VRCForge/ImportedOutfits/GoldenPath")
    parser.add_argument("--vsk-package", default="")
    parser.add_argument("--release-manifest", default="dist/release/release-manifest.json")
    parser.add_argument("--include-cli", action="store_true")
    parser.add_argument("--include-external-agent", action="store_true")
    parser.add_argument("--include-live-writes", action="store_true")
    parser.add_argument("--include-vsk-import", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Treat skipped optional golden paths as failures.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--python-exe", default=sys.executable)

    parser.add_argument("--optimizer-tool", default="")
    parser.add_argument("--optimizer-option", action="append", default=[])
    parser.add_argument("--material", action="append", default=[])
    parser.add_argument("--renderer-path", default="")
    parser.add_argument("--relative-vertex-count", type=float, default=None)
    parser.add_argument("--capture-screenshots", action="store_true")

    parser.add_argument("--shader-renderer-path", default="")
    parser.add_argument("--shader-slot-index", type=int, default=None)
    parser.add_argument("--shader-semantic-property", default="")
    return parser.parse_args()


class GoldenPathMatrixSmoke:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        request_func: RequestFunc | None = None,
        run_command_func: RunCommandFunc | None = None,
    ) -> None:
        self.args = args
        self.base_url = str(args.base_url).rstrip("/")
        self.app_token_path = resolve_app_token_path(args.app_token_file)
        self.app_token = read_text_file(self.app_token_path).strip()
        self.request_func = request_func or request_json
        self.run_command_func = run_command_func or subprocess.run
        self.started_at = utc_now()
        self.run_id = f"golden-path-matrix-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.artifact_root = Path.cwd() / "artifacts" / "golden-path-matrix"
        self.paths: list[dict[str, Any]] = []
        self.bootstrap_payload: dict[str, Any] = {}
        self.runtime_version = ""
        self.unity_unavailable_reason = ""
        self.project_root = str(Path(args.project_root).expanduser().resolve()) if args.project_root else ""
        self.avatar_path = str(args.avatar_path or "")
        self.outfit_package = resolve_optional_path(args.outfit_package)
        self.vsk_package = resolve_optional_path(args.vsk_package)

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "ok": False,
            "schema": SCHEMA,
            "version": "",
            "releaseBinding": {},
            "startedAt": self.started_at,
            "baseUrl": self.base_url,
            "appTokenFile": str(self.app_token_path) if self.app_token_path else "",
            "appTokenConfigured": bool(self.app_token),
            "projectRoot": self.project_root,
            "avatarPath": self.avatar_path,
            "targetProfile": self.args.target_profile,
            "includeLiveWrites": bool(self.args.include_live_writes),
            "strict": bool(self.args.strict),
            "artifactRunId": self.run_id,
            "paths": self.paths,
            "summary": {},
        }
        try:
            self.install_doctor_provider_connect()
            self.scan_avatar_validation()
            self.face_material_edit_checkpoint_rollback()
            self.booth_outfit_import_validation_rollback()
            self.model_optimization_validation_rollback()
            self.external_agent_write_request_rollback()
            self.vsk_import_dry_run_cleanup()
            self.cli_doctor_readiness_checkpoint()
        except Exception as exc:  # noqa: BLE001 - always emit an evidence report.
            self.add_path(
                "matrix.unhandled_error",
                "Unhandled matrix error",
                status="failed",
                mode="safe",
                required=True,
                steps=[{"name": "matrix.error", "ok": False, "error": str(exc)}],
            )
        finally:
            report["projectRoot"] = self.project_root
            report["avatarPath"] = self.avatar_path
            report["version"] = self.runtime_version
            report["releaseBinding"] = load_release_binding(
                Path(str(getattr(self.args, "release_manifest", "") or "dist/release/release-manifest.json")),
                self.runtime_version,
                base_url=self.base_url,
                runtime_payload=self.bootstrap_payload,
            )
            report["finishedAt"] = utc_now()
            report["paths"] = self.paths
            report["summary"] = self.build_summary()
            report["ok"] = report["summary"]["failedCount"] == 0
        return redact_evidence(report)

    def install_doctor_provider_connect(self) -> None:
        steps: list[dict[str, Any]] = []
        try:
            select_step = self.select_project()
            if select_step:
                steps.append(select_step)
            bootstrap = self.request_app_json("GET", "/api/app/bootstrap")
            self.bootstrap_payload = bootstrap
            self.set_project_from_bootstrap(bootstrap)
            bootstrap_version = str(ensure_dict(bootstrap.get("app")).get("version") or bootstrap.get("version") or "").strip()
            self.runtime_version = bootstrap_version
            steps.append(
                {
                    "name": "runtime.bootstrap",
                    "ok": bool(bootstrap.get("ok", True)) and bool(bootstrap_version),
                    "version": bootstrap_version,
                    "selectedProject": self.project_root,
                }
            )
            components = ensure_dict(ensure_dict(bootstrap.get("health")).get("components"))
            unity_components = {
                key: value
                for key, value in components.items()
                if any(fragment in key.lower() for fragment in ("unity", "mcp", "vrcforge"))
            }
            self.remember_unity_unavailable(confirmed_unity_unavailable_from_components(components))
            doctor = self.request_app_json("GET", "/api/app/doctor")
            self.remember_unity_unavailable(confirmed_unity_unavailable_from_doctor(doctor))
            doctor_contract_error = doctor_hard_failure_reason(doctor, bootstrap_version)
            doctor_report_ok = bool(doctor.get("ok", True))
            doctor_accepted = not doctor_contract_error and (
                doctor_report_ok or self.safe_default_unity_unavailable()
            )
            steps.append(
                {
                    "name": "doctor.report",
                    "ok": doctor_accepted,
                    "reportOk": doctor_report_ok,
                    "schema": doctor.get("schema"),
                    "version": doctor.get("version"),
                    "status": doctor.get("status") or ensure_dict(doctor.get("summary")).get("status"),
                    "contractError": doctor_contract_error,
                    "unityComponents": unity_components,
                }
            )
            if self.safe_default_unity_unavailable() and all(step.get("ok") for step in steps):
                self.add_path(
                    "install_doctor_provider_connect",
                    "Install -> Doctor -> provider fallback -> Unity connect",
                    status="skipped",
                    mode="safe",
                    required=False,
                    steps=[
                        *steps,
                        {
                            "name": "unity.connect",
                            "ok": True,
                            "status": "skipped",
                            "reason": self.unity_unavailable_reason,
                        },
                    ],
                )
                return
            self.add_path(
                "install_doctor_provider_connect",
                "Install -> Doctor -> provider fallback -> Unity connect",
                status="passed" if all(step.get("ok") for step in steps) else "failed",
                mode="safe",
                required=True,
                steps=steps,
            )
        except Exception as exc:  # noqa: BLE001
            self.add_path(
                "install_doctor_provider_connect",
                "Install -> Doctor -> provider fallback -> Unity connect",
                status="failed",
                mode="safe",
                required=True,
                steps=[*steps, {"name": "install_doctor_provider_connect.error", "ok": False, "error": str(exc)}],
            )

    def select_project(self) -> dict[str, Any] | None:
        if not self.project_root:
            return None
        payload = self.request_func(
            self.base_url,
            "POST",
            "/api/state",
            self.app_token,
            {"projectPath": self.project_root},
            False,
            self.args.timeout,
        )
        selected = str(payload.get("selectedProjectPath") or payload.get("projectPath") or "")
        return {
            "name": "runtime.select_project",
            "ok": bool(payload) and normalize_path(selected) == normalize_path(self.project_root),
            "selectedProjectPath": selected,
        }

    def scan_avatar_validation(self) -> None:
        if not self.project_root:
            self.add_skipped(
                "scan_avatar_validation",
                "Scan avatar -> validation report",
                "No project root was provided or discovered from bootstrap.",
                required=False,
            )
            return
        if self.safe_default_unity_unavailable():
            self.add_skipped(
                "scan_avatar_validation",
                "Scan avatar -> validation report",
                self.unity_unavailable_reason,
                required=False,
            )
            return
        steps: list[dict[str, Any]] = []
        try:
            avatars = self.request_app_json("POST", "/api/app/avatars", {"projectPath": self.project_root})
            avatar_items = ensure_list(avatars.get("avatars"))
            if not self.avatar_path and len(avatar_items) == 1 and isinstance(avatar_items[0], dict):
                self.avatar_path = str(avatar_items[0].get("avatarPath") or "")
            steps.append(
                {
                    "name": "avatars.scan",
                    "ok": bool(avatars.get("ok", True)),
                    "avatarCount": avatars.get("avatarCount", len(avatar_items)),
                    "selectedAvatar": self.avatar_path,
                }
            )
            validation = self.request_app_json("POST", "/api/app/validation/report", self.validation_payload())
            steps.append({"name": "validation.report", **validation_summary(validation)})
            self.add_path(
                "scan_avatar_validation",
                "Scan avatar -> validation report",
                status="passed" if all(step.get("ok") for step in steps) else "failed",
                mode="safe",
                required=False,
                steps=steps,
            )
        except Exception as exc:  # noqa: BLE001
            if not self.args.include_live_writes and is_confirmed_unity_unavailable_http_error(exc, "/api/app/avatars"):
                self.remember_unity_unavailable(str(exc))
                self.add_skipped(
                    "scan_avatar_validation",
                    "Scan avatar -> validation report",
                    self.unity_unavailable_reason,
                    required=False,
                )
                return
            self.add_path(
                "scan_avatar_validation",
                "Scan avatar -> validation report",
                status="failed",
                mode="safe",
                required=False,
                steps=[*steps, {"name": "scan_avatar_validation.error", "ok": False, "error": str(exc)}],
            )

    def face_material_edit_checkpoint_rollback(self) -> None:
        if not self.args.include_live_writes:
            self.add_skipped(
                "face_material_edit_checkpoint_rollback",
                "Face/material edit -> checkpoint -> validation -> rollback",
                "Live Unity writes are disabled. Pass --include-live-writes plus shader target flags to run this path.",
                mode="live-write",
                required=False,
            )
            return
        if not self.project_root or not self.avatar_path:
            self.add_blocked(
                "face_material_edit_checkpoint_rollback",
                "Face/material edit -> checkpoint -> validation -> rollback",
                "Live material proof needs --project-root and --avatar-path.",
                mode="live-write",
            )
            return
        command = [
            self.args.python_exe,
            str(SCRIPTS_DIR / "smoke_shader_adapter_apply_rollback.py"),
            "--base-url",
            self.base_url,
            "--project-root",
            self.project_root,
            "--avatar-path",
            self.avatar_path,
            "--timeout",
            str(int(self.args.timeout)),
        ]
        if self.app_token_path:
            command += ["--app-token-file", str(self.app_token_path)]
        if self.args.shader_renderer_path:
            command += ["--renderer-path", str(self.args.shader_renderer_path)]
        if self.args.shader_slot_index is not None:
            command += ["--slot-index", str(int(self.args.shader_slot_index))]
        if self.args.shader_semantic_property:
            command += ["--semantic-property", str(self.args.shader_semantic_property)]
        if self.args.capture_screenshots:
            command.append("--capture-screenshots")
        self.add_subprocess_path(
            "face_material_edit_checkpoint_rollback",
            "Face/material edit -> checkpoint -> validation -> rollback",
            command,
            mode="live-write",
        )

    def booth_outfit_import_validation_rollback(self) -> None:
        package_path = self.outfit_package
        if not package_path:
            self.add_skipped(
                "booth_outfit_import_validation_rollback",
                "Booth ZIP/folder -> outfit import -> validation -> rollback",
                "No --outfit-package was provided.",
                required=False,
            )
            return
        steps: list[dict[str, Any]] = []
        try:
            inspect_payload = self.request_app_json("POST", "/api/app/outfit-packages/inspect", {"packagePath": package_path})
            steps.append(
                {
                    "name": "outfit.inspect",
                    "ok": bool(inspect_payload.get("ok")),
                    "schema": inspect_payload.get("schema"),
                    "summary": inspect_payload.get("summary"),
                }
            )
            plan_payload = self.request_app_json(
                "POST",
                "/api/app/outfit-imports/plan",
                {
                    "packagePath": package_path,
                    "projectPath": self.project_root,
                    "targetFolder": self.args.outfit_target_folder,
                },
            )
            plan = ensure_dict(plan_payload.get("plan"))
            steps.append(
                {
                    "name": "outfit.plan",
                    "ok": bool(plan_payload.get("ok")),
                    "readyToApply": plan.get("readyToApply"),
                    "requiresApproval": plan.get("requiresApproval"),
                    "requiresCheckpoint": plan.get("requiresCheckpoint"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.add_path(
                "booth_outfit_import_validation_rollback",
                "Booth ZIP/folder -> outfit import -> validation -> rollback",
                status="failed",
                mode="safe",
                required=False,
                steps=[*steps, {"name": "outfit.safe_preflight.error", "ok": False, "error": str(exc)}],
            )
            return
        if not self.args.include_live_writes:
            self.add_path(
                "booth_outfit_import_validation_rollback",
                "Booth ZIP/folder -> outfit import -> validation -> rollback",
                status="passed" if all(step.get("ok") for step in steps) else "failed",
                mode="safe",
                required=False,
                steps=[
                    *steps,
                    {
                        "name": "outfit.live_apply",
                        "ok": True,
                        "status": "skipped",
                        "reason": "Live import/rollback disabled. Pass --include-live-writes to call smoke_outfit_import_rollback.py.",
                    },
                ],
            )
            return
        command = [
            self.args.python_exe,
            str(SCRIPTS_DIR / "smoke_outfit_import_rollback.py"),
            package_path,
            "--base-url",
            self.base_url,
            "--project-root",
            self.project_root,
            "--target-folder",
            self.args.outfit_target_folder,
            "--timeout",
            str(int(self.args.timeout)),
        ]
        if self.app_token_path:
            command += ["--app-token-file", str(self.app_token_path)]
        self.add_subprocess_path(
            "booth_outfit_import_validation_rollback",
            "Booth ZIP/folder -> outfit import -> validation -> rollback",
            command,
            mode="live-write",
            prefix_steps=steps,
        )

    def model_optimization_validation_rollback(self) -> None:
        if not self.project_root:
            self.add_skipped(
                "model_optimization_validation_rollback",
                "Model optimization profile -> optimizer step -> validation -> rollback",
                "No project root was provided or discovered from bootstrap.",
                required=False,
            )
            return
        if self.safe_default_unity_unavailable():
            self.add_skipped(
                "model_optimization_validation_rollback",
                "Model optimization profile -> optimizer step -> validation -> rollback",
                self.unity_unavailable_reason,
                required=False,
            )
            return
        steps: list[dict[str, Any]] = []
        try:
            plan = self.request_app_json(
                "POST",
                "/api/app/optimization/plan",
                {
                    "projectPath": self.project_root,
                    "avatarPath": self.avatar_path,
                    "targetProfile": self.args.target_profile,
                    "includeQuest": bool(self.args.include_quest),
                },
            )
            steps.append(
                {
                    "name": "optimization.plan",
                    "ok": bool(plan.get("ok", True)) and plan.get("schema") == "vrcforge.optimization.v1",
                    "schema": plan.get("schema"),
                    "targetProfile": ensure_dict(plan.get("targetProfile")).get("id") or self.args.target_profile,
                    "recommendedStepCount": len(ensure_list(plan.get("recommendedSteps"))),
                }
            )
        except Exception as exc:  # noqa: BLE001
            if not self.args.include_live_writes and is_confirmed_unity_unavailable_http_error(exc, "/api/app/optimization/plan"):
                self.remember_unity_unavailable(str(exc))
                self.add_skipped(
                    "model_optimization_validation_rollback",
                    "Model optimization profile -> optimizer step -> validation -> rollback",
                    self.unity_unavailable_reason,
                    required=False,
                )
                return
            self.add_path(
                "model_optimization_validation_rollback",
                "Model optimization profile -> optimizer step -> validation -> rollback",
                status="failed",
                mode="safe",
                required=False,
                steps=[{"name": "optimization.plan.error", "ok": False, "error": str(exc)}],
            )
            return
        if not self.args.include_live_writes:
            self.add_path(
                "model_optimization_validation_rollback",
                "Model optimization profile -> optimizer step -> validation -> rollback",
                status="passed" if all(step.get("ok") for step in steps) else "failed",
                mode="safe",
                required=False,
                steps=[
                    *steps,
                    {
                        "name": "optimization.live_apply",
                        "ok": True,
                        "status": "skipped",
                        "reason": "Live optimizer apply/rollback disabled. Pass --include-live-writes and --optimizer-tool to call smoke_optimizer_apply_rollback.py.",
                    },
                ],
            )
            return
        if not self.args.optimizer_tool or not self.avatar_path:
            self.add_blocked(
                "model_optimization_validation_rollback",
                "Model optimization profile -> optimizer step -> validation -> rollback",
                "Live optimizer proof needs --optimizer-tool and --avatar-path.",
                mode="live-write",
                steps=steps,
            )
            return
        command = [
            self.args.python_exe,
            str(SCRIPTS_DIR / "smoke_optimizer_apply_rollback.py"),
            "--base-url",
            self.base_url,
            "--project-root",
            self.project_root,
            "--avatar-path",
            self.avatar_path,
            "--tool",
            self.args.optimizer_tool,
            "--target-profile",
            self.args.target_profile,
            "--timeout",
            str(int(self.args.timeout)),
        ]
        if self.app_token_path:
            command += ["--app-token-file", str(self.app_token_path)]
        for option in self.args.optimizer_option:
            command += ["--option", str(option)]
        for material in self.args.material:
            command += ["--material", str(material)]
        if self.args.renderer_path:
            command += ["--renderer-path", str(self.args.renderer_path)]
        if self.args.relative_vertex_count is not None:
            command += ["--relative-vertex-count", str(float(self.args.relative_vertex_count))]
        if self.args.capture_screenshots:
            command.append("--capture-screenshots")
        self.add_subprocess_path(
            "model_optimization_validation_rollback",
            "Model optimization profile -> optimizer step -> validation -> rollback",
            command,
            mode="live-write",
            prefix_steps=steps,
        )

    def external_agent_write_request_rollback(self) -> None:
        if not self.args.include_external_agent:
            self.add_skipped(
                "external_agent_write_request_rollback",
                "External agent read/plan/write-request -> approval -> rollback",
                "External-agent smoke is opt-in because it temporarily enables gateway state.",
                mode="gateway",
                required=False,
            )
            return
        command = [
            self.args.python_exe,
            str(SCRIPTS_DIR / "smoke_external_agent_bridge.py"),
            "--base-url",
            self.base_url,
            "--enable-gateway",
            "--timeout",
            str(int(self.args.timeout)),
        ]
        if self.app_token_path:
            command += ["--app-token-file", str(self.app_token_path)]
        if self.project_root:
            command += ["--project-root", self.project_root]
        if self.avatar_path:
            command += ["--avatar-path", self.avatar_path]
        if self.args.optimizer_tool:
            command += ["--optimizer-write-request", "--optimizer-tool", self.args.optimizer_tool, "--target-profile", self.args.target_profile]
        if self.args.include_live_writes:
            command.append("--live-write-rollback")
        self.add_subprocess_path(
            "external_agent_write_request_rollback",
            "External agent read/plan/write-request -> approval -> rollback",
            command,
            mode="gateway",
        )

    def vsk_import_dry_run_cleanup(self) -> None:
        package_path = self.vsk_package
        title = ".vsk import -> dry-run -> disable/uninstall"
        if not package_path:
            self.add_skipped(
                "vsk_import_dry_run_cleanup",
                title,
                "No --vsk-package was provided.",
                required=False,
            )
            return
        steps: list[dict[str, Any]] = []
        try:
            preflight = self.request_app_json("POST", "/api/app/skill-packages/preflight", {"packagePath": package_path})
            preview = ensure_dict(preflight.get("preview")) or preflight
            manifest = ensure_dict(preview.get("manifest"))
            preview_id = str(preview.get("id") or manifest.get("id") or "").strip()
            steps.append(
                {
                    "name": "vsk.preflight",
                    "ok": bool(preflight.get("ok") is True and preview_id),
                    "id": preview_id,
                    "packageName": preview.get("name") or manifest.get("name"),
                    "version": preview.get("version") or manifest.get("version"),
                    "riskLevel": preview.get("riskLevel") or preview.get("risk_level"),
                    "updateAction": preview.get("updateAction") or preview.get("update_action"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.add_path(
                "vsk_import_dry_run_cleanup",
                title,
                status="failed",
                mode="safe",
                required=False,
                steps=[{"name": "vsk.preflight.error", "ok": False, "error": str(exc)}],
            )
            return
        if not steps[0]["ok"]:
            self.add_path(
                "vsk_import_dry_run_cleanup",
                title,
                status="failed",
                mode="safe",
                required=bool(self.args.include_vsk_import),
                steps=steps,
            )
            return
        try:
            installed_before = self.request_app_json("GET", "/api/app/skill-packages", None)
            installed_before_items = installed_before.get("installed") if isinstance(installed_before.get("installed"), list) else []
            installed_before_ids = {
                str(item.get("id") or "").strip()
                for item in installed_before_items
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            if self.args.include_vsk_import and preview_id in installed_before_ids:
                steps.append(
                    {
                        "name": "vsk.preexisting_guard",
                        "ok": False,
                        "id": preview_id,
                        "error": "Refusing to overwrite or uninstall a pre-existing skill package.",
                    }
                )
                self.add_path(
                    "vsk_import_dry_run_cleanup",
                    title,
                    status="failed",
                    mode="local-write",
                    required=True,
                    steps=steps,
                )
                return
            dry_run = self.request_app_json(
                "POST",
                "/api/app/skill-packages/import",
                {"packagePath": package_path, "dryRun": True},
            )
            dry_preview = ensure_dict(dry_run.get("preview"))
            dry_policy = ensure_dict(dry_preview.get("dryRun"))
            installed_after = self.request_app_json("GET", "/api/app/skill-packages", None)
            registry_unchanged = skill_package_registry_projection(installed_before) == skill_package_registry_projection(
                installed_after
            )
            steps.append(
                {
                    "name": "vsk.dry_run",
                    "ok": bool(
                        dry_run.get("ok") is True
                        and dry_run.get("dryRun") is True
                        and dry_policy.get("willWrite") is False
                        and registry_unchanged
                    ),
                    "dryRun": dry_run.get("dryRun"),
                    "willWrite": dry_policy.get("willWrite"),
                    "registryUnchanged": registry_unchanged,
                }
            )
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": "vsk.dry_run.error", "ok": False, "error": str(exc)})
            self.add_path(
                "vsk_import_dry_run_cleanup",
                title,
                status="failed",
                mode="safe",
                required=bool(self.args.include_vsk_import),
                steps=steps,
            )
            return
        if not self.args.include_vsk_import:
            self.add_path(
                "vsk_import_dry_run_cleanup",
                title,
                status="passed" if all(step.get("ok") for step in steps) else "failed",
                mode="safe",
                required=False,
                steps=[
                    *steps,
                    {
                        "name": "vsk.import_cleanup",
                        "ok": True,
                        "status": "skipped",
                        "reason": "Local skill-store writes are disabled. Pass --include-vsk-import to import, disable, and uninstall.",
                    },
                ],
            )
            return
        cleanup_id = preview_id
        import_attempted = False
        try:
            if not all(step.get("ok") for step in steps):
                raise RuntimeError("Dry-run validation failed; refusing the real import.")
            import_attempted = True
            imported = self.request_app_json("POST", "/api/app/skill-packages/import", {"packagePath": package_path})
            import_result = ensure_dict(imported.get("imported"))
            installed = ensure_dict(import_result.get("registry_entry"))
            skill_package_id = str(installed.get("id") or "").strip()
            projected_skill = ensure_dict(imported.get("projectedSkill"))
            if skill_package_id:
                cleanup_id = skill_package_id
            steps.append(
                {
                    "name": "vsk.import",
                    "ok": bool(
                        imported.get("ok") is True
                        and import_result.get("changed") is True
                        and skill_package_id == preview_id
                        and str(projected_skill.get("name") or "").strip()
                    ),
                    "changed": import_result.get("changed"),
                    "installed": skill_package_id,
                    "projectedSkill": projected_skill.get("name"),
                }
            )
            if not steps[-1]["ok"]:
                raise RuntimeError("Imported package did not match the preflight identity or projected skill contract.")
            disabled = self.request_app_json(
                "PUT",
                f"/api/app/skill-packages/{skill_package_id}",
                {"enabled": False, "syncProjectedSkill": True},
            )
            disabled_entry = ensure_dict(ensure_dict(disabled.get("state")).get("registry_entry"))
            steps.append(
                {
                    "name": "vsk.disable",
                    "ok": bool(
                        disabled.get("ok") is True
                        and str(disabled_entry.get("id") or "").strip() == skill_package_id
                        and disabled_entry.get("enabled") is False
                    ),
                    "enabled": disabled_entry.get("enabled"),
                    "projectedSkill": disabled.get("projectedSkill"),
                }
            )
            if not steps[-1]["ok"]:
                raise RuntimeError("Imported package did not enter the disabled state.")
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": "vsk.lifecycle.error", "ok": False, "error": str(exc)})
        finally:
            if import_attempted:
                try:
                    uninstalled = self.request_app_json(
                        "DELETE",
                        f"/api/app/skill-packages/{cleanup_id}",
                        {"removeProjectedSkill": True},
                    )
                    installed_final = self.request_app_json("GET", "/api/app/skill-packages", None)
                    installed_final_items = installed_final.get("installed") if isinstance(installed_final.get("installed"), list) else []
                    remaining_ids = {
                        str(item.get("id") or "").strip()
                        for item in installed_final_items
                        if isinstance(item, dict)
                    }
                    removed_id = str(ensure_dict(uninstalled.get("uninstalled")).get("skill_id") or "").strip()
                    steps.append(
                        {
                            "name": "vsk.uninstall",
                            "ok": bool(
                                uninstalled.get("ok") is True
                                and removed_id == cleanup_id
                                and cleanup_id not in remaining_ids
                            ),
                            "removed": removed_id,
                            "absentAfterReadback": cleanup_id not in remaining_ids,
                            "projectedSkill": uninstalled.get("projectedSkill"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    steps.append({"name": "vsk.uninstall", "ok": False, "error": str(exc)})
        self.add_path(
            "vsk_import_dry_run_cleanup",
            title,
            status="passed" if all(step.get("ok") for step in steps) else "failed",
            mode="local-write",
            required=True,
            steps=steps,
        )

    def cli_doctor_readiness_checkpoint(self) -> None:
        if not self.args.include_cli:
            self.add_skipped(
                "cli_doctor_readiness_checkpoint",
                "CLI doctor/readiness/validation/checkpoint preview",
                "CLI smoke is opt-in. Pass --include-cli to invoke tools/vrcforge_cli.py.",
                mode="cli",
                required=False,
            )
            return
        base = self.cli_base_command()
        commands: list[list[str]] = [
            [*base, "doctor"],
            [*base, "unity", "status"],
        ]
        if self.project_root:
            commands.append(
                [
                    *base,
                    "validation",
                    "run",
                    "--project",
                    self.project_root,
                    "--avatar",
                    self.avatar_path,
                ]
            )
            commands.append(
                [
                    *base,
                    "build-test",
                    "readiness",
                    "--project",
                    self.project_root,
                    "--avatar",
                    self.avatar_path,
                ]
            )
            commands.append(
                [
                    *base,
                    "optimization",
                    "plan",
                    "--project",
                    self.project_root,
                    "--avatar",
                    self.avatar_path,
                    "--target-profile",
                    self.args.target_profile,
                ]
            )
            commands.append([*base, "checkpoint", "list", "--project", self.project_root, "--limit", "5"])
        steps: list[dict[str, Any]] = []
        for command in commands:
            step = self.run_command_step(command, timeout=self.args.timeout)
            steps.append(step)
            if "checkpoint" in command and "list" in command:
                checkpoint_id = first_checkpoint_id(ensure_dict(step.get("stdoutJson")))
                if checkpoint_id:
                    steps.append(self.run_command_step([*base, "checkpoint", "preview", checkpoint_id], timeout=self.args.timeout))
                else:
                    steps.append({"name": "vrcforge_cli.py", "ok": True, "status": "skipped", "reason": "No checkpoint id returned by CLI checkpoint list."})
        self.add_path(
            "cli_doctor_readiness_checkpoint",
            "CLI doctor/readiness/validation/checkpoint preview",
            status="passed" if all(step.get("ok") for step in steps) else "failed",
            mode="cli",
            required=False,
            steps=steps,
        )

    def cli_base_command(self) -> list[str]:
        command = [
            self.args.python_exe,
            str(REPO_ROOT / "tools" / "vrcforge_cli.py"),
            "--endpoint",
            self.base_url,
            "--timeout",
            str(float(self.args.timeout)),
        ]
        if self.app_token:
            command += ["--token", self.app_token]
        command.append("--json")
        return command

    def add_subprocess_path(
        self,
        path_id: str,
        title: str,
        command: Sequence[str],
        *,
        mode: str,
        prefix_steps: list[dict[str, Any]] | None = None,
    ) -> None:
        step = self.run_command_step(list(command), timeout=max(float(self.args.timeout), 30.0) + 30.0)
        steps = [*(prefix_steps or []), step]
        self.add_path(
            path_id,
            title,
            status="passed" if all(item.get("ok") for item in steps) else "failed",
            mode=mode,
            required=False,
            steps=steps,
        )

    def run_command_step(self, command: list[str], *, timeout: float) -> dict[str, Any]:
        try:
            completed = self.run_command_func(
                command,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return {"name": command_name(command), "ok": False, "command": safe_command(command), "error": str(exc)}
        output = parse_json_tail(completed.stdout)
        step = {
            "name": command_name(command),
            "ok": completed.returncode == 0 and bool(output.get("ok", True)),
            "command": safe_command(command),
            "exitCode": completed.returncode,
            "stdoutJson": output,
            "stdoutTail": (completed.stdout or "")[-2000:],
            "stderrTail": (completed.stderr or "")[-2000:],
        }
        rollback_audit = ensure_dict(output.get("rollbackCoverageAudit"))
        if rollback_audit:
            step["rollbackCoverageAudit"] = rollback_audit
            step["rollbackCoverageGateStatus"] = rollback_audit.get("gateStatus")
        return step

    def request_app_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.app_token:
            raise RuntimeError("App session token was not found. Start VRCForge or pass --app-token-file.")
        return self.request_func(self.base_url, method, path, self.app_token, payload, False, self.args.timeout)

    def validation_payload(self) -> dict[str, Any]:
        return {
            "projectPath": self.project_root,
            "avatarPath": self.avatar_path,
            "includeQuest": bool(self.args.include_quest),
            "includeReadiness": True,
            "maxErrors": 50,
        }

    def set_project_from_bootstrap(self, bootstrap: dict[str, Any]) -> None:
        if self.project_root:
            return
        health = ensure_dict(bootstrap.get("health"))
        state = ensure_dict(health.get("state"))
        selected = str(state.get("selected_project_path") or "")
        if not selected:
            selected = str(ensure_dict(bootstrap.get("projects")).get("selectedProjectPath") or "")
        if selected:
            self.project_root = selected

    def remember_unity_unavailable(self, reason: str) -> None:
        if reason and not self.unity_unavailable_reason:
            self.unity_unavailable_reason = reason

    def safe_default_unity_unavailable(self) -> bool:
        return not bool(self.args.include_live_writes) and bool(self.unity_unavailable_reason)

    def add_skipped(
        self,
        path_id: str,
        title: str,
        reason: str,
        *,
        mode: str = "safe",
        required: bool = False,
    ) -> None:
        self.add_path(path_id, title, status="skipped", mode=mode, required=required, steps=[{"name": f"{path_id}.skipped", "ok": True, "reason": reason}])

    def add_blocked(
        self,
        path_id: str,
        title: str,
        reason: str,
        *,
        mode: str = "safe",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        self.add_path(path_id, title, status="blocked", mode=mode, required=True, steps=[*(steps or []), {"name": f"{path_id}.blocked", "ok": False, "reason": reason}])

    def add_path(
        self,
        path_id: str,
        title: str,
        *,
        status: str,
        mode: str,
        required: bool,
        steps: list[dict[str, Any]],
    ) -> None:
        effective_ok = path_effective_ok(status, required=required, strict=bool(self.args.strict))
        if status == "passed":
            effective_ok = effective_ok and all(bool(step.get("ok")) for step in steps)
        if status == "failed":
            effective_ok = False
        self.paths.append(
            {
                "id": path_id,
                "title": title,
                "status": status,
                "ok": effective_ok,
                "mode": mode,
                "required": bool(required),
                "steps": redact_evidence(steps),
            }
        )

    def build_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.paths:
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        failed = [item["id"] for item in self.paths if not item.get("ok")]
        return {
            "status": "passed" if not failed else "failed",
            "pathCount": len(self.paths),
            "counts": counts,
            "failedCount": len(failed),
            "failedPaths": failed,
            "safeDefault": not bool(self.args.include_live_writes),
            "liveWritePathsEnabled": bool(self.args.include_live_writes),
            "skippedPaths": [item["id"] for item in self.paths if item.get("status") == "skipped"],
            "blockedPaths": [item["id"] for item in self.paths if item.get("status") == "blocked"],
        }

    def write_report(self, report: dict[str, Any]) -> Path:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        path = self.artifact_root / f"{self.run_id}.json"
        path.write_text(json.dumps(redact_evidence(report), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return path


def path_effective_ok(status: str, *, required: bool, strict: bool) -> bool:
    if status == "passed":
        return True
    if status == "failed":
        return False
    if status in {"skipped", "blocked"}:
        return not required and not strict
    return False


def skill_package_registry_projection(payload: dict[str, Any]) -> str:
    installed = payload.get("installed") if isinstance(payload.get("installed"), list) else []
    projection = [
        {
            "id": str(item.get("id") or ""),
            "version": str(item.get("version") or ""),
            "enabled": item.get("enabled"),
            "packageSha256": str(item.get("package_sha256") or item.get("packageSha256") or ""),
        }
        for item in installed
        if isinstance(item, dict)
    ]
    projection.sort(key=lambda item: (item["id"], item["version"], item["packageSha256"]))
    return json.dumps(projection, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def load_release_binding(
    manifest_path: Path,
    runtime_version: str,
    *,
    base_url: str = "",
    runtime_payload: dict[str, Any] | None = None,
    listener_query_func: ListenerQueryFunc | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    payload_name = f"VRCForge_Windows_x64_{runtime_version}.zip"
    payload_path = manifest_path.parent / payload_name
    runtime_provenance = build_runtime_provenance(
        base_url=base_url,
        runtime_payload=runtime_payload or {},
        payload_path=payload_path,
        listener_query_func=listener_query_func,
        platform_name=platform_name,
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {
            "version": runtime_version,
            "manifestCommit": "",
            "payloadZipSha256": "",
            "payloadMatchesManifest": False,
            "artifactLocationSafe": False,
            "runtimeProvenance": runtime_provenance,
        }
    manifest_version = str(manifest.get("version") or "")
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
    matches = [
        value
        for value in artifacts
        if isinstance(value, dict)
        and Path(str(value.get("name") or value.get("path") or "")).name == payload_name
    ]
    item = matches[0] if len(matches) == 1 else {}
    declared_name = str(item.get("name") or "")
    declared_path = str(item.get("path") or "")
    artifact_location_safe = bool(
        len(matches) == 1
        and declared_name == payload_name
        and (not declared_path or declared_path == payload_name)
    )
    expected_sha = str(item.get("sha256") or item.get("sha256sum") or "").strip().lower()
    current_sha = ""
    if payload_path.is_file():
        digest = hashlib.sha256()
        with payload_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        current_sha = digest.hexdigest()
    return {
        "version": manifest_version,
        "manifestCommit": str(manifest.get("commit") or "").strip().lower(),
        "payloadZipSha256": expected_sha,
        "artifactLocationSafe": artifact_location_safe,
        "runtimeProvenance": runtime_provenance,
        "payloadMatchesManifest": bool(
            manifest_version == runtime_version
            and artifact_location_safe
            and expected_sha
            and current_sha == expected_sha
        ),
    }


def build_runtime_provenance(
    *,
    base_url: str,
    runtime_payload: dict[str, Any],
    payload_path: Path,
    listener_query_func: ListenerQueryFunc | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    health = ensure_dict(runtime_payload.get("health")) or ensure_dict(runtime_payload)
    paths = ensure_dict(health.get("paths"))
    portable_mode = health.get("portableMode") is True
    result: dict[str, Any] = {
        "ok": False,
        "portableMode": portable_mode,
        "listenerUnique": False,
        "executableInsideProgramDir": False,
        "executableMatchesArchive": False,
        "executableName": "",
        "hash": "",
    }
    try:
        if not str(platform_name or sys.platform).lower().startswith("win"):
            return result
        program_dir = str(paths.get("programDir") or health.get("programDir") or "").strip()
        port = listener_port_from_base_url(base_url)
        if not portable_mode or not program_dir or port is None or not payload_path.is_file():
            return result
        listeners = (listener_query_func or query_windows_listener_processes)(port)
        result["listenerUnique"] = isinstance(listeners, list) and len(listeners) == 1
        if not result["listenerUnique"]:
            return result
        listener = listeners[0] if isinstance(listeners[0], dict) else {}
        if int(listener.get("processId") or 0) <= 0:
            return result
        executable_text = str(listener.get("executable") or "").strip()
        if not executable_text:
            return result
        executable_path = Path(executable_text)
        result["executableName"] = executable_text.replace("\\", "/").rsplit("/", 1)[-1]
        expected_path = Path(program_dir) / "backend" / "vrcforge_backend.exe"
        executable_normalized = normalized_resolved_path(executable_path)
        expected_normalized = normalized_resolved_path(expected_path)
        program_dir_normalized = normalized_resolved_path(Path(program_dir))
        result["executableInsideProgramDir"] = bool(
            executable_normalized
            and expected_normalized
            and program_dir_normalized
            and executable_normalized == expected_normalized
            and expected_normalized.startswith(f"{program_dir_normalized}/")
        )
        if executable_path.is_file():
            result["hash"] = sha256_path(executable_path)
        archive_hash = portable_backend_archive_sha256(payload_path)
        result["executableMatchesArchive"] = bool(result["hash"] and archive_hash and result["hash"] == archive_hash)
        result["ok"] = bool(
            result["portableMode"]
            and result["listenerUnique"]
            and result["executableInsideProgramDir"]
            and result["executableMatchesArchive"]
            and result["executableName"].casefold() == "vrcforge_backend.exe"
        )
    except Exception:  # noqa: BLE001 - provenance must fail closed without suppressing the report.
        result["ok"] = False
    return result


def listener_port_from_base_url(base_url: str) -> int | None:
    try:
        parsed = urlsplit(str(base_url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname.casefold()
        if host != "localhost":
            address = ipaddress.ip_address(host)
            mapped = getattr(address, "ipv4_mapped", None)
            if not address.is_loopback and not bool(mapped and mapped.is_loopback):
                return None
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return port if 1 <= int(port) <= 65535 else None
    except (TypeError, ValueError):
        return None


def query_windows_listener_processes(port: int) -> list[dict[str, Any]]:
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
            f"$connections = @(Get-NetTCPConnection -State Listen -LocalPort {int(port)} -ErrorAction Stop)",
            "$ownerIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)",
            "$rows = @()",
            "foreach ($ownerId in $ownerIds) {",
            "  $process = Get-Process -Id $ownerId -ErrorAction Stop",
            "  if ([string]::IsNullOrWhiteSpace([string]$process.Path)) { throw 'Listener executable path unavailable.' }",
            "  $rows += [pscustomobject]@{ processId = [int]$ownerId; executable = [string]$process.Path }",
            "}",
            "$rows | ConvertTo-Json -Compress",
        )
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15.0,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Windows listener provenance query failed.")
    raw = (completed.stdout or "").strip()
    decoded = json.loads(raw) if raw else []
    rows = decoded if isinstance(decoded, list) else [decoded]
    listeners: list[dict[str, Any]] = []
    seen_process_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Windows listener provenance query returned an invalid row.")
        process_id = int(row.get("processId") or 0)
        executable = str(row.get("executable") or "").strip()
        if process_id <= 0 or not executable:
            raise RuntimeError("Windows listener provenance query returned incomplete process data.")
        if process_id in seen_process_ids:
            continue
        seen_process_ids.add(process_id)
        listeners.append({"processId": process_id, "executable": executable})
    return listeners


def normalized_resolved_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False)).replace("\\", "/").rstrip("/").casefold()
    except (OSError, RuntimeError, ValueError):
        return ""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def portable_backend_archive_sha256(payload_path: Path) -> str:
    try:
        with zipfile.ZipFile(payload_path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and info.filename.replace("\\", "/").casefold() == "backend/vrcforge_backend.exe"
            ]
            if len(matches) != 1:
                return ""
            digest = hashlib.sha256()
            with archive.open(matches[0], "r") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            return digest.hexdigest()
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return ""


def confirmed_unity_unavailable_from_components(components: dict[str, Any]) -> str:
    bridge = ensure_dict(components.get("unityMcpBridgeReachable"))
    bridge_status = str(bridge.get("status") or "").lower()
    bridge_diagnostic = " ".join(str(bridge.get(key) or "") for key in ("message", "detail"))
    if bridge_status in {"warning", "error"} and explicitly_unavailable_diagnostic(bridge_diagnostic):
        return "Unity/MCP-dependent path skipped: bootstrap confirms the Unity MCP bridge is unavailable."

    instance = ensure_dict(components.get("unityMcpInstance"))
    instance_status = str(instance.get("status") or "").lower()
    instance_detail = ensure_dict(instance.get("detail"))
    if instance_status in {"warning", "error"} and (
        instance_detail.get("activeInstanceCount") == 0
        or "no unity instance" in str(instance.get("message") or "").lower()
        or "not registered" in str(instance.get("message") or "").lower()
    ):
        return "Unity/MCP-dependent path skipped: bootstrap confirms no Unity editor instance is registered."
    return ""


def confirmed_unity_unavailable_from_doctor(doctor: dict[str, Any]) -> str:
    for check in ensure_list(doctor.get("checks")):
        if not isinstance(check, dict):
            continue
        check_id = str(check.get("id") or "")
        status = str(check.get("status") or "").lower()
        diagnostic = " ".join(str(check.get(key) or "") for key in ("message", "whatFailed", "howToFix", "detail"))
        if check_id == "unity.mcp.bridge" and status in {"warning", "error"} and explicitly_unavailable_diagnostic(diagnostic):
            return "Unity/MCP-dependent path skipped: Doctor confirms the Unity MCP bridge is unavailable."
        if check_id == "unity.mcp.instance" and status in {"warning", "error"} and explicitly_unavailable_diagnostic(diagnostic):
            return "Unity/MCP-dependent path skipped: Doctor confirms no usable Unity editor instance."
    return ""


def explicitly_unavailable_diagnostic(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(
        marker in lowered
        for marker in (
            "not reachable",
            "not connected",
            "no unity instance",
            "no unity editor instance",
            "no usable unity",
            "not registered",
            "connection refused",
            "server is not ready",
            "bridge unavailable",
        )
    )


def doctor_hard_failure_reason(doctor: dict[str, Any], bootstrap_version: str) -> str:
    if doctor.get("schema") != "vrcforge.doctor.v1":
        return "Doctor returned an unexpected schema."
    doctor_version = str(doctor.get("version") or "").strip()
    if not bootstrap_version or not doctor_version:
        return "Bootstrap/Doctor version evidence is missing."
    if doctor_version != bootstrap_version:
        return f"Bootstrap/Doctor version mismatch: {bootstrap_version} != {doctor_version}."

    checks = [check for check in ensure_list(doctor.get("checks")) if isinstance(check, dict)]
    if doctor.get("ok") is False and not checks:
        return "Doctor returned ok=false without classified checks."
    for check in checks:
        check_id = str(check.get("id") or "")
        status = str(check.get("status") or "").lower()
        if status == "ok":
            continue
        diagnostic = " ".join(str(check.get(key) or "") for key in ("message", "whatFailed", "whyItMatters", "howToFix")).lower()
        if any(marker in diagnostic for marker in ("version mismatch", "incompatible version", "unsupported version")):
            return f"Doctor reported a version error in {check_id or 'an unknown check'}."
        if check_id == "doctor.degraded":
            return "Doctor diagnostics degraded before Unity availability could be assessed safely."
        if status == "error" and not check_id.startswith(("unity.", "package.")):
            return f"Doctor reported a non-Unity error in {check_id or 'an unknown check'}."
    return ""


def is_confirmed_unity_unavailable_http_error(exc: Exception, endpoint: str) -> bool:
    text = str(exc)
    if not text.lower().startswith(f"http 503 from {endpoint}:".lower()):
        return False
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "unity mcp server is not ready",
            "no unity instances connected",
            "unity bridge unavailable",
            "connection refused",
        )
    )


def validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = ensure_dict(payload.get("summary"))
    gate = ensure_dict(payload.get("gate"))
    gate_status = summary.get("gateStatus") or gate.get("status")
    report_ok = payload.get("ok") is True
    return {
        "ok": payload.get("schema") == "vrcforge.validation.v1" and report_ok and gate_status == "pass",
        "reportOk": report_ok,
        "schema": payload.get("schema"),
        "severityCounts": summary.get("severityCounts"),
        "findingCount": summary.get("findingCount"),
        "gateStatus": gate_status,
    }


def parse_json_tail(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    for start in range(len(stripped)):
        if stripped[start] != "{":
            continue
        try:
            parsed = json.loads(stripped[start:])
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else {}
    return {}


def command_name(command: Sequence[str]) -> str:
    script = next((Path(part).name for part in command if str(part).endswith(".py")), "")
    return script or Path(command[0]).name if command else "command"


def resolve_optional_path(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())


def normalize_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/").casefold()


def first_checkpoint_id(payload: dict[str, Any]) -> str:
    candidates = ensure_list(payload.get("checkpoints"))
    if not candidates:
        candidates = ensure_list(ensure_dict(payload.get("result")).get("checkpoints"))
    for item in candidates:
        if not isinstance(item, dict):
            continue
        checkpoint_id = str(item.get("id") or item.get("checkpointId") or "").strip()
        if checkpoint_id:
            return checkpoint_id
    return ""


def safe_command(command: Sequence[str]) -> list[str]:
    parts = [str(part) for part in command]
    redacted: list[str] = []
    redact_next = False
    for part in parts:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        redacted.append(part)
        if part in {"--token", "--app-token", "--gateway-token"}:
            redact_next = True
    return redacted


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
