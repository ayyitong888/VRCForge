"""Requests for Unity's discovered editor extension points, without plugin adapters."""
from __future__ import annotations

from typing import Any


def editor_menu_request(arguments: dict[str, Any]) -> dict[str, Any]:
    path = arguments.get("menuItemPath")
    selected = arguments.get("selectionGlobalObjectIds")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("menuItemPath must be one exact discovered menu path.")
    if (not isinstance(selected, list) or len(selected) > 32
            or any(not isinstance(item, str) or not item.strip() for item in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError("selectionGlobalObjectIds must be an explicit array of 0–32 unique exact object identities.")
    return {"menuItemPath": path.strip(), "selectionGlobalObjectIds": list(selected)}


EDITOR_CAPABILITIES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["projectPath"],
    "properties": {
        "projectPath": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": ["menus", "windows"], "default": "menus"},
        "search": {"type": "string"},
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
}

MATERIAL_PROPERTIES_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["projectPath", "materialAssetPath"],
    "properties": {
        "projectPath": {"type": "string", "minLength": 1},
        "materialAssetPath": {"type": "string", "pattern": "^Assets/.*\\.mat$"},
        "search": {"type": "string"},
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 256},
    },
}

EDITOR_MENU_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["projectPath", "menuItemPath", "selectionGlobalObjectIds"],
    "properties": {
        "projectPath": {"type": "string", "minLength": 1},
        "menuItemPath": {"type": "string", "minLength": 1},
        "selectionGlobalObjectIds": {"type": "array", "maxItems": 32, "uniqueItems": True,
                                     "items": {"type": "string", "minLength": 1}},
    },
}
