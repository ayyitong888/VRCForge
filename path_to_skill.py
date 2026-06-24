from __future__ import annotations

import json
import ntpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from skill_packages import SKILL_ID_RE, SkillPackageService


PATH_TO_SKILL_SCHEMA = "vrcforge.path_to_skill.v1"
DEFAULT_MIN_VRCFORGE_VERSION = "0.9.0-beta"
WORKFLOW_ENTRYPOINT = "workflows/captured-path.json"

SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"gateway[_-]?token|session[_-]?token|secret|password|credential|"
    r"authorization|cookie|private[_-]?key|signing[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
DISALLOWED_PAYLOAD_FIELD_RE = re.compile(
    r"(?:bytes|binary|blob|base64|filecontents|rawcontent|archivecontents|"
    r"zipbytes|payloadbytes|binarypayload|packagepayload|unitypackagebytes|"
    r"fbxbytes|texturebytes|materialbytes|assetpayload|paidasset|"
    r"boothzipcontents|packagecontents|screenshotbytes|"
    r"imagebytes|meshbytes|prefabbytes)",
    re.IGNORECASE,
)
ABSOLUTE_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
EMBEDDED_WINDOWS_PATH_RE = re.compile(r"(?P<path>(?<![A-Za-z0-9+.-])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|]+)")
ABSOLUTE_PATH_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9+.-])(?:[A-Za-z]:[\\/]|\\\\)")
URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
PATH_KEY_RE = re.compile(r"(?:path|root|dir|directory|file)$", re.IGNORECASE)


class PathToSkillError(ValueError):
    """Base exception for unsafe or invalid Path-to-Skill captures."""


class PathToSkillSecurityError(PathToSkillError):
    pass


class PathToSkillValidationError(PathToSkillError):
    pass


@dataclass(frozen=True)
class CapturedSkillSource:
    manifest: dict[str, Any]
    skill_markdown: str
    workflow: dict[str, Any]
    source_files: dict[str, str]

    def write_to(self, output_dir: str | Path) -> Path:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for relative, content in self.source_files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        return destination


class _CaptureContext:
    def __init__(self, project_root: str | None) -> None:
        self.project_root = _normalize_windows_path(project_root) if project_root and _is_absolute_path(project_root) else None
        self.variables: dict[str, dict[str, Any]] = {}
        self.remappings: list[dict[str, str]] = []
        self._absolute_path_variables: dict[str, str] = {}

    def sanitize(self, value: Any, field_path: str = "source") -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_item in value.items():
                key = str(raw_key)
                _reject_key(key, field_path)
                result[key] = self.sanitize(raw_item, f"{field_path}.{key}" if field_path else key)
            return result
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item, f"{field_path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise PathToSkillSecurityError(f"{field_path} contains binary data and cannot be captured.")
        if isinstance(value, str):
            return self._sanitize_text(value, field_path)
        return value

    def _sanitize_text(self, value: str, field_path: str) -> str:
        _reject_sensitive_text(value, field_path)
        stripped = value.strip()
        if URL_RE.match(stripped):
            return value
        if _is_absolute_path(stripped):
            return self._remap_absolute_path(stripped, field_path)
        if _looks_like_path_field(field_path):
            return value
        if ABSOLUTE_PATH_PREFIX_RE.search(value):
            raise PathToSkillSecurityError(
                f"{field_path} contains an embedded absolute path; capture it as a remappable path field instead."
            )
        return value

    def _remap_absolute_path(self, raw_path: str, field_path: str) -> str:
        normalized = _normalize_windows_path(raw_path)
        project_root = self.project_root
        if project_root and normalized == project_root:
            self._ensure_variable("projectPath", "Unity project root selected at import or dry-run time.")
            self._record_remapping(field_path, "projectPath", "absolute project path redacted")
            return "{{projectPath}}"
        if project_root and _is_child_windows_path(normalized, project_root):
            self._ensure_variable("projectPath", "Unity project root selected at import or dry-run time.")
            relative = ntpath.relpath(raw_path, project_root).replace("\\", "/")
            self._record_remapping(field_path, "projectPath", "absolute in-project path redacted")
            return "{{projectPath}}/" + relative
        variable_name = self._absolute_path_variables.get(normalized)
        if not variable_name:
            variable_name = self._variable_name_from_field(field_path)
            suffix = 2
            base = variable_name
            while variable_name in self.variables:
                variable_name = f"{base}{suffix}"
                suffix += 1
            self._absolute_path_variables[normalized] = variable_name
        self._ensure_variable(variable_name, f"Remap {field_path} before dry-run or apply.")
        self._record_remapping(field_path, variable_name, "absolute path redacted")
        return "{{" + variable_name + "}}"

    def _variable_name_from_field(self, field_path: str) -> str:
        key = re.split(r"[.\[]", field_path)[-1].strip("]")
        aliases = {
            "projectpath": "projectPath",
            "projectroot": "projectPath",
            "avatarpath": "avatarPath",
            "rendererpath": "rendererPath",
            "materialpath": "materialPath",
            "packagepath": "packagePath",
            "assetpath": "assetPath",
            "artifactpath": "artifactPath",
            "outputpath": "outputPath",
        }
        compact = re.sub(r"[^A-Za-z0-9]+", "", key)
        variable = aliases.get(compact.lower())
        if variable:
            return variable
        if not compact:
            compact = "path"
        return compact[0].lower() + compact[1:]

    def _ensure_variable(self, name: str, description: str) -> None:
        if name not in self.variables:
            self.variables[name] = {
                "placeholder": "{{" + name + "}}",
                "required": True,
                "description": description,
                "source": "redacted",
            }

    def _record_remapping(self, field_path: str, variable_name: str, reason: str) -> None:
        record = {"field": field_path, "variable": variable_name, "reason": reason}
        if record not in self.remappings:
            self.remappings.append(record)


def build_path_to_skill_source(
    summary: Mapping[str, Any],
    *,
    package_id: str | None = None,
    skill_name: str | None = None,
    title: str | None = None,
    version: str = "1.0.0",
    author: str = "VRCForge User",
    min_vrcforge_version: str = DEFAULT_MIN_VRCFORGE_VERSION,
) -> CapturedSkillSource:
    if not isinstance(summary, Mapping):
        raise PathToSkillValidationError("Path-to-Skill summary must be a JSON object.")
    _reject_key_tree(summary)
    workflow_id = str(summary.get("workflow") or summary.get("name") or "captured_workflow").strip()
    workflow_slug = _slug(workflow_id, fallback="captured-workflow")
    package_id = package_id or f"community.path-to-skill.{workflow_slug}"
    if not SKILL_ID_RE.fullmatch(package_id):
        raise PathToSkillValidationError("package_id must be a lowercase reverse-domain skill package id.")
    skill_name = _skill_slug(skill_name or workflow_slug)
    title = (title or _title_from_slug(workflow_slug))[:120]

    context = _CaptureContext(_find_project_root(summary))
    sanitized_summary = context.sanitize(dict(summary), "source")
    workflow_payload = _build_workflow_payload(
        package_id=package_id,
        skill_name=skill_name,
        title=title,
        workflow_id=workflow_id,
        sanitized_summary=sanitized_summary,
        context=context,
    )
    manifest = _build_manifest(
        package_id=package_id,
        skill_name=skill_name,
        title=title,
        description=_description(workflow_id),
        version=version,
        author=author,
        min_vrcforge_version=min_vrcforge_version,
        permissions=_infer_permissions(workflow_id, sanitized_summary),
    )
    skill_markdown = _render_skill_markdown(_build_skill_metadata(skill_name, title, workflow_id, workflow_payload))
    source_files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "SKILL.md": skill_markdown,
        WORKFLOW_ENTRYPOINT: json.dumps(workflow_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    _assert_no_private_paths_or_secrets(source_files)
    return CapturedSkillSource(
        manifest=manifest,
        skill_markdown=skill_markdown,
        workflow=workflow_payload,
        source_files=source_files,
    )


def write_path_to_skill_source(
    summary: Mapping[str, Any],
    output_dir: str | Path,
    **kwargs: Any,
) -> CapturedSkillSource:
    captured = build_path_to_skill_source(summary, **kwargs)
    captured.write_to(output_dir)
    return captured


def _build_workflow_payload(
    *,
    package_id: str,
    skill_name: str,
    title: str,
    workflow_id: str,
    sanitized_summary: dict[str, Any],
    context: _CaptureContext,
) -> dict[str, Any]:
    validation = sanitized_summary.get("validation") if isinstance(sanitized_summary.get("validation"), dict) else {}
    validation = {
        "requiresApproval": bool(validation.get("requiresApproval", True)),
        "requiresCheckpoint": bool(validation.get("requiresCheckpoint", True)),
        "requiresRollback": bool(validation.get("requiresRollback", True)),
        **validation,
    }
    steps = sanitized_summary.get("steps")
    if not isinstance(steps, list):
        steps = sanitized_summary.get("skillPath") if isinstance(sanitized_summary.get("skillPath"), list) else []
    return {
        "schema": PATH_TO_SKILL_SCHEMA,
        "id": package_id,
        "skillName": skill_name,
        "title": title,
        "workflow": workflow_id,
        "proofPassed": _proof_passed(sanitized_summary),
        "variables": context.variables,
        "remapping": {
            "required": sorted(context.variables),
            "fields": context.remappings,
        },
        "steps": steps,
        "validation": validation,
        "sourceSummary": sanitized_summary,
        "privacy": {
            "absolutePaths": "redacted_to_variables",
            "secrets": "rejected",
            "binaryPayloads": "rejected",
            "paidAssetPayloads": "rejected",
        },
    }


def _build_manifest(
    *,
    package_id: str,
    skill_name: str,
    title: str,
    description: str,
    version: str,
    author: str,
    min_vrcforge_version: str,
    permissions: list[str],
) -> dict[str, Any]:
    return {
        "id": package_id,
        "name": title[:160],
        "skill_name": skill_name,
        "version": version,
        "author": author[:160],
        "description": description[:4000],
        "min_vrcforge_version": min_vrcforge_version,
        "permissions": permissions,
        "entrypoints": {
            "skill": "SKILL.md",
            "workflow": WORKFLOW_ENTRYPOINT,
        },
        "agent": {
            "schema": PATH_TO_SKILL_SCHEMA,
            "dry_run_required": True,
            "write_path": "request_only",
        },
    }


def _build_skill_metadata(skill_name: str, title: str, workflow_id: str, workflow_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": skill_name,
        "title": title,
        "description": _description(workflow_id),
        "category": "workflow",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": f"Reuse the proven {workflow_id} workflow with remapped project variables.",
        "inputs": ["projectPath remapping", "avatarPath or workflow target remapping", "dry-run evidence"],
        "outputs": ["Preview plan", "approval request", "validation delta", "rollback verification"],
        "sideEffects": "dry-run is read-only; approved execution may request one supervised VRCForge write",
        "backupRestore": "requires approval, checkpoint, validation, and rollback verification",
        "allowedTools": _infer_allowed_tools(workflow_id, workflow_payload),
        "disallowedTools": ["vrcforge_execute_shell", "direct_unity_asset_write"],
        "entrypointTool": _entrypoint_tool(workflow_id),
        "userInvocable": True,
        "disableModelInvocation": False,
        "argumentHint": "projectPath={{projectPath}} avatarPath={{avatarPath}}",
        "supportFiles": [WORKFLOW_ENTRYPOINT],
        "testCommand": "dry-run captured workflow before any apply request",
        "enabled": True,
        "tags": ["path-to-skill", "captured-workflow", "request-only"],
        "instructions": _skill_instructions(workflow_id),
    }


def _skill_instructions(workflow_id: str) -> str:
    return "\n".join(
        [
            f"Use {WORKFLOW_ENTRYPOINT} as the captured workflow definition for `{workflow_id}`.",
            "Before using it on a new avatar, require the user to remap every variable listed in the workflow payload.",
            "Run the read/preview tools first and compare validation signals with the captured proof summary.",
            "For any project write, create a VRCForge approval request only; do not call direct apply tools yourself.",
            "After an approved write, verify the checkpoint, validation delta, and rollback verification evidence.",
        ]
    )


def _infer_allowed_tools(workflow_id: str, workflow_payload: Mapping[str, Any]) -> list[str]:
    text = json.dumps({"workflow": workflow_id, "payload": workflow_payload}, ensure_ascii=False).lower()
    tools = ["vrcforge_health", "vrcforge_unity_status", "vrcforge_request_apply"]
    if "shader" in text or "material" in text:
        tools.extend(["vrcforge_scan_materials", "vrcforge_plan_shader_tuning", "vrcforge_apply_shader_tuning"])
    if "optimizer" in text or "optimization" in text or "meshia" in text or "aao" in text or "parameter" in text:
        tools.extend(["vrcforge_optimization_plan", "vrcforge_optimization_validation_delta"])
    if "booth" in text or "outfit" in text or "prefab" in text:
        tools.extend(["vrcforge_inspect_outfit_package", "vrcforge_preview_add_outfit"])
    return sorted(dict.fromkeys(tools))


def _entrypoint_tool(workflow_id: str) -> str:
    text = workflow_id.lower()
    if "shader" in text or "material" in text:
        return "vrcforge_plan_shader_tuning"
    if "optimizer" in text or "optimization" in text or "parameter" in text or "meshia" in text:
        return "vrcforge_optimization_plan"
    if "outfit" in text or "booth" in text:
        return "vrcforge_preview_add_outfit"
    return "vrcforge_health"


def _infer_permissions(workflow_id: str, sanitized_summary: Mapping[str, Any]) -> list[str]:
    text = json.dumps({"workflow": workflow_id, "summary": sanitized_summary}, ensure_ascii=False).lower()
    permissions = {"read_project", "unity_scan_scene", "unity_run_validation"}
    if "shader" in text or "material" in text:
        permissions.add("unity_modify_materials")
    if "optimizer" in text or "optimization" in text or "meshia" in text or "aao" in text or "parameter" in text:
        permissions.add("unity_modify_components")
    if "booth" in text or "outfit" in text or "prefab" in text:
        permissions.add("unity_modify_prefab")
    return sorted(permissions)


def _render_skill_markdown(skill: Mapping[str, Any]) -> str:
    metadata_keys = [
        ("name", "name"),
        ("title", "title"),
        ("description", "description"),
        ("category", "category"),
        ("permission-mode", "permissionMode"),
        ("risk-level", "riskLevel"),
        ("when-to-use", "whenToUse"),
        ("inputs", "inputs"),
        ("outputs", "outputs"),
        ("side-effects", "sideEffects"),
        ("backup-restore", "backupRestore"),
        ("allowed-tools", "allowedTools"),
        ("disallowed-tools", "disallowedTools"),
        ("entrypoint-tool", "entrypointTool"),
        ("user-invocable", "userInvocable"),
        ("disable-model-invocation", "disableModelInvocation"),
        ("argument-hint", "argumentHint"),
        ("support-files", "supportFiles"),
        ("test-command", "testCommand"),
        ("enabled", "enabled"),
        ("tags", "tags"),
    ]
    lines = ["---"]
    for yaml_key, key in metadata_keys:
        value = skill.get(key)
        if isinstance(value, list):
            lines.append(f"{yaml_key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(str(item))}")
        elif isinstance(value, bool):
            lines.append(f"{yaml_key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{yaml_key}: {_yaml_scalar(str(value or ''))}")
    lines.append("---")
    lines.append("")
    lines.append(str(skill.get("instructions") or "").strip())
    lines.append("")
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return '""'
    if re.search(r"[:#\[\],`{}]|^\s|\s$", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _proof_passed(summary: Mapping[str, Any]) -> bool:
    if "proofPassed" in summary:
        return bool(summary.get("proofPassed"))
    status = str(summary.get("status") or summary.get("result") or "").strip().lower()
    return status in {"passed", "pass", "ok", "success", "succeeded"}


def _find_project_root(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            compact = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if compact in {"projectpath", "projectroot", "unityprojectroot"} and isinstance(item, str):
                return item
        for item in value.values():
            found = _find_project_root(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_project_root(item)
            if found:
                return found
    return None


def _reject_key_tree(value: Any, field_path: str = "source") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            _reject_key(key, field_path)
            _reject_key_tree(item, f"{field_path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_key_tree(item, f"{field_path}[{index}]")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raise PathToSkillSecurityError(f"{field_path} contains binary data and cannot be captured.")


def _reject_key(key: str, field_path: str) -> None:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    if SECRET_KEY_RE.search(key) or SECRET_KEY_RE.search(compact):
        raise PathToSkillSecurityError(f"{field_path}.{key} is a secret/token field and cannot be captured.")
    if DISALLOWED_PAYLOAD_FIELD_RE.search(compact):
        raise PathToSkillSecurityError(f"{field_path}.{key} looks like a paid asset or binary payload field.")


def _reject_sensitive_text(value: str, field_path: str) -> None:
    if not value:
        return
    if SkillPackageService._contains_sensitive_content(value.encode("utf-8", errors="ignore")):
        raise PathToSkillSecurityError(f"{field_path} contains secret or binary-looking material.")


def _looks_like_path_field(field_path: str) -> bool:
    key = re.split(r"[.\[]", field_path)[-1].strip("]")
    return bool(PATH_KEY_RE.search(key))


def _is_absolute_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text or URL_RE.match(text):
        return False
    return bool(ABSOLUTE_WINDOWS_PATH_RE.match(text) or text.startswith("\\\\") or (text.startswith("/") and not text.startswith("//")))


def _normalize_windows_path(value: str | None) -> str:
    if not value:
        return ""
    return ntpath.normcase(ntpath.normpath(str(value).strip()))


def _is_child_windows_path(path: str, parent: str) -> bool:
    try:
        common = ntpath.commonpath([path, parent])
    except ValueError:
        return False
    return common == parent and path != parent


def _assert_no_private_paths_or_secrets(source_files: Mapping[str, str]) -> None:
    serialized = "\n".join(source_files.values())
    if EMBEDDED_WINDOWS_PATH_RE.search(serialized):
        raise PathToSkillSecurityError("Captured skill output still contains an absolute Windows path.")
    if SkillPackageService._contains_sensitive_content(serialized.encode("utf-8")):
        raise PathToSkillSecurityError("Captured skill output still contains secret or binary-looking material.")


def _slug(value: str, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return text or fallback


def _skill_slug(value: str) -> str:
    text = _slug(value, fallback="captured-workflow")
    if not re.match(r"^[a-z]", text):
        text = f"skill-{text}"
    return text[:80].rstrip("-_") or "captured-workflow"


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part) or "Captured Workflow"


def _description(workflow_id: str) -> str:
    return (
        f"Captured VRCForge workflow recipe for {workflow_id}; variables must be remapped "
        "and any write must use approval, checkpoint, validation, and rollback verification."
    )
