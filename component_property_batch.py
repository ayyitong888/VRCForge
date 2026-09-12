"""Bounded read batching; every component retains its exact bound identity."""
from collections.abc import Mapping
import re

from execution_target import validate_execution_target, validate_runtime_execution_target


def _fields(*names):
    return {name: {"type": "string", "minLength": 1} for name in names}


COMPONENT_PROPERTY_TARGET_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "description": "Exact component executionTarget returned by bind_execution_target; never synthesize or replace identity with paths.",
    "required": ["schema", "namespace", "scope", "project", "editor", "scene", "object", "component"],
    "properties": {
        "schema": {"const": "vrcforge.execution_target.v1"}, "scope": {"const": "component"},
        "namespace": {"type": "string", "minLength": 1},
        "project": {"type": "object", "required": ["root", "projectId"], "properties": _fields("root", "projectId")},
        "editor": {"type": "object", "required": ["unityPid", "processStartTime", "coreInstanceId"],
                   "properties": {**_fields("processStartTime", "coreInstanceId"), "unityPid": {"type": "integer", "minimum": 1}}},
        "scene": {"type": "object", "required": ["assetPath", "guid", "revision", "digest"],
                  "properties": _fields("assetPath", "absolutePath", "guid", "revision", "digest")},
        "avatar": {"type": "object", "required": ["globalObjectId", "exactHierarchyPath"],
                   "properties": _fields("globalObjectId", "exactHierarchyPath")},
        "object": {"type": "object", "required": ["globalObjectId", "exactHierarchyPath"],
                   "properties": _fields("globalObjectId", "exactHierarchyPath")},
        "component": {"type": "object", "required": ["globalObjectId", "type"], "properties": _fields("globalObjectId", "type")},
    },
}

COMPONENT_PROPERTY_QUERIES_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": 128,
    "description": "Read up to 128 bound components and 512 total simple properties in one synchronous Core call. Each row needs its own component executionTarget in the anchor's project/editor/scene/avatar namespace. No nested paths, collections, or automatic binding.",
    "items": {
        "type": "object", "additionalProperties": False,
        "required": ["gameObjectPath", "componentType", "propertyNames", "executionTarget"],
        "properties": {
            "gameObjectPath": {"type": "string", "minLength": 1},
            "componentType": {"type": "string", "minLength": 1, "description": "Exact fully qualified type in the component identity."},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
            "propertyNames": {"type": "array", "minItems": 1, "maxItems": 16, "uniqueItems": True,
                              "items": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}},
            "executionTarget": COMPONENT_PROPERTY_TARGET_SCHEMA,
        },
    },
}


def prepare_component_property_batch(params):
    """Validate the complete batch before invoking Core, without repeated scene hashing."""
    scalar_keys = {"gameObjectPath", "game_object_path", "componentType", "component_type", "componentIndex", "component_index",
                   "propertyPath", "property_path", "maxItems", "max_items"}
    if scalar_keys.intersection(params):
        raise ValueError("queries and scalar property arguments are mutually exclusive.")
    queries = params.get("queries")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 128:
        raise ValueError("queries must contain 1–128 components.")
    project_root = params.get("projectPath")
    if not isinstance(project_root, str) or not project_root.strip():
        raise ValueError("projectPath is required.")
    anchor = validate_runtime_execution_target(params.get("executionTarget"), project_root=project_root, required_scope="component")
    if anchor.get("scope") != "component":
        raise ValueError("The batch anchor must be an exact component executionTarget.")
    projected, seen, property_count = [], set(), 0
    for query in queries:
        if not isinstance(query, Mapping) or set(query) - {"gameObjectPath", "componentType", "componentIndex", "propertyNames", "executionTarget"}:
            raise ValueError("Invalid component property query fields.")
        names = query.get("propertyNames")
        if (not isinstance(names, list) or not 1 <= len(names) <= 16
                or any(not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None for name in names)
                or len(set(names)) != len(names)):
            raise ValueError("propertyNames must contain 1–16 distinct simple property names.")
        property_count += len(names)
        if property_count > 512:
            raise ValueError("A batch may read at most 512 properties.")
        component_index = query.get("componentIndex", 0)
        if type(component_index) is not int or component_index < 0:
            raise ValueError("componentIndex must be a nonnegative integer.")
        target = validate_execution_target(query.get("executionTarget"), project_root=project_root, required_scope="component")
        if target.get("scope") != "component" or any(target.get(group) != anchor.get(group) for group in ("project", "editor", "scene", "avatar")):
            raise ValueError("Every query must share the verified anchor project/editor/scene/avatar identity.")
        if query.get("gameObjectPath") != target["object"]["exactHierarchyPath"] or query.get("componentType") != target["component"]["type"]:
            raise ValueError("Query path/type differs from its bound component identity.")
        component_id = target["component"]["globalObjectId"]
        if component_id in seen:
            raise ValueError("Duplicate component query; combine its propertyNames.")
        seen.add(component_id)
        projected.append({
            "gameObjectPath": query["gameObjectPath"], "componentType": query["componentType"],
            "componentIndex": component_index, "propertyNames": list(names),
            "objectGlobalObjectId": target["object"]["globalObjectId"], "componentGlobalObjectId": component_id,
            "scenePath": target["scene"]["assetPath"], "sceneGuid": target["scene"]["guid"],
            **({"avatarGlobalObjectId": target["avatar"]["globalObjectId"], "avatarPath": target["avatar"]["exactHierarchyPath"]} if target.get("avatar") else {}),
        })
    return {"queries": projected}
