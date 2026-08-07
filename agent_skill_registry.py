from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from agent_gateway import (
    BUILTIN_SKILL_GROUPS,
    BUILTIN_SKILL_OVERRIDES,
    AgentGatewayConfig,
    AgentGatewayError,
    AgentTool,
    AgentWriteHandler,
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
    first_payload_value,
    normalize_bool,
    normalize_risk_level,
    normalize_skill_id,
    normalize_skill_permission,
    parse_skill_markdown,
    remove_tree,
    render_skill_markdown,
    title_from_name,
    tool_usage_description,
)


class AgentSkillRegistryService:
    """Own builtin skill projection while the gateway keeps all host state."""

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_builtin_skill_definitions(self, config: AgentGatewayConfig) -> list[dict[str, Any]]:
        skills: list[dict[str, Any]] = []
        for group in BUILTIN_SKILL_GROUPS:
            skills.append(self._skill_from_builtin_group(group, config))
        for tool in self._tools.values():
            skills.append(self._skill_from_tool(tool, config))
        for handler in self._write_handlers.values():
            if handler.name in WRAPPER_ONLY_WRITE_TARGETS:
                continue
            skills.append(self._skill_from_write_handler(handler, config))
        return sorted(skills, key=lambda item: (str(item.get("category") or ""), str(item.get("name") or "")))

    def _impl_skill_from_builtin_group(self, group: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
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

    def _impl_skill_from_tool(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        override = BUILTIN_SKILL_OVERRIDES.get(tool.name, {})
        advanced = bool(tool.advanced)
        permission_mode = str(override.get("permissionMode") or self._permission_mode_for_tool(tool))
        tags = sorted({*ensure_string_list(override.get("tags")), tool.category, "builtin", *("advanced" if advanced else "",)})
        return {
            "schema": "vrcforge.skill.v1", "name": tool.name,
            "title": override.get("title") or title_from_name(tool.name),
            "description": tool_usage_description(tool.name, tool.description, write=tool.write),
            "category": tool.category, "source": "builtin", "skillType": "tool",
            "enabled": True, "available": self._tool_visible(tool, config),
            "permissionMode": permission_mode, "riskLevel": "critical" if advanced else "medium" if tool.write else "low",
            "whenToUse": override.get("whenToUse") or tool.description,
            "inputs": ensure_string_list(override.get("inputs")) or ["Tool-specific JSON arguments."],
            "outputs": ensure_string_list(override.get("outputs")) or ["Tool result JSON."],
            "sideEffects": override.get("sideEffects") or ("may request or perform approved writes" if tool.write else "none"),
            "backupRestore": override.get("backupRestore") or ("required before writes" if tool.write else "not required"),
            "tools": [tool.name], "allowedTools": [tool.name], "disallowedTools": [], "entrypointTool": tool.name,
            "userInvocable": normalize_bool(override.get("userInvocable"), True),
            "disableModelInvocation": normalize_bool(override.get("disableModelInvocation"), False) or (tool.requires_user_activation and not self.computer_use_model_invocable(config)),
            "argumentHint": "", "requiresEnv": [], "requiresBinaries": [], "supportedOs": [], "supportFiles": [],
            "testCommand": override.get("testCommand") or "", "instructions": "", "advanced": advanced, "write": tool.write,
            "tags": [tag for tag in tags if tag],
        }

    def _impl_skill_from_write_handler(self, handler: AgentWriteHandler, config: AgentGatewayConfig) -> dict[str, Any]:
        override = BUILTIN_SKILL_OVERRIDES.get(handler.name, {})
        advanced = bool(handler.advanced)
        tags = sorted({*ensure_string_list(override.get("tags")), "supervised-write", "builtin", *("advanced" if advanced else "",)})
        return {
            "schema": "vrcforge.skill.v1", "name": handler.name,
            "title": override.get("title") or title_from_name(handler.name),
            "description": tool_usage_description(handler.name, handler.description, write=True),
            "category": "supervised-write", "source": "builtin", "skillType": "tool",
            "enabled": True, "available": self._write_handler_visible(handler, config),
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

    def _impl_skill_dependency_visible(self, tool_name: str, config: AgentGatewayConfig) -> bool:
        tool_name = str(tool_name or "").strip()
        tool = self._tools.get(tool_name)
        if tool:
            return self._tool_visible(tool, config)
        handler = self._write_handlers.get(tool_name)
        if handler:
            return self._write_handler_visible(handler, config)
        return False

    def _impl_user_skills_dir(self) -> Path:
        if self.config_path.parent.name.lower() == "config":
            return self.config_path.parent.parent / "skills"
        user_data_dir = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data_dir:
            return Path(user_data_dir) / "skills"
        return self.config_path.parent / "skills"

    def _impl_load_user_skills(self) -> list[dict[str, Any]]:
        skills_dir = self.user_skills_dir
        if not skills_dir.exists():
            return []
        skills: list[dict[str, Any]] = []
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                parsed = parse_skill_markdown(skill_file)
                normalized = self._normalize_user_skill(parsed, existing_id=str(parsed.get("name") or skill_file.parent.name))
                projected_state = self._load_projected_skill_state(skill_file)
                if projected_state is not None:
                    normalized["enabled"] = projected_state
                    normalized["available"] = projected_state
                normalized["storagePath"] = str(skill_file)
                skills.append(normalized)
            except Exception as exc:  # noqa: BLE001 - one broken user skill must not break startup.
                fallback_name = normalize_skill_id(skill_file.parent.name)
                skills.append({
                    "schema": "vrcforge.skill.v1", "name": fallback_name, "title": fallback_name,
                    "description": "User skill could not be loaded.", "category": "user", "source": "user",
                    "skillType": "package", "enabled": False, "available": False,
                    "permissionMode": "instruction_only", "riskLevel": "low", "whenToUse": "", "inputs": [],
                    "outputs": [], "sideEffects": "none", "backupRestore": "not required", "tools": [],
                    "allowedTools": [], "disallowedTools": [], "entrypointTool": "", "userInvocable": False,
                    "disableModelInvocation": True, "argumentHint": "", "requiresEnv": [], "requiresBinaries": [],
                    "supportedOs": [], "supportFiles": [], "testCommand": "", "instructions": "", "advanced": False,
                    "write": False, "tags": ["user", "invalid"], "storagePath": str(skill_file), "loadError": str(exc),
                })
        return skills

    def _impl_load_projected_skill_state(self, skill_file: Path) -> bool | None:
        state_path = skill_file.parent / PROJECTED_SKILL_STATE_NAME
        if not state_path.exists():
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
        if not isinstance(state, dict) or state.get("schema") != PROJECTED_SKILL_STATE_SCHEMA or not isinstance(state.get("enabled"), bool):
            raise AgentGatewayError("Projected skill state has an invalid schema.", status_code=400)
        return bool(state["enabled"])

    def _impl_find_user_skill(self, skill_id: str) -> dict[str, Any] | None:
        skill_id = normalize_skill_id(skill_id)
        for skill in self._load_user_skills():
            if skill.get("name") == skill_id:
                return skill
        return None

    def _impl_save_user_skills(self, skills: list[dict[str, Any]]) -> None:
        skills_dir = self.user_skills_dir
        skills_dir.mkdir(parents=True, exist_ok=True)
        existing_dirs = {path.name: path for path in skills_dir.iterdir() if path.is_dir()}
        wanted = {str(skill.get("name") or "") for skill in skills}
        for name, path in existing_dirs.items():
            if name not in wanted and SKILL_ID_RE.fullmatch(name):
                remove_tree(path)
        for skill in skills:
            self._save_user_skill(skill)

    def _impl_save_user_skill(self, skill: dict[str, Any]) -> None:
        skill_id = normalize_skill_id(str(skill.get("name") or ""))
        skill_dir = self.user_skills_dir / skill_id
        if skill_dir.exists() and _path_is_link_like(skill_dir):
            raise AgentGatewayError(f"Refusing to write through a linked user skill directory: {skill_id}", status_code=400)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists() and _path_is_link_like(skill_file):
            raise AgentGatewayError(f"Refusing to overwrite a linked user skill file: {skill_id}", status_code=400)
        skill_file.write_text(render_skill_markdown(skill), encoding="utf-8")

    def _impl_normalize_user_skill(self, payload: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
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

    def _impl_ensure_user_skill_can_use_id(self, skill_id: str, skills: list[dict[str, Any]]) -> None:
        if skill_id in self._tools or skill_id in self._write_handlers:
            raise AgentGatewayError(f"Skill name conflicts with a builtin tool: {skill_id}", status_code=409)
        if any(skill.get("name") == skill_id for skill in skills):
            raise AgentGatewayError(f"User skill already exists: {skill_id}", status_code=409)

    def _impl_decorate_skill_validation(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        next_skill = dict(skill)
        validation = self._validate_skill(next_skill, config)
        next_skill["validation"] = validation
        next_skill["availabilityReasons"] = ensure_string_list(validation.get("reasons"))
        if validation.get("status") == "error":
            next_skill["available"] = False
        return next_skill

    def _impl_validate_skill(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        status = "ok"
        reasons: list[str] = []
        if skill.get("loadError"):
            return {"status": "error", "reasons": [str(skill.get("loadError"))]}
        if not skill.get("enabled", True):
            status = "warning"
            reasons.append("skill disabled")

        known_tools = set(self._tools) | set(self._write_handlers)
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

    def _impl_load_runtime_skill_support_files(self, skill: dict[str, Any]) -> list[dict[str, str]]:
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
            user_skills_root = self.user_skills_dir.resolve(strict=True)
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

    def _impl_create_user_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._user_skill_lock:
            skills = self._load_user_skills()
            skill = self._normalize_user_skill(payload)
            skill_id = str(skill["name"])
            self._ensure_user_skill_can_use_id(skill_id, skills)
            self._save_user_skill(skill)
            self.append_audit({"event": "user_skill_created", "skill": skill_id})
            return {"ok": True, "skill": skill, **self.build_skill_registry()}

    def _impl_update_user_skill(self, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._user_skill_lock:
            skill_id = normalize_skill_id(skill_id)
            skills = self._load_user_skills()
            for index, existing in enumerate(skills):
                if existing.get("name") == skill_id:
                    next_payload = {**existing, **payload, "name": skill_id}
                    skills[index] = self._normalize_user_skill(next_payload, existing_id=skill_id)
                    self._save_user_skill(skills[index])
                    self.append_audit({"event": "user_skill_updated", "skill": skill_id})
                    return {"ok": True, "skill": skills[index], **self.build_skill_registry()}
            raise AgentGatewayError(f"User skill was not found: {skill_id}", status_code=404)

    def _impl_delete_user_skill(self, skill_id: str) -> dict[str, Any]:
        with self._user_skill_lock:
            skill_id = normalize_skill_id(skill_id)
            skills = self._load_user_skills()
            kept = [skill for skill in skills if skill.get("name") != skill_id]
            if len(kept) == len(skills):
                raise AgentGatewayError(f"User skill was not found: {skill_id}", status_code=404)
            skill_dir = self.user_skills_dir / skill_id
            if _path_is_link_like(skill_dir):
                raise AgentGatewayError(f"Refusing to delete a linked user skill directory: {skill_id}", status_code=400)
            remove_tree(skill_dir)
            self.append_audit({"event": "user_skill_deleted", "skill": skill_id})
            return {"ok": True, "deleted": skill_id, **self.build_skill_registry()}
