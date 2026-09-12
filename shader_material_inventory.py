"""Validate a material scan before planning, persisting, or applying it."""

from typing import Any

from vrchat_blendshape_agent import UnityMcpError


def require_material_inventory(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UnityMcpError("Material scan did not return a JSON inventory.", cause_code="material_inventory_invalid")
    if payload.get("ok") is False or payload.get("success") is False or payload.get("isError") is True:
        raise UnityMcpError(
            str(payload.get("error") or payload.get("message") or "Material scan failed."),
            cause_code=str(payload.get("errorCode") or "material_scan_failed"),
            core_tool="vrc_scan_avatar_materials",
            raw_result=payload,
            failure_layer=str(payload.get("failureLayer") or "shader_material_scan"),
            failure_phase=str(payload.get("failurePhase") or "inventory_readback"),
            operation_kind="read",
            tool_routing_started=payload.get("toolRoutingStarted"),
            mutation_started=False,
            committed=False,
        )
    if not isinstance(payload.get("materials"), list):
        raise UnityMcpError(
            "Material scan returned no materials array; refresh the inventory before planning or applying changes.",
            cause_code="material_inventory_invalid", raw_result=payload,
            core_tool="vrc_scan_avatar_materials", operation_kind="read",
            mutation_started=False, committed=False,
        )
    return payload


def project_selected_material_summary(
    inventory: dict[str, Any], materials: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project counts onto the material rows actually returned by a selection.

    Unity counts renderers before applying ``materialIds``. Keep that raw,
    mixed-scope reported summary as ``sourceSummary`` while making the public
    selection summary describe the rows the caller can inspect.
    """
    source_summary = dict(inventory.get("summary") or {})
    selected_summary = dict(source_summary)
    valid_materials = [row for row in materials if isinstance(row, dict)]
    renderer_paths = {
        str(row.get("renderer_path") or row.get("rendererPath") or "").strip()
        for row in valid_materials
    }
    renderer_paths.discard("")
    selected_summary.update(
        {
            "rendererCount": len(renderer_paths),
            "materialCount": len(valid_materials),
            "lilToonCount": sum(row.get("shader_family") == "lilToon" for row in valid_materials),
            "poiyomiCount": sum(row.get("shader_family") == "Poiyomi" for row in valid_materials),
            "genericCount": sum(row.get("shader_family") == "Generic" for row in valid_materials),
            "unsupportedCount": sum(row.get("shader_family") == "Unsupported" for row in valid_materials),
        }
    )
    return selected_summary, source_summary
