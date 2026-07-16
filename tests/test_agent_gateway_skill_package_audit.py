from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_gateway import AgentGateway
from skill_packages import SkillPackageService


def _write_signed_skill_source(root: Path) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "id": "community.tests.runtime-audit",
                "name": "Runtime Audit Fixture",
                "skill_name": "runtime-audit-fixture",
                "version": "1.2.3",
                "author": "VRCForge Tests",
                "description": "Signed runtime audit fixture.",
                "min_vrcforge_version": "0.0.0",
                "permissions": ["read_project"],
                "entrypoints": {
                    "skill": "SKILL.md",
                    "workflow": "workflows/runtime-audit.json",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (source / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: runtime-audit-fixture",
                "title: Runtime Audit Fixture",
                "description: Exercise one read-only runtime entrypoint.",
                "permission-mode: read_only",
                "risk-level: low",
                "allowed-tools:",
                "  - vrcforge_health",
                "entrypoint-tool: vrcforge_health",
                "support-files:",
                "  - workflows/runtime-audit.json",
                "---",
                "Run the health entrypoint and return its result.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    workflow = source / "workflows" / "runtime-audit.json"
    workflow.parent.mkdir()
    workflow.write_text(
        json.dumps({"schema": "vrcforge.test-workflow.v1", "steps": [{"tool": "vrcforge_health"}]}),
        encoding="utf-8",
    )
    return source


def _project_signed_skill(gateway: AgentGateway, installed_path: Path) -> Path:
    projected_root = gateway.user_skills_dir / "runtime-audit-fixture"
    (projected_root / "workflows").mkdir(parents=True)
    shutil.copy2(installed_path / "SKILL.md", projected_root / "SKILL.md")
    shutil.copy2(
        installed_path / "workflows" / "runtime-audit.json",
        projected_root / "workflows" / "runtime-audit.json",
    )
    return projected_root / "SKILL.md"


def _read_runtime_package_events(gateway: AgentGateway) -> list[dict[str, object]]:
    events = [json.loads(line) for line in gateway.audit_log_path.read_text(encoding="utf-8").splitlines()]
    return [
        event
        for event in events
        if event.get("event") in {"runtime_skill_package_loaded", "runtime_skill_entrypoint_executed"}
    ]


def test_signed_package_runtime_audit_includes_locked_identity_and_signer_context(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_health", "Read runtime health.", "read/debug", lambda _params: {"ok": True})
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        _write_signed_skill_source(tmp_path),
        tmp_path / "runtime-audit.vsk",
        key_pair.private_key_pem,
    ).package_path
    service.trust_signer(key_pair.fingerprint, reason="focused runtime audit test")
    installed = service.install(package)

    projected = _project_signed_skill(gateway, installed.installed_path)

    result = gateway.execute_runtime_skill("runtime-audit-fixture", {}, "test-agent")

    assert result["status"] == "executed"
    assert result["result"]["supportFiles"] == [
        {
            "path": "workflows/runtime-audit.json",
            "content": (installed.installed_path / "workflows" / "runtime-audit.json").read_text(encoding="utf-8"),
        }
    ]
    events = _read_runtime_package_events(gateway)
    assert [event["event"] for event in events] == [
        "runtime_skill_entrypoint_executed",
        "runtime_skill_package_loaded",
    ]
    expected_context = {
        "packageId": "community.tests.runtime-audit",
        "packageVersion": "1.2.3",
        "packageSha256": installed.preview.package_sha256,
        "lockSha256": installed.preview.lock_sha256,
        "signatureStatus": "signed",
        "signerFingerprint": key_pair.fingerprint,
        "signerTrustStatus": "trusted",
    }
    for event in events:
        assert {key: event.get(key) for key in expected_context} == expected_context
        assert "source" not in event
        assert "storagePath" not in event


def test_modified_projection_keeps_legacy_audit_shape_without_signer_misattribution(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_health", "Read runtime health.", "read/debug", lambda _params: {"ok": True})
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        _write_signed_skill_source(tmp_path),
        tmp_path / "runtime-audit.vsk",
        key_pair.private_key_pem,
    ).package_path
    service.trust_signer(key_pair.fingerprint)
    installed = service.install(package)

    projected = _project_signed_skill(gateway, installed.installed_path)
    projected.write_text(projected.read_text(encoding="utf-8") + "Locally edited instructions.\n", encoding="utf-8")

    result = gateway.execute_runtime_skill("runtime-audit-fixture", {}, "test-agent")

    assert result["status"] == "executed"
    events = _read_runtime_package_events(gateway)
    assert len(events) == 2
    package_keys = {
        "packageId",
        "packageVersion",
        "packageSha256",
        "lockSha256",
        "signatureStatus",
        "signerFingerprint",
        "signerTrustStatus",
    }
    for event in events:
        assert package_keys.isdisjoint(event)


def test_modified_locked_or_projected_support_file_cannot_keep_signed_runtime_attribution(tmp_path: Path) -> None:
    for tamper_target in ("installed", "projected"):
        case_root = tmp_path / tamper_target
        gateway = AgentGateway(case_root / "config" / "agent_gateway.json", case_root / "audit")
        gateway.register_tool("vrcforge_health", "Read runtime health.", "read/debug", lambda _params: {"ok": True})
        service = SkillPackageService(case_root / "skill-packages", vrcforge_version="0.0.0")
        key_pair = service.generate_signing_keypair()
        package = service.export_release(
            _write_signed_skill_source(case_root),
            case_root / "runtime-audit.vsk",
            key_pair.private_key_pem,
        ).package_path
        service.trust_signer(key_pair.fingerprint)
        installed = service.install(package)
        projected = _project_signed_skill(gateway, installed.installed_path)
        target = (
            installed.installed_path / "workflows" / "runtime-audit.json"
            if tamper_target == "installed"
            else projected.parent / "workflows" / "runtime-audit.json"
        )
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        result = gateway.execute_runtime_skill("runtime-audit-fixture", {}, "test-agent")

        assert result["status"] == "executed"
        for event in _read_runtime_package_events(gateway):
            assert "packageId" not in event
            assert "signerFingerprint" not in event


def test_registry_package_sha_mismatch_cannot_keep_signed_runtime_attribution(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_health", "Read runtime health.", "read/debug", lambda _params: {"ok": True})
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        _write_signed_skill_source(tmp_path),
        tmp_path / "runtime-audit.vsk",
        key_pair.private_key_pem,
    ).package_path
    service.trust_signer(key_pair.fingerprint)
    installed = service.install(package)
    _project_signed_skill(gateway, installed.installed_path)

    registry = json.loads(service.registry_path.read_text(encoding="utf-8"))
    registry["skills"]["community.tests.runtime-audit"]["package_sha256"] = "0" * 64
    service.registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    result = gateway.execute_runtime_skill("runtime-audit-fixture", {}, "test-agent")

    assert result["status"] == "executed"
    for event in _read_runtime_package_events(gateway):
        assert "packageId" not in event
        assert "packageSha256" not in event


@pytest.mark.parametrize(
    ("support_path", "support_content"),
    [
        ("../outside.json", "{}"),
        ("workflows/secret.txt", "api_key=sk-1234567890abcdefghijklmnop"),
    ],
)
def test_runtime_support_loader_blocks_traversal_and_sensitive_text(
    tmp_path: Path,
    support_path: str,
    support_content: str,
) -> None:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_health", "Read runtime health.", "read/debug", lambda _params: {"ok": True})
    skill_root = gateway.user_skills_dir / "unsafe-support"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: unsafe-support",
                "title: Unsafe Support",
                "permission-mode: read_only",
                "risk-level: low",
                "allowed-tools:",
                "  - vrcforge_health",
                "entrypoint-tool: vrcforge_health",
                "support-files:",
                f"  - {support_path}",
                "---",
                "Load support only through the bounded runtime projection.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    support_file = skill_root / support_path
    support_file.parent.mkdir(parents=True, exist_ok=True)
    support_file.write_text(support_content, encoding="utf-8")

    result = gateway.execute_runtime_skill("unsafe-support", {}, "test-agent")

    assert result["status"] == "blocked"
    assert result["ok"] is False
    assert "support" in result["error"]
