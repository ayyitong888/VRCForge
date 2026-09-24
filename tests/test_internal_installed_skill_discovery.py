from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_gateway import AgentGatewayConfig, summarize_skill_registry
from agent_task_loop import AgentTaskLoop
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


def test_loaded_real_guide_explicit_exit_restores_task_scope_not_user_permissions(installed_guide):
    app, gateway = installed_guide
    loaded = gateway.runtime_skills.execute(SKILL_NAME, {}, "test")
    assert loaded["status"] == "loaded"
    audit_before = gateway.audit_log_path.read_text(encoding="utf-8")
    assert "runtime_skill_package_loaded" in audit_before and SKILL_NAME in audit_before
    loop = AgentTaskLoop("Repair Core then inspect wardrobe")
    loop.activate_skill_policy(name=SKILL_NAME, instructions=loaded["result"]["instructions"],
                               allowed_tools=loaded["result"]["allowedTools"], disallowed_tools=[])
    assert loop.skill_policy_block_reason("vrcforge_install_unity_core") == ""
    assert loop.skill_policy_block_reason("vrcforge_scan_wardrobe") == "skill_tool_not_allowed"
    assert loop.skill_policy_block_reason("vrcforge_exit_skill") == ""
    before = gateway.ensure_config()
    with gateway._bind_runtime_skill_scope(loop):
        result = gateway.runtime_skills.execute("vrcforge_exit_skill", {"name": SKILL_NAME, "reason": "Return to the original wardrobe task."}, "test")
    assert result["ok"], result
    assert result["result"]["completionVerified"] is False
    assert loop.skill_policy_block_reason("vrcforge_scan_wardrobe") == ""
    assert not loop.approval_seed().get("skillPolicy")
    assert gateway.ensure_config() == before
    audit_after = gateway.audit_log_path.read_text(encoding="utf-8")
    assert audit_after.startswith(audit_before)
    assert "vrcforge_exit_skill" in audit_after
    assert gateway.ensure_config().allow_write_requests is False
    with pytest.raises(Exception, match="disabled|not allowed|write requests"):
        gateway.approval_transactions.create_apply_request({"target_tool": "vrcforge_install_unity_core", "arguments": {"projectPath": "denied"}})
    # Caller-supplied names/session IDs cannot acquire another turn's loop.
    outside = gateway.runtime_skills.execute("vrcforge_exit_skill", {"name": SKILL_NAME, "reason": "outside"}, "test")
    assert outside["ok"] is False


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


@pytest.mark.parametrize("name,arguments", [
    ("vrcforge_list_internal_tool_blocks", {}),
    ("vrcforge_load_internal_tool_block", {"block": "project_environment/files"}),
])
def test_shared_skill_runtime_names_route_through_registered_internal_alias(installed_guide, name, arguments):
    app, gateway = installed_guide
    read = gateway.runtime_skills.execute("vrcforge_read_installed_skill", {"name": SKILL_NAME}, "test")
    assert read["ok"]
    prompts = []
    planner = RuntimePlannerService(
        catalog=app._RuntimePlannerCatalog(), desktop=app._RuntimePlannerDesktopObservation(),
        model=SimpleNamespace(plan=lambda prompt: (prompts.append(prompt) or PlannerModelResult(json.dumps({
            "action": "skill", "skill_tool": name, "skill_params": arguments,
        })))),
    )
    plan = planner.plan_agent_turn(
        "Continue the read-only guide", {"_projectContextActive": False, "_internalToolBlocks": ["core"]}, {},
        loop_state=[{"tool": "vrcforge_read_installed_skill", "status": "executed", "result": read["result"]}],
    )
    assert plan.get("skillNeeded"), plan
    assert plan["skillTool"] == name
    assert not plan.get("writeNeeded")
    assert f"runtimeAlias={name}" in prompts[0]
    result = gateway.runtime_skills.execute(plan["skillTool"], {**plan["skillParams"], "sessionId": "skill-alias-test"}, "test")
    assert result["ok"], result


@pytest.mark.parametrize("requested,visible", [
    ("vrcforge_hidden", ()),
    ("vrcforge_unknown", (PlannerTool(name="unknown", description="read", category="read/debug"),)),
    ("vrcforge_shared", (
        PlannerTool(name="one", runtime_name="vrcforge_shared", description="read", category="read/debug"),
        PlannerTool(name="two", runtime_name="vrcforge_shared", description="read", category="read/debug"),
    )),
    ("vrcforge_write", (PlannerTool(name="write_alias", runtime_name="vrcforge_write", description="write", category="write", write=True),)),
])
def test_runtime_alias_never_exposes_hidden_inferred_ambiguous_or_write_tools(requested, visible):
    catalog = PlannerCatalogSnapshot(visible_tools=visible, routable_tools=(
        PlannerTool(name="hidden", runtime_name="vrcforge_hidden", description="hidden", category="read/debug"),
    ))
    planner = RuntimePlannerService(
        catalog=SimpleNamespace(read=lambda *_args, **_kwargs: catalog), desktop=SimpleNamespace(),
        model=SimpleNamespace(plan=lambda _prompt: PlannerModelResult(json.dumps({
            "action": "skill", "skill_tool": requested, "skill_params": {},
        }))),
    )
    plan = planner.plan_agent_turn("Read only", {"_projectContextActive": False}, {})
    assert not plan.get("skillNeeded"), plan
    assert not plan.get("writeNeeded"), plan


def test_activated_bundled_guide_allows_its_readonly_discovery_and_support_reads(installed_guide):
    app, gateway = installed_guide
    loaded = gateway.runtime_skills.execute(SKILL_NAME, {}, "test")
    assert loaded["ok"] and loaded["status"] == "loaded"
    loop = AgentTaskLoop("Follow the connection guide")
    loop.activate_skill_policy(name=SKILL_NAME, allowed_tools=loaded["result"]["allowedTools"], disallowed_tools=[])
    for name, arguments in (
        ("vrcforge_list_internal_tool_blocks", {}),
        ("vrcforge_load_internal_tool_block", {"block": "project_environment/files"}),
        ("vrcforge_read_installed_skill", {"name": SKILL_NAME, "file": "references/repair-guide.md"}),
    ):
        planner = RuntimePlannerService(
            catalog=app._RuntimePlannerCatalog(), desktop=app._RuntimePlannerDesktopObservation(),
            model=SimpleNamespace(plan=lambda _prompt: PlannerModelResult(json.dumps({
                "action": "skill", "skill_tool": name, "skill_params": arguments,
            }))),
        )
        plan = planner.plan_agent_turn("Follow the guide", {"_projectContextActive": True}, {}, exposure_layer="execution")
        assert plan.get("skillNeeded"), plan
        assert loop.skill_policy_block_reason(plan["skillTool"]) == ""
        result = gateway.runtime_skills.execute(plan["skillTool"], {**plan["skillParams"], "sessionId": "guide-policy-test"}, "test")
        assert result["ok"], result
    assert loop.skill_policy_block_reason("vrcforge_execute_shell") == "skill_tool_not_allowed"
    assert loop.skill_policy_block_reason("vrcforge_delete_path") == "skill_tool_not_allowed"


def test_observe_summary_keeps_installed_guides_after_large_builtin_catalogue():
    registry = {"skills": [
        *[{"name": f"builtin-{index}", "source": "builtin", "skillType": "tool"} for index in range(325)],
        {"name": SKILL_NAME, "source": "user", "skillType": "package", "description": "Connection repair"},
    ]}
    summary = summarize_skill_registry(registry)
    assert summary["skills"][0]["name"] == SKILL_NAME
    assert summary["skills"][0]["description"] == "Connection repair"
    assert summary["truncated"] and summary["shownCount"] == 80
