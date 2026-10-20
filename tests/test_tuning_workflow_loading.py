from __future__ import annotations

from dataclasses import replace

import pytest

import dashboard_server


@pytest.mark.parametrize(
    ("skill_name", "planner_tool"),
    [
        ("face-tuning-workflow", "vrcforge_plan_face_tuning"),
        ("shader-material-workflow", "vrcforge_plan_shader_tuning"),
    ],
)
def test_tuning_workflow_load_does_not_auto_invoke_planner(
    monkeypatch: pytest.MonkeyPatch, skill_name: str, planner_tool: str
) -> None:
    """Loading a workflow exposes instructions; planning is an explicit model action."""
    executor = dashboard_server.AGENT_GATEWAY.runtime_skills
    original_ports = executor._ports
    invoked: list[str] = []

    def invoke(tool, _params, _agent, _owner):
        invoked.append(tool.name)
        return {"ok": True, "status": "executed", "summary": "fixture"}

    monkeypatch.setattr(executor, "_ports", replace(original_ports, invoke_tool=invoke))
    try:
        loaded = executor.execute(skill_name, {}, "tuning-workflow-test")
    finally:
        monkeypatch.setattr(executor, "_ports", original_ports)

    assert loaded["ok"] is True
    assert loaded["status"] == "loaded"
    assert loaded.get("entrypointTool", "") == ""
    assert loaded.get("entrypoint") is None
    assert invoked == [], f"loading {skill_name} unexpectedly invoked {planner_tool}"
