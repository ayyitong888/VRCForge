"""Compact VRCForge Skill definitions for avatar-composition work.

These definitions are orchestration hints, not workflow handlers.  Internal
Agentic mode loads them as ``vrcforge.skill.v1`` Skills; the external MCP tree
projects the same definitions without exposing the internal Agent loop.
"""

from __future__ import annotations

from typing import Any


AVATAR_COMPOSITION_RUNTIME_CONTRACT_FIELDS: tuple[str, ...] = (
    "problemBreakdown",
    "steps",
    "acceptance",
    "pitfalls",
    "transplantModes",
    "sizeMismatchPriority",
    "skinningContract",
    "geometryAlignmentContract",
    "rigAndShapeContract",
    "physboneAbContract",
    "poseSweep",
    "acceptanceViews",
    "pitfallMatrix",
    "failureEtiologyFields",
    "dynamicAcceptance",
    "singleSlotRemapContract",
    "capabilityGapContract",
    "partRoutingContract",
    "externalPreprocessingContract",
    "atomicCompositionResultContract",
    "catEarDeformationDiagnosticsContract",
    "headRendererConsumerClosureContract",
    "communityAvatarModificationContract",
    "neckSeamResolutionContract",
)


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
        "toolBlocks": ["core", "avatar", "materials", "integrations/gesture-manager", "diagnostics"],
        "problemBreakdown": [
            "Detect face tracking first; route to avatar-head-swap-face-tracked or avatar-head-swap-gesture-only before copying assets.",
            "Identify the one active final head renderer and prove it—not an inactive/donor renderer—is the Descriptor viseme/lipsync target and the target of every expected retained expression animation binding.",
            "Fit head/neck; fit ears to Head and tail to Hips without scaling body/clothes.",
            "Prove one hierarchy, motion chain, seams, and future-clothes clearance.",
        ],
        "steps": [
            {
                "goal": "Inventory both avatars; decide face-tracking branch.",
                "tools": [
                    "vrcforge_list_avatars", "vrcforge_read_avatar_descriptor",
                    "vrcforge_scan_blendshapes", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_parameters", "vrcforge_scan_fx_animator",
                ],
            },
            {
                "goal": "Read used bones + Rest skinning delta; missing single-slot remap => capabilityGap=true, ready=false, needsDccRerig if required.",
                "tools": [
                    "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_get_property",
                ],
                "requiredAtomicCapability": "vrcforge_remap_skinned_mesh_bone",
            },
            {
                "goal": "Duplicate/reparent source head, fit local scale, bind ears/tail PB/colliders to final bones.",
                "tools": [
                    "vrcforge_get_gameobject", "vrcforge_get_property",
                    "vrcforge_preview_scene_object_duplicate", "vrcforge_duplicate_scene_object",
                    "vrcforge_reparent_gameobject", "vrcforge_set_property",
                    "vrcforge_set_gameobject_active",
                ],
            },
            {
                "goal": "Unity-first community head route: fit the donor head locally, preview/run MA Setup Outfit and Merge Armature, then use MA ReplaceObject only when its exact atom or user-owned manual Unity step exists. Preserve the target body and mark the original head EditorOnly. Escalate to Blender only for a demonstrated incompatible mesh aperture or donor-specific weights.",
                "tools": [
                    "vrcforge_preview_setup_outfit", "vrcforge_setup_outfit",
                    "vrcforge_preview_add_modular_avatar_component", "vrcforge_add_modular_avatar_component",
                    "vrcforge_get_property", "vrcforge_set_property",
                ],
                "conditional": "community_ma_head_swap_route",
            },
            {
                "goal": "Load materials; identify the actual visible neck/jaw renderer slot; preserve the user's current body material and textures; match shader, Probe Anchor and shadow lighting before previewing an exact preauthored head-texture assignment. Local UV painting or mesh authoring is a separate manual/DCC capability gap, never an invented Unity atom.",
                "tools": [
                    "vrcforge_scan_materials", "vrcforge_plan_shader_tuning",
                    "vrcforge_apply_shader_tuning", "vrcforge_preview_material_texture_assignment",
                    "vrcforge_set_material_texture", "vrcforge_preview_project_asset_duplicate",
                    "vrcforge_duplicate_project_asset", "vrcforge_get_property", "vrcforge_set_property",
                ],
                "conditional": "visible_neck_shading_or_texture_seam",
            },
            {
                "goal": "Cat ears: at a reversible checkpoint prove Head attachment, the complete local sway chain, root scale 1, explicit PhysBone Root/Collider closure, then disable PhysBone A/B, read Rest AABB/skin-matrix metrics, and restore enabled state.",
                "tools": [
                    "vrcforge_get_property", "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_inspect_skinned_mesh_deformation", "vrcforge_set_property",
                ],
                "conditional": "cat_ears",
            },
            {
                "goal": "Choose face-tracked (face-tracking-four-piece-merge) or gesture-only; remap one preview-bound vrcforge_remap_skinned_mesh_bone slot, never bulk.",
                "tools": [
                    "vrcforge_scan_animation_bindings", "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_scan_inbound_reference_closure",
                ],
            },
            {
                "goal": "Before old-head removal, read back Descriptor.VisemeSkinnedMesh/lipsync and scan the final Animator bindings: the active head renderer must receive every expected viseme, lipsync, gesture, and expression binding; no inactive/donor renderer may remain a Descriptor target or the sole Animator target.",
                "tools": [
                    "vrcforge_read_avatar_descriptor", "vrcforge_get_gameobject",
                    "vrcforge_scan_animation_bindings", "vrcforge_scan_inbound_reference_closure",
                ],
            },
            {
        "goal": "GM Play Mode: hide hair/collar; sweep Rest/AFK/Upright/VelocityZ/AngularY; capture unobstructed Front (0,0,0), Side Left (0,+90,0), Side Right (0,-90,0), Back (0,180,0), true Bottom (-90,0,0), full shoulder-neck visible; validate cameraEvidence/causes.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "Exactly one intended head is active; head, body, ears, and tail move as one avatar.",
            "Head-to-body proportions read as intentional anime proportions; ears match head size and tail matches body scale.",
            "Back or Bottom is a hard failure: open rim, hollow/internal geometry, backface, overlap, normal/shading/color break; motion/face/gestures must not open it; Build & Test cannot override visible geometry.",
            "PB roots/colliders resolve to final bones and remain stable under several GM parameters, not only idle.",
            "The active head renderer is the read-back Descriptor viseme/lipsync target and receives the complete expected expression-binding set; inactive/donor renderers are neither Descriptor targets nor sole Animator targets.",
        ],
        "pitfalls": [
            "Do not scale the target body to solve a donor-head mismatch; fit the imported head and accessories locally.",
            "receipts prove requests, pixels prove geometry; unnamed capture can echo 0/0/0 without setting the view.",
            "Static match can hide duplicated inner Neck/Head inheritance; inspect neck-weighted bone target during GM motion.",
            "Do not delete the old head or accessories until inbound references and weighted-bone use are closed.",
            "An active renderer visible in pixels is not sufficient when Descriptor.VisemeSkinnedMesh or expression bindings still resolve only to an inactive/donor renderer.",
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
            "Bind Descriptor viseme/lipsync and every expected face/gesture animation path to the active retained renderer; an inactive/donor renderer cannot remain the only consumer.",
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
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                    "vrcforge_build_test_readiness", "vrcforge_build_test_avatar",
                ],
            },
        ],
        "acceptance": [
            "Descriptor points to the intended final FX, Parameters, and Menu; all face curves resolve to the retained mesh.",
            "Descriptor.VisemeSkinnedMesh/lipsync and every expected face/gesture binding resolve to the active retained renderer, never solely to an inactive/donor renderer.",
            "In the final merged GM/Build state, face tracking works, ordinary left/right gesture fallback works without face tracking, and no target-body clip incorrectly drives the retained donor face mesh.",
            "Target-authored body adjustments and the default/future wardrobe controls remain reachable and animated.",
            "Final synced parameter cost is measured from the merged descriptor and stays within 256 bits; no orphan menu or FX parameter remains.",
        ],
        "pitfalls": [
            "Do not report only the small hand-authored Parameters asset; include all final merged contributors.",
            "Do not count bindings without resolving their exact target paths and active renderer BlendShape consumers.",
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
            "Prove the active final head renderer owns Descriptor viseme/lipsync and every expected expression binding; inactive/donor renderers cannot remain the sole consumer target.",
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
                "goal": "Read back the active renderer path, Descriptor.VisemeSkinnedMesh/lipsync, all expected expression animation bindings, BlendShape names, FX controller consumers, Parameters, and Menu. Require complete closure on the active renderer and zero inactive/donor-only Descriptor or Animator targets.",
                "tools": [
                    "vrcforge_read_avatar_descriptor", "vrcforge_get_gameobject",
                    "vrcforge_scan_blendshapes", "vrcforge_scan_animation_bindings",
                    "vrcforge_scan_avatar_controls", "vrcforge_scan_parameters",
                    "vrcforge_scan_fx_animator", "vrcforge_scan_inbound_reference_closure",
                ],
            },
            {
                "goal": "Run final merged GM/Build face, gesture fallback, body, wardrobe, and neck views: Front (0,0,0), Side Left (0,+90,0), Side Right (0,-90,0), Back (0,180,0), Bottom (-90,0,0), before old-head removal.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                    "vrcforge_build_test_readiness", "vrcforge_build_test_avatar",
                ],
            },
        ],
        "acceptance": [
            "Face tracking drives only the retained mesh and the merged descriptor references the intended FX, Parameters, and Menu.",
            "Ordinary gesture fallback, body tuning, and wardrobe controls remain reachable and animated.",
            "Final synced parameters stay within 256 bits with no orphan face consumer.",
            "The active renderer owns viseme/lipsync and every expected face/gesture binding, and its BlendShapes, FX controller, Parameters, and Menu form a closed mutually referenced contract; inactive/donor renderers own none of these as the only target.",
            "Back/Bottom pixels show a closed neck at idle and in motion; Build success cannot override visible geometry.",
        ],
        "pitfalls": [
            "Do not replace body controllers wholesale or copy one of the four face assets without the other three.",
            "Do not delete gesture fallback merely because face tracking is present.",
            "Static alignment does not prove dynamic Neck/Head inheritance; verify the neck-weighted bone target while GM motion is active.",
            "Do not remove the old head before reference, GM, and visible-geometry acceptance closes.",
            "Do not accept counts alone: every expected binding path must resolve to the active renderer, and every face parameter/menu/controller reference must reach an existing active-renderer BlendShape consumer.",
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
            "Prove the active final head renderer owns Descriptor viseme/lipsync and every expected retained gesture/expression binding; inactive/donor renderers cannot remain the sole consumer target.",
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
                "goal": "Read back the active renderer path, Descriptor.VisemeSkinnedMesh/lipsync, and every expected retained gesture/expression binding. Require all paths to resolve to the active renderer and zero inactive/donor-only Descriptor or Animator targets.",
                "tools": [
                    "vrcforge_read_avatar_descriptor", "vrcforge_get_gameobject",
                    "vrcforge_scan_animation_bindings", "vrcforge_scan_inbound_reference_closure",
                ],
            },
            {
                "goal": "Run GM left/right gestures, body, wardrobe, and neck views: Front (0,0,0), Side Left (0,+90,0), Side Right (0,-90,0), Back (0,180,0), Bottom (-90,0,0), before old-head removal.",
                "tools": [
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "Left/right hand gestures animate the retained head while body and wardrobe controls still behave.",
            "No face-tracking asset, parameter, menu control, layer, or orphan animation is copied into the final avatar.",
            "Exactly one intended head is active and all retained animation paths resolve.",
            "The active renderer is the viseme/lipsync target and receives every expected retained gesture/expression binding; no inactive/donor renderer remains a Descriptor target or sole Animator target.",
            "Back/Bottom pixels show a closed neck at idle and in motion; Build success cannot override visible geometry.",
        ],
        "pitfalls": [
            "Do not invent face tracking from similarly named BlendShapes or copy a partial face controller.",
            "Do not replace target body FX or Parameters merely to obtain source gestures.",
            "Static alignment does not prove dynamic Neck/Head inheritance; verify the neck-weighted bone target while GM motion is active.",
            "Do not accept a front pose or Build result without visible Back/Bottom seam proof.",
            "Do not infer consumer closure from an active renderer alone; read back Descriptor and binding targets and reject inactive/donor-only paths.",
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
            "Classify the part before choosing tools: rigid accessory, independent PhysBone branch, or same/foreign-skeleton weighted SMR.",
            "Rigid accessories attach to one target bone through reparenting or MA Bone Proxy; they never run Setup Outfit or Merge Armature.",
            "Derive a minimal weighted chain only for clothing or donor-body-weighted parts; never copy all bone slots.",
            "Duplicate only the bounded branch into disabled staging; never move/delete source.",
            "Rebind SMR bones/rootBone and close every PB root/collider/probe, constraint, contact, animation, controller, and other external object reference before enabling.",
        ],
        "steps": [
            {
                "goal": "Inventory closure, non-zero bone usage, PB/colliders/probeAnchor plus PB root/collider/probe details, constraint sources, contacts, all external object references, animations/controllers, and source/target world/local transform.",
                "tools": [
                    "vrcforge_get_gameobject", "vrcforge_get_property",
                    "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_scan_animation_bindings", "vrcforge_scan_avatar_controls",
                    "vrcforge_scan_parameters", "vrcforge_scan_fx_animator",
                ],
            },
            {
                "goal": "Classify first. Rigid/static accessory: one target bone/Bone Proxy + local TRS + optional toggle. Independent PhysBone accessory: preserve its whole bounded bone chain. Only same/foreign-skeleton SMR clothing or donor-body-weighted accessories continue to Rest skinning delta and single-slot remap.",
                "tools": ["vrcforge_inspect_skinned_mesh_bone_usage", "vrcforge_get_property"],
                "requiredAtomicCapability": "vrcforge_remap_skinned_mesh_bone",
                "conditionalRoutes": ["same_skeleton_smr", "foreign_skeleton_smr"],
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
                "goal": "Checkpoint; for rigid accessories preview/add MA Bone Proxy or reparent to the exact target bone and read back local TRS without Setup Outfit/Merge Armature; for SMR clothing/weighted accessories rebind bones/rootBone; rescan closure.",
                "tools": [
                    "vrcforge_preview_atomic_reference_rename", "vrcforge_atomic_reference_rename",
                    "vrcforge_preview_add_modular_avatar_component", "vrcforge_add_modular_avatar_component",
                    "vrcforge_set_property", "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_inspect_skinned_mesh_bone_usage",
                ],
            },
            {
                "goal": "Clothing routes only: compatible clothing uses MA Setup Outfit then documented body BlendShapes for penetration; non-compatible clothing uses external MochiFitter only when both profiles exist, otherwise external Blender scale/armature/target-body weight transfer and FBX reimport before MA Setup Outfit. Never write Unity C# weight-transfer or vertex-realignment scripts.",
                "tools": [
                    "vrcforge_preview_setup_outfit", "vrcforge_setup_outfit",
                    "vrcforge_preview_add_modular_avatar_component", "vrcforge_add_modular_avatar_component",
                ],
                "conditionalRoutes": ["same_skeleton_smr", "foreign_skeleton_smr"],
            },
            {
                "goal": "Independent PhysBone accessories create distinct installContainer and swayChainRoot nodes and fit only installContainer uniformly; cat ears hard-code MA Bone Proxy target=Head (or direct-parent installContainer to target Head equivalently), while tails keep their separate Hips target. Keep swayChainRoot and every descendant bone at Rest localScale=(1,1,1), close PhysBone root/collider/probe, constraint, contact, and every external object reference, then require same-framing close-root Rest/AFK views before PhysBone A/B isolation.",
                "tools": [
                    "vrcforge_get_property", "vrcforge_inspect_skinned_mesh_bone_usage",
                    "vrcforge_inspect_skinned_mesh_deformation", "vrcforge_set_property",
                ],
                "conditional": "cat_ears",
            },
            {
                "goal": "Optional toggle: compose exact Parameter/Animator/Menu atoms only when they fully express the requested control. Module Creator export and unsupported MA Toggle Control authoring are external/manual preprocessing capability gaps, never fake successes.",
                "tools": [
                    "vrcforge_preview_ensure_expression_parameter", "vrcforge_preview_ensure_animator_state",
                    "vrcforge_preview_ensure_expression_menu_control",
                    "vrcforge_ensure_expression_parameter", "vrcforge_ensure_animator_state",
                    "vrcforge_ensure_expression_menu_control",
                ],
                "conditional": "optional_toggle",
            },
            {
                "goal": "Enable after Rest readback; no paired morph/fit => needsDccRerig=true (never occlusion/body scale). Sweep Rest/AFK/Upright/VelocityZ/AngularY over unobstructed Front (0,0,0), Side Left (0,+90,0), Side Right (0,-90,0), Back (0,180,0), Bottom (-90,0,0), with cameraEvidence.",
                "tools": [
                    "vrcforge_set_gameobject_active", "vrcforge_scan_inbound_reference_closure",
                    "vrcforge_gesture_manager_enter_play_mode",
                    "vrcforge_gesture_manager_set_parameter",
                    "vrcforge_inspect_skinned_mesh_deformation",
                    "vrcforge_capture_status", "vrcforge_capture_screenshot",
                ],
            },
        ],
        "acceptance": [
            "The source avatar remains unchanged and exactly one intended copy exists in one target hierarchy.",
            "Every non-zero weighted bone, SMR rootBone, PhysBone root/collider/probeAnchor, constraint source, contact, animation, controller, and other external object reference resolves inside the copied branch or intended target hierarchy.",
            "Rigid accessory acceptance proves the exact target bone/Bone Proxy target and local TRS, without Setup Outfit or Merge Armature; an optional toggle proves both on and off states.",
            "Independent PhysBone accessory acceptance proves distinct installContainer/swayChainRoot nodes, uniform fit only on installContainer, swayChainRoot and every descendant bone Rest localScale 1, fixed Head attachment for cat ears, complete target-side external-reference closure, same-framing close-root Rest/AFK multi-view evidence, finite vertices, stable AABB/skin-matrix metrics, and no root shift during motion.",
            "Static and GM motion evidence shows correct scale/deformation with no detached mesh, clipping, seam, or frozen accessory.",
            "Do not delete a donor here; later deletion requires closure complete and non-truncated, then original-avatar-part-extraction.",
        ],
        "pitfalls": [
            "Do not treat a visible mesh as self-contained or copy all bones slots; only non-zero usage defines the minimal chain.",
            "Do not route glasses, earrings, hair clips, weapons, or another rigid accessory through clothing Setup Outfit/Merge Armature.",
            "Do not collapse installContainer and swayChainRoot into one transform or scale swayChainRoot/any descendant bone away from its Rest value 1; uniform size fitting belongs only on installContainer.",
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
    rendered = (
        f"Breakdown: {' '.join(skill['problemBreakdown'])} "
        f"Steps: {steps} "
        f"Accept only when: {' '.join(skill['acceptance'])} "
        f"Avoid: {' '.join(skill['pitfalls'])}"
    )
    # Keep the projected planner hint compact; complete contracts remain in
    # the structured fields below and in the package reference guide.
    return rendered if len(rendered) < 2_990 else rendered[:2_986] + "..."


_CAUSE_AND_COMMIT_TRUTH_PITFALL = (
    "Call success=true/status=ok does not imply domain ready=true; preserve error, failedStep, diagnostics, ready, blockingReasons, "
    "failureLayer/failurePhase/failureCause, rootCause/causeChain, observed/expected/delta, "
    "mutationStarted, committed, commitState, sceneSaved, persistedReadback, evidence, recovery, "
    "and nextAction. A write proposal/timeout is not commit proof; unknown commit blocks retry "
    "until per-target readback, and a missing cause blocks diagnosis."
)


for _skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
    _skill["pitfalls"].append(_CAUSE_AND_COMMIT_TRUTH_PITFALL)
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

# 5.6-sol research contract.  Keep this data explicit: a planner may project
# these fields to a package, but must not infer a successful transplant from a
# transform receipt, a Build result, or a truncated scan.
TRANSPLANT_MODES: tuple[dict[str, Any], ...] = (
    {
        "id": "rigid",
        "when": "rigid/static accessory with no donor-body skin; examples include glasses, earrings, hair clips, weapons, or rigid wings",
        "steps": ["inventory exact branch", "stage inactive copy", "attach to exact target bone by reparent or MA Bone Proxy", "fit and read back local TRS", "optionally prove toggle on/off"],
        "allowed": ["direct_target_bone_parent", "ma_bone_proxy", "local_trs", "optional_toggle"],
        "forbidden": ["setup_outfit", "merge_armature", "copy_donor_armature"],
    },
    {
        "id": "same_skeleton_smr",
        "when": "clothing or close-fitting weighted accessory whose SkinnedMeshRenderer uses the target-compatible skeleton",
        "steps": ["prove non-zero weights", "author/prove target-compatible armature merge or explicit used-bone remap", "match bones/rootBone and bindposes", "check index/count contract", "run dynamic sweep"],
    },
    {
        "id": "foreign_skeleton_smr",
        "when": "clothing or close-fitting weighted accessory whose SMR references an incompatible or donor-only skeleton",
        "steps": ["derive minimal weighted chain", "author armature merge or map every used bone explicitly", "reject mixed unresolved chains", "read back deformation"],
    },
    {
        "id": "independent_skeleton_physbone",
        "when": "accessory such as cat ears or a tail owns a short independent sway bone chain and PhysBone/collider behavior",
        "steps": ["copy the whole accessory prefab and complete bounded root-to-tip chain", "create distinct installContainer and swayChainRoot nodes", "for cat ears set MA Bone Proxy target=Head or direct-parent installContainer to target Head equivalently", "use only uniform installContainer scale/position for Unity size fitting while swayChainRoot and every descendant bone remain Rest localScale 1", "close PhysBone root/collider/probe, constraint, contact, and every external object reference", "prove renderer used-bone closure", "capture same-framing close-root Rest/AFK pairs across all required views, then sweep head-down/head-turn motion and collision"],
        "allowed": ["direct_target_bone_parent", "ma_bone_proxy", "whole_bounded_sway_chain", "target_side_physbone_reference_rebind"],
        "forbidden": ["setup_outfit", "merge_armature", "partial_bone_chain", "donor_collider_reference"],
    },
    {
        "id": "animator_menu",
        "when": "part behavior is driven by Animator, FX, Parameters, or Expressions Menu",
        "steps": ["scan inbound consumers", "remap exact paths/parameters", "retain target body/wardrobe controls", "validate menu and parameter behavior"],
    },
)

PART_TRANSPLANT_ROUTING_CONTRACT: dict[str, Any] = {
    "classifyBeforeWrite": True,
    "routes": {
        "rigid": "single_target_bone_attachment_without_skeleton_merge",
        "independent_skeleton_physbone": "single_target_bone_attachment_plus_complete_local_sway_chain",
        "same_skeleton_smr": "clothing_or_weighted_accessory_on_target_compatible_skeleton",
        "foreign_skeleton_smr": "clothing_or_weighted_accessory_requiring_explicit_used_bone_mapping",
        "animator_menu": "behavior_consumer_addition_after_attachment_route",
    },
    "rigidAccessory": {
        "attachment": ["direct_reparent", "ma_bone_proxy"],
        "requiredReadback": ["targetBoneOrProxyTarget", "localPosition", "localRotation", "localScale"],
        "optionalToggle": "compose_parameter_animator_menu_atoms_only_when_complete",
        "forbidden": ["setup_outfit", "merge_armature"],
    },
    "independentPhysBoneAccessory": {
        "examples": ["cat_ears", "tail"],
        "required": [
            "completeBoundedBoneChain", "targetAttachmentBone", "rootScale",
            "installContainer", "swayChainRoot", "physBoneRootTransform",
            "descendantBoneRestLocalScales",
            "consumedColliderReferences", "physBoneColliderReferences",
            "probeAnchorReferences", "constraintSourceReferences", "contactReferences",
            "allExternalObjectReferences",
            "rendererUsedBoneClosure", "restAndPerPoseDeformationReadback",
        ],
        "defaultRootScale": [1.0, 1.0, 1.0],
        "unitySizeFit": "uniform whole-prefab/installContainer scale and position only; installContainer and swayChainRoot are distinct; keep swayChainRoot and every descendant bone Rest localScale at 1",
        "nodeContract": {
            "installContainer": {
                "role": "whole_accessory_uniform_fit_and_target_attachment",
                "uniformLocalScaleAllowed": True,
                "requiredReadback": ["parentOrProxyTarget", "localPosition", "localRotation", "uniformLocalScale"],
            },
            "swayChainRoot": {
                "role": "physbone_simulation_root",
                "mustBeDistinctFromInstallContainer": True,
                "localScale": [1.0, 1.0, 1.0],
                "descendantBoneRestLocalScale": [1.0, 1.0, 1.0],
                "requireEveryDescendantBoneRestScaleOne": True,
                "preserveInternalLocalTrs": True,
            },
        },
        "attachmentTargetByClass": {
            "cat_ears": {
                "maBoneProxyTarget": "Head",
                "directMountEquivalent": "installContainer_parent_is_target_avatar_Head",
                "targetIsFixed": True,
            },
            "tail": {
                "maBoneProxyTarget": "Hips",
                "directMountEquivalent": "installContainer_parent_is_target_avatar_Hips",
                "targetIsFixed": True,
            },
            "other": "exact_user_authorized_target_bone",
        },
        "externalReferenceClosure": {
            "required": [
                "physBoneRootTransform", "physBoneColliderReferences",
                "probeAnchorReferences", "constraintSourceReferences",
                "contactReferences", "allExternalObjectReferences",
            ],
            "allowedRoots": ["copiedAccessoryBranch", "targetAvatarHierarchy"],
            "sourceAvatarReferencesRemaining": 0,
            "unresolvedReferences": 0,
            "scanMustBeCompleteAndNonTruncated": True,
        },
        "skinningPolicy": "preserve_internal_mesh_to_sway_chain_binding; do_not_remap_to_target_armature_unless_reclassified_as_weighted_smr",
        "deformationReadbackTool": "vrcforge_inspect_skinned_mesh_deformation",
        "acceptance": [
            "rest_attachment_pixels", "gesture_manager_motion", "no_root_shift",
            "physbone_motion_and_collision", "finite_vertices_all_poses",
            "stable_aabb_and_skin_matrix_metrics",
        ],
        "forbidden": ["setup_outfit", "merge_armature", "partial_chain_copy", "donor_only_collider_reference"],
    },
    "weightedSmr": {
        "routes": ["same_skeleton_smr", "foreign_skeleton_smr"],
        "appliesTo": ["clothing", "close_fitting_weighted_accessory", "donor_body_weighted_part"],
        "requiredOperation": "target-compatible armature merge or explicit used-bone/rootBone/bindpose remap",
    },
}

EXTERNAL_PREPROCESSING_CONTRACT: dict[str, Any] = {
    "moduleCreator": {
        "role": "optional_external_manual_prefab_export_before_vrcforge_transplant",
        "internalAtomicCapability": None,
        "whenRequired": {"capabilityGap": True, "ready": False, "failureCause": "module_creator_export_atom_unavailable"},
        "mustNotClaim": "module_created_prefab_or_export_success",
    },
    "dccEscalationOnlyWhen": [
        "mesh_shape_or_mesh_level_size_requires_editing",
        "close_fitting_part_retains_donor_body_specific_weights",
    ],
    "notDccReasons": [
        "rigid_accessory_target_bone_attachment",
        "physbone_root_or_collider_reference_rebind",
        "independent_accessory_root_scale_normalization",
    ],
}

ATOMIC_COMPOSITION_RESULT_CONTRACT: dict[str, Any] = {
    "stopOnFailedStep": True,
    "failedStepRequiredOnFailure": True,
    "surfaces": ["internal_agent_loop", "external_mcp_agent"],
    "surfaceParityRequired": True,
    "preserveEachStep": [
        "step", "tool", "result", "success", "status", "ready", "error",
        "failureCause", "rootCause", "causeChain", "failedStep", "diagnostics",
    ],
    "preserveNestedResultUnchanged": True,
    "failureDiagnosisRequired": {
        "required": ["success", "status", "failedStep"],
        "atLeastOneOf": ["error", "failureCause", "rootCause", "causeChain", "diagnostics"],
        "onMissing": "block_and_report_contract_failure",
    },
    "mustNot": [
        "collapse_tool_error_to_false", "drop_failed_step", "drop_handler_diagnostics",
        "replace_specific_cause_with_generic_failure", "continue_after_failed_write",
    ],
}

CAT_EAR_DEFORMATION_DIAGNOSTICS_CONTRACT: dict[str, Any] = {
    "route": "independent_skeleton_physbone",
    "attachment": {
        "targetBone": "Head",
        "maBoneProxyTarget": "Head",
        "directMountEquivalent": "installContainer_parent_is_target_avatar_Head",
        "installContainer": {
            "role": "whole_accessory_uniform_fit_and_head_attachment",
            "uniformLocalScaleAllowed": True,
            "requiredReadback": ["parentOrProxyTarget", "localPosition", "localRotation", "uniformLocalScale"],
        },
        "swayChainRoot": {
            "mustBeDistinctFromInstallContainer": True,
            "localScale": [1.0, 1.0, 1.0],
            "descendantBoneRestLocalScale": [1.0, 1.0, 1.0],
            "requireEveryDescendantBoneRestScaleOne": True,
            "preserveInternalLocalTrs": True,
        },
        "rootLocalScale": [1.0, 1.0, 1.0],
        "rootLocalScaleMeaning": "swayChainRoot_rest_scale_not_installContainer_scale",
        "requiredReadback": [
            "completeBoundedBoneChain", "targetAttachmentBone", "rootLocalScale",
            "installContainer", "swayChainRoot", "physBoneRootTransform",
            "descendantBoneRestLocalScales",
            "consumedColliderReferences", "physBoneColliderReferences",
            "probeAnchorReferences", "constraintSourceReferences", "contactReferences",
            "allExternalObjectReferences",
        ],
    },
    "externalReferenceClosure": {
        "required": [
            "physBoneRootTransform", "physBoneColliderReferences",
            "probeAnchorReferences", "constraintSourceReferences",
            "contactReferences", "allExternalObjectReferences",
        ],
        "allowedRoots": ["copiedAccessoryBranch", "targetAvatarHierarchy"],
        "sourceAvatarReferencesRemaining": 0,
        "unresolvedReferences": 0,
        "scanMustBeCompleteAndNonTruncated": True,
    },
    "tool": "vrcforge_inspect_skinned_mesh_deformation",
    "diagnosticOnly": True,
    "requiresSeparateExplicitUserAuthorization": True,
    "defaultCommunityRoute": "do_not_run_matrix_determinant_or_bindpose_analysis",
    "countsTowardVisualAcceptance": False,
    "restRequired": [
        "rest.vertexCount", "rest.finiteVertexCount", "rest.aabb",
        "play.vertexCount", "play.finiteVertexCount", "world.aabb",
        "world.distanceSummary", "usedBoneReconstructedSkinMatrix",
    ],
    "skinMatrixMetrics": [
        "sampleCount", "maxTranslationMagnitude", "maxAbsDeviation",
        "determinantMin", "determinantMax",
    ],
    "restSkinMatrixGate": {
        "enabledOnlyWhenSeparatelyAuthorized": True,
        "maxAbsDeviation": 0.001,
        "determinantRange": [0.999, 1.001],
        "unless": "the_source_fbx_rest_baseline_proves_a_different_finite_value",
    },
    "perPose": ["Rest", "AFK", "HeadDown", "HeadTurnLeft", "HeadTurnRight"],
    "perPoseRequired": [
        "allVerticesFinite", "worldAabb", "distanceP95AndMax",
        "skinMatrixMetrics", "sameCloseRootFiveViewPixels",
    ],
    "comparisonBaseline": "accepted_rest_and_same_pose_source_or_pre_migration_metrics",
    "sameCameraRestMotionPairs": True,
    "sameFramingRestAfkMultiViewPairs": True,
    "failIf": [
        "finite_vertex_count_changes", "aabb_inflates_collapses_or_translates_beyond_declared_baseline_tolerance",
        "skin_matrix_scale_or_translation_jumps_beyond_declared_baseline_tolerance",
        "ear_root_shifts_from_head", "ear_root_gap_or_seam_changes_size_between_poses",
        "ear_size_or_attachment_changes_between_rest_and_motion",
    ],
    "views": ["front", "side_left", "side_right", "back", "bottom"],
    "unobstructedCloseRootView": True,
}

HEAD_RENDERER_CONSUMER_CLOSURE_CONTRACT: dict[str, Any] = {
    "requiredBeforeOldHeadRemoval": True,
    "activeRenderer": {
        "activeInHierarchy": True,
        "requiredTargets": [
            "descriptorVisemeSkinnedMesh", "descriptorLipSync",
            "allExpectedVisemeBindings", "allExpectedExpressionBindings",
            "allExpectedGestureBindings",
        ],
        "bindingSetComparison": "exact_expected_source_or_accepted_baseline_paths_remapped_to_active_renderer",
        "blendShapeNamesMustResolve": True,
    },
    "inactiveOrDonorRenderer": {
        "mayRemainDescriptorTarget": False,
        "mayRemainSoleAnimatorTarget": False,
        "maySatisfyExpectedBindingOnlyOnInactivePath": False,
    },
    "requiredReadback": [
        "activeRendererPath", "activeRendererHierarchyState",
        "descriptorVisemeSkinnedMeshPath", "descriptorLipSyncMode",
        "expectedExpressionBindingPaths", "resolvedActiveRendererBindingPaths",
        "inactiveOrDonorBindingPaths", "missingBindingPaths", "orphanBindingPaths",
    ],
    "faceTrackedBranch": {
        "requiredClosure": [
            "activeRendererBlendShapes", "fxControllerBindings",
            "expressionParameters", "expressionsMenu", "descriptorReferences",
        ],
        "everyParameterAndMenuControlMustReachControllerAndActiveRendererConsumer": True,
        "everyExpectedControllerBlendShapeMustExistOnActiveRenderer": True,
        "inactiveOrDonorOnlyConsumerCount": 0,
    },
    "failure": {
        "ready": False,
        "failureCauses": [
            "active_renderer_not_descriptor_viseme_target",
            "active_renderer_missing_expected_expression_binding",
            "inactive_or_donor_renderer_is_only_consumer",
            "face_tracking_parameter_menu_controller_blendshape_closure_incomplete",
        ],
    },
}

COMMUNITY_AVATAR_MODIFICATION_CONTRACT: dict[str, Any] = {
    "source": "user_supplied_avatar_mod_rules",
    "precedence": "when a community route is selected, this matrix and visual policy overrides generic matrix/bindpose/neck-ring diagnostic gates unless the user separately authorizes those diagnostics",
    "matrixPolicy": {
        "default": "prohibited",
        "requiresSeparateExplicitUserAuthorization": True,
        "forbiddenByDefault": [
            "skin_matrix_analysis", "determinant_checks", "bindpose_comparison",
            "programmatic_transform_guessing", "sub_millimeter_vertex_comparison",
        ],
        "neverCountsTowardVisualAcceptance": True,
        "authorizedDiagnosticsRemainDiagnosticOnly": True,
    },
    "visibleCandidateTimebox": {
        "minutesPerStep": 20,
        "onNoVisibleCandidate": "stop_report_current_state_with_screenshot_and_ask_for_direction",
        "mustNot": "continue_exploring_after_timebox",
    },
    "visualAcceptance": {
        "authority": "free_camera_pixels_at_normal_vrchat_viewing_distance",
        "normalViewingDistanceMeters": [1.0, 2.0],
        "failIf": ["visible_gap", "floating_geometry", "visible_distortion"],
        "matrixNormalMapOrSubMillimeterEvidenceCannotPass": True,
        "remoteScreenshotWithoutVerifiedCameraEvidenceCannotPass": True,
    },
    "headSwap": {
        "classification": "full_head_transplant_not_rigid_or_independent_accessory_or_clothing_part_route",
        "mergeArmatureScope": "full_head_community_route_only_does_not_authorize_setup_outfit_for_rigid_or_independent_accessories",
        "method": "unity_first_modular_avatar_merge_armature_replace_object_and_layered_neck_seam_resolution",
        "steps": [
            "match_overall_head_body_scale", "place_head_prefab_under_target_avatar",
            "ma_setup_outfit_then_merge_armature", "match_bone_names_to_integration_target_when_needed",
            "ma_replace_object_head_body_reference_to_target_body_path",
            "mark_original_target_head_face_mesh_editor_only",
            "inspect_visible_neck_geometry_and_attachment_motion_without_hair_or_collar",
            "preserve_user_selected_body_material_and_texture",
            "match_shader_probe_anchor_and_shadow_lighting",
            "identify_exact_visible_head_renderer_material_slot_and_neck_uv_region",
            "assign_only_approved_preauthored_head_local_seam_texture_when_required",
            "reconfigure_eye_bone_lipsync_visemes_eyelids_and_viewpoint",
            "repair_fx_animation_paths", "verify_active_renderer_consumer_closure",
        ],
        "visualAcceptance": {
            "poses": ["Rest", "AFK"],
            "minimumViews": ["front", "back", "side_left", "side_right"],
            "sameFramingPairs": True,
            "pass": "no_visible_neck_gap_or_floating_geometry_at_screen_distance",
            "onFail": "classify_geometry_weight_lighting_or_local_texture_failure_and_fix_only_that_layer",
        },
        "legacyConcealmentRequiresExplicitUserChoice": [
            "verified_head_mesh_overlap", "mesh_hole_shrinker", "color_matched_primitive",
        ],
        "forbidden": [
            "csharp_mesh_vertex_manipulation", "programmatic_neck_ring_diameter_matching",
            "camera_occlusion_as_acceptance",
        ],
        "capabilityBoundary": {
            "maSetupOutfitAndMergeArmature": "vrcforge_supported_preview_and_write_atoms",
            "maReplaceObject": "manual_or_capability_gap_when_no_exact_atom",
            "meshHoleShrinker": "external_manual_or_capability_gap",
            "localTextureAuthoring": "external_manual_or_capability_gap_when_no_exact_atom",
            "existingTextureAssignment": "vrcforge_preview_material_texture_assignment_then_approved_vrcforge_set_material_texture",
        },
    },
    "clothing": {
        "compatible": [
            "import_package", "place_prefab_under_avatar", "ma_setup_outfit",
            "use_documented_body_blendshapes_for_penetration",
        ],
        "nonCompatiblePreferred": "external_mochi_fitter_only_when_source_and_target_profiles_exist_then_ma_setup_outfit",
        "nonCompatibleFallback": "external_blender_scale_position_armature_transfer_target_body_weights_export_fbx_then_ma_setup_outfit",
        "forbidden": [
            "unity_csharp_weight_transfer", "unity_csharp_vertex_realignment",
        ],
    },
    "rigidAccessory": {
        "steps": [
            "place_prefab_under_avatar", "ma_bone_proxy_to_exact_target_bone",
            "fit_local_position_rotation_scale", "optional_ma_toggle_and_menu_installer",
        ],
        "visualAcceptanceMinimumViews": 2,
        "forbidden": ["matrix_analysis", "weight_transfer", "armature_merge"],
    },
    "independentPhysBoneAccessory": {
        "classification": "accessory_not_head_swap",
        "steps": [
            "place_prefab_under_avatar", "ma_bone_proxy_to_attachment_bone",
            "uniform_size_fit_on_installContainer_only",
            "keep_swayChainRoot_and_descendant_bones_rest_scale_one",
            "rebind_physbone_root_to_owned_chain_root",
            "rebind_or_remove_every_collider_constraint_contact_and_external_reference",
            "optional_ma_toggle", "play_mode_natural_motion_acceptance",
        ],
        "visualAcceptance": ["natural_swing", "no_visible_distortion", "no_frozen_bones"],
        "onNoMotion": "check_physbone_root_transform_and_reference_closure_first",
    },
}

NECK_SEAM_RESOLUTION_CONTRACT: dict[str, Any] = {
    "defaultRoute": "unity_first_modular_avatar_head_swap",
    "dccFallbackOnlyWhen": ["actual_neck_mesh_aperture_cannot_fit", "donor_specific_weights_cannot_be_reconciled"],
    "classifyInOrder": ["visible_geometry", "pose_dependent_weights", "lighting_and_shader", "renderer_slot_ownership", "localized_head_texture"],
    "bodyBaseline": {
        "source": "the user's actual currently assigned target body renderer material and textures",
        "mustPreserve": ["material_asset", "main_texture", "masks", "body_details", "body_armature_scale"],
        "mustNotSubstitute": ["assumed_official_default", "third_party_body_texture", "historically_rejected_body_candidate"],
    },
    "unityMaterialSteps": [
        "load_materials_tool_block", "scan_exact_head_and_body_materials", "identify_visible_renderer_slot",
        "match_shader_and_probe_anchor", "optionally_duplicate_target_compatible_material_and_retain_donor_face_texture",
        "plan_minimal_shadow_and_lighting_changes",
        "approve_each_exact_material_write", "capture_same_camera_light_on_and_off",
    ],
    "localizedTexture": {
        "allowedTarget": "approved generated copy of the exact visible head-face material texture",
        "mask": "only the visible neck interface or front-jaw UV islands confirmed from the actual mesh",
        "sampling": "actual current body appearance compensated for target head material tint and lighting",
        "protect": ["target_body_texture", "mouth", "nose", "eyes", "heterochromia", "alpha", "pixels_outside_exact_uv_mask"],
        "authoringAtomAvailable": False,
        "onMissingAuthoringAtom": {
            "capabilityGap": True, "ready": False,
            "failureCause": "seam_authoring_atom_unavailable",
            "nextAction": "user_owned_external_texture_authoring_then_approved_unity_texture_assignment",
        },
        "materialTemplateAtoms": ["vrcforge_preview_project_asset_duplicate", "vrcforge_duplicate_project_asset"],
        "assignmentAtoms": ["vrcforge_preview_material_texture_assignment", "vrcforge_set_material_texture"],
    },
    "fallbackConcealment": "only when the user explicitly accepts a collar, overlap, primitive, or external hole-shrinker; never claim an actual seam repair",
    "acceptance": ["hair_and_collar_hidden_for_interface_inspection", "front_left_right_back_same_camera", "rest_afk_head_down_and_turns", "body_material_unchanged", "no_visible_gap_or_abrupt_skin_color_step"],
}

SIZE_MISMATCH_PRIORITY: tuple[str, ...] = (
    "confirm intended attachment point and measurement space",
    "first fit the imported whole prefab/container with uniform local scale and position in Unity",
    "keep any independent sway-chain root and every descendant bone at Rest local scale 1",
    "diagnose parent, used-bone closure, and bindpose/skin-matrix metrics before accepting the fit",
    "adjust bounded accessory root or clothing clearance",
    "use DCC re-rig/export only when real mesh aperture, shape, proportions, or donor-specific weights cannot be reconciled",
    "never scale target body, armature, or whole clothing set to hide a donor mismatch",
)

SKINNING_CONTRACT: dict[str, Any] = {
    "required": [
        "renderer.bones", "renderer.rootBone", "renderer.bindposes",
        "allUsedBonesResolved", "allUsedBindposesResolved", "noOutOfRangeWeights",
        "mixedChainClosure", "safeForWeightedRemap",
    ],
    "informational": ["nullBoneCount", "unusedBoundBoneCount"],
    "rules": [
        "every non-zero weighted index resolves to the intended final hierarchy",
        "all used bindposes and the bone-array/index contract are read back after rebind",
        "mixed donor/target chains are rejected unless every link is explicitly proven",
        "unweighted null slots are informational and must not block; truncated scans are not empty closure",
        "weighted remap is allowed only when allUsedBonesResolved, allUsedBindposesResolved, noOutOfRangeWeights, mixedChainClosure, and safeForWeightedRemap are true",
    ],
    "communityRouteOverride": "read reference/count closure only; matrix-value and bindpose comparison require separate explicit user authorization and never count as visual acceptance",
}

GEOMETRY_ALIGNMENT_CONTRACT: dict[str, Any] = {
    "required": ["attachmentFrame", "neckRingCenter", "neckRingNormal", "neckRingRadius", "pivot", "boneRoll"],
    "measureIn": "target_neck_rest_frame",
    "order": ["measure_source_and_target", "align_center_and_normal", "fit_uniform_local_scale", "check_gap_and_overlap"],
    "allowedMutation": "imported_part_local_transform_only",
    "allowedVerifiedSeamConcealment": ["head_mesh_overlap", "mesh_hole_shrinker", "color_matched_primitive"],
    "communityHeadRouteOverride": "prefer direct Unity head fitting and layered visible-seam repair; verified overlap concealment requires explicit user acceptance; pixel evidence is authoritative",
    "forbidden": ["scale_target_body_or_armature", "camera_occlusion_or_unverified_cover_as_geometry_fix", "bulk_remesh_when_blendshapes_must_survive"],
    "onUnreconciled": {"needsDccRerig": True, "ready": False, "failureCauseRequired": True},
}

RIG_AND_SHAPE_CONTRACT: dict[str, Any] = {
    "required": ["targetRestPose", "boneRollReadback", "bindposeReadback", "localWeightTransfer", "blendShapeOrder", "arkitNames"],
    "bindpose": "diagnose_and_prove_target_rest_inverse_bind_matrices; raw matrix editing is not a routine Unity user step; use the clothing armature/remap route or DCC rerig when unreconciled",
    "weights": "transfer_only_minimal_used_chain; normalize_non_zero_weights; bulk_remap_forbidden",
    "blendShapes": "preserve_basis_vertex_order_and_all_source_shape_keys; arkit_names_exact",
    "onFailure": {"needsDccRerig": True, "ready": False, "failureCauseRequired": True, "mustNotClaim": "Build_success"},
    "communityRouteOverride": "skip bindpose matrix analysis and programmatic weight/vertex manipulation unless separately explicitly authorized",
}

PHYSBONE_AB_CONTRACT: dict[str, Any] = {
    "required": ["enabledBefore", "rootTransform", "bonePath", "colliderAndProbeReferences", "colliderReferences", "probeAnchorReferences", "constraintSourceReferences", "contactReferences", "allExternalObjectReferences", "enabledAfter"],
    "isolation": ["record_state", "disable_A_and_B_temporarily", "capture_rest_and_dynamic", "restore_state", "readback_restoration"],
    "restoreFailure": {"ready": False, "failureCause": "physbone_baseline_restore_failed"},
}

POSE_SWEEP: tuple[str, ...] = (
    "Rest", "AFK", "Upright", "VelocityZ", "AngularY", "body-size-min", "body-size-max",
    "face-tracking-min", "face-tracking-max",
)

ACCEPTANCE_VIEWS: tuple[dict[str, Any], ...] = (
    {"angle": "front", "rotation": [0, 0, 0]},
    {"angle": "side_left", "rotation": [0, 90, 0]},
    {"angle": "side_right", "rotation": [0, -90, 0]},
    {"angle": "back", "rotation": [0, 180, 0]},
    {"angle": "bottom", "rotation": [-90, 0, 0]},
)

FAILURE_ETIOLOGY_FIELDS: tuple[str, ...] = (
    "success", "status", "error", "failedStep", "diagnostics",
    "failureLayer", "failurePhase", "failureCause", "rootCause", "causeChain",
    "observed", "expected", "delta", "blockingReasons", "capabilityGap",
    "needsDccRerig", "mutationStarted", "committed", "commitState",
    "sceneSaved", "persistedReadback", "evidence", "recovery", "nextAction",
)

REQUIRED_DYNAMIC_ACCEPTANCE: dict[str, Any] = {
    "poses": ["Rest", "AFK", "Upright", "VelocityZ", "AngularY"],
    "optionalExtremes": ["body-size-min", "body-size-max", "face-tracking-min", "face-tracking-max"],
    "views": ["front", "side_left", "side_right", "back", "bottom"],
    "unobstructed": True,
    "cameraEvidence": ["position", "target", "basis", "quaternion", "projection", "matrix"],
    "orthogonalBasis": True,
    "obliqueAuxiliaryViewsAllowed": True,
    "obliqueAuxiliaryViewsCountTowardsAcceptance": False,
    "thresholds": ["returnedRotationMatchesRequest", "artifactNonEmpty", "noCropOrOccluder", "restoreBaselineParameters"],
    "baselineContract": {"recordBeforePoseSweep": True, "readBackAfterEachPose": True, "restoreAfterSweep": True, "blockIfRestoreReadbackFails": True},
}

SINGLE_SLOT_REMAP_CONTRACT: dict[str, Any] = {
    "capability": "vrcforge_remap_skinned_mesh_bone",
    "order": ["inspectUsedBoneIndices", "readRestSkinningDelta", "previewOneSlot", "comparePredictedSkinningMetrics", "approvedExecuteOneSlot", "readbackBonesRootBoneBindposes"],
    "bulkRemap": "forbidden",
    "predictedMetricGate": {
        "required": ["current", "target", "donorChainBaseline"],
        "compare": ["translationMagnitude", "maxAbsDeviation", "nearIdentity", "mixedChainClosure"],
        "executeOnlyWhen": "target is materially closer to donorChainBaseline than current and allUsedBonesResolved/allUsedBindposesResolved/noOutOfRangeWeights/mixedChainClosure/safeForWeightedRemap remain true",
        "otherwise": {"needsDccRerig": True, "ready": False, "capabilityGap": False},
    },
    "onMissing": {"capabilityGap": True, "ready": False, "needsDccRerig": "when_no_paired_morph_or_bindpose_reconciliation"},
    "communityRouteOverride": "do not run predicted skin-matrix comparison or remap by default; requires separate explicit authorization and remains non-acceptance diagnostic work",
}

PITFALL_MATRIX: tuple[dict[str, str], ...] = (
    {"symptom": "floating, stretched, or exploding mesh", "rootCause": "wrong parent, mixed bone chain, or bindpose mismatch", "check": "SMR weights/bones/rootBone/bindposes and target hierarchy", "forbidden": "scale body or accept Build as proof", "reversibleFix": "disable staged copy; restore checkpoint; remap used bones", "dynamicAcceptance": "Rest+AFK+Upright+VelocityZ+AngularY with no detachment"},
    {"symptom": "static seam passes but opens in motion", "rootCause": "duplicate Neck/Head chain or wrong neck-weighted target", "check": "weighted bone target and inheritance during GM motion", "forbidden": "front-only screenshot or unnamed camera capture", "reversibleFix": "keep old head; reparent/rebind exact chain", "dynamicAcceptance": "five views at rest and motion, including Back/Bottom"},
    {"symptom": "PhysBone jumps or collider is inert", "rootCause": "donor root/collider/probe anchor left in source hierarchy", "check": "component closure and target readback", "forbidden": "copy component without its live consumers", "reversibleFix": "disable staged branch and rebind target-side references", "dynamicAcceptance": "VelocityZ/AngularY and relevant contact/gesture sweep"},
    {"symptom": "face or menu control silently does nothing", "rootCause": "Mesh/FX/Parameters/Menu contract is partial or path-renamed incorrectly", "check": "all four consumers plus final merged Build projection", "forbidden": "copy one asset or claim from source counts", "reversibleFix": "restore checkpoint; remap exact paths and parameters", "dynamicAcceptance": "face min/max plus left/right gesture fallback and menu readback"},
    {"symptom": "scan says empty but references remain", "rootCause": "truncated/unavailable capability or unsupported atom", "check": "ready, blockingReasons, capabilityGap, needsDccRerig and scan completeness", "forbidden": "interpret zero/truncated as closure complete", "reversibleFix": "stop before write; request missing atom or DCC re-rig", "dynamicAcceptance": "must remain ready=false; no fake pass"},
)

for _skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
    _skill["transplantModes"] = [dict(mode) for mode in TRANSPLANT_MODES]
    _skill["sizeMismatchPriority"] = list(SIZE_MISMATCH_PRIORITY)
    _skill["skinningContract"] = dict(SKINNING_CONTRACT)
    _skill["geometryAlignmentContract"] = dict(GEOMETRY_ALIGNMENT_CONTRACT)
    _skill["rigAndShapeContract"] = dict(RIG_AND_SHAPE_CONTRACT)
    _skill["physboneAbContract"] = dict(PHYSBONE_AB_CONTRACT)
    _skill["poseSweep"] = list(POSE_SWEEP)
    _skill["acceptanceViews"] = [dict(view) for view in ACCEPTANCE_VIEWS]
    _skill["pitfallMatrix"] = [dict(row) for row in PITFALL_MATRIX]
    _skill["failureEtiologyFields"] = list(FAILURE_ETIOLOGY_FIELDS)
    _skill["dynamicAcceptance"] = dict(REQUIRED_DYNAMIC_ACCEPTANCE)
    _skill["singleSlotRemapContract"] = dict(SINGLE_SLOT_REMAP_CONTRACT)
    _skill["capabilityGapContract"] = {
        "missingCapabilityFields": ["capabilityGap", "needsDccRerig"],
        "onMissing": "blocked_not_ready",
        "mustNot": "claim_ready_or_pass_from_unavailable_atom",
        "freeCameraConvention": "workflow may reference capture tool fields angle, rotation, scope, returnedRotation, unobstructed, and evidence",
    }

_part_transplant_skill = next(
    skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
    if skill["name"] == "source-avatar-part-transplant"
)
_part_transplant_skill["partRoutingContract"] = dict(PART_TRANSPLANT_ROUTING_CONTRACT)
_part_transplant_skill["externalPreprocessingContract"] = dict(EXTERNAL_PREPROCESSING_CONTRACT)
_part_transplant_skill["atomicCompositionResultContract"] = dict(ATOMIC_COMPOSITION_RESULT_CONTRACT)
_part_transplant_skill["catEarDeformationDiagnosticsContract"] = dict(CAT_EAR_DEFORMATION_DIAGNOSTICS_CONTRACT)
_part_transplant_skill["communityAvatarModificationContract"] = dict(COMMUNITY_AVATAR_MODIFICATION_CONTRACT)

_face_merge_skill = next(
    skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
    if skill["name"] == "face-tracking-four-piece-merge"
)
_face_merge_skill["headRendererConsumerClosureContract"] = dict(HEAD_RENDERER_CONSUMER_CLOSURE_CONTRACT)
_face_merge_skill["communityAvatarModificationContract"] = dict(COMMUNITY_AVATAR_MODIFICATION_CONTRACT)

for _head_skill_name in (
    "avatar-head-swap", "avatar-head-swap-face-tracked", "avatar-head-swap-gesture-only",
):
    _head_skill = next(
        skill for skill in AVATAR_COMPOSITION_WORKFLOW_SKILLS
        if skill["name"] == _head_skill_name
    )
    _head_skill["atomicCompositionResultContract"] = dict(ATOMIC_COMPOSITION_RESULT_CONTRACT)
    _head_skill["catEarDeformationDiagnosticsContract"] = dict(CAT_EAR_DEFORMATION_DIAGNOSTICS_CONTRACT)
    _head_skill["headRendererConsumerClosureContract"] = dict(HEAD_RENDERER_CONSUMER_CLOSURE_CONTRACT)
    _head_skill["communityAvatarModificationContract"] = dict(COMMUNITY_AVATAR_MODIFICATION_CONTRACT)
    _head_skill["neckSeamResolutionContract"] = dict(NECK_SEAM_RESOLUTION_CONTRACT)
