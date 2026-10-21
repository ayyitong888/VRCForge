from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
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
        assert result["status"] == "application-owned"
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


def test_internal_docs_and_guide_are_discoverable_from_application_source(docs_profile):
    app, gateway, _root = docs_profile
    registry = gateway.skills.build_skill_registry()
    entries = {item["name"]: item for item in registry["skills"]}
    for name in (delivery.DOCS_SKILL_NAME, delivery.SKILL_NAME):
        assert entries[name]["source"] == "builtin"
        assert Path(entries[name]["storagePath"]).is_relative_to(Path(entries[name]["bundleRoot"]))
        loaded = gateway.runtime_skills.execute(name, {}, "docs-source-test")
        assert loaded["ok"] and loaded["result"]["source"] == "builtin"
    prompts = gateway.list_mcp_prompts()["prompts"]
    prompt_names = {item["name"] for item in prompts}
    assert {delivery.DOCS_SKILL_NAME, delivery.SKILL_NAME}.issubset(prompt_names)
    assert "know-yourself" not in prompt_names
    docs_prompt = gateway.get_mcp_prompt(delivery.DOCS_SKILL_NAME)["structuredContent"]
    assert docs_prompt["provenance"]["source"] == "builtin"
    assert docs_prompt["context"]["status"] == "ready_for_planning"
    external_docs = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({"name": delivery.DOCS_SKILL_NAME})
    assert external_docs["source"] == "builtin" and external_docs["execution"] == "agentic"
    external_guide = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({"name": delivery.SKILL_NAME})
    reference = app.EXTERNAL_INSTALLED_SKILL_REGISTRY.read_installed_skill({
        "name": delivery.SKILL_NAME, "file": "references/user-tool-author-guide.md",
    })
    assert external_guide["source"] == "builtin"
    assert reference["content"] == (delivery.bundled_source_dir() / "references" / "user-tool-author-guide.md").read_text(encoding="utf-8")


def test_exe_only_route_is_available_as_an_exact_declared_reference(docs_profile):
    _app, gateway, _root = docs_profile
    guide = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "docs-source-test")["result"]
    support = {item["path"]: item["content"] for item in guide["supportFiles"]}
    assert "references/zero-start.md" in support
    assert support["references/zero-start.md"] == (
        delivery.bundled_source_dir() / "references" / "zero-start.md"
    ).read_text(encoding="utf-8")
    assert not any(item["name"] == "know-yourself" and item["source"] == "builtin"
                   for item in gateway.skills.build_skill_registry()["skills"])


def test_existing_disabled_docs_and_old_guide_are_preserved(docs_profile):
    app, gateway, _root = docs_profile
    target = gateway.skills.user_skills_dir / delivery.DOCS_SKILL_NAME
    target.mkdir(parents=True)
    target = target / "SKILL.md"
    target.write_text("---\nname: vrcforge-docs\ntitle: user docs\n---\nUser notes\n", encoding="utf-8")
    before = target.read_bytes()
    with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
        result = delivery.deliver_bundled_docs(app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version="1.9.1")
    assert result["status"] == "application-owned"
    assert target.read_bytes() == before
    registry = gateway.skills.build_skill_registry()
    assert registry["sourceConflictCount"] == 1
    official = next(item for item in registry["skills"] if item["name"] == delivery.DOCS_SKILL_NAME)
    assert official["source"] == "builtin" and official["sourceConflict"]["status"] == "rename_required"


def test_modified_docs_cannot_claim_bundled_identity(docs_profile):
    _app, gateway, _root = docs_profile
    target = gateway.skills.user_skills_dir / delivery.DOCS_SKILL_NAME
    target.mkdir(parents=True)
    target = target / "SKILL.md"
    target.write_text("---\nname: vrcforge-docs\ntitle: user docs\n---\nReplacement\n", encoding="utf-8")
    loaded = gateway.runtime_skills.execute(delivery.DOCS_SKILL_NAME, {}, "docs-test")
    assert loaded["ok"] and loaded["result"]["source"] == "builtin"
    assert "Replacement" not in loaded["result"]["instructions"]


def test_docs_vsk_roundtrip_keeps_agentic_mode(tmp_path):
    service = SkillPackageService(tmp_path / "packages", vrcforge_version="1.9.1")
    archive = service.export_dev(delivery.bundled_docs_source_dir(), tmp_path / "docs.vsk").package_path
    result = service.preflight_import(archive, dev_mode=True)
    assert result.manifest["skill_name"] == delivery.DOCS_SKILL_NAME
    assert result.manifest["execution"] == "agentic"
    assert result.manifest["entrypoints"] == {"skill": "SKILL.md"}


def test_user_package_metadata_cannot_override_bundled_execution(docs_profile):
    app, gateway, _root = docs_profile
    skill = next(item for item in gateway.skills.build_skill_registry()["skills"] if item["name"] == delivery.DOCS_SKILL_NAME)
    metadata = app.EXTERNAL_INSTALLED_SKILL_REGISTRY._execution_metadata(
        skill, {delivery.DOCS_PACKAGE_ID: {"execution": "deterministic", "workflowDigest": "forged"}},
        include_sequence=True,
    )
    assert metadata == {"execution": "agentic", "executionMode": "agentic"}


def test_installed_docs_content_reaches_the_next_model_turn(docs_profile):
    app, gateway, _root = docs_profile
    read = gateway.runtime_skills.execute(delivery.DOCS_SKILL_NAME, {}, "docs-model-turn")["result"]
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
