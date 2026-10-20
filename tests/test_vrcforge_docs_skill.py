from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bundled_skill_delivery as delivery
from agent_gateway import AgentGatewayConfig
from agent_task_loop import AgentTaskLoop
from runtime_planner_service import PlannerModelResult, RuntimePlannerService
from skill_packages import SkillPackageService


@pytest.fixture
def docs_profile(tmp_path, monkeypatch):
    import dashboard_server as app

    monkeypatch.delenv("VRCFORGE_USER_DATA_DIR", raising=False)
    gateway = app.AGENT_GATEWAY
    previous = gateway.config_path, gateway.audit_dir
    gateway.configure_paths(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, allow_write_requests=True))
    try:
        with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
            delivery.deliver_bundled_guide(app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version="1.9.1")
            result = delivery.deliver_bundled_docs(app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version="1.9.1")
        assert result["status"] == "installed"
        yield app, gateway, tmp_path
    finally:
        gateway.configure_paths(*previous)


def test_internal_load_is_agentic_without_automatic_diagnosis(docs_profile, monkeypatch):
    _app, gateway, _root = docs_profile
    executor = gateway.runtime_skills
    monkeypatch.setattr(executor, "_ports", replace(
        executor._ports, invoke_tool=lambda *_args: pytest.fail("Loading docs must not run a diagnostic or repair"),
    ))
    loaded = executor.execute(delivery.DOCS_SKILL_NAME, {}, "docs-test")
    assert loaded["ok"] and loaded["status"] == "loaded", loaded
    assert loaded["execution"] == "agentic"
    assert "entrypoint" not in loaded
    assert loaded["result"]["entrypointTool"] == ""
    assert len(loaded["result"]["instructions"]) <= 6000  # active Skill context retains the whole route
    assert loaded["result"]["supportFiles"] == []  # no eager reference payload
    loop = AgentTaskLoop("Download the official Unity Hub installer")
    loop.activate_skill_policy(
        name=delivery.DOCS_SKILL_NAME, instructions=loaded["result"]["instructions"],
        allowed_tools=loaded["result"]["allowedTools"], disallowed_tools=[],
    )
    assert loop.skill_policy_block_reason("vrcforge_execute_shell") == ""
    assert loop.skill_policy_block_reason("vrcforge_shell_process") == ""


def test_external_prompt_and_exact_reader_share_native_docs(docs_profile):
    app, gateway, _root = docs_profile
    listing = gateway.list_mcp_prompts()
    names = {item["name"] for item in listing["prompts"]}
    assert delivery.DOCS_SKILL_NAME in names
    assert delivery.SKILL_NAME in names
    prompt = gateway.get_mcp_prompt(delivery.DOCS_SKILL_NAME)["structuredContent"]
    assert prompt["context"]["status"] == "ready_for_planning"
    assert prompt["context"]["missingRequiredResources"] == []
    assert prompt["context"]["gameOnlyAcceptance"] == []
    assert "vrcforge_know_yourself" in prompt["context"]["planningTools"]
    assert "vrcforge_install_unity_core" not in prompt["context"]["planningTools"]
    assert "vrcforge_execute_shell" not in prompt["context"]["planningTools"]
    assert "vrcforge_execute_shell" not in prompt["context"]["executionWriteTools"]  # external MCP keeps host Shell private
    read = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({"name": delivery.DOCS_SKILL_NAME})
    assert read["instructions"] == prompt["skill"]["instructions"]
    assert read["execution"] == "agentic"
    assert read["supportFiles"] == []


def test_exe_only_route_is_available_as_an_exact_declared_reference(docs_profile):
    app, gateway, _root = docs_profile
    guide = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({"name": delivery.SKILL_NAME})
    assert "references/zero-start.md" in guide["supportFiles"]
    zero = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({
        "name": delivery.SKILL_NAME, "file": "references/zero-start.md",
    })
    assert zero["ok"] and zero["content"] == (
        delivery.bundled_source_dir() / "references" / "zero-start.md"
    ).read_text(encoding="utf-8")
    assert not any(item["name"] == "know-yourself" and item["source"] == "builtin"
                   for item in gateway.skills.build_skill_registry()["skills"])


def test_existing_disabled_docs_and_old_guide_are_preserved(docs_profile):
    app, gateway, _root = docs_profile
    target = gateway.skills.user_skills_dir / delivery.DOCS_SKILL_NAME / "SKILL.md"
    manifest = json.loads((delivery.bundled_docs_source_dir() / "manifest.json").read_text(encoding="utf-8"))
    app.SKILL_PACKAGE_PROJECTION.set_enabled_batch([manifest], False)
    target.write_text(target.read_text(encoding="utf-8") + "\nUser notes\n", encoding="utf-8")
    before = target.read_bytes()
    with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
        result = delivery.deliver_bundled_docs(app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version="1.9.1")
    assert result["status"] == "preserved"
    assert target.read_bytes() == before
    assert gateway.skills.find_user_skill(delivery.DOCS_SKILL_NAME)["enabled"] is False
    assert gateway.skills.find_user_skill(delivery.SKILL_NAME)["enabled"] is True


def test_modified_docs_cannot_claim_bundled_identity(docs_profile):
    _app, gateway, _root = docs_profile
    target = gateway.skills.user_skills_dir / delivery.DOCS_SKILL_NAME / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nReplacement\n", encoding="utf-8")
    loaded = gateway.runtime_skills.execute(delivery.DOCS_SKILL_NAME, {}, "docs-test")
    assert not loaded["ok"]
    assert "identity could not be verified" in loaded["error"]


def test_docs_vsk_roundtrip_keeps_agentic_mode(tmp_path):
    service = SkillPackageService(tmp_path / "packages", vrcforge_version="1.9.1")
    archive = service.export_dev(delivery.bundled_docs_source_dir(), tmp_path / "docs.vsk").package_path
    result = service.preflight_import(archive, dev_mode=True)
    assert result.manifest["skill_name"] == delivery.DOCS_SKILL_NAME
    assert result.manifest["execution"] == "agentic"
    assert result.manifest["entrypoints"] == {"skill": "SKILL.md"}


def test_installed_docs_content_reaches_the_next_model_turn(docs_profile):
    app, gateway, _root = docs_profile
    read = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({"name": delivery.DOCS_SKILL_NAME})
    prompts = []
    planner = RuntimePlannerService(
        catalog=app._RuntimePlannerCatalog(), desktop=app._RuntimePlannerDesktopObservation(),
        model=SimpleNamespace(plan=lambda prompt: (prompts.append(prompt) or PlannerModelResult('{"action":"reply","reply":"fixture"}'))),
    )
    planner.plan_agent_turn(
        "Use VRCForge Docs to explain the connection workflow. Read only.",
        {"_projectContextActive": False, "_internalToolBlocks": ["project_environment/files"]}, {},
        loop_state=[{"tool": "vrcforge_read_installed_skill", "status": "executed", "result": read}],
    )
    assert delivery.DOCS_SKILL_NAME in prompts[0]
    assert read["instructions"].splitlines()[0] in prompts[0]
    assert read["instructions"].splitlines()[-1] in prompts[0]
