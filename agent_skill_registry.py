from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agent_gateway import (
    BUILTIN_SKILL_GROUPS,
    BUILTIN_SKILL_OVERRIDES,
    AgentGatewayConfig,
    AgentGatewayError,
    EXPOSURE_LAYER_EXECUTION,
    EXPOSURE_LAYER_PLANNING,
    LEGACY_PROJECTED_SKILL_STATE_SCHEMA,
    PROJECTED_SKILL_STATE_MAX_BYTES,
    PROJECTED_SKILL_STATE_NAME,
    PROJECTED_SKILL_STATE_SCHEMA,
    RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES,
    RUNTIME_SKILL_SUPPORT_MAX_FILES,
    RUNTIME_SKILL_SUPPORT_MAX_TOTAL_BYTES,
    SKILL_ID_RE,
    WRAPPER_ONLY_WRITE_TARGETS,
    _path_is_link_like,
    _path_has_link_like_parent,
    current_os_key,
    ensure_string_list,
    ensure_dict,
    ensure_list,
    first_payload_value,
    fsync_directory_best_effort,
    normalize_bool,
    normalize_risk_level,
    normalize_skill_id,
    normalize_skill_permission,
    normalize_exposure_layer,
    parse_skill_markdown,
    remove_tree,
    render_skill_markdown,
    title_from_name,
    tool_usage_description,
)


USER_SKILL_MANIFEST_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SkillToolDescriptor:
    name: str
    description: str
    category: str
    write: bool
    advanced: bool
    requires_user_activation: bool


@dataclass(frozen=True, slots=True)
class SkillWriteHandlerDescriptor:
    name: str
    description: str
    risk_level: str
    advanced: bool


@dataclass(frozen=True, slots=True)
class RuntimeSkillSnapshot:
    skill: dict[str, Any]
    validation: dict[str, Any]
    support_files: tuple[dict[str, str], ...]
    package_audit_context: dict[str, Any]
    loaded: bool


@dataclass(frozen=True, slots=True)
class AgentSkillRegistryPorts:
    config_path: Callable[[], Path]
    ensure_config: Callable[[], AgentGatewayConfig]
    list_tools: Callable[[], tuple[SkillToolDescriptor, ...]]
    list_write_handlers: Callable[[], tuple[SkillWriteHandlerDescriptor, ...]]
    tool_visible: Callable[[str, AgentGatewayConfig], bool]
    write_handler_visible: Callable[[str, AgentGatewayConfig], bool]
    computer_use_model_invocable: Callable[[AgentGatewayConfig], bool]
    append_audit: Callable[[dict[str, Any]], None]
    user_skill_lock: AbstractContextManager[object]
    local_state_write_guard: Callable[[], AbstractContextManager[object]]


class AgentSkillRegistryService:
    """Own builtin and user Skill registry policy and persistence."""

    __slots__ = ("_ports",)

    def __init__(self, ports: AgentSkillRegistryPorts) -> None:
        self._ports = ports

    @property
    def write_lock(self) -> AbstractContextManager[object]:
        return self._ports.user_skill_lock

    @property
    def user_skills_dir(self) -> Path:
        config_path = self._ports.config_path()
        if config_path.parent.name.lower() == "config":
            return config_path.parent.parent / "skills"
        user_data_dir = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data_dir:
            return Path(user_data_dir) / "skills"
        return config_path.parent / "skills"

    def _tool(self, tool_name: str) -> SkillToolDescriptor | None:
        return next((tool for tool in self._ports.list_tools() if tool.name == tool_name), None)

    def _write_handler(self, tool_name: str) -> SkillWriteHandlerDescriptor | None:
        return next((handler for handler in self._ports.list_write_handlers() if handler.name == tool_name), None)

    def _reserved_skill_ids(self) -> set[str]:
        return {
            *(normalize_skill_id(str(group.get("name") or "")) for group in BUILTIN_SKILL_GROUPS),
            *(tool.name for tool in self._ports.list_tools()),
            *(handler.name for handler in self._ports.list_write_handlers()),
        }

    def validate_projection_name(self, skill_id: str) -> None:
        with self.write_lock:
            normalized = normalize_skill_id(skill_id)
            if normalized in self._reserved_skill_ids():
                raise AgentGatewayError(f"Skill name conflicts with a builtin tool: {normalized}", status_code=409)
            collisions = [
                skill
                for skill in self._load_user_skills()
                if normalize_skill_id(str(skill.get("name") or "")) == normalized
                and Path(str(skill.get("storagePath") or "")).parent.name
                != normalized
            ]
            if collisions:
                raise AgentGatewayError(
                    f"Skill name is ambiguous with a non-canonical user skill path: {normalized}",
                    status_code=409,
                )

    def _builtin_skill_definitions(self, config: AgentGatewayConfig) -> list[dict[str, Any]]:
        skills: list[dict[str, Any]] = []
        for group in BUILTIN_SKILL_GROUPS:
            skills.append(self._skill_from_builtin_group(group, config))
        for tool in self._ports.list_tools():
            skills.append(self._skill_from_tool(tool, config))
        for handler in self._ports.list_write_handlers():
            if handler.name in WRAPPER_ONLY_WRITE_TARGETS:
                continue
            skills.append(self._skill_from_write_handler(handler, config))
        return sorted(skills, key=lambda item: (str(item.get("category") or ""), str(item.get("name") or "")))

    def _skill_from_builtin_group(self, group: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        allowed_tools = ensure_string_list(group.get("allowedTools") or group.get("tools"))
        permission_mode = normalize_skill_permission(group.get("permissionMode"))
        available = bool(group.get("enabled", True)) and all(
            self._skill_dependency_visible(tool_name, config) for tool_name in allowed_tools
        )
        return {
            "schema": "vrcforge.skill.v1",
            "name": str(group.get("name") or ""),
            "title": str(group.get("title") or title_from_name(str(group.get("name") or ""))),
            "description": str(group.get("description") or ""),
            "category": str(group.get("category") or "builtin"),
            "source": "builtin",
            "skillType": "group",
            "enabled": bool(group.get("enabled", True)),
            "available": available,
            "permissionMode": permission_mode,
            "riskLevel": normalize_risk_level(group.get("riskLevel")),
            "whenToUse": str(group.get("whenToUse") or ""),
            "inputs": ensure_string_list(group.get("inputs")),
            "outputs": ensure_string_list(group.get("outputs")),
            "sideEffects": str(group.get("sideEffects") or "none"),
            "backupRestore": str(group.get("backupRestore") or "not required"),
            "tools": allowed_tools,
            "allowedTools": allowed_tools,
            "disallowedTools": ensure_string_list(group.get("disallowedTools")),
            "entrypointTool": str(group.get("entrypointTool") or ""),
            "userInvocable": normalize_bool(group.get("userInvocable"), True),
            "disableModelInvocation": normalize_bool(group.get("disableModelInvocation"), False),
            "argumentHint": str(group.get("argumentHint") or ""),
            "requiresEnv": ensure_string_list(group.get("requiresEnv")),
            "requiresBinaries": ensure_string_list(group.get("requiresBinaries")),
            "supportedOs": ensure_string_list(group.get("supportedOs")),
            "supportFiles": ensure_string_list(group.get("supportFiles")),
            "testCommand": str(group.get("testCommand") or ""),
            "instructions": str(group.get("instructions") or ""),
            "advanced": permission_mode == "advanced_power_mode",
            "write": permission_mode in {"approval_required", "advanced_power_mode"},
            "tags": sorted({"builtin", "group", *ensure_string_list(group.get("tags"))}),
        }

    def _skill_from_tool(self, tool: SkillToolDescriptor, config: AgentGatewayConfig) -> dict[str, Any]:
        override = BUILTIN_SKILL_OVERRIDES.get(tool.name, {})
        advanced = bool(tool.advanced)
        permission_mode = str(override.get("permissionMode") or self._permission_mode_for_tool(tool))
        tags = sorted({*ensure_string_list(override.get("tags")), tool.category, "builtin", *("advanced" if advanced else "",)})
        return {
            "schema": "vrcforge.skill.v1", "name": tool.name,
            "title": override.get("title") or title_from_name(tool.name),
            "description": tool_usage_description(tool.name, tool.description, write=tool.write),
            "category": tool.category, "source": "builtin", "skillType": "tool",
            "enabled": True, "available": self._ports.tool_visible(tool.name, config),
            "permissionMode": permission_mode, "riskLevel": "critical" if advanced else "medium" if tool.write else "low",
            "whenToUse": override.get("whenToUse") or tool.description,
            "inputs": ensure_string_list(override.get("inputs")) or ["Tool-specific JSON arguments."],
            "outputs": ensure_string_list(override.get("outputs")) or ["Tool result JSON."],
            "sideEffects": override.get("sideEffects") or ("may request or perform approved writes" if tool.write else "none"),
            "backupRestore": override.get("backupRestore") or ("required before writes" if tool.write else "not required"),
            "tools": [tool.name], "allowedTools": [tool.name], "disallowedTools": [], "entrypointTool": tool.name,
            "userInvocable": normalize_bool(override.get("userInvocable"), True),
            "disableModelInvocation": normalize_bool(override.get("disableModelInvocation"), False) or (tool.requires_user_activation and not self._ports.computer_use_model_invocable(config)),
            "argumentHint": "", "requiresEnv": [], "requiresBinaries": [], "supportedOs": [], "supportFiles": [],
            "testCommand": override.get("testCommand") or "", "instructions": "", "advanced": advanced, "write": tool.write,
            "tags": [tag for tag in tags if tag],
        }

    def _skill_from_write_handler(self, handler: SkillWriteHandlerDescriptor, config: AgentGatewayConfig) -> dict[str, Any]:
        override = BUILTIN_SKILL_OVERRIDES.get(handler.name, {})
        advanced = bool(handler.advanced)
        tags = sorted({*ensure_string_list(override.get("tags")), "supervised-write", "builtin", *("advanced" if advanced else "",)})
        return {
            "schema": "vrcforge.skill.v1", "name": handler.name,
            "title": override.get("title") or title_from_name(handler.name),
            "description": tool_usage_description(handler.name, handler.description, write=True),
            "category": "supervised-write", "source": "builtin", "skillType": "tool",
            "enabled": True, "available": self._ports.write_handler_visible(handler.name, config),
            "permissionMode": str(override.get("permissionMode") or ("advanced_power_mode" if advanced else "approval_required")),
            "riskLevel": handler.risk_level, "whenToUse": override.get("whenToUse") or handler.description,
            "inputs": ensure_string_list(override.get("inputs")) or ["Approved write payload."],
            "outputs": ensure_string_list(override.get("outputs")) or ["Write result JSON and audit record."],
            "sideEffects": override.get("sideEffects") or "writes Unity project or generated artifacts after approval",
            "backupRestore": override.get("backupRestore") or "requires preview, backup, apply, validate, restore path",
            "tools": ["vrcforge_request_apply", "vrcforge_apply_approved", handler.name],
            "allowedTools": ["vrcforge_request_apply", "vrcforge_apply_approved", handler.name],
            "disallowedTools": [], "entrypointTool": handler.name, "userInvocable": True,
            "disableModelInvocation": False, "argumentHint": "", "requiresEnv": [], "requiresBinaries": [],
            "supportedOs": [], "supportFiles": [], "testCommand": override.get("testCommand") or "",
            "instructions": "", "advanced": advanced, "write": True, "tags": [tag for tag in tags if tag],
        }

    @staticmethod
    def _permission_mode_for_tool(tool: SkillToolDescriptor) -> str:
        if tool.advanced:
            return "advanced_power_mode"
        if tool.write:
            return "approval_required"
        if tool.category == "plan/preview":
            return "preview"
        return "read_only"

    def _skill_dependency_visible(self, tool_name: str, config: AgentGatewayConfig) -> bool:
        tool_name = str(tool_name or "").strip()
        tool = self._tool(tool_name)
        if tool:
            return self._ports.tool_visible(tool.name, config)
        handler = self._write_handler(tool_name)
        if handler:
            return self._ports.write_handler_visible(handler.name, config)
        return False

    def _validated_user_skills_root(self, *, create: bool) -> Path | None:
        skills_dir = self.user_skills_dir
        if not os.path.lexists(skills_dir):
            if not create:
                return None
            skills_dir.mkdir(parents=True, exist_ok=True)
        if _path_is_link_like(skills_dir) or not skills_dir.is_dir():
            raise AgentGatewayError("User skill store must be a regular non-link directory.", status_code=400)
        return skills_dir

    def validated_user_skills_root(self) -> Path | None:
        with self.write_lock:
            return self._validated_user_skills_root(create=False)

    def validated_user_skill_source(self, skill: dict[str, Any]) -> Path:
        with self.write_lock:
            return self._validated_user_skill_source(skill)

    def _validated_user_skill_source(self, skill: dict[str, Any]) -> Path:
        storage_value = str(skill.get("storagePath") or "").strip()
        if not storage_value:
            raise AgentGatewayError("User skill storage path is missing.", status_code=404)
        skills_dir = self._validated_user_skills_root(create=False)
        if skills_dir is None:
            raise AgentGatewayError("User skill store was not found.", status_code=404)
        storage_path = Path(storage_value)
        skill_dir = storage_path.parent
        if (
            skill_dir.parent != skills_dir
            or not os.path.lexists(skill_dir)
            or _path_is_link_like(skill_dir)
            or not skill_dir.is_dir()
            or not os.path.lexists(storage_path)
            or _path_is_link_like(storage_path)
            or not storage_path.is_file()
        ):
            raise AgentGatewayError("User skill source is not a regular confined file.", status_code=400)
        try:
            root = skills_dir.resolve(strict=True)
            resolved_dir = skill_dir.resolve(strict=True)
            resolved_path = storage_path.resolve(strict=True)
        except OSError as exc:
            raise AgentGatewayError("User skill source could not be resolved safely.", status_code=400) from exc
        if resolved_dir.parent != root or resolved_path.parent != resolved_dir:
            raise AgentGatewayError("User skill source escapes the user skill store.", status_code=400)
        expected_name = normalize_skill_id(str(skill.get("name") or ""))
        if (
            storage_path.name != "SKILL.md"
            or skill_dir.name != expected_name
        ):
            raise AgentGatewayError("User skill source identity does not match its directory.", status_code=400)
        return storage_path

    def read_user_skill_manifest_bytes(self, skill: dict[str, Any]) -> bytes:
        with self.write_lock:
            source = self._validated_user_skill_source(skill)
            try:
                metadata = source.stat(follow_symlinks=False)
                if metadata.st_size > USER_SKILL_MANIFEST_MAX_BYTES:
                    raise AgentGatewayError("User skill manifest exceeds its size limit.", status_code=400)
                with source.open("rb") as stream:
                    data = stream.read(USER_SKILL_MANIFEST_MAX_BYTES + 1)
            except OSError as exc:
                raise AgentGatewayError("User skill manifest could not be read safely.", status_code=400) from exc
            if len(data) > USER_SKILL_MANIFEST_MAX_BYTES:
                raise AgentGatewayError("User skill manifest exceeds its size limit.", status_code=400)
            return data

    def _load_user_skills(self) -> list[dict[str, Any]]:
        skills_dir = self._validated_user_skills_root(create=False)
        if skills_dir is None:
            return []
        skills: list[dict[str, Any]] = []
        for skill_dir in sorted(skills_dir.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if _path_is_link_like(skill_dir):
                skills.append(
                    self._invalid_user_skill(
                        skill_file,
                        AgentGatewayError(
                            "User skill directory must be a regular non-link directory.",
                            status_code=400,
                        ),
                    )
                )
                continue
            if not skill_dir.is_dir() or not os.path.lexists(skill_file):
                continue
            try:
                if (
                    _path_has_link_like_parent(skill_file, skills_dir)
                    or not skill_file.is_file()
                ):
                    raise AgentGatewayError(
                        "User skill manifest must be a regular non-link file.",
                        status_code=400,
                    )
                metadata = skill_file.stat(follow_symlinks=False)
                if metadata.st_size > USER_SKILL_MANIFEST_MAX_BYTES:
                    raise AgentGatewayError(
                        "User skill manifest exceeds its size limit.",
                        status_code=400,
                    )
                parsed = parse_skill_markdown(
                    skill_file,
                    max_bytes=USER_SKILL_MANIFEST_MAX_BYTES,
                )
                normalized = self._normalize_user_skill(parsed, existing_id=str(parsed.get("name") or skill_file.parent.name))
                directory_skill_id = normalize_skill_id(skill_file.parent.name)
                if (
                    skill_file.parent.name != directory_skill_id
                    or normalized["name"] != directory_skill_id
                ):
                    raise AgentGatewayError(
                        "User skill manifest name must match its directory name.",
                        status_code=400,
                    )
                projected_state = self._load_projected_skill_state(skill_file)
                if projected_state is not None:
                    normalized["enabled"] = projected_state
                    normalized["available"] = projected_state
                normalized["storagePath"] = str(skill_file)
                skills.append(normalized)
            except Exception as exc:  # noqa: BLE001 - one broken user skill must not break startup.
                skills.append(self._invalid_user_skill(skill_file, exc))
        return skills

    @staticmethod
    def _invalid_user_skill(skill_file: Path, exc: Exception) -> dict[str, Any]:
        fallback_name = normalize_skill_id(skill_file.parent.name)
        return {
            "schema": "vrcforge.skill.v1", "name": fallback_name, "title": fallback_name,
            "description": "User skill could not be loaded.", "category": "user", "source": "user",
            "skillType": "package", "enabled": False, "available": False,
            "permissionMode": "instruction_only", "riskLevel": "low", "whenToUse": "", "inputs": [],
            "outputs": [], "sideEffects": "none", "backupRestore": "not required", "tools": [],
            "allowedTools": [], "disallowedTools": [], "entrypointTool": "", "userInvocable": False,
            "disableModelInvocation": True, "argumentHint": "", "requiresEnv": [], "requiresBinaries": [],
            "supportedOs": [], "supportFiles": [], "testCommand": "", "instructions": "", "advanced": False,
            "write": False, "tags": ["user", "invalid"], "storagePath": str(skill_file), "loadError": str(exc),
        }

    def _load_projected_skill_state(self, skill_file: Path) -> bool | None:
        state_path = skill_file.parent / PROJECTED_SKILL_STATE_NAME
        if not os.path.lexists(state_path):
            return None
        if _path_is_link_like(state_path) or not state_path.is_file():
            raise AgentGatewayError("Projected skill state must be a regular non-link file.", status_code=400)
        metadata = state_path.stat(follow_symlinks=False)
        if metadata.st_size > PROJECTED_SKILL_STATE_MAX_BYTES:
            raise AgentGatewayError("Projected skill state exceeds its size limit.", status_code=400)
        with state_path.open("rb") as stream:
            raw = stream.read(PROJECTED_SKILL_STATE_MAX_BYTES + 1)
        if len(raw) > PROJECTED_SKILL_STATE_MAX_BYTES:
            raise AgentGatewayError("Projected skill state exceeds its size limit.", status_code=400)
        try:
            state = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("Projected skill state is not valid UTF-8 JSON.", status_code=400) from exc
        if (
            not isinstance(state, dict)
            or state.get("schema")
            not in {
                LEGACY_PROJECTED_SKILL_STATE_SCHEMA,
                PROJECTED_SKILL_STATE_SCHEMA,
            }
            or not isinstance(state.get("enabled"), bool)
        ):
            raise AgentGatewayError("Projected skill state has an invalid schema.", status_code=400)
        return bool(state["enabled"])

    def find_user_skill(self, skill_id: str) -> dict[str, Any] | None:
        with self.write_lock:
            return self._find_user_skill(skill_id)

    def _find_user_skill(self, skill_id: str) -> dict[str, Any] | None:
        skill_id = normalize_skill_id(skill_id)
        matches = [
            skill
            for skill in self._load_user_skills()
            if skill.get("name") == skill_id
        ]
        if len(matches) > 1:
            raise AgentGatewayError(
                f"User skill id is ambiguous on disk: {skill_id}",
                status_code=409,
            )
        return matches[0] if matches else None

    def _save_user_skill(self, skill: dict[str, Any]) -> None:
        skill_id = normalize_skill_id(str(skill.get("name") or ""))
        rendered = render_skill_markdown(skill)
        skills_dir = self._validated_user_skills_root(create=True)
        assert skills_dir is not None
        skill_dir = skills_dir / skill_id
        created_skill_dir = not os.path.lexists(skill_dir)
        if not created_skill_dir and (_path_is_link_like(skill_dir) or not skill_dir.is_dir()):
            raise AgentGatewayError(f"Refusing to write through an unsafe user skill directory: {skill_id}", status_code=400)
        skill_dir.mkdir(parents=True, exist_ok=True)
        if _path_is_link_like(skill_dir) or not skill_dir.is_dir():
            raise AgentGatewayError(f"Refusing to write through an unsafe user skill directory: {skill_id}", status_code=400)
        skill_file = skill_dir / "SKILL.md"
        if os.path.lexists(skill_file) and (_path_is_link_like(skill_file) or not skill_file.is_file()):
            raise AgentGatewayError(f"Refusing to overwrite an unsafe user skill file: {skill_id}", status_code=400)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=skill_dir,
                prefix=".SKILL.md.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(rendered)
                temporary.flush()
                os.fsync(temporary.fileno())
            if os.path.lexists(skill_file) and (
                _path_is_link_like(skill_file) or not skill_file.is_file()
            ):
                raise AgentGatewayError(
                    f"Refusing to overwrite an unsafe user skill file: {skill_id}",
                    status_code=400,
                )
            os.replace(temporary_path, skill_file)
            temporary_path = None
            fsync_directory_best_effort(skill_dir)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
            if created_skill_dir and not os.path.lexists(skill_file):
                try:
                    skill_dir.rmdir()
                except OSError:
                    pass

    def _capture_user_skill_target(self, skill_id: str) -> tuple[bool, bytes | None]:
        skills_dir = self._validated_user_skills_root(create=False)
        if skills_dir is None:
            return False, None
        skill_dir = skills_dir / skill_id
        directory_existed = os.path.lexists(skill_dir)
        if not directory_existed:
            return False, None
        if _path_is_link_like(skill_dir) or not skill_dir.is_dir():
            raise AgentGatewayError(
                f"Refusing to write through an unsafe user skill directory: {skill_id}",
                status_code=400,
            )
        skill_file = skill_dir / "SKILL.md"
        if not os.path.lexists(skill_file):
            return True, None
        if _path_is_link_like(skill_file) or not skill_file.is_file():
            raise AgentGatewayError(
                f"Refusing to overwrite an unsafe user skill file: {skill_id}",
                status_code=400,
            )
        try:
            metadata = skill_file.stat(follow_symlinks=False)
            if metadata.st_size > USER_SKILL_MANIFEST_MAX_BYTES:
                raise AgentGatewayError(
                    "User skill manifest exceeds its size limit.",
                    status_code=400,
                )
            with skill_file.open("rb") as stream:
                original_bytes = stream.read(USER_SKILL_MANIFEST_MAX_BYTES + 1)
        except OSError as exc:
            raise AgentGatewayError(
                "User skill manifest could not be read safely.",
                status_code=400,
            ) from exc
        if len(original_bytes) > USER_SKILL_MANIFEST_MAX_BYTES:
            raise AgentGatewayError(
                "User skill manifest exceeds its size limit.",
                status_code=400,
            )
        return True, original_bytes

    @staticmethod
    def _atomic_restore_user_skill_file(skill_file: Path, data: bytes) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=skill_file.parent,
                prefix=".SKILL.md.rollback.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            if os.path.lexists(skill_file) and (
                _path_is_link_like(skill_file) or not skill_file.is_file()
            ):
                raise AgentGatewayError(
                    "Refusing to restore through an unsafe user skill file.",
                    status_code=400,
                )
            os.replace(temporary_path, skill_file)
            temporary_path = None
            fsync_directory_best_effort(skill_file.parent)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def _rollback_user_skill_save(
        self,
        skill_id: str,
        directory_existed: bool,
        original_bytes: bytes | None,
        *,
        skills_root_existed: bool,
    ) -> None:
        skills_dir = self._validated_user_skills_root(create=False)
        if skills_dir is None:
            if directory_existed or original_bytes is not None:
                raise RuntimeError("User skill rollback root disappeared.")
            return
        skill_dir = skills_dir / skill_id
        skill_file = skill_dir / "SKILL.md"
        if original_bytes is not None:
            if not os.path.lexists(skill_dir) or _path_is_link_like(skill_dir) or not skill_dir.is_dir():
                raise RuntimeError("User skill rollback directory is unsafe.")
            self._atomic_restore_user_skill_file(skill_file, original_bytes)
            return
        if os.path.lexists(skill_file):
            if _path_is_link_like(skill_file) or not skill_file.is_file():
                raise RuntimeError("User skill rollback target is unsafe.")
            skill_file.unlink()
            fsync_directory_best_effort(skill_dir)
        if not directory_existed and os.path.lexists(skill_dir):
            if _path_is_link_like(skill_dir) or not skill_dir.is_dir():
                raise RuntimeError("Created user skill directory became unsafe during rollback.")
            skill_dir.rmdir()

        if not skills_root_existed and os.path.lexists(skills_dir):
            if _path_is_link_like(skills_dir) or not skills_dir.is_dir():
                raise RuntimeError("Created user skill root became unsafe during rollback.")
            skills_dir.rmdir()

    def _user_skill_delete_staging_root(self, skills_dir: Path) -> tuple[Path, bool]:
        staging_root = skills_dir.parent / ".skill-registry-staging"
        created = not os.path.lexists(staging_root)
        if os.path.lexists(staging_root) and (
            _path_is_link_like(staging_root) or not staging_root.is_dir()
        ):
            raise AgentGatewayError("User skill delete staging root is unsafe.", status_code=400)
        staging_root.mkdir(parents=True, exist_ok=True)
        if _path_is_link_like(staging_root) or not staging_root.is_dir():
            raise AgentGatewayError("User skill delete staging root is unsafe.", status_code=400)
        return staging_root, created

    @staticmethod
    def _reject_package_managed_user_skill(skill_file: Path) -> None:
        if os.path.lexists(skill_file.parent / PROJECTED_SKILL_STATE_NAME):
            raise AgentGatewayError(
                "Package-projected skills must be managed through the Skill Package Manager.",
                status_code=409,
            )

    def _normalize_user_skill(self, payload: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
        skill_id = normalize_skill_id(str(first_payload_value(payload, "name") or existing_id or ""))
        if not SKILL_ID_RE.fullmatch(skill_id):
            raise AgentGatewayError("Skill name must match [a-z][a-z0-9_.-]{1,80}.", status_code=400)
        permission_mode = normalize_skill_permission(first_payload_value(payload, "permissionMode", "permission_mode", "permission-mode"))
        tools = ensure_string_list(first_payload_value(payload, "tools", "allowedTools", "allowed_tools", "allowed-tools"))
        allowed_tools = ensure_string_list(first_payload_value(payload, "allowedTools", "allowed_tools", "allowed-tools", default=tools))
        disallowed_tools = ensure_string_list(first_payload_value(payload, "disallowedTools", "disallowed_tools", "disallowed-tools"))
        title = str(first_payload_value(payload, "title", default=title_from_name(skill_id))).strip()
        instructions = str(first_payload_value(payload, "instructions", "body", default="")).strip()
        return {
            "schema": "vrcforge.skill.v1", "name": skill_id, "title": title[:120],
            "description": str(first_payload_value(payload, "description", default="")).strip()[:500],
            "category": str(first_payload_value(payload, "category", default="user")).strip()[:80],
            "source": "user", "skillType": "package",
            "enabled": normalize_bool(first_payload_value(payload, "enabled", default=True), True),
            "available": normalize_bool(first_payload_value(payload, "enabled", default=True), True),
            "permissionMode": permission_mode,
            "riskLevel": normalize_risk_level(first_payload_value(payload, "riskLevel", "risk_level", "risk-level")),
            "whenToUse": str(first_payload_value(payload, "whenToUse", "when_to_use", "when-to-use", default="")).strip()[:1000],
            "inputs": ensure_string_list(first_payload_value(payload, "inputs")),
            "outputs": ensure_string_list(first_payload_value(payload, "outputs")),
            "sideEffects": str(first_payload_value(payload, "sideEffects", "side_effects", "side-effects", default="none")).strip()[:500],
            "backupRestore": str(first_payload_value(payload, "backupRestore", "backup_restore", "backup-restore", default="not required")).strip()[:500],
            "tools": tools, "allowedTools": allowed_tools, "disallowedTools": disallowed_tools,
            "entrypointTool": str(first_payload_value(payload, "entrypointTool", "entrypoint_tool", "entrypoint-tool", default="")).strip(),
            "userInvocable": normalize_bool(first_payload_value(payload, "userInvocable", "user_invocable", "user-invocable", default=True), True),
            "disableModelInvocation": normalize_bool(first_payload_value(payload, "disableModelInvocation", "disable_model_invocation", "disable-model-invocation", default=False), False),
            "argumentHint": str(first_payload_value(payload, "argumentHint", "argument_hint", "argument-hint", default="")).strip()[:240],
            "requiresEnv": ensure_string_list(first_payload_value(payload, "requiresEnv", "requires_env", "requires-env")),
            "requiresBinaries": ensure_string_list(first_payload_value(payload, "requiresBinaries", "requires_binaries", "requires-binaries")),
            "supportedOs": ensure_string_list(first_payload_value(payload, "supportedOs", "supported_os", "supported-os")),
            "supportFiles": ensure_string_list(first_payload_value(payload, "supportFiles", "support_files", "support-files")),
            "testCommand": str(first_payload_value(payload, "testCommand", "test_command", "test-command", default="")).strip()[:500],
            "instructions": instructions, "advanced": permission_mode == "advanced_power_mode",
            "write": permission_mode in {"approval_required", "advanced_power_mode"},
            "tags": sorted({"user", *ensure_string_list(first_payload_value(payload, "tags"))}),
        }

    def _ensure_user_skill_can_use_id(self, skill_id: str, skills: list[dict[str, Any]]) -> None:
        self.validate_projection_name(skill_id)
        if any(skill.get("name") == skill_id for skill in skills):
            raise AgentGatewayError(f"User skill already exists: {skill_id}", status_code=409)

    def _decorate_skill_validation(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        next_skill = dict(skill)
        validation = self._validate_skill(next_skill, config)
        next_skill["validation"] = validation
        next_skill["availabilityReasons"] = ensure_string_list(validation.get("reasons"))
        if validation.get("status") == "error":
            next_skill["available"] = False
        return next_skill

    def validate_skill(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        with self.write_lock:
            return self._validate_skill(skill, config)

    def _validate_skill(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        status = "ok"
        reasons: list[str] = []
        if skill.get("loadError"):
            return {"status": "error", "reasons": [str(skill.get("loadError"))]}
        skill_name = normalize_skill_id(str(skill.get("name") or ""))
        if str(skill.get("source") or "") == "user" and skill_name in self._reserved_skill_ids():
            status = "error"
            reasons.append(f"skill name conflicts with a builtin skill: {skill_name}")
        if not skill.get("enabled", True):
            if status == "ok":
                status = "warning"
            reasons.append("skill disabled")

        known_tools = {tool.name for tool in self._ports.list_tools()} | {
            handler.name for handler in self._ports.list_write_handlers()
        }
        allowed_tools = ensure_string_list(skill.get("allowedTools") or skill.get("tools"))
        disallowed_tools = ensure_string_list(skill.get("disallowedTools"))
        unknown_allowed = [item for item in allowed_tools if item and item not in known_tools]
        unknown_disallowed = [item for item in disallowed_tools if item and item not in known_tools]
        if unknown_allowed:
            status = "error"
            reasons.append("unknown allowed tools: " + ", ".join(unknown_allowed[:8]))
        elif unknown_disallowed and status == "ok":
            status = "warning"
            reasons.append("unknown disallowed tools: " + ", ".join(unknown_disallowed[:8]))

        entrypoint = str(skill.get("entrypointTool") or "").strip()
        if entrypoint:
            if entrypoint not in known_tools:
                status = "error"
                reasons.append(f"unknown entrypoint tool: {entrypoint}")
            elif entrypoint in disallowed_tools:
                status = "error"
                reasons.append(f"entrypoint tool is disallowed: {entrypoint}")
            elif allowed_tools and entrypoint not in allowed_tools:
                status = "error"
                reasons.append(f"entrypoint tool is not in allowed tools: {entrypoint}")
            elif not self._skill_dependency_visible(entrypoint, config) and status == "ok":
                status = "warning"
                reasons.append(f"entrypoint tool is unavailable: {entrypoint}")

        missing_env = [name for name in ensure_string_list(skill.get("requiresEnv")) if name and not os.environ.get(name)]
        if missing_env:
            status = "error"
            reasons.append("missing env: " + ", ".join(missing_env[:8]))
        missing_bins = [name for name in ensure_string_list(skill.get("requiresBinaries")) if name and not shutil.which(name)]
        if missing_bins:
            status = "error"
            reasons.append("missing binaries: " + ", ".join(missing_bins[:8]))

        supported_os = [item.lower() for item in ensure_string_list(skill.get("supportedOs")) if item]
        if supported_os and current_os_key() not in supported_os and "any" not in supported_os:
            status = "error"
            reasons.append(f"unsupported os: {current_os_key()}")

        try:
            self._load_runtime_skill_support_files(skill)
        except AgentGatewayError as exc:
            status = "error"
            reasons.append(str(exc))

        if not skill.get("available", True) and status == "ok":
            status = "warning"
            reasons.append("dependencies unavailable")
        return {"status": status, "reasons": reasons}

    def load_runtime_skill_support_files(self, skill: dict[str, Any]) -> list[dict[str, str]]:
        with self.write_lock:
            return self._load_runtime_skill_support_files(skill)

    def prepare_runtime_skill(
        self,
        skill_id: str,
        config: AgentGatewayConfig,
        package_audit_context: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> RuntimeSkillSnapshot | None:
        """Capture one runtime package view while the user-skill lock is held."""

        normalized_id = normalize_skill_id(skill_id)
        with self.write_lock:
            matches = [
                item
                for item in ensure_list(
                    self._build_skill_registry(config).get("skills")
                )
                if isinstance(item, dict)
                and normalize_skill_id(str(item.get("name") or ""))
                == normalized_id
            ]
            if len(matches) > 1:
                raise AgentGatewayError(
                    f"Skill id is ambiguous in the runtime registry: {normalized_id}",
                    status_code=409,
                )
            skill = matches[0] if matches else None
            if skill is None:
                return None
            audit_context = package_audit_context(skill)
            validation = ensure_dict(skill.get("validation")) or self._validate_skill(
                skill,
                config,
            )
            loaded = bool(skill.get("enabled", True)) and validation.get("status") != "error"
            support_files: list[dict[str, str]] = []
            if loaded:
                try:
                    support_files = self._load_runtime_skill_support_files(skill)
                except AgentGatewayError as exc:
                    loaded = False
                    validation = {"status": "error", "reasons": [str(exc)]}
            return RuntimeSkillSnapshot(
                skill=dict(skill),
                validation=dict(validation),
                support_files=tuple(dict(item) for item in support_files),
                package_audit_context=dict(audit_context),
                loaded=loaded,
            )

    def _load_runtime_skill_support_files(self, skill: dict[str, Any]) -> list[dict[str, str]]:
        """Load declared projected support files through a small, text-only boundary."""

        declared = ensure_string_list(skill.get("supportFiles"))
        if not declared:
            return []
        if len(declared) > RUNTIME_SKILL_SUPPORT_MAX_FILES:
            raise AgentGatewayError(
                f"skill support files exceed the {RUNTIME_SKILL_SUPPORT_MAX_FILES}-file runtime limit",
                status_code=400,
            )

        storage_value = str(skill.get("storagePath") or "").strip()
        if not storage_value:
            raise AgentGatewayError("skill support files require a projected SKILL.md storage path", status_code=400)
        storage_path = Path(storage_value)
        try:
            storage_resolved = storage_path.resolve(strict=True)
            support_root = storage_resolved.parent
            user_skills_root = self._validated_user_skills_root(create=False)
            if user_skills_root is None:
                raise FileNotFoundError("user skill store is missing")
            user_skills_root = user_skills_root.resolve(strict=True)
            support_root.relative_to(user_skills_root)
        except (OSError, ValueError) as exc:
            raise AgentGatewayError("skill support root is missing or outside the user skill store", status_code=400) from exc
        if _path_is_link_like(storage_path) or not storage_resolved.is_file():
            raise AgentGatewayError("projected SKILL.md must be a regular non-link file", status_code=400)

        try:
            from skill_packages import SkillPackageService
        except ImportError as exc:  # pragma: no cover - packaged builds always include package support.
            raise AgentGatewayError("skill support validation is unavailable", status_code=500) from exc

        loaded: list[dict[str, str]] = []
        total_bytes = 0
        seen: set[str] = set()
        for raw_relative in declared:
            relative = str(raw_relative or "").strip()
            if not relative or "\\" in relative:
                raise AgentGatewayError("skill support paths must use non-empty forward-slash relative paths", status_code=400)
            relative_path = PurePosixPath(relative)
            if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
                raise AgentGatewayError(f"unsafe skill support path: {relative}", status_code=400)
            normalized = relative_path.as_posix()
            collision_key = normalized.casefold()
            if collision_key in seen:
                raise AgentGatewayError(f"duplicate skill support path: {normalized}", status_code=400)
            seen.add(collision_key)

            candidate = support_root.joinpath(*relative_path.parts)
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(support_root)
            except (OSError, ValueError) as exc:
                raise AgentGatewayError(f"skill support file is missing or escapes its projection: {normalized}", status_code=400) from exc
            if _path_is_link_like(candidate) or _path_has_link_like_parent(candidate, support_root) or not resolved.is_file():
                raise AgentGatewayError(f"skill support file must be a regular non-link file: {normalized}", status_code=400)
            try:
                metadata = resolved.stat(follow_symlinks=False)
            except OSError as exc:
                raise AgentGatewayError(f"skill support file metadata is unavailable: {normalized}", status_code=400) from exc
            if metadata.st_size > RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES:
                raise AgentGatewayError(
                    f"skill support file exceeds the {RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES}-byte limit: {normalized}",
                    status_code=400,
                )
            if total_bytes + metadata.st_size > RUNTIME_SKILL_SUPPORT_MAX_TOTAL_BYTES:
                raise AgentGatewayError(
                    f"skill support files exceed the {RUNTIME_SKILL_SUPPORT_MAX_TOTAL_BYTES}-byte total limit",
                    status_code=400,
                )
            try:
                with resolved.open("rb") as stream:
                    data = stream.read(RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES + 1)
            except OSError as exc:
                raise AgentGatewayError(f"skill support file cannot be read: {normalized}", status_code=400) from exc
            if len(data) > RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES:
                raise AgentGatewayError(
                    f"skill support file exceeds the {RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES}-byte limit: {normalized}",
                    status_code=400,
                )
            total_bytes += len(data)
            if total_bytes > RUNTIME_SKILL_SUPPORT_MAX_TOTAL_BYTES:
                raise AgentGatewayError(
                    f"skill support files exceed the {RUNTIME_SKILL_SUPPORT_MAX_TOTAL_BYTES}-byte total limit",
                    status_code=400,
                )
            if SkillPackageService._contains_sensitive_content(data):
                raise AgentGatewayError(f"skill support file contains secret or binary material: {normalized}", status_code=400)
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AgentGatewayError(f"skill support file must be UTF-8 text: {normalized}", status_code=400) from exc
            loaded.append({"path": normalized, "content": content})
        return loaded

    def create_user_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._ports.local_state_write_guard(), self.write_lock:
            skills_root_existed = self._validated_user_skills_root(create=False) is not None
            skills = self._load_user_skills()
            skill = self._normalize_user_skill(payload)
            skill_id = str(skill["name"])
            self._ensure_user_skill_can_use_id(skill_id, skills)
            directory_existed, original_bytes = self._capture_user_skill_target(skill_id)
            try:
                self._save_user_skill(skill)
                registry = self._build_skill_registry()
                self._ports.append_audit({"event": "user_skill_created", "skill": skill_id})
            except Exception:
                self._rollback_user_skill_save(
                    skill_id,
                    directory_existed,
                    original_bytes,
                    skills_root_existed=skills_root_existed,
                )
                raise
            return {"ok": True, "skill": skill, **registry}

    def update_user_skill(self, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._ports.local_state_write_guard(), self.write_lock:
            skill_id = normalize_skill_id(skill_id)
            skills = self._load_user_skills()
            matches = [
                (index, skill)
                for index, skill in enumerate(skills)
                if skill.get("name") == skill_id
            ]
            if not matches:
                raise AgentGatewayError(f"User skill was not found: {skill_id}", status_code=404)
            if len(matches) != 1:
                raise AgentGatewayError(
                    f"User skill id is ambiguous on disk: {skill_id}",
                    status_code=409,
                )
            index, existing = matches[0]
            existing_source = self._validated_user_skill_source(existing)
            self._reject_package_managed_user_skill(existing_source)
            next_payload = {**existing, **payload, "name": skill_id}
            skills[index] = self._normalize_user_skill(next_payload, existing_id=skill_id)
            directory_existed, original_bytes = self._capture_user_skill_target(skill_id)
            try:
                self._save_user_skill(skills[index])
                registry = self._build_skill_registry()
                self._ports.append_audit({"event": "user_skill_updated", "skill": skill_id})
            except Exception:
                self._rollback_user_skill_save(
                    skill_id,
                    directory_existed,
                    original_bytes,
                    skills_root_existed=True,
                )
                raise
            return {"ok": True, "skill": skills[index], **registry}

    def delete_user_skill(self, skill_id: str) -> dict[str, Any]:
        with self._ports.local_state_write_guard(), self.write_lock:
            skill_id = normalize_skill_id(skill_id)
            skills = self._load_user_skills()
            matches = [skill for skill in skills if skill.get("name") == skill_id]
            if not matches:
                raise AgentGatewayError(f"User skill was not found: {skill_id}", status_code=404)
            if len(matches) != 1:
                raise AgentGatewayError(
                    f"User skill id is ambiguous on disk: {skill_id}",
                    status_code=409,
                )
            existing = matches[0]
            skill_file = self._validated_user_skill_source(existing)
            self._reject_package_managed_user_skill(skill_file)
            skills_dir = self._validated_user_skills_root(create=False)
            assert skills_dir is not None
            skill_dir = skill_file.parent
            if not os.path.lexists(skill_dir) or _path_is_link_like(skill_dir) or not skill_dir.is_dir():
                raise AgentGatewayError(f"Refusing to delete an unsafe user skill directory: {skill_id}", status_code=400)
            staging_root, staging_root_created = self._user_skill_delete_staging_root(skills_dir)
            isolated = staging_root / f"{skill_id}.{secrets.token_hex(8)}.deleted"
            try:
                os.replace(skill_dir, isolated)
                try:
                    registry = self._build_skill_registry()
                    self._ports.append_audit({"event": "user_skill_deleted", "skill": skill_id})
                except Exception:
                    if os.path.lexists(skill_dir):
                        raise RuntimeError("User skill delete rollback target was recreated.")
                    os.replace(isolated, skill_dir)
                    raise
                try:
                    remove_tree(isolated)
                except OSError:
                    pass
            finally:
                if staging_root_created and os.path.lexists(staging_root):
                    try:
                        staging_root.rmdir()
                    except OSError:
                        pass
            return {"ok": True, "deleted": skill_id, **registry}

    def build_skill_registry(
        self,
        config: AgentGatewayConfig | None = None,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> dict[str, Any]:
        with self.write_lock:
            return self._build_skill_registry(config, exposure_layer)

    def _build_skill_registry(
        self,
        config: AgentGatewayConfig | None = None,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> dict[str, Any]:
        exposure_layer = normalize_exposure_layer(exposure_layer)
        config = config or self._ports.ensure_config()
        builtin_skills = self._builtin_skill_definitions(config)
        user_skills = self._load_user_skills()
        skills = [*builtin_skills, *user_skills]
        skills = [self._decorate_skill_validation(skill, config) for skill in skills]
        if exposure_layer == EXPOSURE_LAYER_PLANNING:
            skills = [skill for skill in skills if not bool(skill.get("write"))]
        available_count = sum(1 for skill in skills if skill.get("available") and skill.get("enabled", True))
        warning_count = sum(1 for skill in skills if ensure_dict(skill.get("validation")).get("status") == "warning")
        error_count = sum(1 for skill in skills if ensure_dict(skill.get("validation")).get("status") == "error")
        return {
            "ok": True,
            "schema": "vrcforge.skills.v1",
            "exposureLayer": exposure_layer,
            "skills": skills,
            "count": len(skills),
            "availableCount": available_count,
            "builtinCount": len(builtin_skills),
            "userCount": len(user_skills),
            "warningCount": warning_count,
            "errorCount": error_count,
            "storage": {
                "scope": "user-data",
                "writable": True,
                "path": str(self.user_skills_dir),
            },
        }

    def check_skill_registry(
        self,
        config: AgentGatewayConfig | None = None,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> dict[str, Any]:
        with self.write_lock:
            config = config or self._ports.ensure_config()
            registry = self._build_skill_registry(config, exposure_layer)
        checks = []
        for skill in registry["skills"]:
            validation = ensure_dict(skill.get("validation"))
            checks.append(
                {
                    "name": skill.get("name"),
                    "title": skill.get("title"),
                    "source": skill.get("source"),
                    "skillType": skill.get("skillType"),
                    "status": validation.get("status") or ("ok" if skill.get("available") else "warning"),
                    "reasons": ensure_string_list(validation.get("reasons")),
                    "available": bool(skill.get("available")),
                }
            )
        errors = [item for item in checks if item["status"] == "error"]
        warnings = [item for item in checks if item["status"] == "warning"]
        return {
            "ok": not errors,
            "schema": "vrcforge.skills.check.v1",
            "count": len(checks),
            "errorCount": len(errors),
            "warningCount": len(warnings),
            "checks": checks,
        }
