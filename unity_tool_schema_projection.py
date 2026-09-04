"""Shared model-facing schema projection, including the exact execution identity contract."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

import runtime_planner_service as planner_policy
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


def _with_execution_target_schema(schema: Mapping[str, Any], *, required: bool) -> dict[str, Any]:
    projected = deepcopy(dict(schema))
    properties = dict(projected.get("properties") or {})
    if "projectPath" not in properties:
        return projected
    properties["executionTarget"] = {
        "type": "object",
        "description": (
            "Exact vrcforge.execution_target.v1 envelope returned by "
            "vrcforge_bind_execution_target. Hierarchy paths are display-only "
            "and never substitute for GlobalObjectId identity."
        ),
        "required": ["schema", "namespace", "scope", "project", "editor"],
        "additionalProperties": True,
    }
    projected["properties"] = properties
    if required:
        required_fields = list(projected.get("required") or [])
        if "executionTarget" not in required_fields:
            required_fields.append("executionTarget")
        projected["required"] = required_fields
    return projected

def canonical_unity_read_tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Return the one model-facing schema shared by internal and external Agents."""

    name = str(tool_name or "").strip()
    registered = UNITY_READ_TOOL_INPUT_SCHEMAS.get(name)
    if isinstance(registered, Mapping):
        return (
            _with_execution_target_schema(registered, required=True)
            if name in {"vrcforge_get_property"} or name.startswith("vrcforge_preview_")
            else deepcopy(dict(registered))
        )
    if name.startswith("vrcforge_preview_"):
        write_name = "vrcforge_" + name.removeprefix("vrcforge_preview_")
        paired = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.get(write_name)
        if isinstance(paired, Mapping):
            return _with_execution_target_schema(paired, required=True)
    hinted = planner_policy.planner_tool_input_schema(name)
    if hinted:
        return (
            _with_execution_target_schema(hinted, required=True)
            if name in {"vrcforge_get_property"}
            else deepcopy(dict(hinted))
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

    # This is part of the canonical write schema, not an external-MCP-only
    # decoration. Internal and external Agents must reason over the exact same
    # namespace lock contract even though their visible Tool projections differ.
    return _with_execution_target_schema(schema, required=True)
