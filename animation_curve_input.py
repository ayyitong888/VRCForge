"""Expand request-local curve templates into the existing bounded Core contract."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import PurePosixPath
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
    envelope.update({key: params[key] for key in ("animatorRootPath", "assetOnly") if key in params})
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


def approved_clip_files(arguments: dict[str, Any], evidence: Any) -> list[dict[str, Any]]:
    """Preserve the first Core preview evidence for the exact requested clip set."""
    clips = arguments.get("clips")
    if isinstance(clips, list):
        requested = [clip.get("clipPath") for clip in clips if isinstance(clip, dict)]
        if len(requested) != len(clips):
            raise ValueError("The approved curve request has an invalid clip list.")
    else:
        requested = [arguments.get("clipPath") or arguments.get("clip_path")]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 32 or len(evidence) != len(requested):
        raise ValueError("The approved curve preview did not identify an exact clip set.")
    paths: set[str] = set()
    guids: set[str] = set()
    approved = []
    for request, item in zip(requested, evidence):
        if not isinstance(request, str) or not isinstance(item, dict):
            raise ValueError("The approved curve preview returned an invalid clip.")
        path = request.strip().replace("\\", "/")
        if (item.get("clipPath") != path or not path.startswith("Assets/")
                or PurePosixPath(path).suffix.casefold() != ".anim"
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or path.casefold() in paths):
            raise ValueError("The approved curve preview returned a different clip path.")
        paths.add(path.casefold())
        exists = item.get("exists")
        if type(exists) is not bool or (isinstance(clips, list) and not exists):
            raise ValueError("The approved curve preview did not identify a saved clip.")
        record = {"clipPath": path, "exists": exists}
        if exists:
            guid = item.get("assetGuid")
            digest = item.get("fileDigest")
            version = item.get("fileVersion")
            if (not isinstance(guid, str) or not re.fullmatch(r"[0-9a-f]{32}", guid) or guid in guids
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or not isinstance(version, str) or (version and not re.fullmatch(r"[0-9]+(?::[0-9]+){3}", version))):
                raise ValueError("The approved curve preview returned invalid clip file evidence.")
            guids.add(guid)
            record.update(assetGuid=guid, fileDigest=digest, fileVersion=version)
        approved.append(record)
    return approved


CORE_TOOL_NAME = "vrc_write_animation_curve"


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    expanded = expand_animation_curve_sets(arguments)
    fields = ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"].keys() - {"projectPath", "executionTarget", "curveSets"}
    return {**{key: deepcopy(value) for key, value in expanded.items() if key in fields}, "preview": True}


def bind_authoritative_preview(wrapper: dict[str, Any], payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("preview") is not True:
        raise ValueError("The Core did not return saved clip evidence for approval.")
    arguments = build_preview_arguments(wrapper["arguments"])
    files = approved_clip_files(arguments, payload.get("clipFiles"))
    return {**wrapper, "arguments": {**arguments, "expectedClipFiles": files, "preview": False}}, payload
