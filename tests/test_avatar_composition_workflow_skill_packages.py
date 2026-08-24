from __future__ import annotations

import json
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import parse_skill_markdown
from skill_packages import PackageCompatibilityError, SkillPackageService


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "examples" / "skill-packages"
PRESERVED_CAUSAL_FIELDS = {
    "ready",
    "blockingReasons",
    "failureLayer",
    "failurePhase",
    "failureCause",
    "rootCause",
    "causeChain",
    "observed",
    "expected",
    "delta",
    "mutationStarted",
    "committed",
    "commitState",
    "sceneSaved",
    "persistedReadback",
    "evidence",
    "recovery",
    "nextAction",
}
CASES = (
    {
        "slug": "vrcforge-avatar-head-transplant",
        "package_id": "com.vrcforge.workflows.avatar_head_transplant",
        "workflow": "workflows/avatar-head-transplant.json",
    },
    {
        "slug": "vrcforge-avatar-part-transplant",
        "package_id": "com.vrcforge.workflows.avatar_part_transplant",
        "workflow": "workflows/avatar-part-transplant.json",
    },
)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["slug"]))
def test_avatar_composition_vsk_source_has_hard_179_acceptance_contract(
    case: dict[str, str],
) -> None:
    source = PACKAGE_ROOT / case["slug"]
    manifest = _load_json(source / "manifest.json")
    workflow = _load_json(source / case["workflow"])
    skill = parse_skill_markdown(source / "SKILL.md")

    assert manifest["id"] == case["package_id"]
    assert manifest["min_vrcforge_version"] == "1.7.9"
    assert manifest["entrypoints"] == {
        "skill": "SKILL.md",
        "workflow": case["workflow"],
        "guide": "references/workflow.md",
    }
    assert set(skill["supportFiles"]) == {
        case["workflow"],
        "references/workflow.md",
    }
    assert workflow["schema"] == "vrcforge.skill-package.workflow.v1"
    assert workflow["mode"] == "approval_required"
    assert workflow["requires"] == {
        "minVRCForgeVersion": "1.7.9",
        "capabilities": [
            "capture_screenshot.angle.bottom",
            "causal_result_contract.v1",
        ],
        "onMissing": "blocked_not_ready",
    }
    cause_contract = workflow["causeContract"]
    assert cause_contract["callAndDomainStatusAreIndependent"] is True
    assert cause_contract["alwaysRequired"] == ["success", "status"]
    assert set(cause_contract["preserveWhenPresent"]) == PRESERVED_CAUSAL_FIELDS
    assert cause_contract["requiredWhenReadyFalse"] == ["ready", "blockingReasons"]
    assert cause_contract["blockedCauseRequired"] == {
        "anyOf": ["failureCause", "rootCause", "causeChain", "delta"],
        "onMissing": "block_and_report_contract_failure",
    }
    assert cause_contract["requiredForWrites"] == [
        "mutationStarted",
        "committed",
        "commitState",
        "sceneSaved",
        "persistedReadback",
    ]
    assert cause_contract["requiredForUnknownCommit"] == [
        "commitState",
        "persistedReadback",
        "recovery",
        "nextAction",
    ]
    assert cause_contract["unknownCommitAction"] == "read_back_before_retry"
    assert cause_contract["missingCauseAction"] == "block_and_report_contract_failure"
    assert workflow["approval"]["required"] is True
    assert workflow["checkpoint"]["required"] is True
    assert workflow["rollback"]["requiresSeparateApproval"] is True

    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source.rglob("*")
        if path.is_file()
    )
    assert "VRCForge 1.7.9" in public_text
    assert "ready=false" in public_text
    assert "codex://" not in public_text
    assert "D:\\" not in public_text
    assert "C:\\" not in public_text

    hidden_internal_tools = {
        "vrcforge_agent_message",
        "vrcforge_apply_approved",
        "vrcforge_ask_user",
        "vrcforge_request_apply",
    }
    assert hidden_internal_tools.isdisjoint(skill["allowedTools"])
    external_tools = {
        tool["name"]
        for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            "execution", tool_blocks=["*"]
        )
    }
    assert set(skill["allowedTools"]) <= external_tools
    write_steps = [step for step in workflow["steps"] if step["writes"]]
    assert write_steps
    assert all(step.get("runtimeApprovalRequired") is True for step in write_steps)
    workflow_tools = {
        tool
        for step in workflow["steps"]
        for tool in [step["tool"], *step.get("tools", [])]
    }
    assert workflow_tools <= set(skill["allowedTools"])


def test_head_vsk_has_two_branches_and_exact_static_dynamic_neck_views() -> None:
    workflow = _load_json(
        PACKAGE_ROOT
        / "vrcforge-avatar-head-transplant"
        / "workflows"
        / "avatar-head-transplant.json"
    )

    assert workflow["branchSelector"]["branches"] == ["gesture-only", "face-tracked"]
    assert workflow["branchSelector"]["faceTrackedRequires"] == [
        "mesh_blendshapes",
        "fx_and_gesture_animation",
        "expression_parameters",
        "expressions_menu",
    ]
    assert workflow["acceptanceViews"] == [
        {"angle": "front", "rotation": [0, 0, 0]},
        {"angle": "side_left", "rotation": [10, 90, 0]},
        {"angle": "side_right", "rotation": [10, -90, 0]},
        {"angle": "back", "rotation": [10, 180, 0]},
        {"angle": "bottom", "rotation": [-90, 0, 0]},
    ]
    assert {
        "static_pixels",
        "gesture_manager_motion",
        "neck_weighted_bone_target_readback",
        "dynamic_neck_head_inheritance",
    } == set(workflow["acceptanceRequires"])


def test_part_vsk_requires_target_dependency_source_and_motion_readback() -> None:
    workflow = _load_json(
        PACKAGE_ROOT
        / "vrcforge-avatar-part-transplant"
        / "workflows"
        / "avatar-part-transplant.json"
    )

    assert workflow["acceptanceViews"] == {
        "default": [
            {"angle": "front", "rotation": [0, 0, 0]},
            {"angle": "side_left", "rotation": [10, 90, 0]},
            {"angle": "side_right", "rotation": [10, -90, 0]},
            {"angle": "back", "rotation": [10, 180, 0]},
        ],
        "undersideDependent": {"angle": "bottom", "rotation": [-90, 0, 0]},
    }
    assert {
        "static_attachment_pixels",
        "gesture_manager_motion",
        "target_dependency_readback",
        "source_unchanged_readback",
    } == set(workflow["acceptanceRequires"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["slug"]))
def test_avatar_composition_vsk_signed_export_import_and_readback(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    source = PACKAGE_ROOT / case["slug"]
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="1.7.9")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        source,
        tmp_path / f"{case['slug']}.vsk",
        key_pair.private_key_pem,
    ).package_path

    untrusted = service.preflight_import(package).as_dict()
    assert untrusted["manifest"]["id"] == case["package_id"]
    assert untrusted["governance"]["signatureVerified"] is True
    assert untrusted["governance"]["signerTrustStatus"] == "untrusted"

    service.trust_signer(key_pair.fingerprint, reason="avatar workflow package test")
    trusted = service.preflight_import(package).as_dict()
    assert trusted["governance"]["signerTrustStatus"] == "trusted"
    assert trusted["governance"]["safeMode"]["defaultEnabled"] is True

    installed = service.install(package, source="avatar-workflow-blackbox-test")
    assert installed.registry_entry["enabled"] is True
    installed_manifest = _load_json(installed.installed_path / "manifest.json")
    installed_workflow = _load_json(installed.installed_path / case["workflow"])
    assert installed_manifest["id"] == case["package_id"]
    assert installed_manifest["min_vrcforge_version"] == "1.7.9"
    installed_cause_contract = installed_workflow["causeContract"]
    assert installed_cause_contract["alwaysRequired"] == ["success", "status"]
    assert set(installed_cause_contract["preserveWhenPresent"]) == PRESERVED_CAUSAL_FIELDS
    assert (installed.installed_path / "references" / "workflow.md").is_file()
    audit_events = [entry["event"] for entry in service.load_registry()["audit"]]
    assert "skill_package_signer_trusted" in audit_events
    assert "skill_package_imported" in audit_events

    older = SkillPackageService(tmp_path / "older", vrcforge_version="1.7.8")
    with pytest.raises(
        PackageCompatibilityError,
        match=r"requires VRCForge 1\.7\.9 or newer",
    ):
        older.preflight_import(package)
