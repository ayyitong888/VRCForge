"""Small, dependency-free state guards for prepared shader tuning writes.

The dashboard owns Unity scans and calls; this module owns neither authority
nor I/O.  Its sole purpose is to make approval-time evidence and the
process-lifetime shader undo stack comparable without creating another write
route.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from threading import RLock
from typing import Any

from operation_context import current_operation_context


SHADER_UNDO_LOCK = RLock()


def canonical_sha256(value: Any) -> str:
    """Return a stable digest for JSON-compatible execution evidence."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Shader execution evidence must be JSON-compatible.") from exc
    return hashlib.sha256(encoded).hexdigest()


def require_exact_evidence(expected: Any, actual: Any, label: str) -> None:
    """Reject a prepared action when a live prerequisite has changed."""
    if canonical_sha256(expected) != canonical_sha256(actual):
        raise RuntimeError(f"Prepared shader {label} drifted after approval.")


def canonical_storage_value(value: Any) -> Any:
    """Compare numeric material values in Unity's float32 storage domain.

    This applies only to scalar readback values. Approval arguments and material
    identities retain their exact evidence checks; adjacent floats stay distinct.
    """
    if isinstance(value, bool):
        raise ValueError("Shader numeric readback cannot be a boolean.")
    if not isinstance(value, (int, float)):
        return value
    if not math.isfinite(value):
        raise ValueError("Shader numeric readback must be finite.")
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError("Shader numeric readback is outside float32 range.") from exc


def _effective_execution_target(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the request-local target after external dispatch strips it.

    External approval dispatch deliberately removes ``executionTarget`` from
    handler arguments and carries the same bound value in operation context.
    When both are present, require exact equality so context cannot silently
    override an explicit caller value.
    """
    explicit = arguments.get("executionTarget")
    context = current_operation_context() or {}
    contextual = context.get("executionTarget")
    if explicit is not None and not isinstance(explicit, dict):
        raise RuntimeError("Shader execution target is invalid.")
    if contextual is not None and not isinstance(contextual, dict):
        raise RuntimeError("Shader operation execution target is invalid.")
    if explicit is not None and contextual is not None and canonical_sha256(explicit) != canonical_sha256(contextual):
        raise RuntimeError("Shader execution target conflicts with the operation context.")
    target = explicit if explicit is not None else contextual
    return dict(target) if isinstance(target, dict) else None


def _arguments_with_effective_target(arguments: dict[str, Any]) -> dict[str, Any]:
    target = _effective_execution_target(arguments)
    if target is None:
        return arguments
    effective = dict(arguments)
    effective["executionTarget"] = target
    return effective


def require_avatar_material_scope(arguments: dict[str, Any], avatar_path: str,
                                  inventory: dict[str, Any] | None = None,
                                  changes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]] | None:
    """Bind semantic routing to the target's Avatar and authoritative selected rows.

    A detailed component target retains its containing Avatar identity. Local
    callers without an external ExecutionTarget keep their existing route.
    """
    arguments = _arguments_with_effective_target(arguments)
    target = arguments.get("executionTarget")
    if target is None:
        return None
    if not isinstance(target, dict) or target.get("scope") not in {"avatar", "object", "component"}:
        raise RuntimeError("Shader tuning requires Avatar identity coverage.")
    avatar = target.get("avatar") or {}
    if not avatar_path or avatar.get("exactHierarchyPath") != avatar_path:
        raise RuntimeError("Shader effective Avatar differs from ExecutionTarget.")
    if inventory is None:
        if changes is not None:
            raise RuntimeError("Authoritative shader material scope inventory is missing.")
        return []
    import os
    scene = target.get("scene") or {}
    scene_path = str(scene.get("assetPath") or "").replace("\\", "/")
    if not scene_path:
        project = (target.get("project") or {}).get("root") or arguments.get("projectPath")
        absolute = scene.get("absolutePath") or scene.get("path")
        if not project or not absolute:
            raise RuntimeError("Shader target scene identity is incomplete.")
        scene_path = os.path.relpath(absolute, project).replace("\\", "/")
    rows = inventory.get("materials")
    if not isinstance(rows, list) or not scene.get("guid"):
        raise RuntimeError("Authoritative shader material scope facts are missing.")
    selected = sorted({str(row.get("material_id") or row.get("materialId") or "") for row in changes or []})
    facts = []
    for material_id in selected:
        matches = [row for row in rows if isinstance(row, dict) and row.get("material_id") == material_id]
        if not material_id or len(matches) != 1:
            raise RuntimeError("Selected shader material is missing or ambiguous in current Avatar facts.")
        row = matches[0]
        path = str(row.get("item_path") or "")
        if (path != avatar_path and not path.startswith(avatar_path + "/")) or row.get("renderer_scene_path") != scene_path or row.get("renderer_scene_guid") != scene["guid"]:
            raise RuntimeError("Selected shader material lies outside the bound scene/Avatar.")
        if not row.get("renderer_component_id"):
            raise RuntimeError("Selected shader renderer identity is missing.")
        facts.append({key: row.get(key) for key in ("material_id", "item_path", "renderer_scene_path", "renderer_scene_guid", "renderer_component_id")})
    return facts


def require_shader_receipt_avatar(arguments: dict[str, Any], avatar_path: str, result: dict[str, Any]) -> None:
    effective_arguments = _arguments_with_effective_target(arguments)
    if effective_arguments.get("executionTarget") is not None:
        require_avatar_material_scope(effective_arguments, avatar_path)
        if result.get("avatarPath") != avatar_path:
            raise RuntimeError("Persisted shader receipt Avatar differs from its approved target.")


def shader_scope_scan_arguments(avatar_path: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"avatarPath": avatar_path, "outputPath": "", "refreshAssets": False,
            "materialIds": sorted({str(row.get("material_id") or row.get("materialId") or "") for row in changes}),
            "includeTextures": False}
