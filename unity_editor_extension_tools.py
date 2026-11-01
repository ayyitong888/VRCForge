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
        "menuItemPath": {"type": "string", "minLength": 1,
                         "description": "Exact menu path returned by vrcforge_inspect_editor_capabilities(kind='menus'), including its full prefix. This field is menuItemPath, not menuPath; do not guess a translated display label."},
        "selectionGlobalObjectIds": {"type": "array", "maxItems": 32, "uniqueItems": True,
                                     "description": "Explicit selection for the callback: exact Unity object GlobalObjectIds from a current read. Use [] for no selection; never rely on Unity's current selection or send hierarchy paths here. Dispatch is not proof the plugin finished setup.",
                                     "items": {"type": "string", "minLength": 1}},
    },
}


def diagnostic_fix_request(arguments: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
    source, index, message = (arguments.get(key) for key in ("source", "index", "message"))
    project = arguments.get("projectPath")
    if source not in {"ndmf", "sdk"} or isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("Select source and index from the current report autoFix.action.")
    if not isinstance(message, str) or not message or not isinstance(project, str) or not project.strip():
        raise ValueError("The exact original message and projectPath are required.")
    request = {"projectPath": project, "source": source, "index": index, "message": message, "preview": preview}
    if source == "sdk":
        avatar = arguments.get("avatarPath")
        if not isinstance(avatar, str) or not avatar.strip():
            raise ValueError("SDK Auto Fix requires the report's exact selected avatarPath.")
        request["avatarPath"] = avatar
    return request


DIAGNOSTIC_FIX_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["projectPath", "source", "index", "message"],
    "properties": {
        "projectPath": {"type": "string", "minLength": 1},
        "source": {"type": "string", "enum": ["ndmf", "sdk"]},
        "index": {"type": "integer", "minimum": 0},
        "message": {"type": "string", "minLength": 1, "description": "Exact original autoFix.action.message; do not summarize."},
        "avatarPath": {"type": "string", "minLength": 1, "description": "Required for SDK: exact selected avatar hierarchy path from the report."},
    },
    "allOf": [{"if": {"properties": {"source": {"const": "sdk"}}}, "then": {"required": ["avatarPath"]}}],
}
