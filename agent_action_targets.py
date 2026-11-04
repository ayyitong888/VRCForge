"""Structured action targets shared by approval evidence and task obligations."""
from collections.abc import Mapping
from typing import Any

from execution_target import execution_target_identity, is_execution_target_reference


def action_target(arguments: Any) -> dict[str, Any]:
    """Keep explicit selectors, without treating content or prose as a target."""
    if not isinstance(arguments, Mapping):
        return {}
    target: dict[str, Any] = {}
    for key, value in arguments.items():
        name = str(key)
        lower = name.casefold()
        if name == "executionTarget" and isinstance(value, Mapping):
            # Mutable revisions are preconditions, not the object's identity.
            target[name] = dict(value) if is_execution_target_reference(value) else execution_target_identity(value)
        elif (lower.endswith(("path", "paths")) or lower in {
            "target", "projectroot", "objectid", "globalobjectid", "componentid", "guid",
        }):
            if isinstance(value, str) and value:
                target[name] = value.replace("\\", "/") if "path" in lower or lower == "projectroot" else value
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                target[name] = [item.replace("\\", "/") for item in value]
    return target
