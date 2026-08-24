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
    "error",
    "failedStep",
    "diagnostics",
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
            "inspect_skinned_mesh_deformation.v1",
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
        {"angle": "side_left", "rotation": [0, 90, 0]},
        {"angle": "side_right", "rotation": [0, -90, 0]},
        {"angle": "back", "rotation": [0, 180, 0]},
        {"angle": "bottom", "rotation": [-90, 0, 0]},
    ]
    assert {
        "static_pixels",
        "gesture_manager_motion",
        "neck_weighted_bone_target_readback",
        "dynamic_neck_head_inheritance",
        "complete_pose_sweep",
        "unobstructed_five_views",
        "cat_ear_aabb_skin_matrix_diagnostics_when_present",
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
            {"angle": "side_left", "rotation": [0, 90, 0]},
            {"angle": "side_right", "rotation": [0, -90, 0]},
            {"angle": "back", "rotation": [0, 180, 0]},
        ],
        "undersideDependent": {"angle": "bottom", "rotation": [-90, 0, 0]},
    }
    assert {
        "static_attachment_pixels",
        "gesture_manager_motion",
        "target_dependency_readback",
        "source_unchanged_readback",
        "complete_pose_sweep",
        "unobstructed_five_views",
        "cat_ear_aabb_skin_matrix_diagnostics_when_present",
    } == set(workflow["acceptanceRequires"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["slug"]))
def test_56_sol_packages_require_projection_skinning_and_capability_gap_contract(
    case: dict[str, str],
) -> None:
    workflow = _load_json(PACKAGE_ROOT / case["slug"] / case["workflow"])
    assert {mode["id"] for mode in workflow["transplantModes"]} == {
        "rigid", "same_skeleton_smr", "foreign_skeleton_smr",
        "independent_skeleton_physbone", "animator_menu",
    }
    assert workflow["capabilityGapContract"]["onMissing"] == "blocked_not_ready"
    assert set(workflow["capabilityGapContract"]["missingFields"]) == {
        "capabilityGap", "needsDccRerig",
    }
    assert set(workflow["skinningContract"]["required"]) >= {
        "renderer.bones", "renderer.rootBone", "renderer.bindposes",
        "allUsedBonesResolved", "allUsedBindposesResolved", "noOutOfRangeWeights", "mixedChainClosure", "safeForWeightedRemap",
    }
    assert set(workflow["geometryAlignmentContract"]["required"]) >= {"neckRingCenter", "neckRingNormal", "neckRingRadius", "pivot", "boneRoll"}
    assert workflow["geometryAlignmentContract"]["allowedMutation"] == "imported_part_local_transform_only"
    assert workflow["geometryAlignmentContract"]["onUnreconciled"]["needsDccRerig"] is True
    assert set(workflow["rigAndShapeContract"]["required"]) >= {"bindposeReadback", "localWeightTransfer", "blendShapeOrder", "arkitNames"}
    assert workflow["physboneAbContract"]["restoreFailure"]["ready"] is False
    assert set(workflow["skinningContract"]["informational"]) >= {"nullBoneCount", "unusedBoundBoneCount"}
    assert workflow["poseSweep"][:5] == ["Rest", "AFK", "Upright", "VelocityZ", "AngularY"]
    assert len(workflow["pitfallMatrix"]) == 5
    assert {
        "symptom", "rootCause", "check", "forbidden", "reversibleFix", "dynamicAcceptance",
    } <= set(workflow["pitfallMatrix"][0])
    assert workflow["acceptanceViewContract"] == {
        "count": 5,
        "requiredAngles": ["front", "side_left", "side_right", "back", "bottom"],
        "unobstructed": True,
        "requiresReturnedRotation": True,
        "basis": "orthogonal",
        "obliqueViewsCountTowardsAcceptance": False,
    }
    views = workflow["acceptanceViews"]
    if isinstance(views, list):
        assert len(views) == 5
    else:
        assert len(views["default"]) == 4
        assert views["undersideDependent"]["angle"] == "bottom"
    assert set(workflow["buildProjection"]["order"]) == {
        "visual_and_motion_acceptance", "build_test_readiness", "build_test_avatar",
    }
    assert {"success", "status", "error", "failedStep", "diagnostics", "failureLayer", "failurePhase", "failureCause", "rootCause", "causeChain", "observed", "expected", "delta", "capabilityGap", "needsDccRerig"} <= set(workflow["failureEtiologyFields"])
    assert workflow["singleSlotRemapContract"]["capability"] == "vrcforge_remap_skinned_mesh_bone"
    assert workflow["singleSlotRemapContract"]["bulkRemap"] == "forbidden"
    assert workflow["singleSlotRemapContract"]["onMissing"]["ready"] is False
    assert set(workflow["singleSlotRemapContract"]["predictedMetricGate"]["required"]) == {"current", "target", "donorChainBaseline"}
    assert "materially closer" in workflow["singleSlotRemapContract"]["predictedMetricGate"]["executeOnlyWhen"]
    assert workflow["dynamicAcceptance"]["poses"] == ["Rest", "AFK", "Upright", "VelocityZ", "AngularY"]
    assert workflow["dynamicAcceptance"]["unobstructed"] is True
    assert workflow["dynamicAcceptance"]["orthogonalBasis"] is True
    assert workflow["dynamicAcceptance"]["obliqueAuxiliaryViewsCountTowardsAcceptance"] is False
    assert set(workflow["dynamicAcceptance"]["cameraEvidence"]) >= {"position", "target", "basis", "quaternion", "projection", "matrix"}
    assert workflow["dynamicAcceptance"]["baselineContract"] == {
        "recordBeforePoseSweep": True,
        "readBackAfterEachPose": True,
        "restoreAfterSweep": True,
        "blockIfRestoreReadbackFails": True,
    }
    assert {"complete_pose_sweep", "unobstructed_five_views"} <= set(workflow["acceptanceRequires"])
    isolation = next(step for step in workflow["steps"] if step["name"] == "cat_ear_physbone_ab_isolation")
    assert isolation["conditional"] == "cat_ears"
    assert isolation["sequence"] == [
        "verify_head_attachment_chain_root_scale_and_explicit_references",
        "record_rest_aabb_and_skin_matrix_baseline",
        "disable_physbone_a_b",
        "read_root_bindpose_scale_rest_delta",
        "restore_previous_enabled_state",
    ]
    assert "vrcforge_inspect_skinned_mesh_deformation" in isolation["tools"]

    diagnostics = workflow["catEarDeformationDiagnosticsContract"]
    assert diagnostics["route"] == "independent_skeleton_physbone"
    assert diagnostics["tool"] == "vrcforge_inspect_skinned_mesh_deformation"
    assert diagnostics["diagnosticOnly"] is True
    assert diagnostics["attachment"]["rootLocalScale"] == [1.0, 1.0, 1.0]
    assert diagnostics["perPose"] == ["Rest", "AFK", "HeadDown", "HeadTurnLeft", "HeadTurnRight"]
    assert diagnostics["sameCameraRestMotionPairs"] is True
    assert {"rest.aabb", "world.aabb", "usedBoneReconstructedSkinMatrix"} <= set(diagnostics["restRequired"])
    assert {"ear_root_shifts_from_head", "ear_root_gap_or_seam_changes_size_between_poses"} <= set(diagnostics["failIf"])

    result_contract = workflow["atomicCompositionResultContract"]
    assert result_contract["surfaces"] == ["internal_agent_loop", "external_mcp_agent"]
    assert result_contract["surfaceParityRequired"] is True
    assert {"success", "status", "error", "failureCause", "rootCause", "causeChain", "failedStep", "diagnostics"} <= set(result_contract["preserveEachStep"])
    assert set(result_contract["failureDiagnosisRequired"]["required"]) == {"success", "status", "failedStep"}


def test_part_package_routes_rigid_physbone_and_weighted_smr_with_truthful_gaps() -> None:
    package = PACKAGE_ROOT / "vrcforge-avatar-part-transplant"
    workflow = _load_json(package / "workflows/avatar-part-transplant.json")
    modes = {mode["id"]: mode for mode in workflow["transplantModes"]}

    assert set(modes) == {
        "rigid", "same_skeleton_smr", "foreign_skeleton_smr",
        "independent_skeleton_physbone", "animator_menu",
    }
    assert {"setup_outfit", "merge_armature"} <= set(modes["rigid"]["forbidden"])
    assert "target_bone_parent_or_ma_bone_proxy" in modes["rigid"]["steps"]
    assert modes["same_skeleton_smr"]["when"].startswith("clothing_or_weighted_accessory")
    assert modes["foreign_skeleton_smr"]["when"].startswith("clothing_or_weighted_accessory")

    routing = workflow["partRoutingContract"]
    assert routing["classifyBeforeWrite"] is True
    assert routing["rigidAccessory"]["attachment"] == ["direct_reparent", "ma_bone_proxy"]
    assert routing["independentPhysBoneAccessory"]["defaultRootScale"] == [1.0, 1.0, 1.0]
    assert routing["independentPhysBoneAccessory"]["skinningPolicy"].startswith("preserve_internal_mesh_to_sway_chain_binding")
    assert {"completeBoundedBoneChain", "physBoneRootTransform", "consumedColliderReferences", "rendererUsedBoneClosure", "restAndPerPoseDeformationReadback"} <= set(routing["independentPhysBoneAccessory"]["required"])
    assert "uniform" in routing["independentPhysBoneAccessory"]["unitySizeFit"]
    assert routing["independentPhysBoneAccessory"]["deformationReadbackTool"] == "vrcforge_inspect_skinned_mesh_deformation"
    assert routing["weightedSmr"]["routes"] == ["same_skeleton_smr", "foreign_skeleton_smr"]
    assert "armature merge" in routing["weightedSmr"]["requiredOperation"]

    external = workflow["externalPreprocessingContract"]
    assert external["moduleCreator"]["internalAtomicCapability"] is None
    assert external["moduleCreator"]["whenRequired"] == {
        "capabilityGap": True,
        "ready": False,
        "failureCause": "module_creator_export_atom_unavailable",
    }
    assert set(external["dccEscalationOnlyWhen"]) == {
        "mesh_shape_or_mesh_level_size_requires_editing",
        "close_fitting_part_retains_donor_body_specific_weights",
    }

    composed = workflow["atomicCompositionResultContract"]
    assert composed["stopOnFailedStep"] is True
    assert composed["failedStepRequiredOnFailure"] is True
    assert {"step", "tool", "result", "success", "status", "error", "failureCause", "rootCause", "causeChain", "failedStep", "diagnostics"} <= set(composed["preserveEachStep"])
    assert composed["preserveNestedResultUnchanged"] is True

    physbone_step = next(step for step in workflow["steps"] if step["name"] == "rebind_independent_physbone_accessory")
    assert physbone_step["conditional"] == "independent_skeleton_physbone"
    assert {"root_scale_one", "physbone_root_transform", "consumed_collider_references", "rest_aabb_skin_matrix_baseline"} <= set(physbone_step["required"])
    remap_gate = next(step for step in workflow["steps"] if step["name"] == "read_rest_delta_and_gate_single_slot_remap")
    assert remap_gate["conditionalRoutes"] == ["same_skeleton_smr", "foreign_skeleton_smr"]
    toggle_step = next(step for step in workflow["steps"] if step["name"] == "optional_accessory_toggle")
    assert toggle_step["acceptance"] == ["off_state_readback_and_pixels", "on_state_readback_and_pixels"]

    public_text = "\n".join(
        (package / relative).read_text(encoding="utf-8")
        for relative in ("SKILL.md", "references/workflow.md")
    )
    assert "Never run Setup Outfit or Merge Armature" in public_text
    assert "Module Creator" in public_text
    assert "scale `(1,1,1)`" in public_text
    assert "failedStep" in public_text
    assert "head-down" in public_text
    assert "skin-matrix" in public_text


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
