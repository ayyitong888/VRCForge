---
name: vrcforge-avatar-part-transplant
title: VRCForge Avatar Part Transplant
description: Copy one bounded hair, ear, tail, accessory, mesh, or supporting branch from a source VRChat avatar into a target avatar; use when dependencies and attachment motion must be preserved, not for whole-avatar copying or donor deletion.
permission-mode: approval_required
risk-level: high
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_inspect_skinned_mesh_deformation
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_preview_scene_object_duplicate
  - vrcforge_duplicate_scene_object
  - vrcforge_reparent_gameobject
  - vrcforge_set_property
  - vrcforge_set_gameobject_active
  - vrcforge_preview_atomic_reference_rename
  - vrcforge_atomic_reference_rename
  - vrcforge_preview_add_modular_avatar_component
  - vrcforge_add_modular_avatar_component
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_preview_ensure_animator_state
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_parameter
  - vrcforge_ensure_animator_state
  - vrcforge_ensure_expression_menu_control
  - vrcforge_gesture_manager_enter_play_mode
  - vrcforge_gesture_manager_set_parameter
  - vrcforge_capture_status
  - vrcforge_capture_screenshot
  - vrcforge_build_test_readiness
  - vrcforge_build_test_avatar
support-files:
  - workflows/avatar-part-transplant.json
  - references/workflow.md
---

Use this workflow for one exact user-authorized donor part and one exact target
Avatar. Read [references/workflow.md](references/workflow.md) before planning
or requesting a write.

This workflow requires VRCForge 1.7.9 or newer because true named `Bottom`
capture for underside-dependent parts and the causal result contract are hard
acceptance gates. On an older runtime, stop with `ready=false` and the exact
missing capability; never silently downgrade or report the transplant ready.

Derive the minimal dependency closure from live renderer weights, `rootBone`,
PhysBone roots/colliders, probe anchors, constraints, animations, FX, Menu, and
Parameters. Never assume a visible mesh is self-contained, and never duplicate
the donor Avatar Descriptor, whole Animator, unrelated skeleton, or body.

Classify before writing:

- A `rigid` accessory (glasses, earrings, hair clip, weapon, rigid wing) has no
  donor-body skin. Attach it to one exact target bone by direct parent or MA
  Bone Proxy, fit/read back local TRS, and optionally compose a complete toggle.
  Never run Setup Outfit or Merge Armature for this route.
- `independent_skeleton_physbone` accessories such as cat ears or tails keep
  their complete bounded sway chain. Move the whole accessory Prefab/branch,
  attach a whole-prefab fit container to Head/the intended target bone, keep
  the actual sway-chain root at local scale `(1,1,1)` with internal chain TRS
  unchanged, and explicitly rebind PhysBone Root Transform plus every consumed
  Collider/probe reference. Keep renderer closure for every non-zero used bone;
  do not remap the local sway chain onto the target armature unless the part is
  reclassified as a weighted SMR route.
- `same_skeleton_smr` and `foreign_skeleton_smr` are for clothing or
  close-fitting/donor-body-weighted accessories. Only these routes use the SMR
  bones/rootBone/bindpose contract and target-compatible armature merge or
  explicit used-bone remap.

For a cross-model size mismatch, first fit the imported whole Prefab/container
with uniform scale and position in Unity. Do not scale the target body or change
the internal sway-chain root away from 1. Escalate to Blender only when the
actual mesh aperture/shape/proportions or donor-body-specific weights cannot be
reconciled; bindpose/skin-matrix readback is diagnosis, not a routine manual
matrix-editing recipe.

Module Creator is optional external/manual preprocessing that can turn a donor
branch into a reusable Prefab; VRCForge has no Module Creator export atom. If
that export is required, return `capabilityGap=true`, `ready=false`, and the
specific missing capability instead of claiming an export. Escalate to Blender
only when mesh shape/mesh-level size must be edited or the part retains
donor-body-specific weights—not for target-bone attachment, PhysBone reference
rebinding, or attachment-root scale normalization.

Duplicate into an inactive target staging branch; keep the source unchanged.
Reparent, fit, and rebind only proven dependencies. Enable the staged copy only
after exact readback succeeds. Donor removal is a separate workflow with a new
closure scan and approval.

All writes remain supervised. Bind each smallest actual `vrcforge_*` write atom
to its accepted preview and let the runtime approval layer expose/invoke it.
Rollback is separately approved and never automatic. Keep tool-call success
separate from domain readiness, preserve exact causal fields, and never retry
an unknown-commit write before target readback.

The 5.6-sol contract requires route classification (rigid, same/foreign
skeleton SMR, independent skeleton + PhysBone, or Animator/Menu), mandatory
`allUsedBonesResolved`/`allUsedBindposesResolved`/`noOutOfRangeWeights`/
`mixedChainClosure`/`safeForWeightedRemap` checks. `nullBoneCount` and other
unweighted null slots are informational, not blockers. Before any single-slot
remap, compare current/target predicted skinning metrics against the donor-chain
baseline; execute only when the target is materially closer, otherwise return
`needsDccRerig=true`, `ready=false`. Require an unobstructed
Rest/AFK/Upright/VelocityZ/AngularY/body-extreme/
face-extreme sweep across Front, both Sides, Back, and Bottom. Missing atoms
must return `capabilityGap` and, when appropriate, `needsDccRerig` with
`ready=false`; never claim acceptance from a Build result.
Record and read back the runtime baseline around every pose; restore it after
the sweep, and block acceptance if restoration readback fails.

For neck or other attachment geometry, measure the source/target ring center,
normal, radius, pivot, and bone roll in the target rest frame before fitting.
Only the imported part's local transform may change; never scale the target
body/armature or use occlusion as a geometry fix. Preserve target bindposes,
transfer only the minimal local weights, and retain Basis/BlendShape order and
exact ARKit names. PhysBone A/B isolation must record state, temporarily
disable, capture Rest/dynamic views, restore, and read back. Any unresolved
geometry, bindpose, weight, Shape Key, or restore condition returns
`needsDccRerig=true`, `ready=false`, plus a specific `failureCause`.

Every composed atom keeps its own `step`, `tool`, nested `result`, `success`,
`status`, `ready`, `error`, `failureCause`, `rootCause`, `causeChain`,
`failedStep`, and `diagnostics` identically on the internal Agent loop and the
external MCP Agent. Stop on the first failed step; do not replace a specific
tool/handler error with a generic workflow failure, drop diagnostics, or
continue after a failed write.

For cat ears and tails, use `vrcforge_inspect_skinned_mesh_deformation` only as
a diagnostic gate. Record finite vertices, Rest/world AABB, distance
percentiles, and reconstructed skin-matrix metrics at Rest, then at AFK,
head-down, left-turn, and right-turn with the exact same close-root Front, both
Sides, Back, and Bottom cameras. Ear-root drift, a gap/seam that grows or
shrinks between poses, sudden AABB inflation/collapse/translation, non-finite
vertices, or an unexplained skin-matrix scale/translation jump is a hard fail.
