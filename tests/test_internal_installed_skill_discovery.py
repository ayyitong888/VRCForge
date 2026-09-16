from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_gateway import AgentGatewayConfig, summarize_skill_registry
from bundled_skill_delivery import SKILL_NAME, deliver_bundled_guide
from runtime_planner_service import (
    PlannerCatalogSnapshot, PlannerModelResult, PlannerSkill, PlannerTool, RuntimePlannerService,
)


@pytest.fixture
def installed_guide(tmp_path):
    import dashboard_server as app
    gateway = app.AGENT_GATEWAY
    previous = gateway.config_path, gateway.audit_dir
    gateway.configure_paths(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, allow_write_requests=False))
    with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
        deliver_bundled_guide(app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version="1.8.1")
    try:
        yield app, gateway
    finally:
        gateway.configure_paths(*previous)


def test_general_planning_provider_receives_compact_guide_metadata(installed_guide):
    app, gateway = installed_guide
    prompts = []
    planner = RuntimePlannerService(
        catalog=app._RuntimePlannerCatalog(), desktop=app._RuntimePlannerDesktopObservation(),
        model=SimpleNamespace(plan=lambda prompt: (prompts.append(prompt) or PlannerModelResult('{"action":"reply","reply":"fixture"}'))),
    )
    planner.plan_agent_turn(
        "以前能用，现在不知道哪里出了问题。请使用连接排障引导检查当前状态。只读，不修改工程。",
        {"_projectContextActive": False, "_internalToolBlocks": ["core"]}, {},
    )
    assert SKILL_NAME in prompts[0]
    assert "连接引导与日常排障" in prompts[0]
    assert "read_installed_skill" in prompts[0]
    assert "project_environment/files" in prompts[0]
    assert "# Purpose and triggering" not in prompts[0]  # Full instructions stay lazy.
    snapshot = app._RuntimePlannerCatalog().read("planning", project_context_active=False)
    names = {tool.runtime_name for tool in snapshot.visible_tools}
    assert {"vrcforge_list_installed_skills", "vrcforge_read_installed_skill"} <= names
    assert "vrcforge_register_project" not in names
    assert all(not tool.write for tool in snapshot.visible_tools)


@pytest.mark.parametrize("file", [None, "references/repair-guide.md"])
def test_actual_skill_reader_content_reaches_next_provider_turn(installed_guide, file):
    app, gateway = installed_guide
    params = {"name": SKILL_NAME, **({"file": file} if file else {})}
    read = gateway.runtime_skills.execute("vrcforge_read_installed_skill", params, "test")
    assert read["ok"] and read["status"] == "executed", read
    content = read["result"]["content" if file else "instructions"]
    assert len(content) > 600
    prompts = []
    planner = RuntimePlannerService(
        catalog=app._RuntimePlannerCatalog(), desktop=app._RuntimePlannerDesktopObservation(),
        model=SimpleNamespace(plan=lambda prompt: (prompts.append(prompt) or PlannerModelResult('{"action":"reply","reply":"fixture"}'))),
    )
    planner.plan_agent_turn(
        "继续按刚读的引导诊断，不修改。",
        {"_projectContextActive": False, "_internalToolBlocks": ["project_environment/files"]}, {},
        loop_state=[{"tool": "vrcforge_read_installed_skill", "status": "executed", "result": read["result"]}],
    )
    # Both beginning and final acceptance guidance survive the model boundary.
    assert content.splitlines()[0] in prompts[0]
    assert content.splitlines()[-1] in prompts[0]
    assert "SkillReadPolicy=" in prompts[0]


def test_disabled_unavailable_and_model_hidden_skills_are_not_advertised():
    catalog = PlannerCatalogSnapshot(
        visible_tools=(PlannerTool(name="read_installed_skill", runtime_name="vrcforge_read_installed_skill", description="Read", category="read/debug"),),
        skills=tuple(
            PlannerSkill(name=name, source="user", skill_type="package", **flags)
            for name, flags in (
                ("visible-guide", {}), ("disabled-guide", {"enabled": False}),
                ("invalid-guide", {"available": False}), ("manual-only-guide", {"disable_model_invocation": True}),
            )
        ),
    )
    planner = RuntimePlannerService(catalog=SimpleNamespace(read=lambda *_args, **_kwargs: catalog), desktop=SimpleNamespace())
    prompt = planner._build_llm_plan_prompt("Help", [], internal_tool_blocks=["core"])
    assert "visible-guide" in prompt
    assert all(name not in prompt for name in ("disabled-guide", "invalid-guide", "manual-only-guide"))


def test_readable_metadata_does_not_enable_general_mode_package_execution(installed_guide):
    app, _gateway = installed_guide
    planner = RuntimePlannerService(
        catalog=app._RuntimePlannerCatalog(), desktop=app._RuntimePlannerDesktopObservation(),
        model=SimpleNamespace(plan=lambda _prompt: PlannerModelResult(
            '{"action":"skill","skill_tool":"vrcforge-first-run-guide","skill_params":{}}'
        )),
    )
    plan = planner.plan_agent_turn(
        "Read the guide", {"_projectContextActive": False}, {}, exposure_layer="execution",
    )
    assert not plan.get("skillNeeded"), plan


def test_observe_summary_keeps_installed_guides_after_large_builtin_catalogue():
    registry = {"skills": [
        *[{"name": f"builtin-{index}", "source": "builtin", "skillType": "tool"} for index in range(325)],
        {"name": SKILL_NAME, "source": "user", "skillType": "package", "description": "Connection repair"},
    ]}
    summary = summarize_skill_registry(registry)
    assert summary["skills"][0]["name"] == SKILL_NAME
    assert summary["skills"][0]["description"] == "Connection repair"
    assert summary["truncated"] and summary["shownCount"] == 80
