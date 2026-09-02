"""Shared Stage 1 MCP descriptor and result-contract projections.

The Gateway and the existing lazy external tree remain the source of truth;
this module only adds a stable public description around those entries.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from execution_target import future_provenance_metadata, standard_identity_metadata


CANONICAL_TOOL_NAMES = {
    "vrcforge_save_current_scene": "vrcforge.scene.save",
}


def canonical_tool_name(name: str) -> str:
    normalized = str(name or "").strip()
    if normalized in CANONICAL_TOOL_NAMES:
        return CANONICAL_TOOL_NAMES[normalized]
    if normalized.startswith("vrcforge_"):
        return "vrcforge." + normalized.removeprefix("vrcforge_").replace("_", ".")
    return normalized


def legacy_aliases(name: str) -> list[str]:
    normalized = str(name or "").strip()
    if normalized == "vrcforge_save_current_scene":
        return [normalized]
    return []


def identity_scope(name: str, *, write: bool = False) -> str:
    normalized = str(name or "").casefold()
    if any(token in normalized for token in ("property", "component", "renderer", "constraint", "material", "texture", "shader")):
        return "component"
    if any(token in normalized for token in ("gameobject", "scene_object", "avatar_object", "hierarchy")):
        return "object"
    if any(token in normalized for token in ("avatar", "fx_", "animator", "menu", "parameter", "outfit", "gesture", "capture")):
        return "avatar"
    if "scene" in normalized:
        return "scene"
    return "project"


def _result_schema(write: bool) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "operationId": {"type": "string"},
            "status": {"type": "string", "enum": ["success", "no_change", "pending", "failed", "unknown", "user_confirmation_required", "preview", "applied"]},
            "commitState": {"type": "string"},
            "mutationStarted": {"type": ["boolean", "null"]},
            "mutationApplied": {"type": ["boolean", "null"]},
            "persistenceState": {"type": "string"},
            "readbackState": {"type": "string"},
            "cleanupState": {"type": "string"},
            "retryable": {"type": "boolean"},
            "nextAction": {"type": ["string", "null"]},
            "beforeResource": {"type": ["string", "null"]},
            "afterResource": {"type": ["string", "null"]},
            "diffResource": {"type": ["string", "null"]},
            "operationResource": {"type": ["string", "null"]},
            "executionTargetDigest": {"type": "string"},
        },
        "x-vrcforge-write": bool(write),
    }


def standardize_tool_descriptor(
    tool: Mapping[str, Any],
    *,
    write: bool,
    block: str = "",
    exposure_layer: str = "execution",
    catalog_generation: int | None = None,
    add_identity_schema: bool = False,
) -> dict[str, Any]:
    """Decorate an existing tool entry without changing its MCP name."""

    result = deepcopy(dict(tool))
    name = str(result.get("name") or "").strip()
    canonical = canonical_tool_name(name)
    scope = identity_scope(name, write=write)
    metadata = dict(result.get("_meta") or {}) if isinstance(result.get("_meta"), Mapping) else {}
    description = str(result.get("description") or name)
    when_to_use = description.split("When NOT to use:", 1)[0].removeprefix("When to use:").strip()
    when_not = description.split("When NOT to use:", 1)[1].split("Negative example:", 1)[0].strip() if "When NOT to use:" in description else "Do not use outside the declared project scope."
    negative = description.split("Negative example:", 1)[1].strip() if "Negative example:" in description else f"Do not use {name} as a substitute for an unrelated operation."
    result.update({
        "canonicalName": canonical,
        "mcpName": name,
        "legacyAliases": legacy_aliases(name),
        "domain": str(result.get("category") or block or "unity"),
        "effect": "write" if write else "read",
        "whenToUse": when_to_use,
        "whenNotToUse": when_not,
        "negativeExamples": [negative],
        "permission": "RequiresApproval" if write else "ReadOnly",
        "sideEffects": ["Unity project state may change"] if write else [],
        "idempotency": "tool-defined; operationId is required for transaction correlation",
        "syncMode": "async-capable" if write else "synchronous-or-readback",
        "requiredIdentity": standard_identity_metadata(scope=scope, write=write),
        "requiredResources": [],
        "producedResources": [],
        "approval": {"required": bool(write), "mode": "risk_based" if write else "none"},
        "checkpoint": {"required": bool(write), "policy": "before_mutation" if write else "not_applicable"},
        "rollback": {"policy": "receipt_declared" if write else "not_applicable"},
        "freshReadback": {"required": bool(write), "mustUseExactTarget": True},
        "errorModel": "vrcforge.tool_result.v1 with fail-closed identity errors",
    })
    metadata.update({
        "canonicalName": canonical,
        "mcpName": name,
        "legacyAliases": legacy_aliases(name),
        "domain": result["domain"],
        "effect": result["effect"],
        "whenToUse": when_to_use,
        "whenNotToUse": when_not,
        "negativeExample": negative,
        "requiredIdentity": result["requiredIdentity"],
        "inputEnvelopeExtension": {
            "field": "executionTarget",
            "schema": "vrcforge.execution_target.v1",
            "requiredAtRuntime": bool(write or scope in {"scene", "avatar", "object", "component"}),
            "acceptedWithoutChangingLegacyRequired": True,
        },
        "approval": result["approval"],
        "checkpoint": result["checkpoint"],
        "rollback": result["rollback"],
        "freshReadback": result["freshReadback"],
        "errorModel": result["errorModel"],
        "exposureLayer": exposure_layer,
        "toolBlock": block or metadata.get("toolBlock"),
        "resultContract": _result_schema(write),
        **future_provenance_metadata(),
    })
    if catalog_generation is not None:
        result["catalogGeneration"] = int(catalog_generation)
        metadata["catalogGeneration"] = int(catalog_generation)
    if add_identity_schema:
        schema = deepcopy(dict(result.get("inputSchema") or {}))
        properties = dict(schema.get("properties") or {})
        properties["executionTarget"] = {
            "type": "object",
            "description": "Exact Stage 1 identity envelope; hierarchy paths are display/navigation only.",
            "$ref": "#/$defs/vrcforge.execution_target.v1",
        }
        schema["properties"] = properties
        schema.setdefault("$defs", {})["vrcforge.execution_target.v1"] = {
            "type": "object",
            "description": "See _meta.requiredIdentity; runtime rejects identity drift and never falls back to hierarchy names.",
            "required": ["schema", "namespace", "scope", "project", "editor"],
            "additionalProperties": True,
        }
        result["inputSchema"] = schema
    result["outputSchema"] = _result_schema(write)
    result["_meta"] = metadata
    return result
