"""Expand request-local curve templates into the existing bounded Core contract."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from jsonschema import Draft202012Validator
from unity_shared_input_schemas import ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA


def expand_animation_curve_sets(params: dict[str, Any]) -> dict[str, Any]:
    """Expand local sets and explicit path groups for both transport and approval."""
    arrays = [params.get("curves", [])]
    if isinstance(params.get("clips"), list):
        arrays.extend(clip.get("curves", []) for clip in params["clips"] if isinstance(clip, dict))
    if isinstance(params.get("curveSets"), list):
        arrays.extend(params["curveSets"])
    has_groups = any(
        isinstance(row, dict) and ("bindingPaths" in row or "properties" in row)
        for rows in arrays if isinstance(rows, list) for row in rows
    )
    if "curveSets" not in params:
        if any(isinstance(clip, dict) and "curveSet" in clip for clip in params.get("clips", []) or []):
            raise ValueError("curveSet requires request-local curveSets.")
        if not has_groups:
            return params
    schema = ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA
    public = {key: value for key, value in params.items() if key in schema["properties"]}
    Draft202012Validator(schema).validate(public)
    sets = params.get("curveSets", [])
    result = dict(params)
    result.pop("curveSets", None)
    clips = []
    key_count = 0
    for clip in params.get("clips", [params]):
        if "curveSet" in clip:
            index = clip["curveSet"]
            if index >= len(sets):
                raise ValueError("Unknown curveSet index in request-local curveSets.")
            curves = sets[index]
        else:
            curves = clip["curves"]
        expanded = []
        for row in curves:
            paths = row.get("bindingPaths")
            properties = row["properties"] if paths is not None else [row]
            count = len(paths) if paths is not None else 1
            if len(expanded) + count * len(properties) > 256:
                raise ValueError("Expanded curves exceed 256 entries per clip.")
            key_count += count * sum(len(prop["keys"]) if "keys" in prop else 1 for prop in properties)
            if key_count > 4096:
                raise ValueError("Expanded curves exceed 4096 total keys.")
            if paths is None:
                expanded.append(deepcopy(row))
            else:
                for path in paths:
                    common = {"bindingPath": path, "componentType": row["componentType"]}
                    if "overwriteExisting" in row:
                        common["overwriteExisting"] = row["overwriteExisting"]
                    expanded.extend({**common, **deepcopy(prop)} for prop in properties)
        clips.append({"clipPath": clip["clipPath"], "curves": expanded})
    # The Core receives these fields, not project/approval metadata. Preserve its
    # expanded-size ceiling; a small template cannot authorize an oversized batch.
    envelope = {"clips": clips} if "clips" in params else dict(clips[0])
    envelope["preview"] = False
    if "action" in params:
        envelope["action"] = params["action"]
    if len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 512 * 1024:
        raise ValueError("Expanded curves exceed 512 KiB.")
    if "clips" in params:
        result["clips"] = clips
    else:
        result["curves"] = clips[0]["curves"]
    return result
