from __future__ import annotations

from typing import Any

from agent_gateway import (
    AgentGatewayError,
    SKILL_ID_RE,
    ensure_dict,
    ensure_list,
    ensure_string_list,
)
from agent_skill_registry import AgentSkillRegistryService
from skill_packages import SkillPackageError, SkillPackageService


class ExternalInstalledSkillRegistryService:
    """Expose the one existing Skill registry through narrow read-only views."""

    __slots__ = ("_registry",)

    def __init__(self, registry: AgentSkillRegistryService) -> None:
        self._registry = registry

    @staticmethod
    def _visible(skill: object, *, include_disabled: bool = False) -> bool:
        return bool(
            isinstance(skill, dict)
            and skill.get("source") == "user"
            and skill.get("skillType") == "package"
            and (
                (skill.get("enabled") and skill.get("available"))
                or (
                    include_disabled
                    and not skill.get("enabled")
                    and ensure_dict(skill.get("validation")).get("status") != "error"
                )
            )
        )

    def list_installed_skills(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        include_disabled = ensure_dict(params).get("includeDisabled") is True
        registry = self._registry.build_skill_registry()
        package_metadata = self._installed_package_metadata()
        skills = [
            {
                "name": str(skill.get("name") or ""),
                "title": str(skill.get("title") or ""),
                "description": str(skill.get("description") or ""),
                "enabled": bool(skill.get("enabled")),
                **self._execution_metadata(skill, package_metadata, include_sequence=False),
                **(
                    {"packageId": str(skill["packageId"])}
                    if str(skill.get("packageId") or "").strip()
                    else {}
                ),
            }
            for skill in ensure_list(registry.get("skills"))
            if self._visible(skill, include_disabled=include_disabled)
        ]
        return {
            "ok": True,
            "schema": "vrcforge.installed_skills.v1",
            "count": len(skills),
            "skills": skills,
        }

    def _installed_package_metadata(self) -> dict[str, dict[str, Any]]:
        package_store = self._registry.user_skills_dir.parent / "skill-packages"
        if not (package_store / "registry.json").is_file():
            return {}
        try:
            registry = SkillPackageService(
                package_store, vrcforge_version="0.0.0"
            ).load_registry()
        except (OSError, ValueError, SkillPackageError) as exc:
            raise AgentGatewayError(
                "Installed Skill package execution metadata is unavailable.",
                status_code=409,
            ) from exc
        return {
            str(package_id): dict(entry)
            for package_id, entry in ensure_dict(registry.get("skills")).items()
            if isinstance(entry, dict)
        }

    @staticmethod
    def _execution_metadata(
        skill: dict[str, Any],
        packages: dict[str, dict[str, Any]],
        *,
        include_sequence: bool,
    ) -> dict[str, Any]:
        package = packages.get(str(skill.get("packageId") or ""), {})
        execution = str(package.get("execution") or package.get("executionMode") or "agentic")
        result: dict[str, Any] = {"execution": execution, "executionMode": execution}
        if execution == "deterministic":
            result["workflowDigest"] = str(package.get("workflowDigest") or "")
            result["runtimeEnforced"] = True
            if include_sequence:
                result["workflowSteps"] = ensure_list(package.get("workflowSteps"))
        return result

    def read_installed_skill(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        arguments = ensure_dict(params)
        name = str(arguments.get("name") or "").strip()
        if not SKILL_ID_RE.fullmatch(name):
            raise AgentGatewayError(
                "An exact valid installed Skill name is required.", status_code=400
            )

        with self._registry.write_lock:
            registry = self._registry.build_skill_registry()
            skill = next(
                (
                    item
                    for item in ensure_list(registry.get("skills"))
                    if self._visible(item) and item.get("name") == name
                ),
                None,
            )
            if skill is None:
                raise AgentGatewayError(
                    f"Installed Skill is unavailable or disabled: {name}",
                    status_code=404,
                )

            support_files = ensure_string_list(skill.get("supportFiles"))
            requested_file = str(arguments.get("file") or "").strip()
            if requested_file:
                if requested_file not in support_files:
                    raise AgentGatewayError(
                        "Only an exact support file declared by this Skill can be read.",
                        status_code=400,
                    )
                loaded = self._registry.load_runtime_skill_support_files(
                    {**skill, "supportFiles": [requested_file]}
                )
                if len(loaded) != 1 or loaded[0].get("path") != requested_file:
                    raise AgentGatewayError(
                        "The requested Skill support file is unavailable.",
                        status_code=404,
                    )
                return {
                    "ok": True,
                    "schema": "vrcforge.installed_skill_file.v1",
                    "name": name,
                    "file": requested_file,
                    "content": loaded[0]["content"],
                }

            return {
                "ok": True,
                "schema": "vrcforge.installed_skill.v1",
                "name": name,
                "title": str(skill.get("title") or ""),
                "description": str(skill.get("description") or ""),
                "instructions": str(skill.get("instructions") or ""),
                "allowedTools": ensure_string_list(skill.get("allowedTools")),
                "supportFiles": support_files,
                **self._execution_metadata(
                    skill, self._installed_package_metadata(), include_sequence=True
                ),
            }

    def create_installed_skill(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Install one new user-authored Skill through the existing atomic registry."""

        arguments = ensure_dict(params)
        name = str(arguments.get("name") or "").strip()
        if not SKILL_ID_RE.fullmatch(name):
            raise AgentGatewayError(
                "An exact valid installed Skill name is required.", status_code=400
            )
        description = str(arguments.get("description") or "").strip()
        instructions = str(arguments.get("instructions") or "").strip()
        if not description or not instructions:
            raise AgentGatewayError(
                "Both description and instructions are required to create a Skill.",
                status_code=400,
            )

        permission = str(arguments.get("permissionMode") or "instruction_only").strip()
        if permission not in {
            "instruction_only", "read_only", "preview", "approval_required"
        }:
            raise AgentGatewayError("Unsupported Skill permission mode.", status_code=400)

        payload = {
            "name": name,
            "title": str(arguments.get("title") or name).strip(),
            "description": description,
            "instructions": instructions,
            "permissionMode": permission,
            "riskLevel": str(arguments.get("riskLevel") or "low").strip(),
            "allowedTools": ensure_string_list(arguments.get("allowedTools")),
            "entrypointTool": str(arguments.get("entrypointTool") or "").strip(),
            "whenToUse": str(arguments.get("whenToUse") or "").strip(),
            "tags": ensure_string_list(arguments.get("tags")),
            "enabled": True,
        }
        result = self._registry.create_user_skill(payload)
        skill = ensure_dict(result.get("skill"))
        return {
            "ok": True,
            "schema": "vrcforge.installed_skill_created.v1",
            "installed": True,
            "installedSkill": {
                "name": str(skill.get("name") or ""),
                "title": str(skill.get("title") or ""),
                "description": str(skill.get("description") or ""),
                "allowedTools": ensure_string_list(skill.get("allowedTools")),
                "enabled": bool(skill.get("enabled")),
            },
        }
