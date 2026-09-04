from contextlib import nullcontext
from pathlib import Path
from threading import RLock

import pytest

from agent_gateway import AgentGatewayConfig
from agent_skill_registry import (
    AgentSkillRegistryPorts,
    AgentSkillRegistryService,
    SkillToolDescriptor,
    SkillWriteHandlerDescriptor,
)


def _service(root: Path, count: int):
    tools = [SkillToolDescriptor(f"read-{i}", "Read fixture.", "read/debug", False, False, False) for i in range(count)]
    handlers = [SkillWriteHandlerDescriptor("write-fixture", "Write fixture.", "medium", False)]
    state = {"tool_reads": 0, "handler_reads": 0, "visible": True}

    def list_tools():
        state["tool_reads"] += 1
        return tuple(tools)

    def list_handlers():
        state["handler_reads"] += 1
        return tuple(handlers)

    service = AgentSkillRegistryService(AgentSkillRegistryPorts(
        config_path=lambda: root / "config.json",
        ensure_config=lambda: AgentGatewayConfig(enabled=True),
        list_tools=list_tools,
        list_write_handlers=list_handlers,
        tool_visible=lambda name, _config: state["visible"] and any(tool.name == name for tool in tools),
        write_handler_visible=lambda name, _config: state["visible"] and any(handler.name == name for handler in handlers),
        computer_use_model_invocable=lambda _config: False,
        append_audit=lambda _entry: None,
        user_skill_lock=RLock(),
        local_state_write_guard=nullcontext,
    ))
    return service, tools, handlers, state


@pytest.mark.parametrize("count", [1, 64])
def test_metadata_enumeration_is_bounded_independently_of_skill_count(tmp_path: Path, count: int):
    service, _tools, _handlers, state = _service(tmp_path, count)

    result = service.build_skill_registry()

    assert len([row for row in result["skills"] if row["name"].startswith("read-")]) == count
    assert state["tool_reads"] <= 2, "one Skill build must not reconstruct all Tool descriptors for every dependency"
    assert state["handler_reads"] <= 2, "one Skill build must not reconstruct all write descriptors for every dependency"


def test_next_build_observes_registration_removal_and_permission_changes(tmp_path: Path):
    service, tools, handlers, state = _service(tmp_path, 1)
    first = {row["name"]: row for row in service.build_skill_registry()["skills"]}
    assert first["read-0"]["available"] is True

    tools[:] = [SkillToolDescriptor("replacement-read", "Replacement.", "read/debug", False, False, False)]
    handlers.clear()
    state["visible"] = False
    second = {row["name"]: row for row in service.build_skill_registry()["skills"]}

    assert "read-0" not in second
    assert "write-fixture" not in second
    assert second["replacement-read"]["available"] is False
    assert "entrypoint tool is unavailable: replacement-read" in second["replacement-read"]["validation"]["reasons"]

    state["visible"] = True
    third = {row["name"]: row for row in service.build_skill_registry(exposure_layer="planning")["skills"]}
    assert third["replacement-read"]["available"] is True
    assert not any(row["write"] for row in third.values())
