"""Load packaged and user-authored sub-agent role declarations."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sub_agent_tasks import SubAgentRole


_logger = logging.getLogger(__name__)
_ROLE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_BUILTIN_IDS = {
    "project_index_review",
    "outfit_package_inspection",
    "validation_triage",
    "selected_context_review",
    "package_install_diagnosis",
    "outfit_import_plan_review",
    "skill_delegate",
    "llm_readonly",
}
_BUILTIN_ORDER = (
    "project_index_review",
    "outfit_package_inspection",
    "validation_triage",
    "selected_context_review",
    "package_install_diagnosis",
    "outfit_import_plan_review",
    "skill_delegate",
    "llm_readonly",
)
_BUILTIN_DIR = Path(__file__).resolve().parent / "resources" / "sub-agents"
_USER_ROLE_RELATIVE_DIR = Path("sub-agents") / "roles"
_ROLE_FIELDS = {
    "id", "title", "name", "description", "handler", "toolProfile", "model",
    "systemPrompt", "system_prompt", "maxSteps", "max_steps", "tools",
}


def load_sub_agent_roles(
    *,
    data_dir: str | Path | None = None,
    builtin_dir: str | Path | None = None,
) -> list[SubAgentRole]:
    """Load eight packaged roles plus valid user-defined LLM roles.

    User definitions are isolated by file: malformed, duplicate, or unsupported
    entries are logged and skipped so one bad file cannot prevent startup.
    Custom definitions always use the existing read-only LLM handler.
    """
    from vrcforge_runtime_paths import DATA_DIR

    packaged_dir = Path(builtin_dir) if builtin_dir is not None else _BUILTIN_DIR
    packaged_roles = _load_builtin_roles(packaged_dir)
    user_root = Path(data_dir) if data_dir is not None else Path(DATA_DIR)
    user_dir = user_root / _USER_ROLE_RELATIVE_DIR
    roles = list(packaged_roles)
    known_ids = {role.id for role in roles}
    if not user_dir.is_dir():
        return roles
    for path in sorted(user_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            role = _parse_role(payload, source="user")
            if role.id in known_ids:
                raise ValueError(f"duplicate role id: {role.id}")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            _logger.warning("Skipping invalid sub-agent role file %s: %s", path, exc)
            continue
        roles.append(role)
        known_ids.add(role.id)
    return roles


def _load_builtin_roles(directory: Path) -> list[SubAgentRole]:
    roles: list[SubAgentRole] = []
    seen: set[str] = set()
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        raise RuntimeError(f"Built-in sub-agent roles are unavailable at {directory}: {exc}") from exc
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            role = _parse_role(payload, source="builtin")
            if role.id in seen:
                raise ValueError(f"duplicate built-in role id: {role.id}")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid built-in sub-agent role file {path}: {exc}") from exc
        roles.append(role)
        seen.add(role.id)
    missing = _BUILTIN_IDS - seen
    if missing:
        raise RuntimeError(f"Built-in sub-agent role files are missing {sorted(missing)} in {directory}.")
    by_id = {role.id: role for role in roles}
    return [by_id[role_id] for role_id in _BUILTIN_ORDER]


def _parse_role(payload: Any, *, source: str) -> SubAgentRole:
    if not isinstance(payload, dict):
        raise ValueError("role definition must be a JSON object")
    unknown = set(payload) - _ROLE_FIELDS
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(map(str, unknown)))}")
    role_id = payload.get("id")
    if not isinstance(role_id, str) or not _ROLE_ID.fullmatch(role_id):
        raise ValueError("id must match ^[a-z][a-z0-9_]{1,63}$")
    title = payload.get("title", payload.get("name"))
    description = payload.get("description")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title (or name) must be a nonempty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be a nonempty string")
    handler = payload.get("handler", "llm_readonly" if source == "user" else role_id)
    if source == "user" and handler != "llm_readonly":
        raise ValueError("user roles must use the read-only LLM handler")
    if not isinstance(handler, str) or not handler:
        raise ValueError("handler must be a nonempty string")
    tool_profile = payload.get("toolProfile", "read-only")
    if source == "user" and tool_profile != "read-only":
        raise ValueError("user roles are restricted to the read-only tool profile")
    if not isinstance(tool_profile, str) or not tool_profile:
        raise ValueError("toolProfile must be a nonempty string")
    model = payload.get("model", "")
    system_prompt = payload.get("systemPrompt", payload.get("system_prompt", ""))
    if not isinstance(model, str) or not isinstance(system_prompt, str):
        raise ValueError("model and systemPrompt must be strings")
    max_steps = payload.get("maxSteps", payload.get("max_steps", 4))
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 12:
        raise ValueError("maxSteps must be an integer between 1 and 12")
    tools = payload.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or any(not isinstance(item, str) or not item.strip() for item in tools):
            raise ValueError("tools must be omitted/null or a list of nonempty tool names")
        tools = tuple(dict.fromkeys(item.strip() for item in tools))
    return SubAgentRole(
        id=role_id,
        title=title.strip(),
        description=description.strip(),
        tool_profile=tool_profile,
        read_only=True,
        handler=handler,
        model=model.strip(),
        system_prompt=system_prompt,
        max_steps=max_steps,
        tools=tools,
    )
