from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayConfig, PROJECTED_SKILL_STATE_SCHEMA
from external_installed_skill_registry import ExternalInstalledSkillRegistryService
from skill_packages import (
    ManifestValidationError,
    PackageIntegrityError,
    PackageSignatureError,
    SkillPackageService,
    canonical_json_bytes,
)


def make_source(
    root: Path,
    *,
    execution: str | None = "deterministic",
    steps: list[dict[str, object]] | None = None,
    with_plan: bool = True,
) -> Path:
    source = root / "source"
    (source / "workflows").mkdir(parents=True)
    manifest: dict[str, object] = {
        "id": "community.tests.deterministic-skill",
        "name": "Deterministic Skill",
        "skill_name": "deterministic-skill",
        "version": "1.0.0",
        "author": "VRCForge Tests",
        "description": "Fixed signed Skill execution regression fixture.",
        "min_vrcforge_version": "0.0.0",
        "permissions": ["read_project"],
        "entrypoints": {"skill": "SKILL.md", "workflow": "workflows/community.json"},
    }
    if execution is not None:
        manifest["execution"] = execution
    if with_plan:
        manifest["entrypoints"]["executionPlan"] = "workflows/execution-plan.json"
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "workflows" / "community.json").write_text(
        json.dumps(
            {
                "schema": "community.workflow.v1",
                "branchSelector": "avatar_type",
                "steps": [{"tool": "vrcforge_first", "tools": ["vrcforge_second"]}],
            }
        ),
        encoding="utf-8",
    )
    if with_plan:
        sequence = steps or [
            {"name": "first", "tool": "vrcforge_first", "arguments": {"fixed": 1}},
            {"name": "second", "tool": "vrcforge_second", "arguments": {}},
        ]
        plan: dict[str, object] = {
            "schema": "vrcforge.deterministic_execution_plan.v1",
            "steps": sequence,
        }
        if any(item.get("writes") for item in sequence):
            plan.update(
                {
                    "approval": {"required": True},
                    "checkpoint": {"required": True},
                    "rollback": {"required": True, "requiresSeparateApproval": True},
                }
            )
        (source / "workflows" / "execution-plan.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
    support_files = ["workflows/community.json"]
    if with_plan:
        support_files.append("workflows/execution-plan.json")
    (source / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: deterministic-skill",
                "title: Deterministic Skill",
                "description: Fixed signed atomic execution.",
                "permission-mode: read_only",
                "risk-level: low",
                "allowed-tools:",
                "  - vrcforge_first",
                "  - vrcforge_second",
                "  - vrcforge_write_target",
                "support-files:",
                *(f"  - {item}" for item in support_files),
                "---",
                "Execute only the separately signed exact atomic tool plan.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return source


def install_signed(
    tmp_path: Path,
    source: Path,
) -> tuple[SkillPackageService, AgentGateway, object]:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_first", "First read.", "read/debug", lambda _params: {"ok": True})
    gateway.register_tool("vrcforge_second", "Second read.", "read/debug", lambda _params: {"ok": True})
    gateway.register_tool(
        "vrcforge_write_target", "Approved write.", "supervised-write",
        lambda _params: {"ok": True}, write=True,
    )
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    pair = service.generate_signing_keypair()
    service.trust_signer(pair.fingerprint)
    package = service.export_release(source, tmp_path / "signed.vsk", pair.private_key_pem)
    installed = service.install(package.package_path)
    projection = gateway.skills.user_skills_dir / "deterministic-skill"
    shutil.copytree(installed.installed_path, projection)
    for filename in ("manifest.json", "skill.lock.json", "skill.sig", "author.pub"):
        (projection / filename).unlink(missing_ok=True)
    (projection / ".vrcforge-package-state.json").write_text(
        json.dumps(
            {
                "schema": PROJECTED_SKILL_STATE_SCHEMA,
                "enabled": True,
                "packageId": "community.tests.deterministic-skill",
            }
        ),
        encoding="utf-8",
    )
    return service, gateway, installed


def test_legacy_and_explicit_agentic_packages_preserve_the_existing_mode(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.0.0")
    source = make_source(tmp_path, execution=None, with_plan=False)

    legacy = service.export_dev(source, tmp_path / "legacy.vsk")

    assert "execution" not in legacy.manifest
    assert legacy.as_dict()["execution"] == "agentic"
    assert service.inspect_package(legacy.package_path).as_dict()["executionMode"] == "agentic"

    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution"] = "agentic"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert service.export_dev(source, tmp_path / "agentic.vsk").as_dict()["execution"] == "agentic"


@pytest.mark.parametrize("execution", ["scripted", "", 42, None])
def test_unknown_explicit_execution_modes_are_rejected(tmp_path: Path, execution: object) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.0.0")
    source = make_source(tmp_path)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution"] = execution

    with pytest.raises((ManifestValidationError, TypeError), match="execution|unhashable"):
        service.validate_manifest(manifest)


def test_deterministic_skill_requires_independent_signed_fixed_execution_plan(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.0.0")
    source = make_source(tmp_path, with_plan=False)

    with pytest.raises(ManifestValidationError, match="executionPlan"):
        service.export_release(source, tmp_path / "missing.vsk", service.generate_signing_keypair().private_key_pem)


def test_unsigned_deterministic_package_is_rejected(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.0.0")

    with pytest.raises(PackageSignatureError, match="signed releases"):
        service.export_dev(make_source(tmp_path), tmp_path / "unsigned.vsk")


@pytest.mark.parametrize(
    "step",
    [
        {"tool": "vrcforge_first", "tools": ["vrcforge_second"]},
        {"tool": "vrcforge_first", "conditional": True},
        {"tool": "vrcforge_request_apply"},
        {"tool": "external_command"},
        {"tool": "vrcforge_first", "arguments": "dynamic"},
    ],
)
def test_deterministic_plan_rejects_branches_dynamic_tools_and_approval_bypass(
    tmp_path: Path, step: dict[str, object]
) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.0.0")

    with pytest.raises(ManifestValidationError):
        service.export_release(
            make_source(tmp_path, steps=[step]),
            tmp_path / "invalid.vsk",
            service.generate_signing_keypair().private_key_pem,
        )


def test_deterministic_write_requires_explicit_approval_checkpoint_and_rollback_contract(
    tmp_path: Path,
) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.0.0")
    source = make_source(
        tmp_path,
        steps=[{"tool": "vrcforge_write_target", "writes": True}],
    )

    with pytest.raises(ManifestValidationError, match="runtime approval"):
        service.export_release(source, tmp_path / "invalid.vsk", service.generate_signing_keypair().private_key_pem)


def test_signed_deterministic_sequence_is_exposed_without_eager_registry_content(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path)
    sequence = json.loads((source / "workflows" / "execution-plan.json").read_text())["steps"]
    service, gateway, installed = install_signed(tmp_path, source)
    digest = hashlib.sha256(canonical_json_bytes(sequence)).hexdigest()
    package = service.list_installed()[0]

    assert installed.preview.as_dict()["execution"] == "deterministic"
    assert package["execution"] == package["executionMode"] == "deterministic"
    assert package["workflowSteps"] == sequence
    assert package["workflowDigest"] == digest
    assert package["runtimeEnforced"] is True

    external = ExternalInstalledSkillRegistryService(gateway.skills)
    indexed = external.list_installed_skills()["skills"][0]
    assert indexed["execution"] == indexed["executionMode"] == "deterministic"
    assert indexed["workflowDigest"] == digest
    assert "workflowSteps" not in indexed
    loaded = external.read_installed_skill({"name": "deterministic-skill"})
    assert loaded["workflowSteps"] == sequence
    assert loaded["runtimeEnforced"] is True


def test_runtime_executes_the_signed_read_sequence_in_exact_order(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    _, gateway, installed = install_signed(tmp_path, source)
    calls: list[tuple[str, dict[str, object]]] = []
    gateway.register_tool(
        "vrcforge_first", "First read.", "read/debug",
        lambda params: calls.append(("first", params)) or {"ok": True},
    )
    gateway.register_tool(
        "vrcforge_second", "Second read.", "read/debug",
        lambda params: calls.append(("second", params)) or {"ok": True},
    )

    result = gateway.runtime_skills.execute(
        "deterministic-skill", {"selectedAvatar": "Avatar", "fixed": "cannot override"}, "test-agent"
    )

    assert result["status"] == "executed"
    assert result["execution"] == "deterministic"
    assert result["workflowDigest"] == installed.registry_entry["workflowDigest"]
    assert [name for name, _ in calls] == ["first", "second"]
    assert calls[0][1]["fixed"] == 1
    assert calls[1][1]["selectedAvatar"] == "Avatar"
    assert [step["tool"] for step in result["steps"]] == ["vrcforge_first", "vrcforge_second"]


def test_runtime_stops_before_write_without_bypassing_approval_or_checkpoint(
    tmp_path: Path,
) -> None:
    source = make_source(
        tmp_path,
        steps=[
            {"tool": "vrcforge_first", "arguments": {}},
            {
                "tool": "vrcforge_write_target",
                "arguments": {"target": "fixed"},
                "writes": True,
                "runtimeApprovalRequired": True,
            },
            {"tool": "vrcforge_second", "arguments": {}},
        ],
    )
    _, gateway, _ = install_signed(tmp_path, source)
    calls: list[str] = []
    gateway.register_tool(
        "vrcforge_first", "First read.", "read/debug", lambda _params: calls.append("read") or {"ok": True}
    )
    gateway.register_tool(
        "vrcforge_write_target", "Write.", "supervised-write",
        lambda _params: calls.append("WRITE") or {"ok": True}, write=True,
    )
    gateway.approval_transactions.register_write_handler(
        "vrcforge_write_target", "Approved write.", "low",
        lambda _params: calls.append("WRITE") or {"ok": True},
    )
    gateway.register_tool(
        "vrcforge_second", "Second read.", "read/debug", lambda _params: calls.append("late") or {"ok": True}
    )

    result = gateway.runtime_skills.execute("deterministic-skill", {}, "test-agent")

    assert result["status"] == "needs_user_action"
    assert result["targetTool"] == "vrcforge_write_target"
    assert result["failedStep"] == 1
    assert result["requiresApproval"] is True
    assert result["checkpointRequired"] is True
    assert result["rollbackRequiresSeparateApproval"] is True
    assert result["approvalId"]
    assert result["steps"][1]["result"]["status"] == "pending"
    assert calls == ["read"]


@pytest.mark.parametrize("execution_mode", ["auto", "roslyn_full_auto"])
def test_allowed_modes_execute_signed_write_through_existing_approval_and_checkpoint(
    tmp_path: Path,
    execution_mode: str,
) -> None:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )
    source = make_source(
        tmp_path,
        steps=[
            {"tool": "vrcforge_first", "arguments": {}},
            {
                "tool": "vrcforge_write_target",
                "arguments": {"fixed": "signed"},
                "writes": True,
                "runtimePermissionGateRequired": True,
            },
            {"tool": "vrcforge_second", "arguments": {}},
        ],
    )
    _, gateway, _ = install_signed(tmp_path, source)
    gateway.save_config(
        AgentGatewayConfig(
            enabled=True,
            allow_write_requests=True,
            execution_mode=execution_mode,
        )
    )
    calls: list[str] = []
    gateway.register_tool(
        "vrcforge_first", "First read.", "read/debug", lambda _params: calls.append("read-first") or {"ok": True}
    )
    gateway.register_tool(
        "vrcforge_second", "Second read.", "read/debug", lambda _params: calls.append("read-second") or {"ok": True}
    )
    gateway.approval_transactions.checkpoint_prepare_handler = (
        lambda _root: calls.append("checkpoint") or {"ok": True}
    )
    gateway.approval_transactions.register_write_handler(
        "vrcforge_write_target", "Approved write.", "low",
        lambda arguments: calls.append("WRITE:" + str(arguments["fixed"])) or {"ok": True},
    )

    result = gateway.runtime_skills.execute(
        "deterministic-skill", {"projectRoot": str(project), "fixed": "override"}, "test-agent"
    )

    assert result["status"] == "executed"
    assert calls == ["read-first", "checkpoint", "WRITE:signed", "read-second"]
    assert result["approvalId"]
    assert result["steps"][1]["result"]["autoApproved"] is True
    approvals = gateway.approval_transactions.list_approvals()
    assert approvals[0]["targetTool"] == "vrcforge_write_target"


def test_runtime_rejects_revoked_deterministic_signer(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    service, gateway, installed = install_signed(tmp_path, source)
    service.revoke_signer(installed.preview.signer_fingerprint, reason="compromised")

    result = gateway.runtime_skills.execute("deterministic-skill", {}, "test-agent")

    assert result["status"] == "blocked"
    assert "could not be verified" in result["error"]


def test_runtime_rejects_signed_plan_changed_after_installation(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    _, gateway, installed = install_signed(tmp_path, source)
    plan_path = installed.installed_path / "workflows" / "execution-plan.json"
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    document["steps"].reverse()
    changed = json.dumps(document)
    plan_path.write_text(changed, encoding="utf-8")
    (gateway.skills.user_skills_dir / "deterministic-skill" / "workflows" / "execution-plan.json").write_text(
        changed, encoding="utf-8"
    )

    result = gateway.runtime_skills.execute("deterministic-skill", {}, "test-agent")

    assert result["status"] == "blocked"
    assert "identity could not be verified" in result["error"]


def test_runtime_refuses_tampered_registry_execution_identity(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    service, gateway, _ = install_signed(tmp_path, source)
    gateway.register_tool("vrcforge_first", "First read.", "read/debug", lambda _params: {"ok": True})
    registry = json.loads(service.registry_path.read_text(encoding="utf-8"))
    registry["skills"]["community.tests.deterministic-skill"]["execution"] = "agentic"
    registry["skills"]["community.tests.deterministic-skill"]["executionMode"] = "agentic"
    service.registry_path.write_bytes(canonical_json_bytes(registry))

    result = gateway.runtime_skills.execute("deterministic-skill", {}, "test-agent")

    assert result["status"] == "blocked"
    assert "identity could not be verified" in result["error"]
