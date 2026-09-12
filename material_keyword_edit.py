"""Keyword-only mode of the existing material shader assignment contract."""
from copy import deepcopy
import re
import os

SCHEMA = "vrcforge.material_keyword_edit.v1"
KEYS = ("materialAssetPath", "keywordChanges", "expectedProjectPath")
FORBIDDEN = ("assignments", "propertyChanges", "renderQueue", "shaderName", "shaderAssetPath", "rendererPath", "rendererComponentId", "slotIndex", "targetShader")


def request(arguments):
    if any(k in arguments for k in FORBIDDEN):
        raise ValueError("keywordChanges is mutually exclusive with shader assignment and renderer selectors.")
    path = arguments.get("materialAssetPath")
    if not isinstance(path, str) or not path.startswith("Assets/") or not path.endswith(".mat") or any(p in ("", ".", "..") for p in path.split("/")) or "\\" in path or ":" in path:
        raise ValueError("Keyword editing requires one exact Assets/... .mat path.")
    rows = arguments.get("keywordChanges")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ValueError("keywordChanges requires 1..128 explicit keyword edits.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"keyword", "enabled"} or not isinstance(row["keyword"], str) or not row["keyword"] or row["keyword"] != row["keyword"].strip() or len(row["keyword"]) > 256 or type(row["enabled"]) is not bool or row["keyword"] in seen:
            raise ValueError("Each unique keyword requires its exact name and a boolean enabled value.")
        seen.add(row["keyword"])
    expected_project = arguments.get("expectedProjectPath")
    if expected_project is not None and (not isinstance(expected_project, str) or not expected_project.strip()):
        raise ValueError("expectedProjectPath must be a non-empty project identity.")
    return {k: deepcopy(arguments[k]) for k in KEYS if k in arguments}


def preview(arguments):
    return dict(request(arguments), preview=True, saveAssets=True)


def _evidence(value, path):
    if not isinstance(value, dict) or value.get("materialAssetPath") != path:
        raise ValueError("Keyword evidence does not identify the requested material.")
    for key, size in (("guid", 32), ("fileDigest", 64), ("metaDigest", 64)):
        if not isinstance(value.get(key), str) or not re.fullmatch("[0-9a-f]{%s}" % size, value[key]):
            raise ValueError("Keyword evidence lacks a valid " + key)
    state = value.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("keywords"), list) or state.get("isVariant") is not False or state.get("parent") != "":
        raise ValueError("Keyword evidence requires an independent material snapshot.")
    shader = state.get("shader")
    if not isinstance(shader, dict) or not shader.get("name") or not shader.get("path") or not re.fullmatch("[0-9a-f]{32}", str(shader.get("guid", ""))) or type(shader.get("localId")) is not int or not shader.get("dependencyHash"):
        raise ValueError("Keyword evidence lacks persistent shader identity.")
    if state["keywords"] != sorted(set(state["keywords"])) or not isinstance(state.get("properties"), list):
        raise ValueError("Keyword evidence snapshot is invalid.")
    return deepcopy(value)


def _expected_state(before, rows):
    state = deepcopy(before["state"])
    keywords = set(state["keywords"])
    for row in rows:
        (keywords.add if row["enabled"] else keywords.discard)(row["keyword"])
    state["keywords"] = sorted(keywords)
    return state


def bind(wrapper, payload):
    args = request(wrapper["arguments"])
    wrapper_project = str(wrapper.get("projectPath") or "").strip()
    expected_project = str(args.get("expectedProjectPath") or wrapper_project).strip()
    if wrapper_project and expected_project and os.path.normcase(os.path.normpath(wrapper_project.replace("\\", "/"))) != os.path.normcase(os.path.normpath(expected_project.replace("\\", "/"))):
        raise ValueError("Keyword project identity conflicts with expectedProjectPath.")
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or any(payload.get(k) is not v for k, v in {"ok": True, "preview": True, "verified": True, "mutationStarted": False, "committed": False}.items()):
        raise ValueError("Keyword preview is not a verified non-mutating receipt.")
    before = _evidence(payload.get("before"), args["materialAssetPath"])
    expected = _expected_state(before, args["keywordChanges"])
    if payload.get("keywordChanges") != args["keywordChanges"] or payload.get("after") != expected or payload.get("wouldChange") is not (expected != before["state"]):
        raise ValueError("Keyword preview differs from requested edits.")
    existing = wrapper["arguments"].get("expectedKeywordEvidence")
    if existing is not None and existing != before:
        raise ValueError("Keyword precondition differs from preview.")
    args.update(expectedProjectPath=expected_project, expectedKeywordEvidence=before, preview=False, saveAssets=True)
    result = deepcopy(wrapper)
    result["arguments"] = args
    return result, {"schema": SCHEMA + ".approval", "toolName": "vrc_set_material_shader", "preview": deepcopy(payload), "rollbackRequired": True}


def validate(arguments, payload):
    args = request(arguments)
    before = _evidence(arguments.get("expectedKeywordEvidence"), args["materialAssetPath"])
    expected = _expected_state(before, args["keywordChanges"])
    changed = expected != before["state"]
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or any(payload.get(k) is not v for k, v in {"ok": True, "preview": False, "verified": True, "persistedReadback": True, "committed": True, "wouldChange": changed, "mutationStarted": changed}.items()):
        raise ValueError("Keyword apply lacks verified persistent readback.")
    readback = _evidence(payload.get("readback"), args["materialAssetPath"])
    if payload.get("before") != before or payload.get("after") != expected or payload.get("keywordChanges") != args["keywordChanges"] or readback["state"] != expected or any(readback[k] != before[k] for k in ("guid", "metaDigest")) or payload.get("commitState") != "committed":
        raise ValueError("Keyword readback differs from approved state or identity.")
    return deepcopy(payload)
