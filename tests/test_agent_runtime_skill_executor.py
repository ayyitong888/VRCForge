from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import dashboard_server

from agent_gateway import AgentGateway
from agent_runtime_skill_executor import AgentRuntimeSkillExecutor, AgentRuntimeSkillExecutorPorts


@dataclass
class FakeTool:
    name: str
    description: str = "Tool description"
    category: str = "read/debug"
    write: bool = False
    advanced: bool = False
    requires_user_activation: bool = False
    handler: Callable[[dict[str, Any]], Any] = lambda params: params


def make_executor(
    *,
    tools: dict[str, FakeTool] | None = None,
    snapshot: Any = None,
    visible: bool = True,
    model_invocable: bool = False,
) -> tuple[AgentRuntimeSkillExecutor, list[Any]]:
    events: list[Any] = []
    tools = tools or {}

    @contextmanager
    def package_lock():
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    def prepare(name: str, _config: Any, audit_context: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
        events.append(("prepare", name, audit_context({"name": name})))
        return snapshot

    executor = AgentRuntimeSkillExecutor(
        AgentRuntimeSkillExecutorPorts(
            ensure_config=lambda: SimpleNamespace(mode="test"),
            tool_for_name=tools.get,
            package_write_lock=package_lock(),
            prepare_runtime_skill=prepare,
            package_audit_context=lambda skill: {"packageId": skill.get("name")},
            computer_use_model_invocable=lambda _config: model_invocable,
            tool_visible=lambda _tool, _config: visible,
            tool_params_audit=lambda name, params: {"tool": name, "keys": sorted(params)},
            read_user_constraints=lambda: "constraints",
            inject_user_constraints=lambda params, _tool, constraints: {**params, "constraint": constraints},
            append_audit=lambda event: events.append(("audit", event)),
            redact=lambda value: {"redacted": value} if not isinstance(value, dict) else value,
            summarize_params=lambda params: {"keys": sorted(params)},
            ensure_string_list=lambda value: value if isinstance(value, list) else ([] if value is None else [str(value)]),
            build_runtime_skill_payload=lambda skill, params, support_files: {
                "skill": skill.get("name"),
                "params": params,
                "supportFiles": support_files,
            },
            invoke_tool=lambda tool, params, _agent, _owner: tool.handler(params),
            blocked_skills=frozenset({"blocked-tool"}),
            direct_categories=frozenset({"read/debug", "plan/preview"}),
            direct_write_tools=frozenset(),
        )
    )
    return executor, events


def test_gateway_and_subagents_share_one_least_authority_runtime_skill_owner(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    assert gateway.runtime_skills is gateway.runtime_skills
    assert not hasattr(gateway, "execute_runtime_skill")
    assert not hasattr(gateway, "_execute_runtime_skill")
    assert not hasattr(gateway, "_execute_skill_package")
    assert not hasattr(gateway, "_execute_skill_entrypoint")
    handler = dashboard_server.SUB_AGENT_COLLABORATION._registry.handlers["project_index_review"]
    assert dashboard_server.AGENT_GATEWAY.runtime_skills in {
        cell.cell_contents for cell in (handler.__closure__ or ())
    }
    assert {field.name for field in fields(AgentRuntimeSkillExecutorPorts)} == {
        "ensure_config",
        "tool_for_name",
        "package_write_lock",
        "prepare_runtime_skill",
        "package_audit_context",
        "computer_use_model_invocable",
        "tool_visible",
        "tool_params_audit",
        "read_user_constraints",
        "inject_user_constraints",
        "append_audit",
        "redact",
        "summarize_params",
        "ensure_string_list",
        "build_runtime_skill_payload",
        "invoke_tool",
        "blocked_skills",
        "direct_categories",
        "direct_write_tools",
    }


def test_direct_tool_allowlist_injection_audit_and_failure_shapes() -> None:
    calls: list[dict[str, Any]] = []
    tool = FakeTool(name="read-tool", handler=lambda params: calls.append(params) or "secret-result")
    executor, events = make_executor(tools={tool.name: tool})

    executed = executor.execute("read-tool", {"value": 1}, "agent")
    assert executed == {
        "ok": True,
        "status": "executed",
        "tool": "read-tool",
        "category": "read/debug",
        "write": False,
        "advanced": False,
        "summary": "Tool description",
        "paramsSummary": {"tool": "read-tool", "keys": ["value"]},
        "result": {"redacted": "secret-result"},
    }
    assert calls == [{"value": 1, "constraint": "constraints"}]
    assert events[-1][0] == "audit"
    assert events[-1][1]["status"] == "ok"

    failing = FakeTool(name="failing", handler=lambda _params: (_ for _ in ()).throw(RuntimeError("boom")))
    failed_executor, failed_events = make_executor(tools={failing.name: failing})
    failed = failed_executor.execute("failing", {}, "agent")
    assert failed["ok"] is False
    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert failed_events[-1][1]["status"] == "error"


def test_blocked_invisible_and_user_activated_tools_keep_existing_policy() -> None:
    blocked = FakeTool(name="blocked-tool")
    write = FakeTool(name="write-tool", category="advanced", write=True)
    activated = FakeTool(
        name="activated-tool",
        category="advanced",
        write=True,
        requires_user_activation=True,
        handler=lambda _params: {"ok": True},
    )

    executor, _events = make_executor(tools={tool.name: tool for tool in (blocked, write, activated)})
    assert executor.execute("blocked-tool", {}, "agent")["status"] == "blocked"
    assert executor.execute("write-tool", {}, "agent")["status"] == "blocked"
    assert executor.execute("activated-tool", {}, "agent")["status"] == "blocked"

    activated_executor, _events = make_executor(
        tools={activated.name: activated},
        model_invocable=True,
    )
    assert activated_executor.execute("activated-tool", {}, "agent")["status"] == "executed"

    invisible_executor, _events = make_executor(
        tools={"read": FakeTool(name="read")},
        visible=False,
    )
    invisible = invisible_executor.execute("read", {}, "agent")
    assert invisible["status"] == "blocked"
    assert invisible["error"] == "This skill is unavailable in the current permission mode."


def test_projected_skill_load_and_entrypoint_share_fixed_tool_policy() -> None:
    entrypoint_calls: list[dict[str, Any]] = []
    entrypoint = FakeTool(
        name="entrypoint",
        handler=lambda params: entrypoint_calls.append(params) or {"done": True},
    )
    snapshot = SimpleNamespace(
        skill={
            "name": "package-skill",
            "title": "Package Skill",
            "category": "read/debug",
            "entrypointTool": "entrypoint",
            "allowedTools": ["entrypoint"],
        },
        package_audit_context={"packageId": "package.id"},
        validation={"reasons": []},
        loaded=True,
        support_files=[{"path": "support.md", "content": "support"}],
    )
    executor, events = make_executor(tools={entrypoint.name: entrypoint}, snapshot=snapshot)

    result = executor.execute("package-skill", {"value": 1, "rawArguments": "drop"}, "agent")

    assert events[:3] == [
        "lock-enter",
        ("prepare", "package-skill", {"packageId": "package-skill"}),
        "lock-exit",
    ]
    assert result["ok"] is True
    assert result["status"] == "executed"
    assert result["entrypointTool"] == "entrypoint"
    assert result["entrypoint"]["result"] == {"done": True}
    assert entrypoint_calls == [{"value": 1, "constraint": "constraints"}]
    assert events[-1][1]["event"] == "runtime_skill_package_loaded"
