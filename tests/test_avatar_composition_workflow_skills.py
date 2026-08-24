from __future__ import annotations

import dashboard_server
from agent_gateway import (
    EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS,
    canonical_unity_read_tool_input_schema,
    canonical_unity_write_tool_input_schema,
)
from avatar_composition_workflow_skills import (
    AVATAR_COMPOSITION_WORKFLOW_SKILL_NAMES,
    AVATAR_COMPOSITION_WORKFLOW_SKILLS,
)
from profiled_tool_registry import CapabilityProfile, ToolSet
from runtime_planner_service import bounded_planner_tool_schema


EXPECTED_NAMES = (
    "avatar-head-swap",
    "face-tracking-four-piece-merge",
    "original-avatar-part-extraction",
    "avatar-head-swap-face-tracked",
    "avatar-head-swap-gesture-only",
    "source-avatar-part-transplant",
)


def test_composition_workflows_are_six_compact_vrcforge_skills() -> None:
    assert AVATAR_COMPOSITION_WORKFLOW_SKILL_NAMES == EXPECTED_NAMES
    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        assert skill["category"] == "avatar-composition"
        assert skill["permissionMode"] == "approval_required"
        assert skill["problemBreakdown"]
        assert skill["steps"]
        assert skill["acceptance"]
        assert skill["pitfalls"]
        assert "checkpoint" in skill["backupRestore"].lower()
        assert "separately approved" in skill["backupRestore"].lower()
        assert len(skill["instructions"]) < 3_000
        assert skill["allowedTools"] == list(dict.fromkeys(skill["allowedTools"]))
        assert all(name.startswith("vrcforge_") for name in skill["allowedTools"])
        assert not {
            "vrcforge_agent_message",
            "vrcforge_apply_approved",
            "vrcforge_ask_user",
            "vrcforge_request_apply",
        }.intersection(skill["allowedTools"])


def test_composition_workflows_are_valid_internal_skills_and_share_only_external_unity_tools() -> None:
    internal_manifest = dashboard_server.AGENT_GATEWAY.build_manifest("execution")
    internal_skills = {
        skill["name"]: skill for skill in internal_manifest["skills"]
        if skill["name"] in EXPECTED_NAMES
    }
    assert set(internal_skills) == set(EXPECTED_NAMES)
    assert all(skill["available"] for skill in internal_skills.values())
    assert all(skill["validation"] == {"status": "ok", "reasons": []} for skill in internal_skills.values())

    external_names = {
        tool["name"]
        for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            "execution", tool_blocks=["*"]
        )
    }
    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        assert set(skill["allowedTools"]).issubset(external_names)


def test_composition_workflow_definitions_project_unchanged_into_internal_agent_skills() -> None:
    internal_manifest = dashboard_server.AGENT_GATEWAY.build_manifest("execution")
    internal_by_name = {
        skill["name"]: skill for skill in internal_manifest["skills"]
        if skill["name"] in EXPECTED_NAMES
    }
    projected_fields = (
        "name",
        "title",
        "description",
        "category",
        "permissionMode",
        "whenToUse",
        "inputs",
        "outputs",
        "sideEffects",
        "backupRestore",
        "allowedTools",
        "entrypointTool",
        "instructions",
    )

    assert set(internal_by_name) == set(EXPECTED_NAMES)
    for source in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        internal = internal_by_name[source["name"]]
        assert {
            field: internal[field] for field in projected_fields
        } == {
            field: source[field] for field in projected_fields
        }


def test_composition_workflow_atoms_have_internal_external_contract_parity() -> None:
    gateway = dashboard_server.AGENT_GATEWAY
    workflow_names = {
        tool_name
        for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
        for tool_name in skill["allowedTools"]
    }
    external_by_name = {
        tool["name"]: tool
        for tool in gateway.build_external_mcp_tools("execution", tool_blocks=["*"])
    }
    internal_by_name = {
        tool.runtime_name: tool
        for tool in dashboard_server._RuntimePlannerCatalog().read(
            "execution", project_context_active=True
        ).routable_tools
    }
    projections = {
        projection.internal_name: projection
        for projection in dashboard_server._RUNTIME_PROFILED_TOOL_REGISTRY.project(
            CapabilityProfile.UNITY_PROJECT
        )
    }

    assert workflow_names <= set(external_by_name)
    assert workflow_names <= set(internal_by_name)
    assert workflow_names <= set(projections)

    schema_mismatches: list[str] = []
    for name in sorted(workflow_names):
        external = external_by_name[name]
        internal = internal_by_name[name]
        projection = projections[name]
        is_write = bool(external.get("write"))

        assert internal.runtime_name == name
        assert internal.write is is_write
        if projection.tool_set is ToolSet.UNITY:
            assert internal.block == f"unity/{external['_meta']['toolBlock']}"

        if is_write:
            handler = gateway._write_handlers[name]
            canonical_schema = canonical_unity_write_tool_input_schema(name)
            assert name in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
            assert projection.handler is handler.handler
        else:
            tool = gateway._tools[name]
            canonical_schema = canonical_unity_read_tool_input_schema(name)
            assert projection.handler is tool.handler

        # Both model surfaces must be projections of one schema and one exact
        # handler. Handler identity also fixes the downstream Core atom and raw
        # result-normalization route instead of maintaining a second MCP path.
        external_schema = external["inputSchema"]
        assert external_schema == canonical_schema, name
        if internal.input_schema != bounded_planner_tool_schema(canonical_schema):
            schema_mismatches.append(name)

    assert schema_mismatches == []


def test_head_swap_branches_before_face_tracking_merge_and_removal_closes_references() -> None:
    by_name = {skill["name"]: skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS}
    head = by_name["avatar-head-swap"]
    assert "Detect face tracking first" in head["problemBreakdown"][0]
    assert any("face-tracking-four-piece-merge" in step["goal"] for step in head["steps"])
    neck_step = next(step["goal"] for step in head["steps"] if "cameraEvidence" in step["goal"])
    assert "GM Play Mode" in neck_step
    assert "Front (0,0,0)" in neck_step
    assert "hide hair/collar" in neck_step
    assert "Side Left (0,+90,0)" in neck_step
    assert "Side Right (0,-90,0)" in neck_step
    assert "Back (0,180,0)" in neck_step
    assert "true Bottom (-90,0,0)" in neck_step
    assert "full shoulder-neck visible" in neck_step
    neck_acceptance = " ".join(head["acceptance"])
    assert "open rim" in neck_acceptance
    assert "hollow/internal geometry" in neck_acceptance
    assert "backface" in neck_acceptance
    assert "overlap" in neck_acceptance
    assert "Back or Bottom is a hard failure" in neck_acceptance
    assert "Build & Test cannot override visible geometry" in neck_acceptance
    neck_pitfalls = " ".join(head["pitfalls"])
    assert "receipts prove requests, pixels prove geometry" in neck_pitfalls
    assert "unnamed capture can echo 0/0/0 without setting the view" in neck_pitfalls

    capture_angle_enum = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS[
        "vrcforge_capture_screenshot"
    ]["properties"]["angle"]["enum"]
    assert "back" in capture_angle_enum
    assert "bottom" in capture_angle_enum

    face = by_name["face-tracking-four-piece-merge"]
    assert "Mesh, FX, Parameters, and Menu" in face["problemBreakdown"][0]
    assert "gesture" in face["acceptance"][1].lower()
    assert "body" in face["acceptance"][2].lower()

    removal = by_name["original-avatar-part-extraction"]
    assert removal["entrypointTool"] == "vrcforge_scan_inbound_reference_closure"
    assert "vrcforge_inspect_skinned_mesh_bone_usage" in removal["allowedTools"]
    assert "PB root" in removal["acceptance"][1]


def test_head_swap_has_two_executable_branches_and_generic_part_transplant() -> None:
    by_name = {skill["name"]: skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS}

    dispatcher = by_name["avatar-head-swap"]
    dispatcher_text = " ".join(dispatcher["problemBreakdown"] + [
        step["goal"] for step in dispatcher["steps"]
    ])
    assert "avatar-head-swap-face-tracked" in dispatcher_text
    assert "avatar-head-swap-gesture-only" in dispatcher_text

    face_tracked = by_name["avatar-head-swap-face-tracked"]
    assert "face tracking" in face_tracked["whenToUse"].lower()
    assert "Mesh, FX, Parameters, and Menu" in " ".join(face_tracked["problemBreakdown"])
    assert {
        "vrcforge_manage_expression_parameters",
        "vrcforge_manage_expression_menu",
        "vrcforge_manage_fx_animator",
        "vrcforge_write_avatar_descriptor",
    }.issubset(face_tracked["allowedTools"])
    assert "gesture fallback" in " ".join(face_tracked["acceptance"]).lower()

    gesture_only = by_name["avatar-head-swap-gesture-only"]
    assert "without face tracking" in gesture_only["whenToUse"].lower()
    assert "vrcforge_manage_fx_animator" in gesture_only["allowedTools"]
    assert "vrcforge_manage_expression_parameters" not in gesture_only["allowedTools"]
    gesture_acceptance = " ".join(gesture_only["acceptance"]).lower()
    assert "left/right hand gestures" in gesture_acceptance
    assert "no face-tracking asset" in gesture_acceptance

    transplant = by_name["source-avatar-part-transplant"]
    assert transplant["entrypointTool"] == "vrcforge_scan_inbound_reference_closure"
    assert {
        "vrcforge_scan_inbound_reference_closure",
        "vrcforge_inspect_skinned_mesh_bone_usage",
        "vrcforge_preview_scene_object_duplicate",
        "vrcforge_duplicate_scene_object",
        "vrcforge_reparent_gameobject",
        "vrcforge_set_property",
        "vrcforge_preview_atomic_reference_rename",
        "vrcforge_atomic_reference_rename",
    }.issubset(transplant["allowedTools"])
    transplant_acceptance = " ".join(transplant["acceptance"]).lower()
    assert "source avatar remains unchanged" in transplant_acceptance
    assert "one target hierarchy" in transplant_acceptance
    assert "physbone" in transplant_acceptance
    assert "static and gm motion" in transplant_acceptance


def test_workflow_evidence_language_distinguishes_proof_plans_and_commits() -> None:
    by_name = {skill["name"]: skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS}
    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        workflow_text = " ".join(
            [*skill["problemBreakdown"], *skill["acceptance"], *skill["pitfalls"]]
        )
        assert "proposal/timeout is not commit proof" in workflow_text
        assert "mutationStarted" in workflow_text
        assert "committed" in workflow_text
        assert "sceneSaved" in workflow_text
        assert "persistedReadback" in workflow_text
        assert "per-target readback" in workflow_text
        assert "success=true/status=ok does not imply domain ready=true" in workflow_text
        assert "blockingReasons" in workflow_text
        assert "failureLayer/failurePhase/failureCause" in workflow_text
        assert "rootCause/causeChain" in workflow_text
        assert "observed/expected/delta" in workflow_text
        assert "commitState" in workflow_text
        assert "unknown commit blocks retry" in workflow_text
        assert "missing cause blocks diagnosis" in workflow_text

    face_tracked = by_name["avatar-head-swap-face-tracked"]
    face_text = " ".join(
        [*face_tracked["problemBreakdown"], *face_tracked["acceptance"], *face_tracked["pitfalls"]]
    )
    assert "Unity/GM proven" in face_text
    assert "earlier neck acceptance was a false positive" in face_text
    assert "merged GM/Build state" in face_text
    assert "gesture fallback" in face_text.lower()

    gesture_only = by_name["avatar-head-swap-gesture-only"]
    gesture_text = " ".join(
        [*gesture_only["problemBreakdown"], *gesture_only["acceptance"], *gesture_only["pitfalls"]]
    )
    assert "plan-derived/not yet E2E proven" in gesture_text
    assert "do not claim it is verified" in gesture_text


def test_part_transplant_copies_only_live_dependencies_and_requires_readback() -> None:
    transplant = next(
        skill
        for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
        if skill["name"] == "source-avatar-part-transplant"
    )
    text = " ".join(
        [
            *transplant["problemBreakdown"],
            *(step["goal"] for step in transplant["steps"]),
            *transplant["acceptance"],
            *transplant["pitfalls"],
        ]
    )
    assert "non-zero bone usage" in text
    assert "minimal chain" in text
    assert "all bones slots" in text
    assert "world/local transform" in text
    assert "preserveWorldTransform receipt" in text
    assert "SMR bones/rootBone" in text
    assert "PB/colliders/probeAnchor" in text
    assert "closure complete" in text
    assert "non-truncated" in text
    assert "donor" in text
    assert "Side Left (0,+90,0)" in text
    assert "Side Right (0,-90,0)" in text
    assert "Bottom (-90,0,0)" in text


def test_head_workflows_reject_static_only_neck_bone_proof() -> None:
    by_name = {skill["name"]: skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS}
    for name in (
        "avatar-head-swap",
        "avatar-head-swap-face-tracked",
        "avatar-head-swap-gesture-only",
    ):
        text = " ".join(
            [
                *(step["goal"] for step in by_name[name]["steps"]),
                *by_name[name]["acceptance"],
                *by_name[name]["pitfalls"],
            ]
        )
        assert "Neck/Head inheritance" in text
        assert "neck-weighted bone target" in text
        assert "GM motion" in text
        assert "Side Left (0,+90,0)" in text
        assert "Side Right (0,-90,0)" in text
        assert "Bottom (-90,0,0)" in text


def test_56_sol_contract_preserves_routes_skinning_sweep_and_capability_gap() -> None:
    from avatar_composition_workflow_skills import (
        FAILURE_ETIOLOGY_FIELDS,
        POSE_SWEEP,
        TRANSPLANT_MODES,
    )

    assert {mode["id"] for mode in TRANSPLANT_MODES} == {
        "rigid", "same_skeleton_smr", "foreign_skeleton_smr",
        "independent_skeleton_physbone", "animator_menu",
    }
    assert set(POSE_SWEEP) >= {
        "Rest", "AFK", "Upright", "VelocityZ", "AngularY",
        "body-size-min", "body-size-max", "face-tracking-min", "face-tracking-max",
    }
    required = {"renderer.bones", "renderer.rootBone", "renderer.bindposes", "allUsedBonesResolved", "allUsedBindposesResolved", "noOutOfRangeWeights", "mixedChainClosure", "safeForWeightedRemap"}
    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        assert required <= set(skill["skinningContract"]["required"])
        assert required <= set(skill["skinningContract"]["required"])
        assert {"success", "status", "error", "failedStep", "diagnostics", "capabilityGap", "needsDccRerig"} <= set(skill["failureEtiologyFields"])
        assert set(FAILURE_ETIOLOGY_FIELDS) <= set(skill["failureEtiologyFields"])
        assert skill["capabilityGapContract"]["onMissing"] == "blocked_not_ready"
        assert len(skill["pitfallMatrix"]) >= 5
        assert set(skill["geometryAlignmentContract"]["required"]) >= {"neckRingCenter", "neckRingNormal", "neckRingRadius", "pivot", "boneRoll"}
        assert skill["geometryAlignmentContract"]["onUnreconciled"]["needsDccRerig"] is True
        assert set(skill["rigAndShapeContract"]["required"]) >= {"bindposeReadback", "localWeightTransfer", "blendShapeOrder", "arkitNames"}
        assert skill["physboneAbContract"]["isolation"][-1] == "readback_restoration"


def test_transplants_gate_rest_delta_single_slot_remap_and_cat_ear_physbone_isolation() -> None:
    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        if skill["name"] not in {"avatar-head-swap", "source-avatar-part-transplant"}:
            continue
        assert skill["singleSlotRemapContract"]["capability"] == "vrcforge_remap_skinned_mesh_bone"
        assert skill["singleSlotRemapContract"]["bulkRemap"] == "forbidden"
        assert skill["singleSlotRemapContract"]["onMissing"]["capabilityGap"] is True
        assert skill["singleSlotRemapContract"]["onMissing"]["ready"] is False
        gate = skill["singleSlotRemapContract"]["predictedMetricGate"]
        assert set(gate["required"]) == {"current", "target", "donorChainBaseline"}
        assert "materially closer" in gate["executeOnlyWhen"]
        text = " ".join(step["goal"] for step in skill["steps"])
        assert "Rest skinning delta" in text
        assert "single-slot" in text
        assert "PhysBone A/B" in text
        assert "no paired morph" in text or "needsDccRerig" in text
        assert skill["dynamicAcceptance"]["unobstructed"] is True
        assert skill["dynamicAcceptance"]["orthogonalBasis"] is True
        assert skill["dynamicAcceptance"]["obliqueAuxiliaryViewsCountTowardsAcceptance"] is False
        assert skill["dynamicAcceptance"]["cameraEvidence"]
        assert skill["dynamicAcceptance"]["baselineContract"] == {
            "recordBeforePoseSweep": True,
            "readBackAfterEachPose": True,
            "restoreAfterSweep": True,
            "blockIfRestoreReadbackFails": True,
        }
        diagnostics = skill["catEarDeformationDiagnosticsContract"]
        assert diagnostics["tool"] == "vrcforge_inspect_skinned_mesh_deformation"
        assert diagnostics["diagnosticOnly"] is True
        assert diagnostics["perPose"] == ["Rest", "AFK", "HeadDown", "HeadTurnLeft", "HeadTurnRight"]
        assert diagnostics["sameCameraRestMotionPairs"] is True
        assert {"ear_root_shifts_from_head", "ear_root_gap_or_seam_changes_size_between_poses"} <= set(diagnostics["failIf"])


def test_part_transplant_routes_rigid_physbone_and_weighted_smr_without_cross_wiring() -> None:
    from avatar_composition_workflow_skills import TRANSPLANT_MODES

    part = next(
        skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
        if skill["name"] == "source-avatar-part-transplant"
    )
    modes = {mode["id"]: mode for mode in TRANSPLANT_MODES}

    assert set(modes) == {
        "rigid", "same_skeleton_smr", "foreign_skeleton_smr",
        "independent_skeleton_physbone", "animator_menu",
    }
    assert {"setup_outfit", "merge_armature"} <= set(modes["rigid"]["forbidden"])
    assert "MA Bone Proxy" in " ".join(modes["rigid"]["steps"])
    assert "local TRS" in " ".join(modes["rigid"]["steps"])
    assert "clothing or close-fitting weighted accessory" in modes["same_skeleton_smr"]["when"]
    assert "clothing or close-fitting weighted accessory" in modes["foreign_skeleton_smr"]["when"]
    assert "armature merge" in " ".join(modes["same_skeleton_smr"]["steps"])
    assert "armature merge" in " ".join(modes["foreign_skeleton_smr"]["steps"])

    physbone = part["partRoutingContract"]["independentPhysBoneAccessory"]
    assert physbone["defaultRootScale"] == [1.0, 1.0, 1.0]
    assert physbone["skinningPolicy"].startswith("preserve_internal_mesh_to_sway_chain_binding")
    assert {"completeBoundedBoneChain", "physBoneRootTransform", "consumedColliderReferences", "rendererUsedBoneClosure", "restAndPerPoseDeformationReadback"} <= set(physbone["required"])
    assert "uniform whole-prefab/container scale" in physbone["unitySizeFit"]
    assert physbone["deformationReadbackTool"] == "vrcforge_inspect_skinned_mesh_deformation"
    assert "stable_aabb_and_skin_matrix_metrics" in physbone["acceptance"]
    assert {"setup_outfit", "merge_armature", "partial_chain_copy"} <= set(physbone["forbidden"])
    assert "armature merge" in part["partRoutingContract"]["weightedSmr"]["requiredOperation"]

    rigid = part["partRoutingContract"]["rigidAccessory"]
    assert rigid["attachment"] == ["direct_reparent", "ma_bone_proxy"]
    assert set(rigid["requiredReadback"]) == {
        "targetBoneOrProxyTarget", "localPosition", "localRotation", "localScale",
    }
    assert {"vrcforge_preview_add_modular_avatar_component", "vrcforge_add_modular_avatar_component"} <= set(part["allowedTools"])
    assert "vrcforge_inspect_skinned_mesh_deformation" in part["allowedTools"]
    assert "vrcforge_setup_outfit" not in part["allowedTools"]
    remap_gate = next(step for step in part["steps"] if step.get("requiredAtomicCapability") == "vrcforge_remap_skinned_mesh_bone")
    assert remap_gate["conditionalRoutes"] == ["same_skeleton_smr", "foreign_skeleton_smr"]


def test_part_transplant_reports_external_preprocessing_and_atomic_step_causes() -> None:
    part = next(
        skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
        if skill["name"] == "source-avatar-part-transplant"
    )

    preprocessing = part["externalPreprocessingContract"]
    assert preprocessing["moduleCreator"]["internalAtomicCapability"] is None
    assert preprocessing["moduleCreator"]["whenRequired"] == {
        "capabilityGap": True,
        "ready": False,
        "failureCause": "module_creator_export_atom_unavailable",
    }
    assert set(preprocessing["dccEscalationOnlyWhen"]) == {
        "mesh_shape_or_mesh_level_size_requires_editing",
        "close_fitting_part_retains_donor_body_specific_weights",
    }
    assert "physbone_root_or_collider_reference_rebind" in preprocessing["notDccReasons"]

    result_contract = part["atomicCompositionResultContract"]
    assert result_contract["stopOnFailedStep"] is True
    assert result_contract["failedStepRequiredOnFailure"] is True
    assert result_contract["surfaces"] == ["internal_agent_loop", "external_mcp_agent"]
    assert result_contract["surfaceParityRequired"] is True
    assert {"step", "tool", "result", "success", "status", "error", "failureCause", "rootCause", "causeChain", "failedStep", "diagnostics"} <= set(result_contract["preserveEachStep"])
    assert result_contract["preserveNestedResultUnchanged"] is True
    assert set(result_contract["failureDiagnosisRequired"]["required"]) == {"success", "status", "failedStep"}


def test_head_branches_and_part_share_diagnostic_result_parity_contract() -> None:
    names = {
        "avatar-head-swap", "avatar-head-swap-face-tracked",
        "avatar-head-swap-gesture-only", "source-avatar-part-transplant",
    }
    for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        if skill["name"] not in names:
            continue
        result_contract = skill["atomicCompositionResultContract"]
        assert result_contract["surfaceParityRequired"] is True
        assert set(result_contract["surfaces"]) == {"internal_agent_loop", "external_mcp_agent"}
        assert {"success", "status", "error", "failureCause", "rootCause", "causeChain", "failedStep", "diagnostics"} <= set(result_contract["preserveEachStep"])
        assert "vrcforge_inspect_skinned_mesh_deformation" in skill["allowedTools"]
