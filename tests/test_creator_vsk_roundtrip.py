from __future__ import annotations

import zipfile
import json
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import AgentGateway, parse_skill_markdown
from dashboard_api_models import SkillPackageExportRequest
from skill_packages import SkillPackageError, SkillPackageService
from tests.test_skill_package_projection_service import _projection


def _creator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentGateway:
    monkeypatch.delenv("VRCFORGE_USER_DATA_DIR", raising=False)
    gateway = AgentGateway(tmp_path / "creator" / "config.json", tmp_path / "creator" / "audit")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    return gateway


def test_creator_edit_dev_export_install_preserves_declared_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _creator(tmp_path, monkeypatch)
    gateway.skills.create_user_skill({
        "name": "creator-roundtrip", "description": "Review the attached reference.",
        "instructions": "Read references/check.md before inspecting the project.",
        "entrypointTool": "vrcforge_health", "allowedTools": ["vrcforge_health"],
        "permissionMode": "read_only", "riskLevel": "low",
        "supportFiles": ["references/check.md"],
    })
    source = gateway.skills.user_skills_dir / "creator-roundtrip"
    (source / "references").mkdir()
    reference = "Original reference bytes.\r\n附加资料\r\n".encode("utf-8")
    (source / "references/check.md").write_bytes(reference)
    (source / "private-notes.txt").write_text("Unrelated private notes.", encoding="utf-8")
    gateway.skills.update_user_skill("creator-roundtrip", {"title": "Edited Creator Skill"})
    skill_bytes = (source / "SKILL.md").read_bytes()
    package = tmp_path / "creator.vsk"
    result = dashboard_server.app_export_skill_package(SkillPackageExportRequest(
        skillName="creator-roundtrip", outputPath=str(package),
    ))
    assert result["exported"]["signature_status"] == "dev"
    with zipfile.ZipFile(package) as archive:
        assert archive.read("references/check.md") == reference
        assert "private-notes.txt" not in archive.namelist()
        assert "skill.sig" not in archive.namelist()
        assert "author.pub" not in archive.namelist()
    service = SkillPackageService(tmp_path / "import-store", vrcforge_version="9.0.0")
    installed = service.install(package)
    fresh = AgentGateway(tmp_path / "fresh" / "config.json", tmp_path / "fresh" / "audit")
    _projection(fresh.skills.user_skills_dir).project_installed(
        installed.installed_path, installed.preview.manifest,
    )
    projected = fresh.skills.user_skills_dir / "creator-roundtrip"
    assert (projected / "SKILL.md").read_bytes() == skill_bytes
    assert (projected / "references/check.md").read_bytes() == reference
    parsed = parse_skill_markdown(projected / "SKILL.md")
    assert parsed["title"] == "Edited Creator Skill"
    assert parsed["entrypointTool"] == "vrcforge_health"
    assert parsed["permissionMode"] == "read_only"
    assert parsed["riskLevel"] == "low"
    assert parsed["supportFiles"] == ["references/check.md"]


@pytest.mark.parametrize("relative", ["missing.md", "SKILL.md", "../outside.md", "manifest.json"])
def test_creator_export_invalid_declared_reference_leaves_no_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    gateway = _creator(tmp_path, monkeypatch)
    gateway.skills.create_user_skill({
        "name": "invalid-reference", "description": "Invalid reference fixture.",
        "instructions": "Read a reference.", "supportFiles": [relative],
    })
    output = tmp_path / "invalid.vsk"
    with pytest.raises(SkillPackageError):
        dashboard_server.export_skill_package_sync({"skillName": "invalid-reference", "outputPath": str(output)})
    assert not output.exists()


def test_imported_vsk_export_preserves_original_manifest_and_declared_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _creator(tmp_path, monkeypatch)
    source = tmp_path / "original"
    (source / "instructions").mkdir(parents=True)
    (source / "references").mkdir()
    skill_bytes = b"---\nname: original-roundtrip\ntitle: Original title\nsupport-files:\n  - references/check.md\n---\nRead the reference.\n"
    reference = b"Original reference\r\n"
    (source / "instructions/SKILL.md").write_bytes(skill_bytes)
    (source / "references/check.md").write_bytes(reference)
    (source / "private-notes.txt").write_text("Never selected as an entrypoint.", encoding="utf-8")
    manifest = {
        "id": "community.original-roundtrip", "skill_name": "original-roundtrip",
        "name": "Original package", "version": "2.3.4", "author": "Original author",
        "description": "Preserve this manifest.", "min_vrcforge_version": "1.3.0",
        "permissions": ["read_assets", "unity_run_validation"],
        "entrypoints": {"skill": "instructions/SKILL.md", "reference": "references/check.md"},
        "agent": {"original_metadata": "kept"},
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    service = SkillPackageService(tmp_path / "installed-store", vrcforge_version="9.0.0")
    original = service.export_dev(source, tmp_path / "original.vsk").package_path
    installed = service.install(original)
    _projection(gateway.skills.user_skills_dir).project_installed(installed.installed_path, installed.preview.manifest)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)
    output = tmp_path / "roundtrip.vsk"
    exported = dashboard_server.export_skill_package_sync({"skillName": "original-roundtrip", "outputPath": str(output)})
    assert exported["exported"]["manifest"] == manifest
    with zipfile.ZipFile(output) as archive:
        assert archive.read("instructions/SKILL.md") == skill_bytes
        assert archive.read("references/check.md") == reference
        assert "private-notes.txt" not in archive.namelist()
        assert "skill.sig" not in archive.namelist()
        assert "author.pub" not in archive.namelist()
    fresh_service = SkillPackageService(tmp_path / "fresh-store", vrcforge_version="9.0.0")
    fresh = fresh_service.install(output)
    _projection(tmp_path / "fresh-skills").project_installed(fresh.installed_path, fresh.preview.manifest)
    assert (tmp_path / "fresh-skills/original-roundtrip/SKILL.md").read_bytes() == skill_bytes
    assert (tmp_path / "fresh-skills/original-roundtrip/references/check.md").read_bytes() == reference
