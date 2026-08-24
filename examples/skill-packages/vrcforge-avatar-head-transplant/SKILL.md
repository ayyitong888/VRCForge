---
name: vrcforge-avatar-head-transplant
title: VRCForge Avatar Head Transplant
description: Transplant a VRChat avatar head onto another body, branching explicitly between face-tracked and gesture-only heads; use for full head replacement, not ordinary face tuning or outfit work.
permission-mode: approval_required
risk-level: high
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_blendshapes
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_inspect_skinned_mesh_deformation
  - vrcforge_preview_scene_object_duplicate
  - vrcforge_duplicate_scene_object
  - vrcforge_reparent_gameobject
  - vrcforge_set_property
  - vrcforge_set_gameobject_active
  - vrcforge_preview_atomic_reference_rename
  - vrcforge_atomic_reference_rename
  - vrcforge_preview_manage_expression_parameters
  - vrcforge_manage_expression_parameters
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_preview_write_avatar_descriptor
  - vrcforge_write_avatar_descriptor
  - vrcforge_gesture_manager_enter_play_mode
  - vrcforge_gesture_manager_set_parameter
  - vrcforge_capture_status
  - vrcforge_capture_screenshot
  - vrcforge_build_test_readiness
  - vrcforge_build_test_avatar
support-files:
  - workflows/avatar-head-transplant.json
  - references/workflow.md
---

Use this workflow only after identifying one exact donor head and one exact
target Avatar. First inspect whether the retained donor head has a complete
face-tracking contract. Read [references/workflow.md](references/workflow.md)
before planning or requesting any write.

This workflow requires VRCForge 1.7.9 or newer because true named `Bottom`
capture and the causal result contract are hard acceptance gates. On an older
runtime, stop with `ready=false` and the exact missing capability; never
degrade to a mislabeled view or discard the cause.

Select exactly one branch:

- `gesture-only`: retain compatible donor gestures and target body/wardrobe
  controls; copy no face-tracking assets.
- `face-tracked`: preserve Mesh/BlendShapes, FX/gesture animations, Expression
  Parameters, and Expressions Menu as one four-part contract.

Keep the target body, body rig, and clothing scale unchanged. Stage the donor
head inactive, and first fit the imported whole head with uniform local scale
and position in Unity. If the actual neck aperture/mesh shape still cannot fit,
stop for DCC mesh work instead of scaling the body. Read back world/local
transforms and renderer bone use, and keep the old target head recoverable until
all acceptance gates pass.

Treat donor cat ears/tails as `independent_skeleton_physbone`, not as rigid
glasses and not as clothing. Move the whole accessory plus its complete bounded
local sway chain, attach a whole-prefab fit container under Head/the exact target
bone, keep the actual sway-chain root local scale `(1,1,1)` and internal TRS,
and explicitly rebind PhysBone Root Transform and every consumed Collider. The
renderer must retain closure for every non-zero used bone.

All Unity writes remain supervised. After an accepted preview, let the runtime
approval layer expose and invoke exactly one matching `vrcforge_*` write atom;
never combine unrelated mutations under one approval. Rollback is a separate
user decision bound to an exact checkpoint and reason.

Treat a tool call and the domain result separately. `success=true/status=ok`
can coexist with `ready=false`. Internal Agent loop and external MCP Agent must
preserve the same nested `result`, `success`, `status`, `error`, `failureCause`,
`rootCause`, `causeChain`, `failedStep`, and `diagnostics`; never replace a
specific handler cause with a generic workflow failure. Do not retry a write
while commit state is unknown. A Build & Test result cannot override a visible
neck gap or a missing dynamic control.

The 5.6-sol contract requires route classification (`rigid`, same/foreign
skeleton SMR, independent-skeleton PhysBone, or Animator/Menu), mandatory
`allUsedBonesResolved`, `allUsedBindposesResolved`, `noOutOfRangeWeights`,
`mixedChainClosure`, and `safeForWeightedRemap` checks. `nullBoneCount` and
other unweighted null slots are informational and must not block. Before any
single-slot remap, compare current/target predicted skinning metrics against
the donor-chain baseline; execute only when the target is materially closer.
Otherwise return `needsDccRerig=true`, `ready=false`. Also require an unobstructed Rest/AFK/Upright/VelocityZ/
AngularY/body-extreme/face-extreme sweep across five views. Missing atoms must
return `capabilityGap` and, when appropriate, `needsDccRerig` with `ready=false`;
never simulate a pass.
Record the runtime baseline before the sweep, read it back after every pose,
and restore/read back the baseline before acceptance; failed restoration blocks.

For neck geometry, measure source and target ring center, outward normal,
radius, attachment frame, pivot, and bone roll in the target Neck rest frame.
Only the imported head's local transform may be fitted. Preserve target rest
bindposes, transfer only the minimal weighted chain, and preserve Basis,
BlendShape order, and exact ARKit names. PhysBone A/B isolation must record,
temporarily disable, capture, restore, and read back both components. Any
unreconciled ring, bindpose, weight, Shape Key, or restoration failure returns
`needsDccRerig=true`, `ready=false`, and a concrete `failureCause`.

For cat ears, `vrcforge_inspect_skinned_mesh_deformation` is diagnostic only:
record finite-vertex, Rest/world AABB, distance percentile, and reconstructed
skin-matrix metrics before motion and after Rest, AFK, head-down, left-turn,
and right-turn poses. Pair every motion capture with the exact same close-root
camera used at Rest across Front, both Sides, Back, and Bottom. Any ear-root
drift, pose-dependent gap/seam size, sudden AABB inflation/collapse/translation,
non-finite vertex, or unexplained skin-matrix scale/translation jump is a hard
failure; do not "repair matrices" as a normal user step.
