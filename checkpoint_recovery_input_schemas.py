"""Public input contracts for interrupted-apply recovery handlers."""
from __future__ import annotations

from typing import Any


INTERRUPTED_APPLY_LIST_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "projectRoot": {"type": "string", "description": "Optional exact project root filter."},
        "project_root": {"type": "string", "description": "Compatibility alias for projectRoot."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        "includeResolved": {"type": "boolean", "default": False},
        "include_resolved": {"type": "boolean", "description": "Compatibility alias for includeResolved."},
    },
}

INTERRUPTED_APPLY_SELECTOR_PROPERTIES: dict[str, Any] = {
    "recoveryId": {"type": "string", "minLength": 1, "description": "Exact recovery record id returned by list_interrupted_apply_recoveries."},
    "recovery_id": {"type": "string", "minLength": 1, "description": "Compatibility alias for recoveryId."},
    "id": {"type": "string", "minLength": 1, "description": "Compatibility alias for recoveryId."},
    "checkpointId": {"type": "string", "minLength": 1, "description": "Exact checkpoint id associated with the recovery."},
    "checkpoint_id": {"type": "string", "minLength": 1, "description": "Compatibility alias for checkpointId."},
    "includeResolved": {"type": "boolean", "default": False},
    "include_resolved": {"type": "boolean", "description": "Compatibility alias for includeResolved."},
}

INTERRUPTED_APPLY_PREVIEW_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": dict(INTERRUPTED_APPLY_SELECTOR_PROPERTIES),
}

INTERRUPTED_APPLY_RESOLVE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **INTERRUPTED_APPLY_SELECTOR_PROPERTIES,
        "confirmResolved": {"type": "boolean", "const": True, "description": "Explicit confirmation that the interrupted write was handled."},
        "confirm_resolved": {"type": "boolean", "const": True, "description": "Compatibility alias for confirmResolved."},
        "note": {"type": "string", "maxLength": 2000},
        "reason": {"type": "string", "maxLength": 2000},
    },
    "anyOf": [{"required": ["confirmResolved"]}, {"required": ["confirm_resolved"]}],
}
