from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import parse_skill_markdown
from skill_packages import (
    PackageCompatibilityError,
    PackageIntegrityError,
    SkillPackageService,
)


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
        "title": "VRChat 换头",
        "workflow": "workflows/avatar-head-transplant.json",
    },
    {
        "slug": "vrcforge-avatar-part-transplant",
        "package_id": "com.vrcforge.workflows.avatar_part_transplant",
        "title": "VRChat 配件移植",
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
    assert manifest["name"] == case["title"]
    assert manifest["min_vrcforge_version"] == "1.7.9"
    assert manifest["execution"] == "agentic"
    assert manifest["entrypoints"] == {
        "skill": "SKILL.md",
        "workflow": case["workflow"],
        "guide": "references/workflow.md",
    }
    assert set(skill["supportFiles"]) == {
        case["workflow"],
        "references/workflow.md",
    }
    assert skill["title"] == case["title"]
    assert workflow["schema"] == "vrcforge.skill-package.workflow.v1"
    assert workflow["mode"] == "approval_required"
    assert (source / case["workflow"]).read_text(encoding="utf-8").count(
        '"uncertaintyPolicy"'
    ) == 1
    assert workflow["uncertaintyPolicy"] == {
        "whenUnclear": [
            "skeleton",
            "neck_seam",
            "material",
            "accessory",
            "physbone",
            "modular_avatar",
            "vrcfury",
        ],
        "consult": [
            "mature_community_guides",
            "asset_author_instructions",
            "official_documentation",
        ],
        "onInsufficientEvidence": {
            "capabilityGap": True,
            "ready": False,
            "action": "stop_and_report",
        },
        "mustNot": ["guess", "invent_tools", "claim_unverified_support"],
    }
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


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["slug"]))
def test_avatar_composition_agentic_workflow_supports_contextual_supervised_choices(
    case: dict[str, str],
) -> None:
    source = PACKAGE_ROOT / case["slug"]
    manifest = _load_json(source / "manifest.json")
    workflow = _load_json(source / manifest["entrypoints"]["workflow"])
    skill = parse_skill_markdown(source / "SKILL.md")

    assert manifest["execution"] == "agentic"
    assert "executionPlan" not in manifest["entrypoints"]
    assert workflow["schema"] == "vrcforge.skill-package.workflow.v1"
    assert len(workflow["steps"]) >= 10
    assert all(step["tool"] in skill["allowedTools"] for step in workflow["steps"])
    assert any(step.get("tools") for step in workflow["steps"])
    writes = [step for step in workflow["steps"] if step["writes"]]
    assert writes
    assert all(step["runtimeApprovalRequired"] is True for step in writes)
    assert workflow["approval"]["required"] is True
    assert workflow["checkpoint"]["required"] is True
    assert workflow["rollback"]["requiresSeparateApproval"] is True
    assert len(skill["supportFiles"]) <= 16


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
    community = workflow["communityAvatarModificationContract"]
    assert "overrides generic matrix_bindpose_neck_ring diagnostic gates" in community["precedence"]
    assert community["matrixPolicy"]["default"] == "prohibited"
    assert community["matrixPolicy"]["requiresSeparateExplicitUserAuthorization"] is True
    assert community["matrixPolicy"]["neverCountsTowardVisualAcceptance"] is True
    assert community["visibleCandidateTimebox"]["minutesPerStep"] == 20
    assert community["visualAcceptance"]["normalViewingDistanceMeters"] == [1.0, 2.0]
    assert community["headSwap"]["classification"] == "full_head_transplant_not_rigid_or_independent_accessory_or_clothing_part_route"
    assert community["headSwap"]["method"] == "unity_first_modular_avatar_merge_armature_replace_object_and_layered_neck_seam_resolution"
    assert community["headSwap"]["visualAcceptance"]["poses"] == ["Rest", "AFK"]
    assert community["headSwap"]["capabilityBoundary"]["maReplaceObject"] == "manual_or_capability_gap_when_no_exact_atom"
    assert "verified_head_mesh_overlap" in community["headSwap"]["legacyConcealmentRequiresExplicitUserChoice"]
    community_step = next(
        step for step in workflow["steps"]
        if step["name"] == "community_unity_ma_merge_armature_replace_object_route"
    )
    assert "ma_replace_object_preserve_target_old_face_path" in community_step["sequence"]
    assert community_step["matrixAnalysis"] == "prohibited_without_separate_explicit_authorization"
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
        "active_head_renderer_viseme_lipsync_and_expected_binding_closure",
        "face_tracked_parameter_menu_controller_blendshape_closure_when_selected",
        "complete_pose_sweep",
        "unobstructed_five_views",
        "cat_ear_aabb_skin_matrix_diagnostics_when_present",
    } == set(workflow["acceptanceRequires"])

    closure = workflow["headRendererConsumerClosureContract"]
    assert closure["requiredBeforeOldHeadRemoval"] is True
    assert {
        "descriptorVisemeSkinnedMesh",
        "descriptorLipSync",
        "allExpectedVisemeBindings",
        "allExpectedExpressionBindings",
        "allExpectedGestureBindings",
    } <= set(closure["activeRenderer"]["requiredTargets"])
    assert closure["inactiveOrDonorRenderer"] == {
        "mayRemainDescriptorTarget": False,
        "mayRemainSoleAnimatorTarget": False,
        "maySatisfyExpectedBindingOnlyOnInactivePath": False,
    }
    assert closure["faceTrackedBranch"]["inactiveOrDonorOnlyConsumerCount"] == 0
    assert closure["failure"]["ready"] is False
    renderer_step = next(
        step for step in workflow["steps"]
        if step["name"] == "verify_active_head_renderer_consumer_closure"
    )
    assert {
        "descriptor_viseme_skinned_mesh_and_lipsync_target_active_renderer",
        "all_expected_viseme_gesture_expression_bindings_target_active_renderer",
        "no_inactive_or_donor_descriptor_target",
        "no_inactive_or_donor_sole_animator_target",
    } <= set(renderer_step["required"])
    assert "parameter_menu_controller_blendshape_closure" in renderer_step["faceTrackedRequired"]


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
    assert {
        "swayChainRoot_and_every_descendant_bone_Rest_localScale_one_readback",
        "same_framing_close_root_Rest_AFK_multiview_pairs",
    } <= set(isolation["preconditions"])
    assert {
        "descendantBoneRestLocalScales",
        "sameFramingRestAfkMultiViewEvidence",
    } <= set(isolation["requiredReadback"])
    assert "vrcforge_inspect_skinned_mesh_deformation" in isolation["tools"]

    diagnostics = workflow["catEarDeformationDiagnosticsContract"]
    assert diagnostics["route"] == "independent_skeleton_physbone"
    assert diagnostics["tool"] == "vrcforge_inspect_skinned_mesh_deformation"
    assert diagnostics["diagnosticOnly"] is True
    assert diagnostics["requiresSeparateExplicitUserAuthorization"] is True
    assert diagnostics["countsTowardVisualAcceptance"] is False
    assert diagnostics["attachment"]["rootLocalScale"] == [1.0, 1.0, 1.0]
    assert diagnostics["perPose"] == ["Rest", "AFK", "HeadDown", "HeadTurnLeft", "HeadTurnRight"]
    assert diagnostics["sameCameraRestMotionPairs"] is True
    assert {"rest.aabb", "world.aabb", "usedBoneReconstructedSkinMatrix"} <= set(diagnostics["restRequired"])
    assert {"ear_root_shifts_from_head", "ear_root_gap_or_seam_changes_size_between_poses"} <= set(diagnostics["failIf"])
    attachment = diagnostics["attachment"]
    assert attachment["targetBone"] == "Head"
    assert attachment["maBoneProxyTarget"] == "Head"
    assert attachment["directMountEquivalent"] == "installContainer_parent_is_target_avatar_Head"
    assert attachment["installContainer"]["uniformLocalScaleAllowed"] is True
    assert attachment["rootLocalScaleMeaning"] == "swayChainRoot_rest_scale_not_installContainer_scale"
    assert attachment["swayChainRoot"] == {
        "mustBeDistinctFromInstallContainer": True,
        "localScale": [1.0, 1.0, 1.0],
        "descendantBoneRestLocalScale": [1.0, 1.0, 1.0],
        "requireEveryDescendantBoneRestScaleOne": True,
        "preserveInternalLocalTrs": True,
    }
    assert "descendantBoneRestLocalScales" in attachment["requiredReadback"]
    assert diagnostics["sameFramingRestAfkMultiViewPairs"] is True
    closure = diagnostics["externalReferenceClosure"]
    assert {
        "physBoneRootTransform",
        "physBoneColliderReferences",
        "probeAnchorReferences",
        "constraintSourceReferences",
        "contactReferences",
        "allExternalObjectReferences",
    } <= set(closure["required"])
    assert closure["sourceAvatarReferencesRemaining"] == 0
    assert closure["unresolvedReferences"] == 0
    assert closure["scanMustBeCompleteAndNonTruncated"] is True

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

    community = workflow["communityAvatarModificationContract"]
    assert "overrides generic matrix_bindpose_neck_ring diagnostic gates" in community["precedence"]
    assert community["matrixPolicy"]["default"] == "prohibited"
    assert community["matrixPolicy"]["requiresSeparateExplicitUserAuthorization"] is True
    assert community["visibleCandidateTimebox"]["minutesPerStep"] == 20
    assert community["visualAcceptance"]["normalViewingDistanceMeters"] == [1.0, 2.0]
    assert "ma_setup_outfit" in community["clothing"]["compatible"]
    assert "external_mochi_fitter" in community["clothing"]["nonCompatiblePreferred"]
    assert community["rigidAccessory"]["visualAcceptanceMinimumViews"] == 2
    assert community["independentPhysBoneAccessory"]["classification"] == "accessory_not_head_swap"
    unity_first = workflow["unityFirstExtractionContract"]
    assert unity_first["sourceLicenseRequired"] is True
    assert unity_first["defaultEnvironment"] == "existing_unity_scene"
    assert unity_first["blenderIsPrerequisite"] is False
    assert unity_first["boneProxyModes"]["preserveExistingFit"] == "as_child_keep_world_pose"
    assert unity_first["physbone"]["preserveUserAdjustedMeshOutline"] is True
    clothing_step = next(
        step for step in workflow["steps"]
        if step["name"] == "community_clothing_setup_outfit_route"
    )
    assert clothing_step["conditionalRoutes"] == ["same_skeleton_smr", "foreign_skeleton_smr"]

    nodes = routing["independentPhysBoneAccessory"]["nodeContract"]
    assert set(nodes) == {"installContainer", "swayChainRoot"}
    assert nodes["installContainer"]["uniformLocalScaleAllowed"] is True
    assert nodes["swayChainRoot"]["mustBeDistinctFromInstallContainer"] is True
    assert nodes["swayChainRoot"]["localScale"] == [1.0, 1.0, 1.0]
    assert nodes["swayChainRoot"]["descendantBoneRestLocalScale"] == [1.0, 1.0, 1.0]
    assert nodes["swayChainRoot"]["requireEveryDescendantBoneRestScaleOne"] is True
    assert routing["independentPhysBoneAccessory"]["attachmentTargetByClass"]["cat_ears"] == {
        "maBoneProxyTarget": "Head",
        "directMountEquivalent": "installContainer_parent_is_target_avatar_Head",
        "targetIsFixed": True,
    }
    assert "nodeContract" not in routing["rigidAccessory"]
    assert "attachmentTargetByClass" not in routing["weightedSmr"]

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
    assert {
        "install_container_uniform_scale_readback",
        "sway_chain_root_local_scale_one_readback",
        "every_descendant_bone_Rest_local_scale_one_readback",
        "probe_anchor_references",
        "constraint_source_references",
        "contact_references",
        "all_external_object_references_closed",
    } <= set(physbone_step["required"])
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


def test_head_package_documents_unity_first_community_route_and_truthful_neck_seam_atoms() -> None:
    package = PACKAGE_ROOT / "vrcforge-avatar-head-transplant"
    workflow = _load_json(package / "workflows/avatar-head-transplant.json")
    contract = workflow["neckSeamResolutionContract"]

    assert contract["defaultRoute"] == "unity_first_modular_avatar_head_swap"
    assert contract["bodyBaseline"]["mustNotSubstitute"] == [
        "assumed_official_default", "third_party_body_texture", "historically_rejected_body_candidate",
    ]
    assert contract["localizedTexture"]["authoringAtomAvailable"] is False
    assert contract["localizedTexture"]["onMissingAuthoringAtom"]["failureCause"] == (
        "seam_authoring_atom_unavailable"
    )
    assert {"mouth", "nose", "eyes", "heterochromia", "alpha"} <= set(
        contract["localizedTexture"]["protect"]
    )
    assert contract["localizedTexture"]["assignmentAtoms"] == [
        "vrcforge_preview_material_texture_assignment", "vrcforge_set_material_texture",
    ]

    scan = next(step for step in workflow["steps"] if step["name"] == (
        "scan_visible_head_body_materials_and_classify_neck_seam"
    ))
    assert scan["requiresToolBlock"] == "materials"
    assert scan["writes"] is False
    material_write = next(step for step in workflow["steps"] if step["name"] == (
        "apply_approved_unity_material_template_or_local_head_texture"
    ))
    assert material_write["runtimeApprovalRequired"] is True
    assert material_write["whenExternalUvAuthoringRequired"]["ready"] is False
    sdk_alerts = next(step for step in workflow["steps"] if step["name"] == (
        "verify_texture_import_and_sdk_alerts_before_local_build"
    ))
    assert sdk_alerts["writes"] is False
    assert "vrcforge_read_vrchat_sdk_builder_alerts" in sdk_alerts["tools"]
    importer_fix = next(step for step in workflow["steps"] if step["name"] == (
        "approve_exact_texture_import_fix_only_when_sdk_blocks"
    ))
    assert importer_fix["runtimeApprovalRequired"] is True

    guide = (package / "references/workflow.md").read_text(encoding="utf-8")
    assert "Unity 直接换头" in guide
    assert "MA Replace Object" in guide
    assert "目标 Avatar 的旧脸 renderer" in guide
    assert "Modular Avatar Setup Outfit" in guide
    assert "seam_authoring_atom_unavailable" in guide

    part_guide = (
        PACKAGE_ROOT / "vrcforge-avatar-part-transplant" / "references/workflow.md"
    ).read_text(encoding="utf-8")
    assert "Unity 直接提取和移植配件" in part_guide
    assert "As child keep world pose" in part_guide
    assert "用户实际调好的 mesh 位置" in part_guide


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
    assert untrusted["manifest"]["execution"] == "agentic"
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
    assert installed_manifest["execution"] == "agentic"
    assert installed.registry_entry["execution"] == "agentic"
    assert installed.registry_entry.get("runtimeEnforced") is not True
    assert "workflowSteps" not in installed.registry_entry
    installed_cause_contract = installed_workflow["causeContract"]
    assert installed_cause_contract["alwaysRequired"] == ["success", "status"]
    assert set(installed_cause_contract["preserveWhenPresent"]) == PRESERVED_CAUSAL_FIELDS
    assert (installed.installed_path / "references" / "workflow.md").is_file()
    audit_events = [entry["event"] for entry in service.load_registry()["audit"]]
    assert "skill_package_signer_trusted" in audit_events
    assert "skill_package_imported" in audit_events

    tampered = tmp_path / f"{case['slug']}-tampered.vsk"
    with zipfile.ZipFile(package, "r") as source_archive:
        payloads = {
            item.filename: source_archive.read(item)
            for item in source_archive.infolist()
            if not item.is_dir()
        }
    tampered_workflow = json.loads(payloads[case["workflow"]])
    tampered_workflow["steps"] = list(reversed(tampered_workflow["steps"]))
    payloads[case["workflow"]] = json.dumps(
        tampered_workflow, ensure_ascii=False
    ).encode("utf-8")
    with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    with pytest.raises(PackageIntegrityError, match="SHA-256 mismatch"):
        service.inspect_package(tampered)

    older = SkillPackageService(tmp_path / "older", vrcforge_version="1.7.8")
    with pytest.raises(
        PackageCompatibilityError,
        match=r"requires VRCForge 1\.7\.9 or newer",
    ):
        older.preflight_import(package)
