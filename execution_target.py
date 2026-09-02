"""Stage 1 execution namespace and identity lock for Unity-facing MCP tools.

This module is deliberately transport-neutral.  It describes the identity
envelope that a future Resources or Prompts stage may reference, but it does
not expose either of those MCP surfaces.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

try:
    import psutil
except ImportError:  # pragma: no cover - the packaged desktop runtime includes psutil.
    psutil = None


EXECUTION_TARGET_SCHEMA = "vrcforge.execution_target.v1"
EXECUTION_NAMESPACE_PREFIX = "vrcforge://"
RESOURCE_PROVENANCE_SCHEMA = "vrcforge.resource_provenance.v1"
PROMPT_PROVENANCE_SCHEMA = "vrcforge.prompt_skill_provenance.v1"


class ExecutionTargetError(ValueError):
    """A fail-closed identity mismatch that must not reach a Unity handler."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_project_root(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ExecutionTargetError("project_root_missing", "ExecutionTarget requires project.root.")
    try:
        path = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ExecutionTargetError("project_root_invalid", "ExecutionTarget project root is invalid.") from exc
    if not path.is_absolute():
        raise ExecutionTargetError("project_root_not_absolute", "ExecutionTarget project root must be absolute.")
    # Preserve the normalized absolute spelling because Unity Core derives its
    # project id from that exact UTF-8 path.  Path comparisons below remain
    # case-insensitive on Windows through normcase.
    return str(path)


def project_identity(project_root: str) -> str:
    return hashlib.sha256(normalize_project_root(project_root).encode("utf-8")).hexdigest()


def process_start_time(pid: int) -> str:
    """Return the host-observed process start time, or fail closed."""

    if psutil is None:
        raise ExecutionTargetError("process_start_time_unavailable", "Cannot verify Unity process start time.")
    try:
        return f"{float(psutil.Process(pid).create_time()):.6f}"
    except (OSError, psutil.Error, TypeError, ValueError) as exc:
        raise ExecutionTargetError("unity_process_unavailable", "The bound Unity process is not running.") from exc


def _required_string(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ExecutionTargetError("identity_field_missing", f"ExecutionTarget field {field} is required.")
    return result


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionTargetError("identity_section_missing", f"ExecutionTarget section {field} is required.")
    return value


def _under_root(value: Any, root: str, field: str) -> str:
    path = normalize_project_root(value)
    try:
        Path(path).relative_to(Path(root))
    except ValueError as exc:
        raise ExecutionTargetError("identity_path_outside_project", f"ExecutionTarget {field} is outside the bound project.") from exc
    return path


def _identity_scope_for_target(target: Mapping[str, Any]) -> str:
    scope = str(target.get("scope") or "project").strip().casefold()
    if scope not in {"project", "scene", "avatar", "object", "component"}:
        raise ExecutionTargetError("identity_scope_invalid", "ExecutionTarget scope is invalid.")
    return scope


def canonical_namespace(target: Mapping[str, Any]) -> str:
    project = _mapping(target.get("project"), "project")
    project_id = _required_string(project.get("projectId"), "project.projectId")
    scene = target.get("scene") if isinstance(target.get("scene"), Mapping) else {}
    avatar = target.get("avatar") if isinstance(target.get("avatar"), Mapping) else {}
    obj = target.get("object") if isinstance(target.get("object"), Mapping) else {}
    component = target.get("component") if isinstance(target.get("component"), Mapping) else {}
    parts = [f"projects/{project_id}"]
    if scene.get("guid"):
        parts.append(f"scenes/{scene['guid']}")
    if avatar.get("globalObjectId"):
        parts.append(f"avatars/{avatar['globalObjectId']}")
    if obj.get("globalObjectId"):
        parts.append(f"objects/{obj['globalObjectId']}")
    if component.get("globalObjectId"):
        parts.append(f"components/{component['globalObjectId']}")
    return EXECUTION_NAMESPACE_PREFIX + "/".join(parts)


def validate_execution_target(
    value: Any,
    *,
    project_root: str | None = None,
    core_identity: Mapping[str, Any] | None = None,
    required_scope: str = "project",
) -> dict[str, Any]:
    """Validate every identity component needed by a Unity operation.

    The function intentionally rejects missing process, scene, Avatar, object,
    or component identity instead of falling back to a hierarchy path.
    """

    target = dict(_mapping(value, "target"))
    if target.get("schema") != EXECUTION_TARGET_SCHEMA:
        raise ExecutionTargetError("identity_schema_invalid", "ExecutionTarget schema is unsupported.")
    scope = _identity_scope_for_target(target)
    requested_scope = str(required_scope or "project").casefold()
    order = {"project": 0, "scene": 1, "avatar": 2, "object": 3, "component": 4}
    if requested_scope not in order or order[scope] < order[requested_scope]:
        raise ExecutionTargetError("identity_scope_insufficient", "ExecutionTarget does not cover the tool's identity scope.")

    project = dict(_mapping(target.get("project"), "project"))
    root = normalize_project_root(project.get("root"))
    if project_root is not None and os.path.normcase(root) != os.path.normcase(normalize_project_root(project_root)):
        raise ExecutionTargetError("project_root_drifted", "ExecutionTarget project root does not match the selected runtime project.")
    expected_project_id = project_identity(root)
    if project.get("projectId") != expected_project_id:
        raise ExecutionTargetError("project_identity_mismatch", "ExecutionTarget project identity does not match its normalized root.")
    if target.get("ambiguous") is True or target.get("resolutionCandidateCount") not in (None, 1):
        raise ExecutionTargetError("identity_ambiguous", "ExecutionTarget resolved to zero or multiple identities.")

    editor = dict(_mapping(target.get("editor"), "editor"))
    pid = editor.get("unityPid")
    if type(pid) is not int or pid <= 0:
        raise ExecutionTargetError("unity_pid_invalid", "ExecutionTarget requires a positive Unity PID.")
    _required_string(editor.get("processStartTime"), "editor.processStartTime")
    _required_string(editor.get("coreInstanceId"), "editor.coreInstanceId")
    if core_identity is not None:
        for target_key, core_key in (("unityPid", "processId"), ("coreInstanceId", "instanceId")):
            if str(editor.get(target_key)) != str(core_identity.get(core_key) or ""):
                raise ExecutionTargetError("editor_identity_mismatch", f"ExecutionTarget {target_key} does not match the active Unity Core.")
        expected_start = str(core_identity.get("processStartTime") or "").strip()
        if expected_start and str(editor.get("processStartTime")) != expected_start:
            raise ExecutionTargetError("process_identity_mismatch", "Unity process start time changed.")

    if scope in {"scene", "avatar", "object", "component"}:
        scene = dict(_mapping(target.get("scene"), "scene"))
        scene_path = _under_root(scene.get("absolutePath") or scene.get("path"), root, "scene.absolutePath")
        if not scene_path.casefold().endswith(".unity"):
            raise ExecutionTargetError("scene_path_invalid", "ExecutionTarget scene must be a Unity scene asset.")
        _required_string(scene.get("guid"), "scene.guid")
        _required_string(scene.get("revision"), "scene.revision")
        _required_string(scene.get("digest"), "scene.digest")
        if core_identity is not None:
            for target_key, core_key in (("guid", "sceneGuid"), ("revision", "sceneRevision"), ("digest", "sceneDigest")):
                expected_scene_value = str(core_identity.get(core_key) or "").strip()
                if expected_scene_value and str(scene.get(target_key)) != expected_scene_value:
                    raise ExecutionTargetError("scene_identity_mismatch", f"ExecutionTarget scene {target_key} changed.")
        scene["absolutePath"] = scene_path
        target["scene"] = scene
    if scope in {"avatar", "object", "component"}:
        avatar = dict(_mapping(target.get("avatar"), "avatar"))
        _required_string(avatar.get("globalObjectId"), "avatar.globalObjectId")
        _required_string(avatar.get("exactHierarchyPath"), "avatar.exactHierarchyPath")
        target["avatar"] = avatar
        if core_identity is not None and core_identity.get("avatarGlobalObjectId"):
            if avatar.get("globalObjectId") != core_identity.get("avatarGlobalObjectId"):
                raise ExecutionTargetError("avatar_identity_mismatch", "The bound Avatar was replaced or changed.")
    if scope in {"object", "component"}:
        obj = dict(_mapping(target.get("object"), "object"))
        _required_string(obj.get("globalObjectId"), "object.globalObjectId")
        _required_string(obj.get("exactHierarchyPath"), "object.exactHierarchyPath")
        target["object"] = obj
        if core_identity is not None and core_identity.get("objectGlobalObjectId"):
            if obj.get("globalObjectId") != core_identity.get("objectGlobalObjectId"):
                raise ExecutionTargetError("object_identity_mismatch", "The bound Unity object was recreated or changed.")
    if scope == "component":
        component = dict(_mapping(target.get("component"), "component"))
        _required_string(component.get("globalObjectId"), "component.globalObjectId")
        _required_string(component.get("type"), "component.type")
        target["component"] = component
        if core_identity is not None and core_identity.get("componentGlobalObjectId"):
            if component.get("globalObjectId") != core_identity.get("componentGlobalObjectId"):
                raise ExecutionTargetError("component_identity_mismatch", "The bound Unity component was recreated or changed.")

    expected_namespace = canonical_namespace({**target, "project": project})
    if target.get("namespace") != expected_namespace:
        raise ExecutionTargetError("namespace_mismatch", "ExecutionTarget namespace does not match bound identities.")
    target["project"] = {**project, "root": root}
    target["scope"] = scope
    target["namespace"] = expected_namespace
    return target


def validate_runtime_execution_target(
    value: Any,
    *,
    project_root: str,
    required_scope: str,
) -> dict[str, Any]:
    """Bind a supplied envelope to the current on-disk Core descriptor."""

    root = normalize_project_root(project_root)
    descriptor_path = Path(root) / "Library" / "VRCForge" / "mcp-core.json"
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExecutionTargetError("execution_target_authority_unavailable", "Unity MCP identity authority is unavailable.") from exc
    if not isinstance(descriptor, Mapping):
        raise ExecutionTargetError("execution_target_authority_unavailable", "Unity Core descriptor is not an object.")
    try:
        pid = int(descriptor.get("processId") or 0)
    except (TypeError, ValueError) as exc:
        raise ExecutionTargetError("unity_pid_invalid", "Unity Core descriptor has an invalid PID.") from exc
    observed_process_start = process_start_time(pid)
    descriptor_process_start = str(descriptor.get("processStartTime") or "").strip()
    if descriptor_process_start:
        try:
            process_start_delta = abs(
                float(descriptor_process_start) - float(observed_process_start)
            )
        except ValueError as exc:
            raise ExecutionTargetError(
                "process_identity_mismatch",
                "Unity Core descriptor process start time is invalid.",
            ) from exc
        # Windows Process.StartTime and psutil read the same OS-owned process
        # creation timestamp through different APIs whose conversions can vary
        # by a few microseconds.  The Core remains authoritative and later
        # requires an exact string match; this local cross-check only rejects a
        # descriptor that no longer identifies the observed PID lifetime.
        if process_start_delta > 0.01:
            raise ExecutionTargetError(
                "process_identity_mismatch",
                "Unity Core descriptor process start time does not match the running process.",
            )
    core = {
        "processId": pid,
        "processStartTime": descriptor_process_start or observed_process_start,
        "instanceId": descriptor.get("instanceId"),
    }
    return validate_execution_target(
        value,
        project_root=root,
        core_identity=core,
        required_scope=required_scope,
    )


def execution_target_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def standard_identity_metadata(*, scope: str, write: bool) -> dict[str, Any]:
    required = ["project.root", "project.projectId", "editor.unityPid", "editor.processStartTime", "editor.coreInstanceId"]
    if scope in {"scene", "avatar", "object", "component"}:
        required.extend(["scene.absolutePath", "scene.guid", "scene.revision", "scene.digest"])
    if scope in {"avatar", "object", "component"}:
        required.extend(["avatar.globalObjectId", "avatar.exactHierarchyPath"])
    if scope in {"object", "component"}:
        required.extend(["object.globalObjectId", "object.exactHierarchyPath"])
    if scope == "component":
        required.extend(["component.globalObjectId", "component.type"])
    return {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": scope,
        "required": required,
        "failClosedOn": [
            "project_or_editor_drift",
            "scene_reload_or_revision_change",
            "avatar_replacement",
            "object_recreation",
            "duplicate_identity",
        ],
        "hierarchyPathRole": "display_and_navigation_only",
        "hierarchyPathFallback": False,
        "writeBinding": "required" if write else "required_for_unity_readback_context",
    }


def future_provenance_metadata() -> dict[str, Any]:
    return {
        "resources": {
            "schema": RESOURCE_PROVENANCE_SCHEMA,
            "status": "unavailable",
            "uri": None,
            "handle": None,
        },
        "promptSkillProvenance": {
            "schema": PROMPT_PROVENANCE_SCHEMA,
            "status": "unavailable",
            "promptId": None,
            "skillId": None,
            "skillVersion": None,
            "source": None,
        },
    }
