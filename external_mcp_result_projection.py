"""External MCP presentation only; immutable resources retain original results."""
from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any
from jsonpointer import JsonPointer, JsonPointerException

RESULT_MODE_META_KEY = "io.vrcforge/resultMode"
TOOL_NAMES_META_KEY = "io.vrcforge/toolNames"
TOOL_SELECTION = {
    "requestMetaKey": TOOL_NAMES_META_KEY,
    "schema": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True,
               "items": {"type": "string", "minLength": 1}},
    "usage": "tools/list only. Use exact names from vrcforge_list_tool_blocks for a leaf, then load that leaf. Omit for all visible tools. Selection does not activate or authorize tools.",
}
RESOURCE_SELECTION_META_KEY = "io.vrcforge/resourceSelection"
RESOURCE_SELECTION = {
    "requestMetaKey": RESOURCE_SELECTION_META_KEY,
    "schema": {"type": "object", "additionalProperties": False, "required": ["pointer"],
               "properties": {"pointer": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                              "limit": {"type": "integer", "minimum": 1, "maximum": 128},
                              "fields": {"type": "array", "minItems": 1, "maxItems": 16, "uniqueItems": True, "items": {"type": "string", "minLength": 1}}}},
    "usage": "resources/read only; RFC6901 pointer starts at contents[0].text JSON. VRCForge envelopes store the original receipt at /data/result; its result is /data/result/result. Append exact object keys (~ becomes ~0, / becomes ~1). Optional offset/limit page arrays; fields select exact top-level object keys without trimming their values. Omit this metadata for the original full resource.",
}
INLINE_RESULT_BYTES = 12_000
RESULT_PRESENTATION = {
    "requestMetaKey": RESULT_MODE_META_KEY,
    "default": "compact",
    "modes": ["compact", "full"],
    "expansion": "Read operationResource with resources/read for the original receipt. Never replay a write to expand its result.",
}


def result_mode(params: Mapping[str, Any]) -> str:
    meta = params.get("_meta")
    value = meta.get(RESULT_MODE_META_KEY, "compact") if isinstance(meta, Mapping) else "compact"
    if value not in ("compact", "full"):
        raise ValueError(f"{RESULT_MODE_META_KEY} must be compact or full")
    return value


def tool_names(params: Mapping[str, Any], method: str) -> list[str] | None:
    meta = params.get("_meta")
    if not isinstance(meta, Mapping) or TOOL_NAMES_META_KEY not in meta:
        return None
    names = meta[TOOL_NAMES_META_KEY]
    if method != "tools/list":
        raise ValueError(f"{TOOL_NAMES_META_KEY} is only valid for tools/list")
    if (
        not isinstance(names, list) or not 1 <= len(names) <= 32
        or any(not isinstance(name, str) or not name or name != name.strip() for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError(f"{TOOL_NAMES_META_KEY} must contain 1..32 unique exact non-empty tool names")
    return names


def select_tools(tools: list[dict[str, Any]], names: list[str] | None, *, mode: str = "compact") -> list[dict[str, Any]]:
    if names is None:
        # This hint affects presentation only. The routers retain the original call catalogue.
        return tools if mode == "full" else [tool for tool in tools if (tool.get("_meta") or {}).get("io.vrcforge/defaultVisible") is not False]
    missing = sorted(set(names) - {tool["name"] for tool in tools})
    if missing:
        raise ValueError(f"{TOOL_NAMES_META_KEY} names are not visible in the current activation/exposure: {', '.join(missing)}. Discover and load the matching leaf first; selection grants no permission.")
    return [tool for tool in tools if tool["name"] in names]


def resource_selection(params: Mapping[str, Any], method: str) -> dict[str, Any] | None:
    meta = params.get("_meta")
    if not isinstance(meta, Mapping) or RESOURCE_SELECTION_META_KEY not in meta:
        return None
    selection = meta[RESOURCE_SELECTION_META_KEY]
    if method != "resources/read" or not isinstance(selection, Mapping):
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} requires an object on resources/read")
    if set(selection) - {"pointer", "offset", "limit", "fields"} or not isinstance(selection.get("pointer"), str):
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} requires pointer and only optional offset/limit/fields")
    if "offset" in selection and (type(selection["offset"]) is not int or selection["offset"] < 0):
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} offset must be a non-negative integer")
    if "limit" in selection and (type(selection["limit"]) is not int or not 1 <= selection["limit"] <= 128):
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} limit must be an integer in 1..128")
    if "fields" in selection:
        fields = selection["fields"]
        if (not isinstance(fields, list) or not 1 <= len(fields) <= 16
            or any(not isinstance(field, str) or not field or field != field.strip() for field in fields)
            or len(set(fields)) != len(fields)):
            raise ValueError(f"{RESOURCE_SELECTION_META_KEY} fields must contain 1..16 unique exact non-empty names")
    try:
        JsonPointer(selection["pointer"])
    except JsonPointerException as exc:
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} pointer must follow RFC6901") from exc
    return dict(selection)


def project_resource(value: Mapping[str, Any], selection: dict[str, Any] | None) -> dict[str, Any]:
    if selection is None:
        return dict(value)
    contents = value.get("contents")
    if not isinstance(contents, list) or len(contents) != 1 or not isinstance(contents[0], Mapping) or not isinstance(contents[0].get("text"), str):
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} requires exactly one JSON text resource")
    try:
        document = json.loads(contents[0]["text"])
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} resource is not valid JSON") from exc
    pointer = JsonPointer(selection["pointer"])
    node = document
    try:
        for part in pointer.parts:
            if not isinstance(node, (dict, list)) or (isinstance(node, list) and not re.fullmatch(r"0|[1-9][0-9]*", part)):
                raise JsonPointerException("Not an object member or RFC6901 array index")
            node = pointer.walk(node, part)
    except (JsonPointerException, IndexError, KeyError, ValueError) as exc:
        # Library exceptions may contain the original document; do not echo it.
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} pointer does not resolve to an existing JSON value") from exc
    source = document if isinstance(document, dict) else {}
    view = {
        "schema": "vrcforge.resource_selection.v1", "sourceUri": contents[0].get("uri"),
        "sourceRevision": source.get("revision"), "sourceContentHash": source.get("contentHash"),
        "selectedPointer": selection["pointer"],
        "sourceMetadata": {key: source[key] for key in ("resourceType", "schemaVersion", "identity", "capturedAt", "stale", "staleReason") if key in source},
    }
    paged = "offset" in selection or "limit" in selection
    if paged and not isinstance(node, list):
        raise ValueError(f"{RESOURCE_SELECTION_META_KEY} offset/limit require an array value")
    if isinstance(node, list):
        offset = selection.get("offset", 0)
        limit = selection.get("limit", 32) if paged else len(node)
        if offset > len(node) or (node and offset == len(node)):
            raise ValueError(f"{RESOURCE_SELECTION_META_KEY} offset is outside the selected array")
        count = len(node)
        node = node[offset:offset + limit]
        view.update(arrayCount=count, offset=offset, returnedCount=len(node), nextOffset=offset + len(node) if offset + len(node) < count else None)
    if "fields" in selection:
        fields = selection["fields"]
        objects = node if isinstance(node, list) else [node]
        if any(not isinstance(item, dict) or any(field not in item for field in fields) for item in objects):
            raise ValueError(f"{RESOURCE_SELECTION_META_KEY} fields require objects containing every requested field")
        projected = [{field: item[field] for field in fields} for item in objects]
        node = projected if isinstance(node, list) else projected[0]
        view["selectedFields"] = fields
    view["value"] = node
    return {"contents": [{"uri": contents[0].get("uri"), "mimeType": "application/json",
                          "text": json.dumps(view, ensure_ascii=False, separators=(",", ":"), allow_nan=False)}],
            "structuredContent": {key: item for key, item in view.items() if key != "value"}}


def _bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _json_copy(text: Any, value: Any) -> bool:
    if not isinstance(text, str):
        return False
    try:
        canonical = lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return canonical(json.loads(text)) == canonical(value)
    except (ValueError, TypeError):
        return False


def _without_transport_copies(value: Any, path: str, omitted: list[str]) -> Any:
    """Remove only proven JSON copies in known transport envelopes."""
    if isinstance(value, list):
        return [_without_transport_copies(item, f"{path}[{index}]", omitted) for index, item in enumerate(value)]
    if not isinstance(value, Mapping):
        return value
    view = dict(value)
    if (
        type(value.get("exitCode")) is int and isinstance(value.get("stderr"), str)
        and "payload" in value and _json_copy(value.get("stdout"), value["payload"])
    ):
        del view["stdout"]
        omitted.append(f"{path}.stdout")
    if (
        isinstance(value.get("content"), list) and "structuredContent" in value
        and isinstance(value.get("isError"), bool)
    ):
        blocks = []
        for index, block in enumerate(value["content"]):
            if (
                isinstance(block, Mapping) and set(block) == {"type", "text"}
                and block.get("type") == "text" and _json_copy(block.get("text"), value["structuredContent"])
            ):
                omitted.append(f"{path}.content[{index}]")
            else:
                blocks.append(block)
        view["content"] = blocks
    return {key: _without_transport_copies(item, f"{path}.{key}" if path else key, omitted) for key, item in view.items()}


def _summary(value: Any) -> Any:
    """Keep scalar receipt facts and explicit counts; never pretend omitted rows were read."""
    if not isinstance(value, Mapping):
        return {"itemCount": len(value)} if isinstance(value, list) else value
    summary: dict[str, Any] = {}
    omitted: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or isinstance(item, (bool, int, float, str)):
            summary[key] = item
        elif key in {"summary", "selection", "paging", "readHints", "index"} and _bytes(item) <= INLINE_RESULT_BYTES:
            summary[key] = item
        elif isinstance(item, list):
            omitted[key] = {"itemCount": len(item)}
        elif isinstance(item, Mapping):
            # Persistence and verification receipts often nest their scalar facts one level.
            summary[key] = {k: v for k, v in item.items() if v is None or isinstance(v, (bool, int, float, str))}
            omitted[key] = {"fieldCount": len(item)}
    if omitted:
        summary["omittedFields"] = omitted
    return summary


def project_result(value: Mapping[str, Any], *, mode: str, resource_readable: bool) -> dict[str, Any]:
    result = dict(value)
    uri = result.get("operationResource")
    # Existing callers without captured resources must continue receiving full data.
    if mode == "full" or not resource_readable or not isinstance(uri, str) or not uri.startswith("vrcforge://"):
        return result
    omitted = []
    result = _without_transport_copies(result, "", omitted)
    for key in ("result", "preview"):
        item = result.get(key)
        if isinstance(item, (Mapping, list)) and _bytes(item) > INLINE_RESULT_BYTES:
            result[key] = _summary(item)
            omitted.append(key)
    for key in ("outcome", "errorDetails", "writeFailure"):
        item = result.get(key)
        if not isinstance(item, Mapping):
            continue
        view = dict(item)
        for duplicate in ("diagnostics", "rawResult"):
            detail = view.get(duplicate)
            redundant = (
                duplicate == "diagnostics" and isinstance(detail, Mapping)
                and "sourceError" in detail and isinstance(result.get("errorDetails"), Mapping)
            ) or (duplicate == "rawResult" and detail == value.get("result"))
            if duplicate in view and (redundant or _bytes(detail) > INLINE_RESULT_BYTES):
                del view[duplicate]
                omitted.append(f"{key}.{duplicate}")
        if isinstance(view.get("data"), (Mapping, list)) and _bytes(view["data"]) > INLINE_RESULT_BYTES:
            view["data"] = _summary(view["data"])
            omitted.append(f"{key}.data")
        result[key] = view
    if omitted:
        result["resultPresentation"] = {
            "mode": "compact", "fullResultUri": uri, "omittedFields": omitted,
            "nextAction": "Use a narrower read query for task data, or resources/read this URI for the original receipt; do not repeat a write.",
            "resourceSelection": RESOURCE_SELECTION,
        }
    return result


def _prompt_structured_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep prompt state/provenance while leaving the canonical body in messages."""
    summary: dict[str, Any] = {}
    for key in ("schema", "context", "provenance", "rules"):
        if key in value:
            summary[key] = value[key]
    skill = value.get("skill")
    if isinstance(skill, Mapping):
        skill_summary = {
            key: skill[key]
            for key in ("id", "title", "description", "whenToUse", "whenNotToUse")
            if key in skill
        }
        files = skill.get("supportFiles")
        if isinstance(files, list):
            skill_summary["supportFiles"] = [
                {key: item[key] for key in ("path", "contentHash", "sha256", "bytes") if key in item}
                if isinstance(item, Mapping) else item
                for item in files
            ]
        summary["skill"] = skill_summary
    return summary


def project_prompt(value: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    """Compact only a proven messages/structuredContent duplicate."""
    result = dict(value)
    if mode == "full":
        return result
    messages = result.get("messages")
    structured = result.get("structuredContent")
    if not isinstance(messages, list) or not isinstance(structured, Mapping):
        return result
    duplicate_index = next((
        index
        for index, message in enumerate(messages)
        if isinstance(message, Mapping)
        and isinstance(message.get("content"), Mapping)
        and message["content"].get("type") == "text"
        and _json_copy(message["content"].get("text"), structured)
    ), None)
    if duplicate_index is not None:
        result["structuredContent"] = _prompt_structured_summary(structured)
        full_content_path = f"messages[{duplicate_index}].content.text"
        result["resultPresentation"] = {
            "mode": "compact",
            "fullContentPath": full_content_path,
            "nextAction": f"Use the complete prompt body in {full_content_path}; structuredContent is concise state and provenance metadata.",
        }
    return result


def project_tool(tool: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    result = dict(tool)
    meta = dict(result.get("_meta") or {})
    if mode != "full":
        # Standard schemas remain authoritative and unchanged. Exact duplicate metadata is removable.
        if meta.get("resultContract") == result.get("outputSchema"):
            meta.pop("resultContract", None)
        for key in tuple(result):
            if key not in {"name", "title", "description", "inputSchema", "outputSchema", "annotations", "_meta"} and key in meta and result[key] == meta[key]:
                del result[key]
    meta["resultPresentation"] = RESULT_PRESENTATION
    result["_meta"] = meta
    return result
