"""Shared Stage 1 MCP descriptor and result-contract projections.

The Gateway and the existing lazy external tree remain the source of truth;
this module only adds a stable public description around those entries.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from execution_target import future_provenance_metadata, standard_identity_metadata
from external_mcp_result_projection import operation_result_required_fields


CANONICAL_TOOL_NAMES = {
    "vrcforge_scene_save": "vrcforge.scene.save",
    "vrcforge_save_current_scene": "vrcforge.scene.save",
}

LEGACY_TOOL_ALIASES = {
    "vrcforge_save_current_scene": "vrcforge_scene_save",
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
    if normalized == "vrcforge_scene_save":
        return ["vrcforge_save_current_scene"]
    return []


def canonical_mcp_tool_name(name: str) -> str:
    """Resolve a callable legacy name to the one displayed MCP Tool name."""

    normalized = str(name or "").strip()
    return LEGACY_TOOL_ALIASES.get(normalized, normalized)


def identity_scope(name: str, *, write: bool = False, arguments: Mapping[str, Any] | None = None) -> str:
    normalized = str(name or "").casefold()
    if normalized.endswith(("inspect_editor_capabilities", "execute_unity_editor_menu", "inspect_material_properties")):
        return "project"
    if normalized.endswith(("preview_save_asset", "save_asset")):
        return "project"
    # Prepared calls execute their nested arguments; idle outer fields cannot choose scope.
    values = arguments or {}
    for key in ("arguments", "params"):
        if isinstance(values.get(key), Mapping):
            values = values[key]
            break
    # First save creates the scene asset identity. The project/editor binding
    # protects discovery; the authoritative preview locks the unsaved scene's
    # handle, hierarchy and absent destination before approval and mutation.
    if normalized.endswith("save_new_scene"):
        return "project"
    if normalized.endswith("unpack_prefab"):
        return "avatar"
    # Source inspection and asset import address the project, before an Avatar
    # instance exists. Outfit setup/installation still bind the existing Avatar.
    if normalized.endswith(("inspect_outfit_package", "plan_outfit_import", "import_outfit_package", "import_unitypackage")):
        return "project"
    # Opening an existing scene addresses a project asset; it does not require
    # the currently loaded scene to have a saved identity.  Keep every other
    # transition action scene-scoped until its identity requirements differ.
    if normalized.endswith("scene_transition") and str(values.get("action") or "").casefold() == "open_single":
        return "project"
    if "material_texture" in normalized or "texture_import_settings" in normalized:
        return "project"
    if "flatten_material_variant" in normalized or "preview_material_variant_flatten" in normalized:
        return "project"
    if normalized.endswith(("apply_shader_tuning", "restore_shader_tuning", "reapply_shader_tuning_history", "apply_shader_tuning_preset")):
        return "avatar"
    if "renderer_material_slot" in normalized and "assignments" in values:
        return "scene"
    if (normalized.endswith("set_material_shader") or normalized.endswith("preview_material_shader_assignment")) and ("assignments" in values or values.get("materialAssetPath")) and not any(key in values for key in ("rendererPath", "rendererComponentId", "slotIndex")):
        return "project"
    if "runtime_observation" in normalized:
        return "avatar"
    if "texture_patch" in normalized:
        # This operation binds a project asset by exact path and source digest;
        # an unrelated live Avatar/component identity would be misleading.
        return "project"
    if "user_adjustment_handoff" in normalized:
        # The handoff binds both the Avatar and the exact target GameObject.
        return "object"
    if normalized.endswith("create_gameobject"):
        # Bind the existing container: an explicit parent, or the active saved
        # scene for a root object. The new object cannot have an identity yet.
        parent = str(values.get("parent_path") or values.get("parentPath") or "").strip()
        return "object" if parent else "scene"
    if normalized in {
        "vrcforge_add_component",
        "vrcforge_add_modular_avatar_component",
        "vrcforge_preview_add_modular_avatar_component",
        "vrcforge_create_component_feature",
        "vrcforge_preview_component_feature",
    }:
        # Creation binds the existing host, not a component that does not exist yet.
        # Existing-component edits and removal retain the component scope below.
        return "object"
    if any(token in normalized for token in ("property", "component", "renderer", "constraint", "material", "texture", "shader")):
        return "component"
    if any(token in normalized for token in ("gameobject", "scene_object", "avatar_object", "hierarchy")):
        return "object"
    if any(token in normalized for token in ("avatar", "fx_", "animator", "menu", "parameter", "outfit", "gesture", "capture")):
        return "avatar"
    if "scene" in normalized:
        return "scene"
    return "project"


_READ_RUNTIME_IDENTITY_SCOPES = frozenset({"scene", "avatar", "object", "component"})
_APPLICATION_OWNED_WRITE_TOOLS = frozenset({
    "vrcforge_create_project", "vrcforge_register_project",
    "vrcforge_register_project_catalog", "vrcforge_restore_unity_core",
    "vrcforge_install_unity_core",
    "vrcforge_rollback_project_catalog_registration",
    "vrcforge_rollback_project_lifecycle", "vrcforge_select_project",
    "vrcforge_restore_checkpoint", "vrcforge_confirm_unity_reload_dialog",
    "vrcforge_handle_unity_editor_dialog",
})
_READ_IDENTITY_BOOTSTRAP_TOOLS = frozenset({
    "vrcforge_inspect_unity_editor_dialogs",
    "vrcforge_list_execution_targets",
    "vrcforge_bind_execution_target",
    "vrcforge_refresh_execution_target",
})


def read_runtime_identity_required(name: str) -> bool:
    """Return the descriptor-owned read projection identity decision."""

    normalized = str(name or "").strip()
    if normalized in _READ_IDENTITY_BOOTSTRAP_TOOLS:
        return False
    return identity_scope(normalized, write=False) in _READ_RUNTIME_IDENTITY_SCOPES


def write_runtime_identity_required(name: str) -> bool:
    """Application-owned preparations/receipts do not depend on a Core binding."""

    return str(name or "").strip() not in _APPLICATION_OWNED_WRITE_TOOLS


def _result_schema(write: bool) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": True,
        "required": [
            "operationId",
            "status",
            "operationStatus",
            "commitState",
            "mutationStarted",
            "mutationApplied",
            "persistenceState",
            "readbackState",
            "cleanupState",
            "retryable",
            "nextAction",
            "beforeResource",
            "afterResource",
            "diffResource",
            "operationResource",
            "resources",
            "promptSkillProvenance",
        ],
        "properties": {
            "operationId": {"type": "string"},
            "status": {"type": "string", "description": "Stable existing domain status; use operationStatus for cross-tool terminal semantics."},
            "operationStatus": {"type": "string", "enum": ["success", "no_change", "pending", "failed", "unknown", "user_confirmation_required", "preview", "applied"]},
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
            "resources": {"type": "object"},
            "promptSkillProvenance": {"type": "object"},
        },
        "x-vrcforge-write": bool(write),
    }
    schema["required"] = operation_result_required_fields(schema["required"], schema["properties"])
    return schema


def tool_identity_metadata(name: str, *, write: bool, input_schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Shared descriptor policy used by every model-facing schema projection."""

    identity = standard_identity_metadata(scope=identity_scope(name, write=write), write=write)
    runtime_identity_required = write_runtime_identity_required(name) if write else read_runtime_identity_required(name)
    if write and not runtime_identity_required:
        identity = {
            "scope": "project",
            "required": list(dict(input_schema or {}).get("required") or []),
            "writeBinding": "handler_managed",
            "validation": "request_preparer_or_receipt",
        }
    conditional_identity = None
    if "renderer_material_slot" in name:
        conditional_identity = {"if": {"required": ["assignments"]}, "then": {"properties": {"executionTarget": {"properties": {"scope": {"enum": ["scene", "avatar"]}}}}}, "else": {"properties": {"executionTarget": {"properties": {"scope": {"const": "component"}}}}}}
    elif name.endswith("set_material_shader") or name.endswith("preview_material_shader_assignment"):
        conditional_identity = {
            "if": {
                "anyOf": [
                    {"required": ["assignments"]},
                    {"required": ["materialAssetPath"], "properties": {"materialAssetPath": {"minLength": 1}}},
                ],
                "not": {"anyOf": [{"required": [key]} for key in ("rendererPath", "rendererComponentId", "slotIndex")]},
            },
            "then": {
                "if": {"required": ["assignments"]},
                "then": {"properties": {"executionTarget": {"properties": {"scope": {"enum": ["project", "scene", "avatar", "object", "component"]}}}}},
                "else": {"properties": {"executionTarget": {"properties": {"scope": {"const": "project"}}}}},
            },
            "else": {"properties": {"executionTarget": {"properties": {"scope": {"const": "component"}}}}},
        }
    if "material_texture" in name or "texture_import_settings" in name:
        conditional_identity = {"properties": {"executionTarget": {"properties": {"scope": {"enum": ["project", "scene", "avatar", "object", "component"]}}}}}
    elif name.endswith("scene_transition"):
        identity["actionAwareScope"] = {
            "when": {"action": "open_single"},
            "minimumScope": "project",
            "otherwise": "scene",
        }
    elif name.endswith("create_gameobject"):
        identity["actionAwareScope"] = {
            "when": {"resolvedNonEmpty": ["parent_path", "parentPath"]},
            "minimumScope": "object",
            "otherwise": "scene",
        }
    elif "flatten_material_variant" in name or "preview_material_variant_flatten" in name:
        conditional_identity = {"properties": {"executionTarget": {"properties": {"scope": {"const": "project"}}}}}
    elif name.endswith(("apply_shader_tuning", "restore_shader_tuning", "reapply_shader_tuning_history", "apply_shader_tuning_preset")):
        conditional_identity = {"properties": {"executionTarget": {"properties": {"scope": {"enum": ["avatar", "object", "component"]}}}}}
    if conditional_identity:
        identity["parameterScope"] = conditional_identity
    return identity


def _identity_condition_text(condition: Mapping[str, Any]) -> str:
    terms = [str(key) + " is present" for key in condition.get("required", [])]
    if "anyOf" in condition:
        terms.append("(" + " or ".join(_identity_condition_text(item) for item in condition["anyOf"]) + ")")
    for key, value in condition.get("properties", {}).items():
        if value.get("minLength") == 1:
            terms.append(str(key) + " is non-empty")
    if "not" in condition:
        terms.append("none of (" + _identity_condition_text(condition["not"]) + ")")
    return " and ".join(terms)


def _identity_parameter_scope_text(rule: Mapping[str, Any]) -> str:
    if "if" in rule:
        return ("if " + _identity_condition_text(rule["if"]) + ": "
                + _identity_parameter_scope_text(rule["then"]) + "; otherwise: "
                + _identity_parameter_scope_text(rule["else"]))
    scope = rule["properties"]["executionTarget"]["properties"]["scope"]
    values = scope.get("enum") or [scope["const"]]
    return "accepted scope: " + " or ".join(values)


def execution_target_scope_guidance(name: str, *, write: bool, required: bool,
                                    identity: Mapping[str, Any] | None = None) -> str:
    """Render the shared identity policy into provider-preserved field prose."""

    policy = identity if identity is not None else tool_identity_metadata(name, write=write)
    prefix = "Identity scope: "
    if name in _READ_IDENTITY_BOOTSTRAP_TOOLS:
        return prefix + "identity bootstrap or refresh; preserve the supplied reference at its own bound scope, without binding an unrelated target."
    if policy.get("writeBinding") == "handler_managed":
        return prefix + "handler-managed preparation or receipt; no runtime executionTarget binding is required."
    if not write and not required:
        return prefix + "optional read reference; if supplied, it is validated at its own bound scope, not required to reach the tool's scope."
    if policy.get("actionAwareScope"):
        action = policy["actionAwareScope"]
        condition = action["when"]
        when = ("str(" + " or ".join(condition["resolvedNonEmpty"]) + " or '').strip() is non-empty"
                if "resolvedNonEmpty" in condition else ", ".join(f"{key}={value}" for key, value in condition.items()))
        return prefix + f"when {when}, minimum scope: {action['minimumScope']}; otherwise minimum scope: {action['otherwise']}. Select this scope when binding the exact target."
    if policy.get("parameterScope"):
        return prefix + _identity_parameter_scope_text(policy["parameterScope"]) + ". Bind a reference for the selected operation and exact target."
    scope = str(policy["scope"])
    text = prefix + "minimum scope: " + scope + ". Bind a reference at this scope or a more specific scope before calling."
    if scope == "object":
        text += " Bind the existing host object; an avatar reference is insufficient."
    elif scope == "component":
        text += " Bind the exact existing component; an avatar or object reference is insufficient."
    return text


def with_execution_target_scope_guidance(schema: Mapping[str, Any], *, name: str, write: bool,
                                        identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Append scope prose while retaining the original field and schema constraints."""

    projected = deepcopy(dict(schema))
    target = projected.get("properties", {}).get("executionTarget")
    if isinstance(target, dict):
        description = str(target.get("description") or "")
        guidance = execution_target_scope_guidance(name, write=write,
            required="executionTarget" in projected.get("required", []), identity=identity)
        if guidance not in description:
            target["description"] = (description + " " + guidance).strip()
    return projected


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
    runtime_identity_required = write_runtime_identity_required(name) if write else read_runtime_identity_required(name)
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
        "requiredIdentity": tool_identity_metadata(name, write=write, input_schema=result.get("inputSchema")),
        "requiredResources": [],
        "producedResources": [],
        "approval": {"required": bool(write), "mode": "risk_based" if write else "none"},
        "checkpoint": {"required": bool(write), "policy": "before_mutation" if write else "not_applicable"},
        "rollback": {"policy": "receipt_declared" if write else "not_applicable"},
        "freshReadback": {"required": bool(write), "mustUseExactTarget": True},
        "errorModel": "vrcforge.tool_result.v1 with fail-closed identity errors",
    })
    conditional_identity = result["requiredIdentity"].get("parameterScope")
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
            "requiredAtRuntime": runtime_identity_required,
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
    if add_identity_schema and name not in _READ_IDENTITY_BOOTSTRAP_TOOLS:
        schema = deepcopy(dict(result.get("inputSchema") or {}))
        properties = dict(schema.get("properties") or {})
        properties.setdefault("executionTarget", {
            "type": "object",
            "description": "Exact Stage 1 identity envelope; hierarchy paths are display/navigation only.",
            "$ref": "#/$defs/vrcforge.execution_target.v1",
        })
        schema["properties"] = properties
        schema.setdefault("$defs", {})["vrcforge.execution_target.v1"] = {
            "type": "object",
            "description": "See _meta.requiredIdentity; runtime rejects identity drift and never falls back to hierarchy names.",
            "required": ["schema", "namespace", "scope", "project", "editor"],
            "additionalProperties": True,
        }
    else:
        schema = deepcopy(dict(result.get("inputSchema") or {}))
        properties = dict(schema.get("properties") or {})
    if conditional_identity:
        schema.setdefault("allOf", []).append(conditional_identity)
    properties["promptSkillProvenance"] = {
        "type": "object",
        "description": "Optional exact provenance returned by prompts/get; Gateway rejects stale or mismatched Skill identity.",
        "$ref": "#/$defs/vrcforge.prompt_skill_provenance.v1",
    }
    schema["properties"] = properties
    schema.setdefault("$defs", {})["vrcforge.prompt_skill_provenance.v1"] = {
        "type": "object",
        "required": ["skillId", "version", "contentHash"],
        "properties": {
            "skillId": {"type": "string"},
            "version": {"type": "string"},
            "contentHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "supportContentHash": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "Exact support-content digest returned by prompts/get; required when the Skill declares support files."},
            "hashScope": {"type": "string", "description": "contentHash binds Prompt guidance and support declarations; supportContentHash separately binds retrieved UTF-8 contents."},
            "source": {"type": "string"},
            "packageId": {"type": "string"},
        },
        "additionalProperties": True,
    }
    result["inputSchema"] = with_execution_target_scope_guidance(schema, name=name, write=write, identity=result["requiredIdentity"])
    result["outputSchema"] = _result_schema(write)
    result["_meta"] = metadata
    return result
