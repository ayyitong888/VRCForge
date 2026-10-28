"""Shared model-facing schema projection, including the exact execution identity contract."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

import runtime_planner_service as planner_policy
from mcp_tool_descriptor import (
    read_runtime_identity_required, write_runtime_identity_required, with_execution_target_scope_guidance,
)
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


_DESKTOP_ACTION_SCHEMA = {
    "type": "object",
    "description": (
        "The outer action selects the desktop bridge (usually computer_use); "
        "params.operation selects the bounded desktop operation."
    ),
    "required": ["action"],
    "properties": {
        "action": {
            "type": "string",
            "enum": ["computer_use", "screenshot", "annotation", "browser", "desktop_rescue"],
            "description": "Desktop bridge action. Use computer_use for params.operation calls.",
        },
        "params": {
            "type": "object",
            "description": (
                "Desktop operation parameters. For a read-only app catalog call, use "
                "{operation: 'list_apps'}. Other operation-specific fields are passed through."
            ),
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "list_apps", "launch_app", "list_windows", "get_window", "window_state",
                        "get_window_state", "inspect_window", "cursor_position", "screenshot",
                        "focus_window", "activate_window", "move_pointer", "move", "click", "drag",
                        "drag_pointer", "scroll", "type_text", "type", "key_press", "press_key",
                        "press", "focus_element", "invoke_element", "invoke", "set_value",
                        "set_element_value", "secondary_action", "perform_secondary_action", "wait",
                        "sleep", "sequence",
                    ],
                    "description": "Bounded desktop operation; list_apps is read-only.",
                }
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}


def _read_uses_runtime_identity(name: str) -> bool:
    """Consume descriptor policy without wrapping identity bootstrap calls."""

    return read_runtime_identity_required(name)


def _with_execution_target_schema(schema: Mapping[str, Any], *, required: bool, name: str = "", write: bool = False) -> dict[str, Any]:
    projected = deepcopy(dict(schema))
    properties = dict(projected.get("properties") or {})
    if "projectPath" not in properties:
        return with_execution_target_scope_guidance(projected, name=name, write=write)
    original_description = str(dict(properties.get("executionTarget") or {}).get("description") or "")
    properties["executionTarget"] = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "schema": {"const": "vrcforge.execution_target_ref.v1"},
                    "ref": {"type": "string", "minLength": 16},
                },
                "required": ["schema", "ref"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["schema", "namespace", "scope", "project", "editor"],
                "additionalProperties": True,
            },
        ],
        "description": (
            "Public vrcforge.execution_target_ref.v1 reference returned by "
            "vrcforge_bind_execution_target for this same project and session. "
            "Pass it unchanged as executionTarget. A legacy exact vrcforge.execution_target.v1 "
            "envelope is accepted only after the same live identity validation. Hierarchy paths "
            "are display-only and never substitute for GlobalObjectId identity."
        ),
    }
    if original_description:
        properties["executionTarget"]["description"] = original_description + " " + properties["executionTarget"]["description"]
    projected["properties"] = properties
    if required:
        required_fields = list(projected.get("required") or [])
        if "executionTarget" not in required_fields:
            required_fields.append("executionTarget")
        projected["required"] = required_fields
    return with_execution_target_scope_guidance(projected, name=name, write=write)


def canonical_unity_read_tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Return the one model-facing schema shared by internal and external Agents."""

    name = str(tool_name or "").strip()
    if name == "vrcforge_agent_desktop_action":
        return deepcopy(_DESKTOP_ACTION_SCHEMA)
    registered = UNITY_READ_TOOL_INPUT_SCHEMAS.get(name)
    if isinstance(registered, Mapping):
        # A retained checkpoint supplies its own project/restore scope, including
        # local-state recovery when no Editor is running.
        if name == "vrcforge_preview_restore_checkpoint":
            return with_execution_target_scope_guidance(registered, name=name, write=False)
        return (
            _with_execution_target_schema(registered, required=True, name=name)
            if name in {"vrcforge_get_property"} or name.startswith("vrcforge_preview_")
            else _with_execution_target_schema(registered, required=False, name=name)
            if _read_uses_runtime_identity(name)
            else with_execution_target_scope_guidance(registered, name=name, write=False)
        )
    if name.startswith("vrcforge_preview_"):
        write_name = (
            {
                "vrcforge_preview_scene_object_prefab": "vrcforge_save_scene_object_as_prefab",
                "vrcforge_preview_component_feature": "vrcforge_create_component_feature",
                "vrcforge_preview_constraint_sources": "vrcforge_set_constraint_sources",
            }.get(name, "vrcforge_" + name.removeprefix("vrcforge_preview_"))
        )
        paired = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.get(write_name)
        if isinstance(paired, Mapping):
            return _with_execution_target_schema(paired, required=True, name=write_name)
    hinted = planner_policy.planner_tool_input_schema(name)
    if hinted:
        return (
            _with_execution_target_schema(hinted, required=True, name=name)
            if name in {"vrcforge_get_property"}
            else _with_execution_target_schema(hinted, required=False, name=name)
            if _read_uses_runtime_identity(name)
            else with_execution_target_scope_guidance(hinted, name=name, write=False)
        )
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": True,
    }

def canonical_unity_write_tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Return the one write schema shared by the internal loop and external MCP."""

    name = str(tool_name or "").strip()
    registered = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.get(name)
    if isinstance(registered, Mapping):
        schema = deepcopy(dict(registered))
    else:
        hinted = planner_policy.planner_tool_input_schema(name)
        schema = deepcopy(dict(hinted)) if hinted else {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": True,
        }

    properties = dict(schema.get("properties") or {})
    properties["checkpointChoice"] = {
        "type": "object",
        "description": (
            "Session checkpoint switch: mode=skip disables checkpoints until the user explicitly re-enables them with mode=require. "
            "Change only on explicit user instruction and copy it into quote. Omit to retain the current session setting; new sessions default to checkpoints enabled."
        ),
        "required": ["mode", "quote"],
        "properties": {
            "mode": {"type": "string", "enum": ["skip", "require"]},
            "quote": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    schema["properties"] = properties

    # This is part of the canonical write schema, not an external-MCP-only
    # decoration. Internal and external Agents must reason over the exact same
    # namespace lock contract even though their visible Tool projections differ.
    return _with_execution_target_schema(
        schema, required=write_runtime_identity_required(name), name=name, write=True,
    )
