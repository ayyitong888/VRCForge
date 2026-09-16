from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import bundled_skill_delivery as delivery
from agent_gateway import AgentGatewayConfig
from external_installed_skill_registry import ExternalInstalledSkillRegistryService
from skill_packages import SkillPackageError, SkillPackageService


@pytest.fixture
def app_profile(tmp_path):
    import dashboard_server as app

    gateway = app.AGENT_GATEWAY
    previous = gateway.config_path, gateway.audit_dir
    gateway.configure_paths(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, allow_write_requests=True))
    try:
        yield app, gateway, tmp_path
    finally:
        gateway.configure_paths(*previous)


def _deliver(app, gateway, version="1.8.1"):
    with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
        return delivery.deliver_bundled_guide(
            app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version=version,
        )


def test_fresh_normal_profile_loads_complete_guide_internally_and_external_prompt(app_profile):
    app, gateway, root = app_profile
    assert _deliver(app, gateway)["status"] == "installed"
    skill = next(item for item in gateway.skills.build_skill_registry()["skills"] if item["name"] == delivery.SKILL_NAME)
    assert skill["enabled"] and skill["available"]
    assert skill["packageId"] == delivery.PACKAGE_ID
    assert skill["validation"]["status"] != "error"
    result = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "test")
    assert result["ok"] and result["status"] == "loaded", result
    assert result["execution"] == "agentic"
    context = gateway._runtime_skill_package_audit_context_locked(skill)
    assert context["distribution"] == "bundled"
    assert context["signatureStatus"] == "not_signed"
    assert context["signerFingerprint"] is None
    assert not (root / "skill-packages" / "registry.json").exists()

    assert delivery.SKILL_NAME in {item["name"] for item in gateway.list_mcp_prompts()["prompts"]}
    prompt = gateway.get_mcp_prompt(delivery.SKILL_NAME)["structuredContent"]
    assert prompt["skill"]["instructions"] == skill["instructions"]
    assert prompt["context"]["status"] == "ready_for_planning"
    assert prompt["context"]["missingRequiredResources"] == []
    assert prompt["context"]["gmCases"] == []
    assert prompt["context"]["gameOnlyAcceptance"] == []
    support = {item["path"]: item["content"] for item in prompt["skill"]["supportFiles"]}
    assert set(support) == {"workflows/first-run.json", "references/repair-guide.md"}
    for relative, content in support.items():
        assert content == (delivery.bundled_source_dir() / relative).read_bytes().decode("utf-8")
    installed = ExternalInstalledSkillRegistryService(gateway.skills)
    assert delivery.SKILL_NAME in {item["name"] for item in installed.list_installed_skills()["skills"]}
    read = installed.read_installed_skill({"name": delivery.SKILL_NAME, "file": "references/repair-guide.md"})
    assert read["content"] == support["references/repair-guide.md"]
    external = gateway.call_external_mcp_tool("vrcforge_read_installed_skill", {
        "name": delivery.SKILL_NAME, "file": "references/repair-guide.md",
    })
    assert external["ok"]
    assert external["result"]["content"] == support["references/repair-guide.md"]


def test_startup_preserves_user_edits_and_disabled_state(app_profile):
    app, gateway, root = app_profile
    _deliver(app, gateway)
    target = gateway.skills.user_skills_dir / delivery.SKILL_NAME
    manifest = json.loads((delivery.bundled_source_dir() / "manifest.json").read_text(encoding="utf-8"))
    app.SKILL_PACKAGE_PROJECTION.set_enabled_batch([manifest], False)
    guide = target / "references" / "repair-guide.md"
    guide.write_text("User's repair notes", encoding="utf-8")
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert _deliver(app, gateway)["status"] == "preserved"
    assert before == {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    skill = gateway.skills.find_user_skill(delivery.SKILL_NAME)
    assert skill["enabled"] is False
    assert delivery.bundled_guide_audit_context(skill, gateway.skills.user_skills_dir, root / "skill-packages") == {}


def test_content_tamper_is_not_authorized_by_projection_marker(app_profile):
    app, gateway, _ = app_profile
    _deliver(app, gateway)
    target = gateway.skills.user_skills_dir / delivery.SKILL_NAME / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nUnapproved replacement\n", encoding="utf-8")
    result = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "test")
    assert not result["ok"]
    assert "identity could not be verified" in result["error"]


def test_existing_package_governance_remains_authoritative(app_profile, tmp_path):
    app, gateway, root = app_profile
    _deliver(app, gateway)
    service = SkillPackageService(root / "skill-packages", vrcforge_version="1.8.1")
    archive = service.export_dev(delivery.bundled_source_dir(), tmp_path / "guide.vsk").package_path
    service.install(archive, dev_mode=True)
    skill = gateway.skills.find_user_skill(delivery.SKILL_NAME)
    assert delivery.bundled_guide_audit_context(skill, gateway.skills.user_skills_dir, root / "skill-packages") == {}


@pytest.mark.parametrize("failure", ["minimum_version", "missing_support"])
def test_incompatible_or_incomplete_bundle_never_leaves_partial_skill(app_profile, monkeypatch, failure):
    app, gateway, root = app_profile
    source = root / "application-bundle"
    shutil.copytree(delivery.bundled_source_dir(), source)
    if failure == "missing_support":
        (source / "references" / "repair-guide.md").unlink()
    monkeypatch.setattr(delivery, "bundled_source_dir", lambda: source)
    with pytest.raises(SkillPackageError):
        _deliver(app, gateway, "1.8.0" if failure == "minimum_version" else "1.8.1")
    assert not (gateway.skills.user_skills_dir / delivery.SKILL_NAME).exists()


def test_normal_release_collects_full_source_and_startup_delivers_it():
    root = Path(__file__).resolve().parents[1]
    build = (root / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")
    assert '--add-data "$bundledGuide;examples/skill-packages/vrcforge-first-run-guide"' in build
    assert "$bundledManifest.entrypoints.PSObject.Properties.Value" in build
    assert "Get-FileHash -LiteralPath $collectedPath" in build
    release = (root / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
    assert "packaging\\build_backend.ps1" in release
    import inspect
    import dashboard_server
    assert "deliver_bundled_guide(" in inspect.getsource(dashboard_server.on_startup)
