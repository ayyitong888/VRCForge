"""Optional offline inspection of Unity text assets, never an execution gate."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Callable

import yaml

TOOL_NAME = "vrcforge_inspect_unity_yaml"
INPUT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["projectPath", "assetPath"], "properties": {
        "projectPath": {"type": "string"},
        "assetPath": {"type": "string", "description": "Project-relative scene/prefab/text asset to inspect without loading Unity."},
        "checkExternalReferences": {"type": "boolean", "default": False, "description": "Also look for referenced GUIDs in existing Assets, Packages and Library/PackageCache metadata. Missing GUIDs are unresolved, not proof of corruption."},
    }}
_HEADER = re.compile(r"^--- !u!([0-9]+) &(-?[0-9]+)(?: stripped)?[ \t]*\r?$", re.MULTILINE)


def inspect_unity_yaml(params: dict[str, Any], resolve_project: Callable[[str], str]) -> dict[str, Any]:
    root = Path(resolve_project(str(params["projectPath"]))).resolve()
    path = (root / str(params["assetPath"])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("assetPath must remain inside the selected project.")
    try:
        text = path.read_text(encoding="utf-8-sig")
        headers = _HEADER.findall(text)
        if not headers:
            return {"ok": False, "error": "This file has no Unity YAML object headers; binary assets are not supported."}
        # Unity's object tags/anchors carry classID/fileID, not YAML aliases.
        # Keep those IDs separately; PyYAML parses the ordinary data mappings.
        # BaseLoader retains GUIDs verbatim (including digit-only GUIDs) and
        # does not construct Python objects from document tags.
        documents = list(yaml.load_all(_HEADER.sub("---", text), Loader=yaml.BaseLoader))
        if len(documents) != len(headers):
            return {"ok": False, "error": "Unity object headers and YAML documents do not match."}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return {"ok": False, "error": str(exc), "assetPath": str(params["assetPath"])}
    counts = Counter(int(file_id) for _, file_id in headers)
    findings = [{"severity": "error", "code": "duplicate_file_id", "fileID": key} for key, count in counts.items() if count > 1]
    external: set[str] = set()

    def visit(value: Any, owner: int, location: str) -> None:
        if isinstance(value, dict):
            if "fileID" in value and set(value) <= {"fileID", "guid", "type"}:
                raw_ref = value["fileID"]
                ref = int(raw_ref) if isinstance(raw_ref, str) and re.fullmatch(r"-?[0-9]+", raw_ref) else None
                guid = str(value.get("guid") or "")
                if guid and guid.strip("0"):
                    external.add(guid.lower())
                elif ref is not None and ref != 0 and ref not in counts:
                    findings.append({"severity": "warning", "code": "unresolved_local_reference", "ownerFileID": owner, "property": location, "fileID": ref,
                                     "detail": "Reference-shaped mapping has no local object with this ID. Unity field types must distinguish object references from custom data."})
            for key, child in value.items():
                visit(child, owner, f"{location}.{key}" if location else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, owner, f"{location}[{index}]")

    for (_, owner), document in zip(headers, documents):
        visit(document, int(owner), "")
        behaviour = document.get("MonoBehaviour") if isinstance(document, dict) else None
        script = behaviour.get("m_Script") if isinstance(behaviour, dict) else None
        if isinstance(script, dict) and script.get("fileID") == "0":
            findings.append({"severity": "error", "code": "missing_script_reference", "ownerFileID": int(owner)})

    unchecked = ["Unity deserialization and component type compatibility", "managed-reference RID/type compatibility", "external subasset fileID validity"]
    if params.get("checkExternalReferences") and external:
        from project_memory_index import read_meta_guid
        unresolved = set(external)
        for folder in (root / "Assets", root / "Packages", root / "Library/PackageCache"):
            if folder.is_dir():
                for meta in folder.rglob("*.meta"):
                    try:
                        unresolved.discard(read_meta_guid(meta))
                    except OSError:
                        continue
                    if not unresolved:
                        break
        for guid in sorted(unresolved):
            findings.append({"severity": "warning", "code": "unresolved_external_guid", "guid": guid,
                             "detail": "Not found in local metadata. Built-in resources and absent package sources require Unity to resolve."})
    elif external:
        unchecked.append("external GUID lookup (enable checkExternalReferences to inspect local metadata)")
    return {"ok": True, "assetPath": str(params["assetPath"]), "objectCount": len(headers),
            "externalGuidCount": len(external), "findings": findings,
            "status": "errors_found" if any(f["severity"] == "error" for f in findings) else "warnings_found" if findings else "no_findings_in_checked_scope",
            "unchecked": unchecked, "unityLoadVerified": False, "mutationStarted": False}


def register_yaml_inspection(gateway: Any, resolve_project: Callable[[str], str]) -> None:
    gateway.register_tool(TOOL_NAME,
        "When to use: inspect a selected project's existing Unity YAML scene/prefab after suspected file damage, even when Unity is down. Reports syntax, duplicate object IDs, missing local references and zero script references; optional metadata lookup reports unresolved external GUIDs. When NOT to use: do not run before normal writes, repair files, or treat no findings as proof of Unity type/managed-reference validity. Negative example: offline text inspection cannot certify that a scene will load or play.",
        "read/debug", lambda params: inspect_unity_yaml(params, resolve_project))
