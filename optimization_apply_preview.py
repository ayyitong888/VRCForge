from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from optimization_service import (
    OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL,
    OPTIMIZATION_APPLY_REQUEST_BY_GATEWAY,
    build_optimization_tool_result,
)


class OptimizationApplyPreviewError(RuntimeError):
    """A bounded authoritative-preview failure surfaced as a blocked preview."""


@dataclass(frozen=True)
class OptimizationApplyPreviewPorts:
    resolve_project_path: Callable[[dict[str, Any]], str]
    package_install_plan: Callable[[dict[str, Any]], dict[str, Any]]
    build_parameter_bit_packing_arguments: Callable[[dict[str, Any]], dict[str, Any]]
    preview_parameter_bit_packing: Callable[[dict[str, Any]], dict[str, Any]]


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_optimization_apply_request_name(tool_name: str) -> str:
    value = str(tool_name or "").strip()
    if value in OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL:
        return value
    definition = OPTIMIZATION_APPLY_REQUEST_BY_GATEWAY.get(value)
    if definition:
        return str(definition["externalName"])
    aliases = {
        "lac": "optimization.lac.apply-request",
        "lac_profile": "optimization.lac.apply-request",
        "aao": "optimization.aao.trace-apply-request",
        "aao_trace": "optimization.aao.trace-apply-request",
        "ttt": "optimization.ttt.atlas-apply-request",
        "textrans": "optimization.ttt.atlas-apply-request",
        "textrans_tool": "optimization.ttt.atlas-apply-request",
        "ma2bt": "optimization.ma2bt.convert-apply-request",
        "ma2bt_pro": "optimization.ma2bt.convert-apply-request",
        "meshia": "optimization.meshia.simplify-apply-request",
        "vrcfury_parameter": "optimization.vrcfury.parameter-compressor-apply-request",
        "vrcfury_parameter_compressor": "optimization.vrcfury.parameter-compressor-apply-request",
        "vrcfury_direct_tree": "optimization.vrcfury.direct-tree-apply-request",
        "hidden_body_cut": "optimization.aao.hidden-body-cut-apply-request",
        "aao_hidden_body_cut": "optimization.aao.hidden-body-cut-apply-request",
        "physbone_cleanup": "optimization.aao.physbone-cleanup-apply-request",
        "aao_physbone_cleanup": "optimization.aao.physbone-cleanup-apply-request",
    }
    key = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if key in aliases:
        return aliases[key]
    raise ValueError(f"Unknown optimization apply-request tool: {tool_name}")


def normalize_optimizer_profile_id(value: Any) -> str:
    raw = str(value or "pc_conservative").strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    aliases = {
        "conservative": "pc_conservative",
        "conservative_pc": "pc_conservative",
        "pc_conservative": "pc_conservative",
        "medium": "pc_medium",
        "balanced": "balanced",
        "balanced_pc": "balanced_pc",
        "pc_medium": "pc_medium",
        "high_quality": "high_quality",
        "quality": "high_quality",
        "custom": "custom",
    }
    return aliases.get(key, key or "pc_conservative")


def _option_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def confirmed_ttt_material_paths(
    params: dict[str, Any],
    options: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    for key in (
        "atlasTargetMaterials",
        "materialPaths",
        "materials",
        "targetMaterialPaths",
        "confirmedMaterialPaths",
        "userConfirmedMaterialPaths",
    ):
        values.extend(_option_string_list(options.get(key)))
        values.extend(_option_string_list(params.get(key)))
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.replace("\\", "/").strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _meshia_renderer_path(params: dict[str, Any], options: dict[str, Any]) -> str:
    return str(
        options.get("rendererPath")
        or options.get("targetRendererPath")
        or params.get("rendererPath")
        or params.get("targetRendererPath")
        or params.get("targetPath")
        or ""
    ).strip()


def meshia_relative_vertex_count(
    profile: str,
    options: dict[str, Any],
) -> tuple[float, str]:
    raw = (
        options.get("relativeVertexCount")
        or options.get("targetRatio")
        or options.get("ratio")
        or options.get("vertexRatio")
        or ""
    )
    if raw == "":
        return (0.9 if profile in {"pc_conservative", "conservative_pc"} else 0.85), ""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0, "Meshia relativeVertexCount must be a number between 0.75 and 1.0 for the stable request path."
    if value < 0.75 or value > 1.0:
        return value, "Meshia stable request path only allows relativeVertexCount between 0.75 and 1.0. Lower ratios remain experimental."
    return value, ""


def _find_optimizer_dependency(dependency_doctor: dict[str, Any], optimizer_id: str) -> dict[str, Any]:
    for dependency in dependency_doctor.get("dependencies") or []:
        if str(dependency.get("id") or "") == optimizer_id:
            return dependency
    return {}


def _optimization_preview_blocked_write_reason(definition: dict[str, Any]) -> str:
    mode = str(definition.get("mode") or "")
    if mode == "vrcfury_parameter_compressor":
        return "The Parameter Compressor request is unavailable because this build did not register its validated writer path."
    if mode == "vrcfury_direct_tree":
        return "VRCFury Direct Tree is experimental: VRCForge exposes the request name but blocks writes until controller behavior and rollback proof exist."
    if mode == "aao_hidden_body_cut":
        return "AAO hidden body cut is experimental: request preview is blocked until manual occlusion evidence, visual confirmation, validation delta, and rollback proof exist."
    if mode == "aao_physbone_cleanup":
        return "AAO PhysBone cleanup is experimental: request preview is blocked until motion behavior proof, validation delta, and rollback proof exist."
    return "This optimizer apply path is still plan-only/experimental; VRCForge will not configure it automatically yet."


def _optimization_preview_hard_gate(
    definition: dict[str, Any],
    dependency_status: str,
    blocked_reasons: list[str],
) -> dict[str, Any]:
    rows = [
        {
            "id": "dependency.installed",
            "label": "Optimizer dependency installed",
            "required": True,
            "status": "pass" if dependency_status == "installed" else "blocked",
            "blockedReason": None if dependency_status == "installed" else "Install or repair the optimizer dependency first.",
        },
        {
            "id": "rollback.required",
            "label": "Rollback proof required",
            "required": True,
            "status": "pass",
            "blockedReason": None,
        },
    ]
    if not definition.get("writeSupported"):
        rows.append(
            {
                "id": "experimental.writer_proof",
                "label": "Experimental writer proof",
                "required": True,
                "status": "blocked",
                "blockedReason": _optimization_preview_blocked_write_reason(definition),
            }
        )
    if blocked_reasons:
        rows.append(
            {
                "id": "preview.blocked_reasons",
                "label": "Preview-specific blockers",
                "required": True,
                "status": "blocked",
                "blockedReason": "; ".join(blocked_reasons),
            }
        )
    blocking = [row for row in rows if row.get("required") and row.get("status") == "blocked"]
    return {
        "status": "blocked" if blocking else "pass",
        "blockingCount": len(blocking),
        "blockingIds": [str(row.get("id")) for row in blocking],
        "rows": rows,
    }


class OptimizationApplyPreviewService:
    """Build one read-only optimizer request preview from frozen dependency ports."""

    def __init__(self, ports: OptimizationApplyPreviewPorts) -> None:
        self._ports = ports

    def build(self, params: dict[str, Any]) -> dict[str, Any]:
        params = params or {}
        tool = normalize_optimization_apply_request_name(str(params.get("tool") or params.get("externalName") or params.get("gatewayName") or ""))
        definition = OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL[tool]
        project_value = self._ports.resolve_project_path(params)
        avatar_path = str(
            params.get("source_avatar_path")
            or params.get("sourceAvatarPath")
            or params.get("avatar_path")
            or params.get("avatarPath")
            or ""
        ).strip()
        profile = normalize_optimizer_profile_id(
            params.get("profile") or params.get("targetProfile") or params.get("target_profile") or "pc_conservative"
        )
        options = _ensure_dict(params.get("options") or {})
        target_path = avatar_path
        dependency_doctor = build_optimization_tool_result(
            "optimization.dependency.doctor",
            {"projectPath": project_value},
            {},
        ).get("result") or {}
        dependency = _find_optimizer_dependency(dependency_doctor, str(definition["optimizerId"]))
        package_ids = [str(item) for item in dependency.get("packageIds") or [] if str(item or "").strip()]
        dependency_status = str(dependency.get("status") or "unknown")
        install_plan = None
        wants_install_plan = bool(
            params.get("installMissingDependencies")
            or params.get("install_missing_dependencies")
            or params.get("allowAgentManagedDownload")
            or dependency_status != "installed"
        )
        if package_ids and wants_install_plan:
            install_plan = self._ports.package_install_plan(
                {
                    "projectPath": project_value,
                    "packageId": package_ids[0],
                    "repository": dependency.get("vpmRepository") or "",
                    "allowAgentManagedDownload": bool(params.get("installMissingDependencies") or params.get("allowAgentManagedDownload")),
                }
            )
        supported_write = bool(definition.get("writeSupported"))
        stable_callable = bool(definition.get("stableCallable"))
        supported_profiles = [str(item) for item in definition.get("supportedProfiles") or []]
        blocked_reasons: list[str] = []
        if not project_value:
            blocked_reasons.append("Unity projectPath is required.")
        if not avatar_path and supported_write:
            blocked_reasons.append("avatarPath is required for supervised optimizer context and rollback proof.")
        if dependency_status != "installed":
            blocked_reasons.append(f"{dependency.get('label') or definition['optimizerId']} is {dependency_status}; install or repair it first.")
        if not supported_write:
            blocked_reasons.append(_optimization_preview_blocked_write_reason(definition))
        if not stable_callable:
            blocked_reasons.append("This optimizer is not yet part of the stable avatar optimization skill set.")
        if supported_profiles and profile not in supported_profiles:
            blocked_reasons.append(f"Profile '{profile}' is not enabled for stable delegated apply yet.")
        mode = str(definition.get("mode") or "")
        authoritative_preview: dict[str, Any] | None = None
        apply_arguments: dict[str, Any] = {"projectPath": project_value}
        if mode == "vrcfury_parameter_compressor":
            source_scene_path = str(
                options.get("sourceScenePath")
                or options.get("source_scene_path")
                or params.get("sourceScenePath")
                or params.get("source_scene_path")
                or ""
            ).strip()
            output_clone_name = str(
                options.get("outputCloneName")
                or options.get("output_clone_name")
                or params.get("outputCloneName")
                or params.get("output_clone_name")
                or ""
            ).strip()
            if not source_scene_path:
                blocked_reasons.append("sourceScenePath is required for the authoritative parameter build preview.")
            if not avatar_path:
                blocked_reasons.append("sourceAvatarPath (or avatarPath) is required for the authoritative parameter build preview.")
            if not output_clone_name:
                blocked_reasons.append("outputCloneName is required for the authoritative parameter build preview.")
            target_path = avatar_path
            if project_value and source_scene_path and avatar_path and output_clone_name:
                try:
                    apply_arguments = self._ports.build_parameter_bit_packing_arguments(
                        {
                            "projectPath": project_value,
                            "sourceScenePath": source_scene_path,
                            "sourceAvatarPath": avatar_path,
                            "outputCloneName": output_clone_name,
                        }
                    )
                except ValueError as exc:
                    blocked_reasons.append(str(exc))
            if not blocked_reasons:
                try:
                    authoritative_preview = self._ports.preview_parameter_bit_packing(
                        {
                            "projectPath": project_value,
                            "sourceScenePath": source_scene_path,
                            "sourceAvatarPath": avatar_path,
                            "outputCloneName": output_clone_name,
                        }
                    ).get("preview")
                except (OptimizationApplyPreviewError, ValueError) as exc:
                    blocked_reasons.append(str(exc))
        elif mode == "ttt_atlas":
            material_paths = confirmed_ttt_material_paths(params, options)
            if not material_paths:
                blocked_reasons.append("TexTransTool atlas setup requires user-confirmed material asset paths in options.atlasTargetMaterials.")
            invalid_material_paths = [item for item in material_paths if not item.replace("\\", "/").startswith("Assets/")]
            if invalid_material_paths:
                blocked_reasons.append("TexTransTool material references must be Unity asset paths under Assets/.")
            options = {**options, "atlasTargetMaterials": material_paths}
        elif mode == "meshia_simplify":
            renderer_path = _meshia_renderer_path(params, options)
            if not renderer_path:
                blocked_reasons.append("Meshia stable setup requires options.rendererPath for one user-selected low-risk Renderer object.")
            target_path = renderer_path or avatar_path
            ratio, ratio_error = meshia_relative_vertex_count(profile, options)
            if ratio_error:
                blocked_reasons.append(ratio_error)
            options = {**options, "rendererPath": renderer_path, "relativeVertexCount": ratio}
        if mode != "vrcfury_parameter_compressor":
            apply_arguments = {
                "projectPath": project_value,
                "avatarPath": avatar_path,
                "targetPath": target_path,
                "optimizerId": definition["optimizerId"],
                "mode": definition["mode"],
                "componentType": definition.get("componentType") or "",
                "profile": profile,
                "options": options,
                "sourceApplyRequestTool": definition["externalName"],
            }
        return {
            "ok": True,
            "schema": "vrcforge.optimization.apply_request.v1",
            "externalName": definition["externalName"],
            "gatewayName": definition["gatewayName"],
            "targetTool": definition["targetTool"],
            "versionStage": definition["versionStage"],
            "directApplyExposed": False,
            "requestOnly": True,
            "requiresApproval": True,
            "requiresCheckpoint": True,
            "requiresValidation": True,
            "requiresRollbackProof": True,
            "hardGate": _optimization_preview_hard_gate(definition, dependency_status, blocked_reasons),
            "rollbackRequirements": {
                "checkpointScope": ["Assets", "Packages", "ProjectSettings"],
                "restoreTool": "vrcforge_restore_checkpoint",
                "postRestoreValidationRequired": True,
                "generatedResidueCheckRequired": True,
            },
            "writeSupported": supported_write,
            "stableCallable": stable_callable,
            "supportedProfiles": supported_profiles,
            "readyToRequest": not blocked_reasons,
            "blockedReasons": blocked_reasons,
            "dependency": dependency,
            "dependencyInstallPlan": install_plan,
            "authoritativePreview": authoritative_preview,
            "plan": build_optimization_tool_result(str(definition["planTool"]), params, {}),
            "applyArguments": apply_arguments,
            "policy": {
                "oneOptimizerStepAtATime": True,
                "noDirectExternalApply": True,
                "noOneClickAllOptimizers": True,
                "checkpointValidationRollbackRequired": True,
            },
        }
