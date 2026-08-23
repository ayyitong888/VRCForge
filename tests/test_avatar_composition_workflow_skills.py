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
        assert len(skill["instructions"]) < 2_500
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
    assert "face-tracking-four-piece-merge" in head["steps"][2]["goal"]
    neck_step = head["steps"][3]["goal"]
    assert "GM Play Mode" in neck_step
    assert "front 0/0/0" in neck_step
    assert "hide hair/collar" in neck_step
    assert "sides yaw +90/-90" in neck_step
    assert "back yaw 180" in neck_step
    assert "manual Bottom" in neck_step
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
    assert "bottom" not in capture_angle_enum

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
