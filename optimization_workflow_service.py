from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


class CreateApplyRequestPort(Protocol):
    def __call__(
        self,
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
    ) -> dict[str, Any]: ...


class OptimizerProofPort(Protocol):
    def list(self, limit: int = 10) -> dict[str, Any]: ...

    def read(self, run_id: str) -> dict[str, Any]: ...

    def screenshot_path(self, run_id: str, stage: str) -> Path: ...


@dataclass(frozen=True)
class OptimizationWorkflowPorts:
    selected_project_path: Callable[[], str]
    build_validation_report: Callable[[dict[str, Any]], dict[str, Any]]
    build_report: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    normalize_tool_name: Callable[[str], str]
    build_tool_result: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
    build_apply_preview: Callable[[dict[str, Any]], dict[str, Any]]
    build_validation_delta: Callable[[dict[str, Any]], dict[str, Any]]
    create_apply_request: CreateApplyRequestPort
    proofs: OptimizerProofPort
    parameter_bit_packing_tool: str


class OptimizationWorkflowService:
    """Own optimizer report, preview/request, validation-delta, and proof entrypoints.

    The owner has no Unity writer, process, transport, auth token, checkpoint,
    approval store, or artifact root of its own. Those capabilities stay behind
    the frozen app ports supplied at composition time.
    """

    def __init__(self, ports: OptimizationWorkflowPorts) -> None:
        self._ports = ports

    def _build_validation_context(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return self._ports.build_validation_report(
            {
                "avatarPath": str(
                    params.get("avatar_path") or params.get("avatarPath") or ""
                ).strip(),
                "projectPath": str(
                    params.get("project_path")
                    or params.get("projectPath")
                    or self._ports.selected_project_path()
                    or ""
                ).strip(),
                "includeQuest": bool(
                    params.get("include_quest", params.get("includeQuest", True))
                ),
                "includeSources": True,
                "includeReadiness": True,
                "gateBuild": False,
                "maxErrors": int(
                    params.get("max_errors") or params.get("maxErrors") or 50
                ),
            }
        )

    def build_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        normalized = params or {}
        validation = self._build_validation_context(normalized)
        return self._ports.build_report(normalized, validation)

    def build_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        normalized = params or {}
        external_name = self._ports.normalize_tool_name(tool_name)
        if external_name in {"optimization.target.profile", "optimization.dependency.doctor"}:
            validation: dict[str, Any] = {}
        else:
            validation = self._build_validation_context(normalized)
        return self._ports.build_tool_result(external_name, normalized, validation)

    def build_apply_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._ports.build_apply_preview(params or {})

    def request_apply(
        self,
        params: dict[str, Any],
        agent_name: str = "external-agent",
    ) -> dict[str, Any]:
        normalized = params or {}
        preview = self.build_apply_preview(normalized)
        install_missing = bool(
            normalized.get("installMissingDependencies")
            or normalized.get("install_missing_dependencies")
        )
        dependency = _ensure_dict(preview.get("dependency"))
        dependency_status = str(dependency.get("status") or "unknown")
        package_ids = [
            str(item)
            for item in dependency.get("packageIds") or []
            if str(item or "").strip()
        ]
        if (
            preview.get("blockedReasons")
            and install_missing
            and dependency_status == "missing"
            and package_ids
        ):
            install_plan = _ensure_dict(preview.get("dependencyInstallPlan"))
            if not install_plan.get("canExecuteCommandInstall"):
                return {
                    "ok": False,
                    "status": "blocked",
                    "error": (
                        "Dependency is missing and no supported package-manager CLI "
                        "is available for a supervised install request."
                    ),
                    "preview": preview,
                    "installPlan": install_plan,
                }
            return self._ports.create_apply_request(
                {
                    "target_tool": "vrcforge_install_vpm_package",
                    "arguments": {
                        "projectPath": _ensure_dict(preview.get("applyArguments")).get("projectPath"),
                        "packageId": package_ids[0],
                        "repository": install_plan.get("repository")
                        or dependency.get("vpmRepository")
                        or "",
                        "includePrerelease": bool(
                            normalized.get("includePrerelease")
                            or normalized.get("include_prerelease")
                            or normalized.get("prerelease")
                        ),
                    },
                    "reason": (
                        f"Install dependency for {preview['externalName']} before "
                        "optimizer configuration."
                    ),
                    "preview": install_plan,
                    "agent_name": agent_name,
                    "requires_explicit_approval": True,
                    "explicit_approval_reason": (
                        "Optimizer dependency install requests require explicit user "
                        "approval even when global auto mode is enabled."
                    ),
                },
                internal_wrapper=True,
            )
        if not preview.get("readyToRequest"):
            return {
                "ok": False,
                "status": "blocked",
                "preview": preview,
                "error": "; ".join(preview.get("blockedReasons") or []),
            }
        apply_arguments = _ensure_dict(preview.get("applyArguments"))
        return self._ports.create_apply_request(
            {
                "target_tool": str(preview["targetTool"]),
                "arguments": preview["applyArguments"],
                "reason": (
                    f"Request supervised optimizer configuration for "
                    f"{preview['externalName']}."
                ),
                "preview": preview,
                "agent_name": agent_name,
                "requires_explicit_approval": True,
                "never_auto_approve": (
                    str(apply_arguments.get("toolName") or "")
                    == self._ports.parameter_bit_packing_tool
                ),
                "explicit_approval_reason": (
                    "Optimizer apply requests require explicit user approval even "
                    "when global auto mode is enabled."
                ),
            },
            internal_wrapper=True,
        )

    def build_validation_delta(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._ports.build_validation_delta(params or {})

    def list_proofs(self, limit: int = 10) -> dict[str, Any]:
        return self._ports.proofs.list(limit)

    def read_proof(self, run_id: str) -> dict[str, Any]:
        return self._ports.proofs.read(run_id)

    def proof_screenshot_path(self, run_id: str, stage: str) -> Path:
        return self._ports.proofs.screenshot_path(run_id, stage)


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class OptimizerProofStorePorts:
    artifact_root: Path
    to_artifact_url: Callable[[str], str]
    to_runtime_artifact_url: Callable[[str], str]


class OptimizerProofStore:
    """Read-only owner for optimizer package-gate reports and screenshots."""

    def __init__(self, ports: OptimizerProofStorePorts) -> None:
        self._ports = ports

    def _root(self) -> Path:
        return self._ports.artifact_root / "optimizer-apply-smoke"

    @staticmethod
    def _run_id(value: str) -> str:
        run_id = str(value or "").strip()
        if run_id.endswith(".json"):
            run_id = run_id[:-5]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}", run_id):
            raise ValueError("Invalid optimizer proof run id.")
        return run_id

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _path(self, run_id: str) -> Path:
        root = self._root().resolve()
        path = (root / f"{self._run_id(run_id)}.json").resolve()
        if not self._is_under(path, root):
            raise ValueError("Invalid optimizer proof path.")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _read_file(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Optimizer proof is not valid JSON: {path.name}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"Optimizer proof JSON must be an object: {path.name}"
            )
        return payload

    @staticmethod
    def _steps(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
        steps = report.get("steps")
        if not isinstance(steps, list):
            return {}
        mapped: dict[str, dict[str, Any]] = {}
        for step in steps:
            if isinstance(step, dict):
                name = str(step.get("name") or "")
                if name:
                    mapped[name] = step
        return mapped

    def _visual_summary(self, report: dict[str, Any]) -> dict[str, Any]:
        visual = _ensure_dict(report.get("visualRegression"))
        screenshots = _ensure_dict(visual.get("screenshots"))
        summarized_screenshots: dict[str, dict[str, Any]] = {}
        for stage, raw in screenshots.items():
            stage_name = str(stage or "")
            entry = _ensure_dict(raw)
            summarized = {
                "stage": stage_name,
                "captured": bool(entry.get("captured")),
                "artifactOk": bool(entry.get("artifactOk")),
                "exists": bool(entry.get("exists")),
                "size": entry.get("size"),
                "sha256": entry.get("sha256"),
                "warning": entry.get("warning") or entry.get("error"),
            }
            image_value = str(entry.get("artifactImagePath") or "").strip()
            image_path = Path(image_value) if image_value else None
            if (
                image_path
                and image_path.exists()
                and self._is_under(image_path, self._ports.artifact_root)
            ):
                summarized["imageUrl"] = self._ports.to_artifact_url(
                    str(image_path)
                ) or self._ports.to_runtime_artifact_url(str(image_path))
            summarized_screenshots[stage_name] = summarized
        return {
            "schema": visual.get("schema"),
            "status": visual.get("status") or "unavailable",
            "proofPassed": bool(visual.get("proofPassed")),
            "requiresHumanReview": bool(visual.get("requiresHumanReview")),
            "scoring": _ensure_dict(visual.get("scoring"))
            or {"mode": "not-run"},
            "screenshots": summarized_screenshots,
        }

    def _summarize(
        self,
        path: Path,
        report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = report or self._read_file(path)
        summary = _ensure_dict(payload.get("summary"))
        steps = self._steps(payload)
        delta = steps.get("validation.delta_after_rollback") or steps.get(
            "validation.delta_after_apply"
        ) or {}
        profile_diff = _ensure_dict(delta.get("profileDiff"))
        parameter_delta = _ensure_dict(delta.get("parameterBudgetDelta"))
        checkpoint_step = _ensure_dict(
            steps.get("optimizer.verify_checkpoint_delta")
        )
        modified = datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
        return {
            "runId": path.stem,
            "schema": payload.get("schema"),
            "ok": bool(payload.get("ok")),
            "status": summary.get("status")
            or ("passed" if payload.get("ok") else "failed"),
            "tool": summary.get("tool"),
            "checkpointId": summary.get("checkpointId"),
            "rollbackDone": bool(
                summary.get("rollbackDone") or payload.get("rollbackDone")
            ),
            "changedFileCount": checkpoint_step.get("changedFileCount"),
            "failedSteps": summary.get("failedSteps")
            if isinstance(summary.get("failedSteps"), list)
            else [],
            "startedAt": payload.get("startedAt"),
            "finishedAt": payload.get("finishedAt"),
            "modifiedAt": modified,
            "visualRegression": self._visual_summary(payload),
            "rollbackProof": _ensure_dict(delta.get("rollbackProof")),
            "profileDiff": profile_diff,
            "profileDiffUnavailable": not bool(profile_diff),
            "parameterBudgetDelta": parameter_delta,
            "reportPath": str(path),
        }

    def list(self, limit: int = 10) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 10), 50))
        root = self._root()
        proofs: list[dict[str, Any]] = []
        if root.exists():
            files = sorted(
                root.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for path in files[:safe_limit]:
                try:
                    proofs.append(self._summarize(path))
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    proofs.append(
                        {
                            "runId": path.stem,
                            "ok": False,
                            "status": "unreadable",
                            "modifiedAt": datetime.fromtimestamp(
                                path.stat().st_mtime,
                                tz=timezone.utc,
                            ).isoformat(),
                            "error": str(exc),
                        }
                    )
        return {
            "ok": True,
            "schema": "vrcforge.optimization.proof_index.v1",
            "readOnly": True,
            "artifactRoot": str(root),
            "count": len(proofs),
            "proofs": proofs,
        }

    def read(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        report = self._read_file(path)
        return {
            "ok": True,
            "schema": "vrcforge.optimization.proof_detail.v1",
            "readOnly": True,
            "proof": self._summarize(path, report),
            "report": report,
        }

    def screenshot_path(self, run_id: str, stage: str) -> Path:
        stage_name = str(stage or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stage_name):
            raise ValueError("Invalid optimizer proof screenshot stage.")
        report = self._read_file(self._path(run_id))
        screenshot = _ensure_dict(
            _ensure_dict(
                _ensure_dict(report.get("visualRegression")).get("screenshots")
            ).get(stage_name)
        )
        path_value = str(screenshot.get("artifactImagePath") or "").strip()
        if not path_value:
            raise FileNotFoundError(stage_name)
        path = Path(path_value).resolve()
        if not self._is_under(path, self._ports.artifact_root):
            raise PermissionError(path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        return path
