from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class RuntimeSkillTool(Protocol):
    name: str
    description: str
    category: str
    write: bool
    advanced: bool
    requires_user_activation: bool
    handler: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class AgentRuntimeSkillExecutorPorts:
    """Least-authority ports for runtime-direct Skill execution.

    The owner may resolve read/direct tools, load verified projected Skills,
    invoke only the already-filtered tool handler, and append audit events. It
    has no write-handler registry, approval, checkpoint, Provider, session,
    filesystem-write, process, or network capability of its own.
    """

    ensure_config: Callable[[], Any]
    tool_for_name: Callable[[str], RuntimeSkillTool | None]
    package_write_lock: AbstractContextManager[Any]
    prepare_runtime_skill: Callable[[str, Any, Callable[[dict[str, Any]], dict[str, Any]]], Any | None]
    package_audit_context: Callable[[dict[str, Any]], dict[str, Any]]
    computer_use_model_invocable: Callable[[Any], bool]
    tool_visible: Callable[[RuntimeSkillTool, Any], bool]
    tool_params_audit: Callable[[str, dict[str, Any]], dict[str, Any]]
    read_user_constraints: Callable[[], Any]
    inject_user_constraints: Callable[[dict[str, Any], RuntimeSkillTool, Any], dict[str, Any]]
    append_audit: Callable[[dict[str, Any]], None]
    redact: Callable[[Any], Any]
    summarize_params: Callable[[Any], dict[str, Any]]
    ensure_string_list: Callable[[Any], list[str]]
    build_runtime_skill_payload: Callable[..., dict[str, Any]]
    invoke_tool: Callable[[RuntimeSkillTool, dict[str, Any], str, str], Any]
    blocked_skills: frozenset[str]
    direct_categories: frozenset[str]
    direct_write_tools: frozenset[str]


class AgentRuntimeSkillExecutor:
    """Execute runtime-direct tools and verified projected Skills."""

    __slots__ = ("_ports",)

    def __init__(self, ports: AgentRuntimeSkillExecutorPorts) -> None:
        self._ports = ports

    def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
        owner_id: str = "",
    ) -> dict[str, Any]:
        config = self._ports.ensure_config()
        tool = self._ports.tool_for_name(tool_name)
        if not tool:
            with self._ports.package_write_lock:
                snapshot = self._ports.prepare_runtime_skill(
                    tool_name,
                    config,
                    self._ports.package_audit_context,
                )
            if snapshot:
                return self._execute_skill_package(snapshot, params, agent_name, config, owner_id)
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool_name,
                "error": f"Unknown skill: {tool_name}",
            }
        user_activated_tool = bool(
            tool.requires_user_activation and self._ports.computer_use_model_invocable(config)
        )
        direct_write_tool = tool.name in self._ports.direct_write_tools
        if (
            tool.name in self._ports.blocked_skills
            or (tool.write and not user_activated_tool and not direct_write_tool)
            or (
                tool.category not in self._ports.direct_categories
                and not user_activated_tool
                and not direct_write_tool
            )
        ):
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "error": "This skill cannot run directly from the runtime loop.",
            }
        if not self._ports.tool_visible(tool, config):
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "error": "This skill is unavailable in the current permission mode.",
            }

        params_summary = self._ports.tool_params_audit(tool.name, params)
        user_constraints = self._ports.read_user_constraints()
        tool_params = self._ports.inject_user_constraints(params, tool, user_constraints)
        try:
            result = self._ports.invoke_tool(tool, tool_params, agent_name, owner_id)
            payload = {
                "ok": True,
                "status": "executed",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "summary": tool.description,
                "paramsSummary": params_summary,
                "result": self._ports.redact(result),
            }
            self._ports.append_audit(
                {
                    "event": "runtime_skill_executed",
                    "tool": tool.name,
                    "agent": agent_name,
                    "paramsSummary": params_summary,
                    "status": "ok",
                }
            )
            return payload
        except Exception as exc:  # noqa: BLE001 - runtime keeps the agent loop alive.
            self._ports.append_audit(
                {
                    "event": "runtime_skill_executed",
                    "tool": tool.name,
                    "agent": agent_name,
                    "paramsSummary": params_summary,
                    "status": "error",
                    "error": str(exc),
                }
            )
            return {
                "ok": False,
                "status": "failed",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "summary": tool.description,
                "paramsSummary": params_summary,
                "error": str(exc),
            }

    def _execute_skill_package(
        self,
        snapshot: Any,
        params: dict[str, Any],
        agent_name: str,
        config: Any,
        owner_id: str,
    ) -> dict[str, Any]:
        skill = snapshot.skill
        package_audit_context = snapshot.package_audit_context
        validation = snapshot.validation
        status = "loaded" if snapshot.loaded else "blocked"
        support_files = list(snapshot.support_files)
        result = self._ports.redact(
            self._ports.build_runtime_skill_payload(skill, params, support_files=support_files)
        )
        payload = {
            "ok": status == "loaded",
            "status": status,
            "tool": str(skill.get("name") or ""),
            "category": str(skill.get("category") or ""),
            "write": bool(skill.get("write")),
            "advanced": bool(skill.get("advanced")),
            "summary": str(skill.get("description") or skill.get("title") or ""),
            "paramsSummary": self._ports.summarize_params(params),
            "result": result,
        }
        if status != "loaded":
            payload["error"] = (
                "; ".join(self._ports.ensure_string_list(validation.get("reasons")))
                or "Skill is unavailable."
            )
            self._ports.append_audit(
                {
                    "event": "runtime_skill_package_loaded",
                    "skill": skill.get("name"),
                    "agent": agent_name,
                    "status": payload["status"],
                    "error": payload.get("error"),
                    **package_audit_context,
                }
            )
            return payload

        entrypoint = str(skill.get("entrypointTool") or "").strip()
        if entrypoint:
            entrypoint_result = self._execute_skill_entrypoint(
                skill,
                entrypoint,
                params,
                agent_name,
                config,
                owner_id,
                package_audit_context=package_audit_context,
            )
            payload["entrypointTool"] = entrypoint
            payload["entrypoint"] = entrypoint_result
            if entrypoint_result.get("status") == "executed":
                payload["status"] = "executed"
                payload["ok"] = True
            elif entrypoint_result.get("status") in {"blocked", "failed"}:
                payload["status"] = entrypoint_result.get("status")
                payload["ok"] = False
                payload["error"] = entrypoint_result.get("error")

        self._ports.append_audit(
            {
                "event": "runtime_skill_package_loaded",
                "skill": skill.get("name"),
                "agent": agent_name,
                "status": payload["status"],
                "entrypointTool": entrypoint,
                **package_audit_context,
            }
        )
        return payload

    def _execute_skill_entrypoint(
        self,
        skill: dict[str, Any],
        entrypoint: str,
        params: dict[str, Any],
        agent_name: str,
        config: Any,
        owner_id: str,
        *,
        package_audit_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed_tools = self._ports.ensure_string_list(skill.get("allowedTools") or skill.get("tools"))
        disallowed_tools = self._ports.ensure_string_list(skill.get("disallowedTools"))
        if entrypoint in disallowed_tools:
            return {
                "ok": False,
                "status": "blocked",
                "tool": entrypoint,
                "error": "Entrypoint tool is disallowed.",
            }
        if allowed_tools and entrypoint not in allowed_tools:
            return {
                "ok": False,
                "status": "blocked",
                "tool": entrypoint,
                "error": "Entrypoint tool is not allowed.",
            }
        tool = self._ports.tool_for_name(entrypoint)
        if not tool:
            return {
                "ok": False,
                "status": "blocked",
                "tool": entrypoint,
                "error": "Entrypoint requires approval or is not callable directly.",
            }
        if (
            tool.name in self._ports.blocked_skills
            or tool.write
            or tool.category not in self._ports.direct_categories
        ):
            return {
                "ok": False,
                "status": "blocked",
                "tool": entrypoint,
                "error": "Entrypoint cannot run directly from the runtime loop.",
            }
        if not self._ports.tool_visible(tool, config):
            return {
                "ok": False,
                "status": "blocked",
                "tool": entrypoint,
                "error": "Entrypoint is unavailable in the current permission mode.",
            }
        tool_params = {
            key: value
            for key, value in params.items()
            if key not in {"arguments", "rawArguments", "skillArguments"}
        }
        user_constraints = self._ports.read_user_constraints()
        tool_params = self._ports.inject_user_constraints(tool_params, tool, user_constraints)
        try:
            result = self._ports.invoke_tool(tool, tool_params, agent_name, owner_id)
            self._ports.append_audit(
                {
                    "event": "runtime_skill_entrypoint_executed",
                    "skill": skill.get("name"),
                    "tool": entrypoint,
                    "agent": agent_name,
                    "status": "ok",
                    **(package_audit_context or {}),
                }
            )
            return {
                "ok": True,
                "status": "executed",
                "tool": entrypoint,
                "category": tool.category,
                "result": self._ports.redact(result),
            }
        except Exception as exc:  # noqa: BLE001 - keep the agent loop alive.
            return {
                "ok": False,
                "status": "failed",
                "tool": entrypoint,
                "category": tool.category,
                "error": str(exc),
            }
