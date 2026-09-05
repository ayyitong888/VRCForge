"""Native MCP Prompt projection of the one VRCForge Skill registry.

This module owns no Skill definitions.  It receives the existing registry and
Tool catalog through callbacks so internal and external Agents observe the
same IDs, instructions, provenance and capability boundaries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping, Sequence


PROMPT_SCHEMA = "vrcforge.skill_prompt.v1"
PROMPT_CONTEXT_SCHEMA = "vrcforge.prompt_context.v1"

SkillSource = Callable[[], Mapping[str, Any]]
ToolSource = Callable[[str], Sequence[Mapping[str, Any]]]
ResourceValidator = Callable[[str, str, Mapping[str, Any] | None], Mapping[str, Any]]
SupportFilesLoader = Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]]


class McpPromptError(ValueError):
    pass


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _parse_json_object(value: Any, field: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return _json_clone(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise McpPromptError(f"Prompt argument {field} must be a JSON object") from exc
        if isinstance(parsed, Mapping):
            return _json_clone(parsed)
    raise McpPromptError(f"Prompt argument {field} must be a JSON object")


def _uri_argument(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise McpPromptError(f"Prompt argument {field} must be a string")
    return value.strip()


def _array_argument(value: Any, field: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise McpPromptError(f"Prompt argument {field} must be a JSON string array") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise McpPromptError(f"Prompt argument {field} must be a JSON string array")
    return value


class McpPromptRegistry:
    def __init__(
        self,
        skill_source: SkillSource,
        tool_source: ToolSource,
        *,
        resource_validate: ResourceValidator | None = None,
        support_files_loader: SupportFilesLoader | None = None,
    ) -> None:
        self._skill_source = skill_source
        self._tool_source = tool_source
        self._resource_validate = resource_validate
        self._support_files_loader = support_files_loader

    def _skills(self) -> list[dict[str, Any]]:
        registry = self._skill_source()
        raw = registry.get("skills") if isinstance(registry, Mapping) else None
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            raise McpPromptError("Shared Skill registry returned an invalid list")
        skills = [dict(item) for item in raw if isinstance(item, Mapping)]
        return [
            item
            for item in skills
            if item.get("source") == "user"
            and item.get("skillType") == "package"
            and item.get("enabled", True)
            and item.get("available", True)
            and not item.get("disableModelInvocation", False)
            and str(item.get("name") or "").strip()
        ]

    @staticmethod
    def _skill_tools(skill: Mapping[str, Any]) -> list[str]:
        names = _string_list(skill.get("allowedTools") or skill.get("tools"))
        for step in skill.get("steps") or ():
            if isinstance(step, Mapping):
                names.extend(_string_list(step.get("tools")))
        entrypoint = str(skill.get("entrypointTool") or "").strip()
        if entrypoint:
            names.append(entrypoint)
        return sorted(set(names))

    @staticmethod
    def _version(skill: Mapping[str, Any], content_hash: str) -> tuple[str, str]:
        declared = str(
            skill.get("version")
            or skill.get("skillVersion")
            or skill.get("packageVersion")
            or ""
        ).strip()
        return (declared, "declared") if declared else (f"content-{content_hash[:12]}", "content-addressed")

    def _descriptor(self, skill: Mapping[str, Any]) -> dict[str, Any]:
        skill_id = str(skill.get("name") or "").strip()
        material = {
            "id": skill_id,
            "instructions": skill.get("instructions"),
            "steps": skill.get("steps"),
            "allowedTools": self._skill_tools(skill),
            "source": skill.get("source"),
            "packageId": skill.get("packageId"),
            "supportFiles": _string_list(skill.get("supportFiles")),
            "guidance": {key: skill.get(key) for key in (
                "whenToUse", "whenNotToUse", "problemBreakdown", "pitfalls", "acceptance",
                "requiredResources", "pausePoints", "gmCases", "gameOnlyAcceptance",
                "permissionMode", "riskLevel", "backupRestore", "toolBlocks",
            )},
        }
        content_hash = _hash(material)
        version, version_source = self._version(skill, content_hash)
        return {
            "name": skill_id,
            "title": str(skill.get("title") or skill_id),
            "description": str(skill.get("description") or skill.get("whenToUse") or skill_id),
            "arguments": [
                {"name": "identityLockUri", "description": "Exact Session Identity Lock Resource URI.", "required": False},
                {"name": "sessionContextUri", "description": "Current session/context Resource URI.", "required": False},
                {"name": "scope", "description": "JSON object with targetItems, doNotTouch, allowedWrites and recordOnlyFindings.", "required": False},
                {"name": "protectedState", "description": "JSON object describing protected objects, assets, properties and user-adjusted state.", "required": False},
                {"name": "userIntent", "description": "The user's current intent, without inferred expansion.", "required": False},
                {"name": "modelSemantics", "description": "Known semantic choices such as the wardrobe parameter meaning.", "required": False},
                {"name": "referenceSources", "description": "JSON array of exact Resource URIs or approved references.", "required": False},
                {"name": "acceptanceCriteria", "description": "JSON array of task-specific acceptance criteria.", "required": False},
            ],
            "_meta": {
                "schema": PROMPT_SCHEMA,
                "skillId": skill_id,
                "version": version,
                "versionSource": version_source,
                "contentHash": content_hash,
                "hashScope": "prompt-and-support-declarations",
                "supportFilePaths": material["supportFiles"],
                "supportContentSource": "Call prompts/get for support content and supportContentHash before invoking a Tool with this provenance.",
                "source": str(skill.get("source") or "builtin"),
                "packageId": str(skill.get("packageId") or ""),
                "permissionMode": str(skill.get("permissionMode") or "instruction_only"),
                "riskLevel": str(skill.get("riskLevel") or "low"),
                "planningVisible": True,
                "writeToolsCallableInPlanning": False,
            },
        }

    def list(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, Mapping) else {}
        try:
            offset = int(params.get("cursor") or 0)
            page_size = int(params.get("pageSize") or 100)
        except (TypeError, ValueError) as exc:
            raise McpPromptError("cursor and pageSize must be integers") from exc
        if offset < 0 or page_size < 1 or page_size > 500:
            raise McpPromptError("cursor/pageSize are out of range")
        prompts = sorted((self._descriptor(skill) for skill in self._skills()), key=lambda item: item["name"])
        page = prompts[offset : offset + page_size]
        next_offset = offset + len(page)
        generation = _hash(
            [(item["name"], item["_meta"]["version"], item["_meta"]["contentHash"]) for item in prompts]
        )
        return {
            "prompts": page,
            "promptGeneration": generation,
            **({"nextCursor": str(next_offset)} if next_offset < len(prompts) else {}),
        }

    def get(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        skill = next((item for item in self._skills() if str(item.get("name") or "") == name), None)
        if skill is None:
            raise McpPromptError(f"Unknown or unavailable Skill Prompt: {name}")
        args = arguments if isinstance(arguments, Mapping) else {}
        scope = {
            "targetItems": [],
            "doNotTouch": [],
            "allowedWrites": [],
            "recordOnlyFindings": [],
            **_parse_json_object(args.get("scope"), "scope"),
        }
        protected_state = {
            "objects": [],
            "assets": [],
            "properties": [],
            "userAdjustedStates": [],
            **_parse_json_object(args.get("protectedState"), "protectedState"),
        }
        identity_lock_uri = _uri_argument(args.get("identityLockUri"), "identityLockUri")
        session_context_uri = _uri_argument(args.get("sessionContextUri"), "sessionContextUri")
        required_resources = _string_list(skill.get("requiredResources")) or [
            "identityLockUri",
            "sessionContextUri",
        ]
        supplied = {
            "identityLockUri": identity_lock_uri,
            "sessionContextUri": session_context_uri,
        }
        missing = [field for field in required_resources if not supplied.get(field)]
        identity_resource = None
        context_resource = None
        if identity_lock_uri and self._resource_validate:
            identity_resource = self._resource_validate(identity_lock_uri, "session_identity_lock", None)
            identity = identity_resource.get("identity") if isinstance(identity_resource, Mapping) else None
            if not isinstance(identity, Mapping) or not any(identity.get(key) for key in ("projectId", "namespace", "projectRoot", "coreInstanceId")):
                raise McpPromptError("Identity Lock Resource is unbound")
        elif identity_lock_uri:
            missing.append("identityLockUri")
        if session_context_uri and self._resource_validate:
            expected = dict(identity_resource.get("identity") or {}) if identity_resource else {}
            context_resource = self._resource_validate(session_context_uri, "", expected or None)
            if context_resource.get("resourceType") not in {
                "operation_receipt", "unity_snapshot", "control_graph", "gm_runtime", "checkpoint_diff"
            }:
                raise McpPromptError("Session context must be captured operation or Unity state, not catalog metadata")
        elif session_context_uri:
            missing.append("sessionContextUri")
        all_tools = {str(item.get("name") or ""): item for item in self._tool_source("execution")}
        planning_names = {str(item.get("name") or "") for item in self._tool_source("planning")}
        skill_tools = self._skill_tools(skill)
        planning_tools = [name for name in skill_tools if name in planning_names]
        write_tools = [name for name in skill_tools if name in all_tools and name not in planning_names]
        write_blocks = sorted(
            {
                str((all_tools[name].get("_meta") or {}).get("toolBlock") or "")
                for name in write_tools
                if isinstance(all_tools[name].get("_meta"), Mapping)
                and str((all_tools[name].get("_meta") or {}).get("toolBlock") or "")
            }
        )
        tool_blocks = _string_list(skill.get("toolBlocks"))
        gm_relevant = "integrations/gesture-manager" in tool_blocks or any("gesture_manager" in name for name in skill_tools)
        prompt_context = {
            "schema": PROMPT_CONTEXT_SCHEMA,
            "identityLockUri": identity_lock_uri or None,
            "sessionContextUri": session_context_uri or None,
            "scope": scope,
            "protectedState": protected_state,
            "userIntent": str(args.get("userIntent") or ""),
            "modelSemantics": str(args.get("modelSemantics") or ""),
            "referenceSources": _array_argument(args.get("referenceSources"), "referenceSources"),
            "acceptanceCriteria": _array_argument(args.get("acceptanceCriteria"), "acceptanceCriteria") or _string_list(skill.get("acceptance")),
            "requiredResources": required_resources,
            "missingRequiredResources": missing,
            "pausePoints": _string_list(skill.get("pausePoints")) or [
                "Before loading or calling any write Tool, confirm execution scope and checkpoint timing.",
                "After a user-adjustment handoff prepare, stop with awaiting_user; do not poll, scan, save, capture or resume in the same turn.",
                "Before game-only acceptance, report what remains for the user to verify in VRChat.",
            ],
            "gmCases": _string_list(skill.get("gmCases")) or (
                [
                    "Enter Play Mode for the exact Avatar and poll until Gesture Manager is ready.",
                    "Set exact parameters, read back parameters/Animator/active objects/components, wait for PhysBone settle, and capture only if requested.",
                    "Restore defaults and exit Play Mode; record gmValidated separately from gameValidated.",
                ]
                if gm_relevant
                else []
            ),
            "gameOnlyAcceptance": _string_list(skill.get("gameOnlyAcceptance")) or [
                "First-person shader behavior, final particle space, actual audio, Contact/DPS interaction, formal Build & Test and Upload remain user/game validation unless explicit evidence exists.",
            ],
            "planningTools": planning_tools,
            "executionWriteTools": write_tools,
            "writeToolBlocksToLoad": write_blocks,
            "checkpointPolicy": str(skill.get("backupRestore") or "Checkpoint before the first approved write and before destructive transitions."),
            "awaitingUserHardStop": True,
            "status": "awaiting_resources" if missing else "ready_for_planning",
        }
        support_files = self._load_support_files(skill)
        descriptor = self._descriptor(skill)
        descriptor["_meta"]["supportContentHash"] = self._support_hash(support_files)
        prompt_payload = {
            "schema": PROMPT_SCHEMA,
            "skill": {
                "id": name,
                "title": descriptor["title"],
                "description": descriptor["description"],
                "whenToUse": str(skill.get("whenToUse") or ""),
                "whenNotToUse": str(skill.get("whenNotToUse") or ""),
                "instructions": str(skill.get("instructions") or ""),
                "problemBreakdown": _json_clone(skill.get("problemBreakdown") or []),
                "steps": _json_clone(skill.get("steps") or []),
                "pitfalls": _json_clone(skill.get("pitfalls") or []),
                "supportFiles": support_files,
            },
            "context": prompt_context,
            "provenance": descriptor["_meta"],
            "rules": [
                "Read required Resources before acting; if unavailable, request an explicit read Tool and do not guess.",
                "Prompt visibility never grants write authority. Planning may use only listed read/preview Tools.",
                "In execution, load only the listed write Tool Blocks and preserve approval, checkpoint, ExecutionTarget and operationId contracts.",
                "Treat hierarchy paths as display/navigation only, never as write identity or fallback.",
            ],
        }
        return {
            "description": descriptor["description"],
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": json.dumps(prompt_payload, ensure_ascii=False, indent=2, allow_nan=False),
                    },
                }
            ],
            "structuredContent": prompt_payload,
            "_meta": descriptor["_meta"],
        }

    def _load_support_files(self, skill: Mapping[str, Any]) -> list[dict[str, str]]:
        declared = _string_list(skill.get("supportFiles"))
        if not declared:
            return []
        if self._support_files_loader is None:
            raise McpPromptError("Declared Skill support content is unavailable; its loader is not configured")
        loaded = self._support_files_loader(skill)
        files: dict[str, dict[str, str]] = {}
        for item in loaded:
            path, value = item.get("path"), item.get("content")
            if path not in declared or path in files or not isinstance(value, str):
                raise McpPromptError("Support loader must return each exact declared UTF-8 file once")
            files[path] = {"path": path, "content": value, "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
        if len(files) != len(declared):
            raise McpPromptError("Declared Skill support files are missing")
        return [files[path] for path in declared]

    @staticmethod
    def _support_hash(files: Sequence[Mapping[str, Any]]) -> str:
        return _hash([(item["path"], item["sha256"]) for item in files])

    def validate_provenance(
        self,
        value: Mapping[str, Any] | None,
        *,
        tool_name: str = "",
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise McpPromptError("promptSkillProvenance must be an object returned by prompts/get")
        skill_id = str(value.get("skillId") or "").strip()
        skill = next(
            (skill for skill in self._skills() if str(skill.get("name") or "") == skill_id),
            None,
        )
        if skill is None:
            raise McpPromptError("Prompt/Skill provenance refers to an unavailable Skill")
        descriptor = self._descriptor(skill)
        if _string_list(skill.get("supportFiles")):
            support_hash = self._support_hash(self._load_support_files(skill))
            if value.get("supportContentHash") != support_hash:
                raise McpPromptError("Prompt/Skill supportContentHash is stale or missing; retrieve prompts/get again")
            descriptor["_meta"]["supportContentHash"] = support_hash
        current = descriptor["_meta"]
        for field in ("skillId", "version", "contentHash"):
            if str(value.get(field) or "") != str(current.get(field) or ""):
                raise McpPromptError(f"Prompt/Skill provenance {field} is stale or mismatched")
        allowed_tools = self._skill_tools(skill)
        if tool_name and allowed_tools and tool_name not in allowed_tools:
            raise McpPromptError(
                f"Tool {tool_name} is outside Skill {skill_id}'s declared allowed Tool set"
            )
        return {
            "status": "verified",
            "schema": PROMPT_SCHEMA,
            **{key: current[key] for key in ("skillId", "version", "versionSource", "contentHash", "source", "packageId")},
            "hashScope": current["hashScope"],
            **({"supportContentHash": current["supportContentHash"]} if "supportContentHash" in current else {}),
        }


__all__ = ["McpPromptError", "McpPromptRegistry", "PROMPT_CONTEXT_SCHEMA", "PROMPT_SCHEMA"]
