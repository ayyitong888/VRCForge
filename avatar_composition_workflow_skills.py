"""Compact VRCForge Skill definitions for avatar-composition work.

These definitions are orchestration hints, not workflow handlers.  Internal
Agentic mode loads them as ``vrcforge.skill.v1`` Skills; the external MCP tree
projects the same definitions without exposing the internal Agent loop.
"""

from __future__ import annotations

from typing import Any


AVATAR_COMPOSITION_WORKFLOW_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "name": "avatar-head-swap",
        "title": "Avatar Head Swap",
        "description": "Fit one source head to a target body while preserving one coherent avatar hierarchy.",
        "category": "avatar-composition",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "swap or transplant a head, then fit ears, tail, neck, bones, PhysBones, and colliders to the final proportions",
        "whenNotToUse": "do not use for a face-only BlendShape adjustment or an outfit-only install",
        "inputs": ["Source head path, target avatar path, and intended retained body/head features."],
        "outputs": ["One fitted head/body hierarchy plus explicit static and dynamic acceptance evidence."],
        "sideEffects": "can duplicate, reparent, resize, disable, or remove scene objects after the owning Agent authorizes each write",
        "backupRestore": "Checkpoints precede common duplicate/reparent writes and replacing or removing the old head; every restore is separately approved.",
        "entrypointTool": "vrcforge_read_avatar_descriptor",
        "toolBlocks": ["core", "avatar", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Detect face tracking first; route to avatar-head-swap-face-tracked or avatar-head-swap-gesture-only before copying assets.",
            "Fit head/neck, then fit ears to Head and tail to Hips without scaling the body or its clothes.",
            "Prove one hierarchy, one motion chain, clean seams, and future-clothes clearance.",
        ],
        "steps": [
            {
                "goal": "Inventory both avatars and decide the face-tracking branch.",
                "tools": [
                    "vrcforge_list_avatars", "vrcforge_read_avatar_descriptor",
                    "vrcforge_scan_blendshapes", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_parameters", "vrcforge_scan_fx_animator",
                ],
            },
            {
                "goal": "Duplicate/reparent the source head, fit local transform and accessory scale, then bind ears/tail and their PB/colliders to final bones.",
                "tools": [
                    "vrcforge_get_gameobject", "vrcforge_get_property",
                    "vrcforge_preview_scene_object_duplicate", "vrcforge_duplicate_scene_object",
                    "vrcforge_reparent_gameobject", "vrcforge_set_property",
                    "vrcforge_set_gameobject_active",
                ],
            },
            {
                "goal": "Execute exactly one branch: avatar-head-swap-face-tracked applies face-tracking-four-piece-merge; avatar-head-swap-gesture-only skips face assets.",
                "tools": [
                    "vrcforge_scan_animation_bindings", "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_scan_inbound_reference_closure",
                ],
            },
            {
                "goal": "In GM Play Mode, hide hair/collar; capture named front 0/0/0, sides yaw +90/-90°, back yaw 180°, plus manual Bottom with the full shoulder-neck visible; repeat during motion, face tracking, and gestures.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "Exactly one intended head is active; head, body, ears, and tail move as one avatar.",
            "Head-to-body proportions read as intentional anime proportions; ears match head size and tail matches body scale.",
            "Any listed defect seen in Back or Bottom is a hard failure: open rim, hollow/internal geometry, backface, overlap, or normal/shading/color break. Motion/face/gestures must not open it; Build & Test cannot override visible geometry.",
            "PB roots/colliders resolve to final bones and remain stable under several GM parameters, not only idle.",
        ],
        "pitfalls": [
            "Do not scale the target body to solve a donor-head mismatch; fit the imported head and accessories locally.",
            "Transforms, Build, labels, and receipts do not prove closure: receipts prove requests, pixels prove geometry; unnamed capture can echo 0/0/0 without setting the view.",
            "Do not delete the old head or accessories until inbound references and weighted-bone use are closed.",
        ],
    },
    {
        "name": "face-tracking-four-piece-merge",
        "title": "Face Tracking Four-Piece Merge",
        "description": "Merge the face Mesh, FX, Expression Parameters, and Menu as one contract while retaining no-face-tracking gestures.",
        "category": "avatar-composition",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "the retained head has face tracking or a head swap must preserve face tracking and ordinary hand-gesture expressions",
        "whenNotToUse": "do not use when the retained head has no face-tracking assets; keep its compatible gesture FX instead",
        "inputs": ["Final avatar path, retained face mesh path, source face-tracking assets, and body controls that must remain."],
        "outputs": ["One descriptor whose Mesh, FX, Parameters, and Menu agree, with gesture fallback and body controls retained."],
        "sideEffects": "can update descriptor, animation bindings, FX, expression parameters, and expression menus after authorization",
        "backupRestore": "Checkpoints precede animation-path remap and merged FX/Parameters/Menu/descriptor writes; every restore is separately approved.",
        "entrypointTool": "vrcforge_read_avatar_descriptor",
        "toolBlocks": ["core", "avatar", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Treat Mesh, FX, Parameters, and Menu as an inseparable four-part contract.",
            "Merge by references; do not replace target-authored body tuning, wardrobe controls, or donor gesture fallback wholesale.",
            "Use the final merged GM/Build state, not source-asset counts, and retain gesture fallback.",
        ],
        "steps": [
            {
                "goal": "Inventory retained mesh BlendShapes and every source/final FX, parameter, menu, and animation reference.",
                "tools": [
                    "vrcforge_read_avatar_descriptor", "vrcforge_get_property",
                    "vrcforge_scan_blendshapes", "vrcforge_scan_parameters",
                    "vrcforge_scan_avatar_controls", "vrcforge_scan_fx_animator",
                    "vrcforge_scan_animation_bindings",
                ],
            },
            {
                "goal": "Map animation curve paths/BlendShape names to the retained mesh before merging assets.",
                "tools": [
                    "vrcforge_preview_atomic_reference_rename", "vrcforge_atomic_reference_rename",
                    "vrcforge_preview_write_animation_curve", "vrcforge_write_animation_curve",
                ],
            },
            {
                "goal": "Merge Parameters, Menu, and FX; retain ordinary donor gesture layers plus target-authored body sizing and wardrobe controls.",
                "tools": [
                    "vrcforge_preview_manage_expression_parameters", "vrcforge_manage_expression_parameters",
                    "vrcforge_preview_manage_expression_menu", "vrcforge_manage_expression_menu",
                    "vrcforge_preview_manage_fx_animator", "vrcforge_manage_fx_animator",
                    "vrcforge_preview_write_avatar_descriptor", "vrcforge_write_avatar_descriptor",
                ],
            },
            {
                "goal": "Exercise face, gesture fallback, body, and wardrobe in GM; validate the final merged GM/Build state.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                    "vrcforge_build_test_readiness", "vrcforge_build_test_avatar",
                ],
            },
        ],
        "acceptance": [
            "Descriptor points to the intended final FX, Parameters, and Menu; all face curves resolve to the retained mesh.",
            "In the final merged GM/Build state, face tracking works, ordinary left/right gesture fallback works without face tracking, and no target-body clip incorrectly drives the retained donor face mesh.",
            "Target-authored body adjustments and the default/future wardrobe controls remain reachable and animated.",
            "Final synced parameter cost is measured from the merged descriptor and stays within 256 bits; no orphan menu or FX parameter remains.",
        ],
        "pitfalls": [
            "Do not report only the small hand-authored Parameters asset; include all final merged contributors.",
            "Do not copy Parameters without matching Menu and FX references, or copy FX without the exact retained mesh paths/BlendShapes.",
            "Do not delete gesture FX merely because face tracking is present; it is the no-face-tracking compatibility path.",
        ],
    },
    {
        "name": "original-avatar-part-extraction",
        "title": "Original Avatar Part Extraction",
        "description": "Extract or remove obsolete original parts only after closing object, bone, PhysBone, collider, animation, FX, menu, and parameter references.",
        "category": "avatar-composition",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "remove or extract an original head, hair, ears, tail, audio cable, accessory, mesh, or supporting bone/component from the final avatar",
        "whenNotToUse": "do not use merely because an object is currently hidden or absent from one camera angle",
        "inputs": ["Candidate object path and the final avatar path that must remain intact."],
        "outputs": ["A reference-closure decision and, if safe, a minimal removal with post-removal proof."],
        "sideEffects": "can disable/delete objects, remove components, and repair exact references after authorization",
        "backupRestore": "Checkpoints precede reference repair and again precede exact disable/delete after closure proof; every restore is separately approved.",
        "entrypointTool": "vrcforge_scan_inbound_reference_closure",
        "toolBlocks": ["core", "avatar", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Hidden is not unused: close every inbound reference before removal.",
            "Separate zero-weight bone slots from live weights, and inspect PB roots/colliders independently.",
            "Repair only proven consumers, then remove the smallest exact target.",
        ],
        "steps": [
            {
                "goal": "Inventory the candidate, inbound closure, weighted-bone use, animation curves, controls, parameters, and FX consumers.",
                "tools": [
                    "vrcforge_get_gameobject", "vrcforge_get_property",
                    "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_scan_animation_bindings", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_parameters", "vrcforge_scan_fx_animator",
                ],
            },
            {
                "goal": "Redirect only proven live bindings or remove exact stale FX/menu/parameter/component references.",
                "tools": [
                    "vrcforge_preview_atomic_reference_rename", "vrcforge_atomic_reference_rename",
                    "vrcforge_preview_manage_expression_parameters", "vrcforge_manage_expression_parameters",
                    "vrcforge_preview_manage_expression_menu", "vrcforge_manage_expression_menu",
                    "vrcforge_preview_manage_fx_animator", "vrcforge_manage_fx_animator",
                    "vrcforge_set_property", "vrcforge_remove_component",
                ],
            },
            {
                "goal": "Disable first when useful for visual proof; delete only the exact candidate after closure is empty.",
                "tools": ["vrcforge_set_gameobject_active", "vrcforge_delete_gameobject"],
            },
            {
                "goal": "Rescan closure and exercise the remaining avatar in GM Scene view.",
                "tools": [
                    "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "The exact removed target is absent; unrelated source assets and final-avatar parts remain.",
            "Inbound closure is empty or every retained reference is explicitly justified; no live weighted bone, PB root, or collider points to the removed branch.",
            "FX/Menu/Parameters contain no stale consumer, and GM gestures/body/wardrobe/face tracking still behave.",
            "Scene-view motion shows no detached mesh, frozen accessory, missing deformation, seam, or new clipping.",
        ],
        "pitfalls": [
            "Do not infer safety from active=false, zero visual contribution, or a zero-weight slot alone.",
            "Do not delete a bone before updating SkinnedMeshRenderer bone arrays and all PB/collider/animation references.",
            "Do not remove an FX layer or parameter solely because its menu entry is absent; scan every consumer first.",
        ],
    },
    {
        "name": "avatar-head-swap-face-tracked",
        "title": "Avatar Head Swap - Face Tracked",
        "description": "Fit a face-tracked source head to a target body and merge its face contract without losing gestures or body controls.",
        "category": "avatar-composition",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "swap a retained head with face tracking when the final avatar must preserve face tracking and ordinary gesture fallback",
        "whenNotToUse": "do not use when the retained head has no face-tracking Mesh/FX/Parameters/Menu contract",
        "inputs": ["Face-tracked source head, target avatar, and retained body/wardrobe controls."],
        "outputs": ["One fitted avatar with a coherent face-tracking four-piece contract and gesture fallback."],
        "sideEffects": "can duplicate/reparent the head and atomically update animation, FX, Parameters, Menu, and descriptor references after authorization",
        "backupRestore": "Checkpoints precede head duplication, reference remap, and four-piece descriptor writes; every restore is separately approved.",
        "entrypointTool": "vrcforge_read_avatar_descriptor",
        "toolBlocks": ["core", "avatar", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Evidence: Unity/GM proven, but the earlier neck acceptance was a false positive; re-prove the neck from Back/Bottom pixels.",
            "Prove the source Mesh, FX, Parameters, and Menu face-tracking contract before copying.",
            "Fit only the imported head/accessories, then remap exact mesh paths and BlendShapes.",
            "Judge the final merged GM/Build state while preserving gesture fallback, body tuning, and wardrobe controls.",
        ],
        "steps": [
            {
                "goal": "Inventory source and target descriptors, BlendShapes, controls, parameters, FX, and animation bindings.",
                "tools": [
                    "vrcforge_list_avatars", "vrcforge_read_avatar_descriptor",
                    "vrcforge_scan_blendshapes", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_parameters", "vrcforge_scan_fx_animator",
                    "vrcforge_scan_animation_bindings",
                ],
            },
            {
                "goal": "At the duplication checkpoint, preview/copy the head, reparent it, fit local transforms, and keep the old head until acceptance.",
                "tools": [
                    "vrcforge_preview_scene_object_duplicate", "vrcforge_duplicate_scene_object",
                    "vrcforge_reparent_gameobject", "vrcforge_set_property",
                    "vrcforge_set_gameobject_active",
                ],
            },
            {
                "goal": "At the contract checkpoint, remap bindings and merge Parameters, Menu, FX, and descriptor as one atomic sequence.",
                "tools": [
                    "vrcforge_preview_atomic_reference_rename", "vrcforge_atomic_reference_rename",
                    "vrcforge_preview_manage_expression_parameters", "vrcforge_manage_expression_parameters",
                    "vrcforge_preview_manage_expression_menu", "vrcforge_manage_expression_menu",
                    "vrcforge_preview_manage_fx_animator", "vrcforge_manage_fx_animator",
                    "vrcforge_preview_write_avatar_descriptor", "vrcforge_write_avatar_descriptor",
                ],
            },
            {
                "goal": "Run final merged GM/Build face, gesture fallback, body, wardrobe, and neck Back/Bottom acceptance before old-head removal.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                    "vrcforge_build_test_readiness", "vrcforge_build_test_avatar",
                ],
            },
        ],
        "acceptance": [
            "Face tracking drives only the retained mesh and the merged descriptor references the intended FX, Parameters, and Menu.",
            "Ordinary gesture fallback, body tuning, and wardrobe controls remain reachable and animated.",
            "Final synced parameters stay within 256 bits with no orphan face consumer.",
            "Back/Bottom pixels show a closed neck at idle and in motion; Build success cannot override visible geometry.",
        ],
        "pitfalls": [
            "Do not replace body controllers wholesale or copy one of the four face assets without the other three.",
            "Do not delete gesture fallback merely because face tracking is present.",
            "Do not remove the old head before reference, GM, and visible-geometry acceptance closes.",
        ],
    },
    {
        "name": "avatar-head-swap-gesture-only",
        "title": "Avatar Head Swap - Gesture Only",
        "description": "Fit a head without face tracking while retaining gesture expressions and target body/wardrobe controls.",
        "category": "avatar-composition",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "swap a retained head without face tracking or when the user explicitly does not require face tracking",
        "whenNotToUse": "do not use when a real face-tracking Mesh/FX/Parameters/Menu contract must be retained",
        "inputs": ["Gesture-driven source head, target avatar, and body/wardrobe controls that must remain."],
        "outputs": ["One fitted gesture-only avatar without copied or orphaned face-tracking assets."],
        "sideEffects": "can duplicate/reparent the head and update gesture FX or descriptor references after authorization",
        "backupRestore": "Checkpoints precede head duplication and gesture FX/descriptor writes; every restore is separately approved.",
        "entrypointTool": "vrcforge_read_avatar_descriptor",
        "toolBlocks": ["core", "avatar", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Evidence is plan-derived/not yet E2E proven; do not claim it is verified until final readback, GM, and Build evidence exists.",
            "Prove face tracking is absent or explicitly unwanted before copying.",
            "Fit only the imported head/accessories; retain compatible gestures and target body controls.",
            "Skip face-tracking assets rather than creating a partial four-piece contract.",
        ],
        "steps": [
            {
                "goal": "Inventory descriptors, BlendShapes, gestures, FX, controls, and animation bindings; record the no-face-tracking decision.",
                "tools": [
                    "vrcforge_list_avatars", "vrcforge_read_avatar_descriptor",
                    "vrcforge_scan_blendshapes", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_fx_animator", "vrcforge_scan_animation_bindings",
                ],
            },
            {
                "goal": "At the duplication checkpoint, preview/copy the head, reparent it, fit local transforms, and keep the old head disabled but recoverable.",
                "tools": [
                    "vrcforge_preview_scene_object_duplicate", "vrcforge_duplicate_scene_object",
                    "vrcforge_reparent_gameobject", "vrcforge_set_property",
                    "vrcforge_set_gameobject_active",
                ],
            },
            {
                "goal": "At the FX checkpoint, remap exact gesture bindings, merge only gesture FX, and preserve body/wardrobe descriptor references.",
                "tools": [
                    "vrcforge_preview_atomic_reference_rename", "vrcforge_atomic_reference_rename",
                    "vrcforge_preview_manage_fx_animator", "vrcforge_manage_fx_animator",
                    "vrcforge_preview_write_avatar_descriptor", "vrcforge_write_avatar_descriptor",
                ],
            },
            {
                "goal": "Run GM left/right gestures, body, wardrobe, and neck Back/Bottom motion acceptance before old-head removal.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "Left/right hand gestures animate the retained head while body and wardrobe controls still behave.",
            "No face-tracking asset, parameter, menu control, layer, or orphan animation is copied into the final avatar.",
            "Exactly one intended head is active and all retained animation paths resolve.",
            "Back/Bottom pixels show a closed neck at idle and in motion; Build success cannot override visible geometry.",
        ],
        "pitfalls": [
            "Do not invent face tracking from similarly named BlendShapes or copy a partial face controller.",
            "Do not replace target body FX or Parameters merely to obtain source gestures.",
            "Do not accept a front pose or Build result without visible Back/Bottom seam proof.",
        ],
    },
    {
        "name": "source-avatar-part-transplant",
        "title": "Source Avatar Part Transplant",
        "description": "Copy a proven source-model part into a target avatar, then rebind only its required bones, PhysBones, colliders, and references.",
        "category": "avatar-composition",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "transplant one user-authorized hair, ear, tail, accessory, mesh, bone branch, or other bounded part from a source model into a target avatar",
        "whenNotToUse": "do not use for deleting a target part, copying a whole avatar/controller, or moving assets without provenance and reuse permission",
        "inputs": ["Exact source part path, target avatar path, intended parent bone, and retained source dependencies."],
        "outputs": ["One staged then enabled target copy with closed dependency and motion proof; source remains unchanged."],
        "sideEffects": "can duplicate/reparent a scene branch and update exact target-side bone, PhysBone, collider, animation, or reference fields after authorization",
        "backupRestore": "Checkpoints precede staged duplication, target reference rewiring, and final enable or target cleanup; every restore is separately approved.",
        "entrypointTool": "vrcforge_scan_inbound_reference_closure",
        "toolBlocks": ["core", "avatar", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Derive the minimal chain from non-zero bone usage; do not copy all bones slots.",
            "Duplicate only the bounded branch into disabled target staging; never move/delete the source.",
            "Rebind SMR bones/rootBone, PB/colliders/probeAnchor, animation, and controller dependencies before enabling.",
        ],
        "steps": [
            {
                "goal": "Inventory closure, non-zero bone usage, PB/colliders/probeAnchor, animations/controllers, and source/target world/local transform.",
                "tools": [
                    "vrcforge_get_gameobject", "vrcforge_get_property",
                    "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_scan_animation_bindings", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_parameters", "vrcforge_scan_fx_animator",
                ],
            },
            {
                "goal": "Checkpoint; duplicate the minimal chain disabled, reparent/fit, then read world/local transform again; do not trust preserveWorldTransform receipt.",
                "tools": [
                    "vrcforge_preview_scene_object_duplicate", "vrcforge_duplicate_scene_object",
                    "vrcforge_reparent_gameobject", "vrcforge_set_property",
                    "vrcforge_set_gameobject_active",
                ],
            },
            {
                "goal": "Checkpoint; atomically rebind SMR bones/rootBone, PB/colliders/probeAnchor, animation, and controller references; rescan closure.",
                "tools": [
                    "vrcforge_preview_atomic_reference_rename", "vrcforge_atomic_reference_rename",
                    "vrcforge_set_property", "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_inspect_skinned_mesh_bone_usage",
                ],
            },
            {
                "goal": "Enable, rescan, and capture static plus GM motion before any separate old-part extraction.",
                "tools": [
                    "vrcforge_set_gameobject_active", "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "The source avatar remains unchanged and exactly one intended copy exists in one target hierarchy.",
            "Every non-zero weighted bone, SMR rootBone, PhysBone root/collider/probeAnchor, animation, and controller reference resolves to the intended target.",
            "Static and GM motion evidence shows correct scale/deformation with no detached mesh, clipping, seam, or frozen accessory.",
            "Do not delete a donor here; later deletion requires closure complete and non-truncated, then original-avatar-part-extraction.",
        ],
        "pitfalls": [
            "Do not treat a visible mesh as self-contained or copy all bones slots; only non-zero usage defines the minimal chain.",
            "Do not trust a preserveWorldTransform receipt; compare pre/post world/local transform readback.",
            "Do not move/delete the source or duplicate an Avatar Descriptor, whole Animator, or unrelated hierarchy.",
            "Never delete a donor from truncated/incomplete closure or before motion proof.",
        ],
    },
)


def _render_instructions(skill: dict[str, Any]) -> str:
    steps = " ".join(
        f"{index}) {step['goal']} [{', '.join(str(tool).removeprefix('vrcforge_') for tool in step['tools'])}]."
        for index, step in enumerate(skill["steps"], start=1)
    )
    return (
        f"Breakdown: {' '.join(skill['problemBreakdown'])} "
        f"Steps: {steps} "
        f"Accept only when: {' '.join(skill['acceptance'])} "
        f"Avoid: {' '.join(skill['pitfalls'])}"
    )


_COMMIT_PROOF_PITFALL = (
    "A write proposal/timeout is not commit proof; require mutationStarted, committed, "
    "sceneSaved and persistedReadback, or per-target readback."
)


for _skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
    _skill["pitfalls"].append(_COMMIT_PROOF_PITFALL)
    _skill["allowedTools"] = list(
        dict.fromkeys(
            str(tool)
            for step in _skill["steps"]
            for tool in step["tools"]
        )
    )
    _skill["instructions"] = _render_instructions(_skill)
    _skill["tags"] = [
        "builtin", "group", "avatar-composition", "unity", "atomic-tools",
    ]


AVATAR_COMPOSITION_WORKFLOW_SKILL_NAMES = tuple(
    str(skill["name"]) for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
)
