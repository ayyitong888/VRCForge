"""Scalar-only mode of the existing supervised material shader tool."""
from copy import deepcopy
import math
import json
import os
import re
import struct

SCHEMA = "vrcforge.material_property_edit.v1"
KEYS = ("materialAssetPath", "propertyChanges", "renderQueue", "expectedProjectPath")
FORBIDDEN = ("assignments", "keywordChanges", "shaderName", "shaderAssetPath", "rendererPath", "rendererComponentId", "slotIndex", "targetShader")
IMPACT_KEYS = ("sharedImpact", "sharedImpactDigestSchema", "sharedImpactDigest", "sharedImpactDisplayDigest", "sharedImpactTailDigest")


def _float32(value):
    if type(value) not in (int, float):
        raise ValueError("Material scalar values must be finite numbers, not booleans.")
    try:
        if not math.isfinite(value):
            raise ValueError("Material scalar values must be finite.")
        result = struct.unpack("f", struct.pack("f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError("Material scalar exceeds float32.") from exc
    if not math.isfinite(result):
        raise ValueError("Material scalar exceeds float32.")
    return result


def request(arguments):
    if any(k in arguments for k in FORBIDDEN):
        raise ValueError("propertyChanges cannot combine with shader assignment, keywords, or renderer selectors.")
    path = arguments.get("materialAssetPath")
    if not isinstance(path, str) or not path.startswith("Assets/") or not path.endswith(".mat") or any(p in ("", ".", "..") for p in path.split("/")) or "\\" in path or ":" in path:
        raise ValueError("Property editing requires one exact Assets/... .mat path.")
    rows = arguments.get("propertyChanges")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("propertyChanges requires 1..32 explicit scalar edits.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"propertyName", "value"} or not isinstance(row["propertyName"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", row["propertyName"]) or row["propertyName"] in seen:
            raise ValueError("Each scalar property needs one unique exact name and value.")
        _float32(row["value"])
        seen.add(row["propertyName"])
    if "renderQueue" in arguments and (type(arguments["renderQueue"]) is not int or not -1 <= arguments["renderQueue"] <= 5000):
        raise ValueError("renderQueue must be an integer -1..5000.")
    if "expectedProjectPath" in arguments and (not isinstance(arguments["expectedProjectPath"], str) or not arguments["expectedProjectPath"].strip()):
        raise ValueError("expectedProjectPath must identify the Unity project.")
    return {k: deepcopy(arguments[k]) for k in KEYS if k in arguments}


def preview(arguments):
    return dict(request(arguments), preview=True, saveAssets=True)


def _evidence(value, path):
    # Reuse the established persistent material/shader and impact contracts.
    from material_keyword_edit import _evidence as material_evidence
    from material_shader_assignment import (_canonical_shared_impact, compute_shared_impact_digest,
        compute_shared_impact_tail_digest, compute_shared_impact_commitment, IMPACT_DIGEST_SCHEMA)
    result = material_evidence(value, path)
    state = result["state"]
    if any(type(state.get(key)) is not int for key in ("renderQueue", "rawRenderQueue")) or type(state["shader"].get("renderQueue")) is not int:
        raise ValueError("Material evidence lacks raw/effective/shader queue identity.")
    if not -1 <= state["rawRenderQueue"] <= 5000 or state["renderQueue"] != (state["shader"]["renderQueue"] if state["rawRenderQueue"] == -1 else state["rawRenderQueue"]):
        raise ValueError("Material queue evidence is inconsistent.")
    impact = _canonical_shared_impact(result.get("sharedImpact"))
    if result.get("sharedImpactDigestSchema") != IMPACT_DIGEST_SCHEMA or compute_shared_impact_digest(impact) != result.get("sharedImpactDisplayDigest"):
        raise ValueError("Property shared impact display differs.")
    tail = result.get("sharedImpactTailDigest")
    if not isinstance(tail, str) or not re.fullmatch("[0-9a-f]{64}", tail):
        raise ValueError("Property shared impact tail is invalid.")
    if not impact["listsTruncated"] and compute_shared_impact_tail_digest(impact, slots=[], assets=[]) != tail:
        raise ValueError("Property shared impact tail differs.")
    if compute_shared_impact_commitment(impact, display_digest=result["sharedImpactDisplayDigest"], tail_digest=tail) != result.get("sharedImpactDigest"):
        raise ValueError("Property shared impact commitment differs.")
    return result


def _expected_state(before, args):
    state = deepcopy(before["state"])
    properties = state["properties"]
    index = {}
    for row in properties:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or row["name"] in index:
            raise ValueError("Material property evidence is ambiguous.")
        index[row["name"]] = row
    for edit in args["propertyChanges"]:
        row = index.get(edit["propertyName"])
        if row is None or row.get("type") not in ("Float", "Range", "Int"):
            raise ValueError("Requested property is not a declared scalar.")
        value = edit["value"]
        if row["type"] == "Int":
            if int(value) != value or not -(2**31) <= value < 2**31:
                raise ValueError("Shader Int values must be integral int32.")
            row["value"] = int(value)
        else:
            value32 = _float32(value)
            if row["type"] == "Range":
                bounds = row.get("range")
                if not isinstance(bounds, list) or len(bounds) != 2 or not _float32(bounds[0]) <= value <= _float32(bounds[1]) or not _float32(bounds[0]) <= value32 <= _float32(bounds[1]):
                    raise ValueError("Value is outside the declared shader Range.")
            row["value"] = value
    if "renderQueue" in args:
        state["rawRenderQueue"] = args["renderQueue"]
        state["renderQueue"] = state["shader"]["renderQueue"] if args["renderQueue"] == -1 else args["renderQueue"]
    return state


def _same_state(actual, expected):
    if not isinstance(actual, dict):
        return False
    left, right = deepcopy(actual), deepcopy(expected)
    for state in (left, right):
        if not isinstance(state.get("properties"), list):
            return False
        for row in state["properties"]:
            if isinstance(row, dict) and row.get("type") in ("Float", "Range"):
                row["value"] = _float32(row.get("value"))
            elif isinstance(row, dict) and row.get("type") == "Int":
                if type(row.get("value")) not in (int, float) or not math.isfinite(row["value"]) or int(row["value"]) != row["value"]:
                    return False
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _check_edits(payload, args):
    echoed = {**args, "propertyChanges": payload.get("propertyChanges")}
    if "renderQueue" in payload:
        echoed["renderQueue"] = payload["renderQueue"]
    request(echoed)
    if payload.get("propertyChanges") != args["propertyChanges"] or ("renderQueue" in payload) != ("renderQueue" in args) or payload.get("renderQueue") != args.get("renderQueue"):
        raise ValueError("Material property receipt differs from requested edits.")


def bind(wrapper, payload):
    args = request(wrapper["arguments"])
    project = str(wrapper.get("projectPath") or "").strip()
    expected_project = str(args.get("expectedProjectPath") or project).strip()
    normalize = lambda value: os.path.normcase(os.path.normpath(value.replace("\\", "/")))
    if project and expected_project and normalize(project) != normalize(expected_project):
        raise ValueError("Property project identity conflicts with expectedProjectPath.")
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or any(payload.get(k) is not v for k, v in {"ok": True, "preview": True, "verified": True, "mutationStarted": False, "committed": False}.items()):
        raise ValueError("Property preview is not a verified non-mutating receipt.")
    _check_edits(payload, args)
    before = _evidence(payload.get("before"), args["materialAssetPath"])
    expected = _expected_state(before, args)
    if not _same_state(payload.get("after"), expected) or payload.get("wouldChange") is not (not _same_state(before["state"], expected)):
        raise ValueError("Property preview differs from the requested final state.")
    existing = wrapper["arguments"].get("expectedPropertyEvidence")
    if existing is not None and existing != before:
        raise ValueError("Property precondition differs from preview.")
    args.update(expectedProjectPath=expected_project, expectedPropertyEvidence=before, preview=False, saveAssets=True)
    result = deepcopy(wrapper); result["arguments"] = args
    return result, {"schema": SCHEMA + ".approval", "toolName": "vrc_set_material_shader", "preview": deepcopy(payload), "rollbackRequired": True}


def validate(arguments, payload):
    args = request(arguments)
    before = _evidence(arguments.get("expectedPropertyEvidence"), args["materialAssetPath"])
    expected = _expected_state(before, args)
    changed = not _same_state(before["state"], expected)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or any(payload.get(k) is not v for k, v in {"ok": True, "preview": False, "verified": True, "persistedReadback": True, "committed": True, "wouldChange": changed, "mutationStarted": changed}.items()):
        raise ValueError("Property apply lacks verified persistent readback.")
    _check_edits(payload, args)
    readback = _evidence(payload.get("readback"), args["materialAssetPath"])
    if (readback["fileDigest"] != before["fileDigest"]) is not changed:
        raise ValueError("Property file digest change differs from the approved mutation state.")
    if payload.get("before") != before or not _same_state(payload.get("after"), expected) or not _same_state(readback["state"], expected) or any(readback[k] != before[k] for k in ("guid", "metaDigest", *IMPACT_KEYS)) or payload.get("commitState") != "committed":
        raise ValueError("Property readback differs from approved state, shared impact, or identity.")
    return deepcopy(payload)
