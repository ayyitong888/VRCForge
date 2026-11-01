from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import bundled_skill_delivery as delivery
from agent_gateway import AgentGatewayConfig
from external_installed_skill_registry import ExternalInstalledSkillRegistryService
from skill_packages import SkillPackageService


ROOT = Path(__file__).resolve().parents[1]
SOURCES = [*sorted(path for path in (ROOT / "artifacts/skills").glob("vrcforge-avatar-*") if path.is_dir()),
           ROOT / "examples/skill-packages/vrcforge-avatar-head-transplant",
           ROOT / "examples/skill-packages/vrcforge-avatar-part-transplant"]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    import dashboard_server as app

    gateway = app.AGENT_GATEWAY
    previous = gateway.config_path, gateway.audit_dir
    gateway.configure_paths(tmp_path / "config/agent_gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, allow_write_requests=True))
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="1.10.3")
    ports = app.SKILL_PACKAGE_PROJECTION._ports
    monkeypatch.setattr(app.SKILL_PACKAGE_PROJECTION, "_ports", replace(ports, installed_package_candidates=service.projection_candidates))
    resources = tmp_path / "application-resources"
    resources.mkdir()
    monkeypatch.setattr(delivery, "bundled_packages_dir", lambda: resources)
    try:
        yield app, gateway, service, resources
    finally:
        gateway.configure_paths(*previous)


def publish(service, resources, sources, key=None):
    rows = []
    for source in sources:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        filename = manifest["id"] + "-" + manifest["version"] + ".vsk"
        if key:
            service.export_release(source, resources / filename, key.private_key_pem)
        else:
            service.export_dev(source, resources / filename)
        rows.append({"id": manifest["id"], "version": manifest["version"], "file": filename})
    (resources / "index.json").write_text(json.dumps({"packages": rows}), encoding="utf-8")
    return rows


def deliver(app, gateway, service):
    with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
        return delivery.deliver_bundled_packages(service, app.SKILL_PACKAGE_PROJECTION)


def test_ten_real_packages_start_disabled_and_share_internal_external_activation(profile):
    app, gateway, service, resources = profile
    assert len(SOURCES) == 10
    rows = publish(service, resources, SOURCES)
    assert_ten_package_activation(app, gateway, service, rows)


def test_final_signed_release_packages_activate_in_fresh_profile(profile):
    source_value = os.environ.get("VRCFORGE_TEST_SKILL_BUNDLE")
    if not source_value:
        pytest.skip("Set VRCFORGE_TEST_SKILL_BUNDLE to replay the actual release packages.")
    source = Path(source_value)
    app, gateway, service, resources = profile
    rows = json.loads((source / "index.json").read_text(encoding="utf-8"))["packages"]
    assert len(rows) == 10
    shutil.copyfile(source / "index.json", resources / "index.json")
    for row in rows:
        shutil.copyfile(source / row["file"], resources / row["file"])
    assert_ten_package_activation(app, gateway, service, rows)
    registry = service.load_registry()
    assert sum(item["signature_status"] == "signed" for item in registry["skills"].values()) == 10
    assert registry["skills"]["community.vrcforge-avatar-slime"]["signature_status"] == "signed"
    assert not registry["governance"]["trusted_signers"]
    assert not registry["governance"]["official_signers"]


def assert_ten_package_activation(app, gateway, service, rows):
    result = deliver(app, gateway, service)
    assert all(item["status"] == "imported" and not item["enabled"] for item in result), result
    assert len(service.load_registry()["skills"]) == 10
    assert all(not item["enabled"] for item in service.load_registry()["skills"].values())
    external = ExternalInstalledSkillRegistryService(gateway.skills)
    listed = external.list_installed_skills({"includeDisabled": True})["skills"]
    for row in rows:
        skill = next(item for item in listed if item.get("packageId") == row["id"])
        assert not skill["enabled"] and skill["source"] == "user"
        assert skill["name"] not in {item["name"] for item in external.list_installed_skills()["skills"]}
        with pytest.raises(Exception, match="unavailable or disabled"):
            external.read_installed_skill({"name": skill["name"]})
        # The existing UI toggle path owns activation and governance checks.
        app.SKILL_PACKAGE_CONTROLLER.set_enabled({"id": row["id"], "enabled": True})
        read = external.read_installed_skill({"name": skill["name"]})
        assert read["instructions"]
        for relative in read["supportFiles"]:
            assert external.read_installed_skill({"name": skill["name"], "file": relative})["ok"]
        runtime = gateway.skills.prepare_runtime_skill(
            skill["name"], gateway.ensure_config(), gateway._runtime_skill_package_audit_context_locked,
        )
        assert runtime is not None and runtime.loaded, runtime
        assert runtime.skill["instructions"] == read["instructions"]
    before = service.registry_path.read_bytes()
    assert all(item["status"] == "retained" for item in deliver(app, gateway, service))
    assert service.registry_path.read_bytes() == before


@pytest.mark.parametrize("enabled", [False, True])
def test_signed_upgrade_keeps_switch_and_existing_signer(profile, tmp_path, enabled):
    app, gateway, service, resources = profile
    source = tmp_path / "source"
    shutil.copytree(ROOT / "artifacts/skills/vrcforge-avatar-audit", source)
    key = service.generate_signing_keypair()
    service.trust_signer(key.fingerprint, reason="temporary replay signer")
    row = publish(service, resources, [source], key)[0]
    assert deliver(app, gateway, service)[0]["status"] == "imported"
    app.SKILL_PACKAGE_CONTROLLER.set_enabled({"id": row["id"], "enabled": enabled})
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "1.0.99"
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    publish(service, resources, [source], key)
    result = deliver(app, gateway, service)
    assert result == [{"id": row["id"], "status": "updated", "version": "1.0.99", "enabled": enabled}]
    installed = service.load_registry()["skills"][row["id"]]
    assert installed["signature_status"] == "signed" and installed["signer_fingerprint"] == key.fingerprint
    skill = gateway.skills.find_user_skill("vrcforge-avatar-audit")
    assert skill["enabled"] == enabled


@pytest.mark.parametrize("edited_tree", ["projection", "package"])
def test_edited_old_package_or_projection_stays_untouched(profile, tmp_path, edited_tree):
    app, gateway, service, resources = profile
    source = tmp_path / "source"
    shutil.copytree(ROOT / "artifacts/skills/vrcforge-avatar-slime", source)
    row = publish(service, resources, [source])[0]
    assert deliver(app, gateway, service)[0]["status"] == "imported"
    edited = (gateway.skills.user_skills_dir / "vrcforge-avatar-slime/SKILL.md" if edited_tree == "projection"
              else service.skill_store / row["id"] / "versions" / row["version"] / "SKILL.md")
    edited.write_bytes(edited.read_bytes() + b"\nUser edit stays.\n")
    before = edited.read_bytes()
    registry_before = service.registry_path.read_bytes()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "1.0.99"
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    publish(service, resources, [source])
    result = deliver(app, gateway, service)
    assert result[0]["status"] == "conflict", result
    assert edited.read_bytes() == before
    assert service.registry_path.read_bytes() == registry_before
    assert not (service.skill_store / row["id"] / "versions/1.0.99").exists()


def test_dev_does_not_replace_signed_package(profile, tmp_path):
    app, gateway, service, resources = profile
    source = tmp_path / "source"
    shutil.copytree(ROOT / "artifacts/skills/vrcforge-avatar-audit", source)
    row = publish(service, resources, [source], service.generate_signing_keypair())[0]
    deliver(app, gateway, service)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "1.0.99"
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    publish(service, resources, [source])
    result = deliver(app, gateway, service)
    assert result[0]["status"] == "conflict" and "cannot overwrite a signed" in result[0]["error"]
    assert service.load_registry()["skills"][row["id"]]["version"] == row["version"]
