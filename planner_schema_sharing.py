"""Lossless sharing of repeated model-facing schemas; runtime contracts stay local."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Mapping


def _same_json_contract(left: object, right: object) -> bool:
    # Python equality conflates True and 1; JSON Schema const/enum do not.
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _shared_planner_schema_defs(
    schemas: list[dict[str, object]],
) -> dict[str, object]:
    """Share exact repeated contracts in the prompt, never in runtime schemas."""

    name = "vrcforge.prompt_skill_provenance.v1"
    occurrences: list[tuple[str, object]] = []
    for schema in schemas:
        defs = schema.get("$defs")
        if not isinstance(defs, Mapping) or name not in defs:
            continue
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if f'"$ref":"#/$defs/{name}"' in encoded:
            definition = defs[name]
            occurrences.append(
                (
                    json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    deepcopy(definition),
                )
            )
    shared = (
        {name: occurrences[0][1]}
        if len(occurrences) >= 2 and all(key == occurrences[0][0] for key, _ in occurrences[1:])
        else {}
    )
    groups: dict[str, list[dict[str, object]]] = {}
    for schema in schemas:
        projected = _planner_schema_without_shared_defs(schema, shared)
        encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        # Keep local reference scopes and anchors local. Only the already-shared
        # provenance reference may occur in these complete shared contracts.
        if any(f'"{key}":' in encoded for key in ("$defs", "definitions", "$id", "$anchor", "$dynamicRef", "$dynamicAnchor")):
            continue
        if any(ref != "#/$defs/" + name for ref in re.findall(r'"\$ref":"([^\"]+)"', encoded)):
            continue
        groups.setdefault(encoded, []).append(projected)
    for encoded, group in groups.items():
        if len(group) < 2:
            continue
        shared_name = "vrcforge.tool_input." + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
        reference = json.dumps({"$ref": "#/$defs/" + shared_name}, separators=(",", ":"))
        # Avoid factoring tiny schemas where references save no useful space.
        if len(encoded) * (len(group) - 1) <= len(reference) * len(group) + len(shared_name) + 128:
            continue
        shared[shared_name] = deepcopy(group[0])
    # Complete schemas take priority. Factor remaining top-level properties
    # only when they contain no reference scopes and the total prompt shrinks.
    property_groups: dict[str, list[object]] = {}
    for schema in schemas:
        projected = _planner_schema_without_shared_defs(schema, shared)
        encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if any(f'"{key}":' in encoded for key in ("$id", "$anchor", "$dynamicRef", "$dynamicAnchor")):
            continue
        for contract in projected.get("properties", {}).values():
            encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if not any(f'"{key}":' in encoded for key in ("$ref", "$defs", "definitions")):
                property_groups.setdefault(encoded, []).append(contract)
    for encoded, group in property_groups.items():
        if len(group) < 2:
            continue
        shared_name = "vrcforge.tool_property." + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
        if any(shared_name in schema.get("$defs", {}) for schema in schemas):
            continue
        reference = json.dumps({"$ref": "#/$defs/" + shared_name}, separators=(",", ":"))
        # Include definition labels and shared-section overhead in the saving.
        if len(encoded.encode("utf-8")) * (len(group) - 1) <= len(reference) * len(group) + len(shared_name) + 384:
            continue
        shared[shared_name] = deepcopy(group[0])
    return shared


def _planner_schema_without_shared_defs(
    schema: dict[str, object],
    shared_defs: Mapping[str, object],
) -> dict[str, object]:
    """Replace only exact contracts/definitions already in the prompt section."""

    if not shared_defs:
        return schema
    projected = deepcopy(schema)
    local_defs = dict(projected.get("$defs") or {})
    encoded_schema = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for name in shared_defs:
        if name in local_defs and json.dumps(
            local_defs[name], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) == json.dumps(
            shared_defs[name], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) and f'"$ref":"#/$defs/{name}"' in encoded_schema:
            del local_defs[name]
    if local_defs:
        projected["$defs"] = local_defs
    else:
        projected.pop("$defs", None)
    for name, definition in shared_defs.items():
        if name.startswith("vrcforge.tool_input.") and _same_json_contract(projected, definition):
            return {"$ref": "#/$defs/" + name}
    encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not any(f'"{key}":' in encoded for key in ("$id", "$anchor", "$dynamicRef", "$dynamicAnchor")):
        for key, contract in projected.get("properties", {}).items():
            for name, definition in shared_defs.items():
                if name.startswith("vrcforge.tool_property.") and _same_json_contract(contract, definition):
                    projected["properties"][key] = {"$ref": "#/$defs/" + name}
                    break
    return projected
