---
name: vrcforge-avatar-part-transplant
title: VRChat 配件移植
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
  - vrcforge_preview_setup_outfit
  - vrcforge_setup_outfit
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

## Uncertainty rule

If skeletons, neck seams, materials, accessories, PhysBones, Modular Avatar,
or VRCFury behavior is unclear, first consult relevant mature community
guides, asset-author instructions, and official documentation. Execute only
evidence-supported steps. If evidence or an exact atom is missing, stop and
report `capabilityGap=true`; never guess, invent a tool, or claim unverified
support.

Use this workflow for one exact user-authorized donor part and one exact target
Avatar. Read [references/workflow.md](references/workflow.md) before planning
or requesting a write.

Use only source assets that the user owns and whose license permits the
intended private modification or redistribution. The default path is entirely
inside Unity: duplicate the exact donor branch, parent or Bone Proxy it to the
target, fit its local container, preserve or rebind its actual dependencies,
and inspect it in Play Mode. Blender, a fitting service, or Module Creator is a
fallback only after a concrete mesh-shape, donor-weight, or optional prefab-
export requirement is proven; none is a prerequisite for ordinary extraction.

This workflow requires VRCForge 1.7.9 or newer because true named `Bottom`
capture for underside-dependent parts and the causal result contract are hard
acceptance gates. On an older runtime, stop with `ready=false` and the exact
missing capability; never silently downgrade or report the transplant ready.

Derive the minimal dependency closure from live renderer weights, `rootBone`,
PhysBone roots/colliders, probe anchors, constraint sources, contacts, all other
external object references, animations, FX, Menu, and Parameters. Never assume
a visible mesh is self-contained, and never duplicate the donor Avatar
Descriptor, whole Animator, unrelated skeleton, or body.

Classify before writing:

- A `rigid` accessory (glasses, earrings, hair clip, weapon, rigid wing) has no
  donor-body skin. Attach it to one exact target bone by direct parent or MA
  Bone Proxy, fit/read back local TRS, and optionally compose a complete toggle.
  Never run Setup Outfit or Merge Armature for this route.
- `independent_skeleton_physbone` accessories such as cat ears or tails keep
  their complete bounded sway chain. Move the whole accessory Prefab/branch and
  create distinct `installContainer` and `swayChainRoot` nodes. Uniform size and
  position fitting belongs only on `installContainer`; `swayChainRoot` and every
  descendant bone stay at Rest local scale `(1,1,1)`. Cat ears hard-code MA
  Bone Proxy target=`Head`, or directly parent `installContainer` to the target
  Avatar's `Head` as the exact equivalent; tails use their declared `Hips`
  target. Rebind PhysBone Root Transform, every consumed Collider, probe anchor,
  constraint source, contact, and all other external object references. No
  reference may remain in the donor Avatar. Keep renderer closure for every
  non-zero used bone; do not remap the local sway chain onto the target armature
  unless the part is reclassified as a weighted SMR route.
- `same_skeleton_smr` and `foreign_skeleton_smr` are for clothing or
  close-fitting/donor-body-weighted accessories. Only these routes use the SMR
  bones/rootBone/bindpose contract and target-compatible armature merge or
  explicit used-bone remap.

For compatible clothing, import/place the clothing Prefab under the Avatar, run
MA Setup Outfit, then use only the clothing author's documented body
BlendShapes for penetration. For non-compatible clothing, prefer external
MochiFitter only when conversion profiles exist for both source and target;
otherwise use external Blender to scale/position the armature, transfer target
body weights, export FBX, reimport, and then run MA Setup Outfit. Never create a
Unity C# weight-transfer or vertex-realignment script.

Rigid accessories use MA Bone Proxy plus local TRS and require at least two
visible angles; they never use weight transfer or armature merge. Independent
PhysBone accessories remain accessories, not head swaps: use MA Bone Proxy,
scale only `installContainer`, keep `swayChainRoot` and descendants at Rest
scale 1, close all references, then prove natural Play-mode swing with no
distortion or frozen bones. If motion is absent, check Root Transform and
reference closure first.

Matrix, determinant, bindpose, transform-guessing, and sub-millimeter
comparisons are prohibited by default and never count as visual
acceptance. A diagnostic call requires separate explicit authorization and
remains diagnostic only. Pixel acceptance is at roughly 1–2 m normal VRChat
viewing distance. If any one step takes 20 minutes without a visible candidate,
stop, report the current state with a screenshot, and ask for direction.

For a cross-model size mismatch, fit only the imported `installContainer` with
uniform scale and position in Unity. Do not scale the target body or change
the distinct internal `swayChainRoot` or any descendant bone away from its Rest
scale 1. Escalate to Blender only when the
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
unweighted null slots are informational, not blockers. Outside the community
route, and only after separate matrix authorization, a single-slot remap may
compare predicted skinning metrics against the donor-chain baseline; execute
only when the target is materially closer, otherwise return
`needsDccRerig=true`, `ready=false`. Require an unobstructed
Rest/AFK/Upright/VelocityZ/AngularY/body-extreme/
face-extreme sweep across Front, both Sides, Back, and Bottom. Missing atoms
must return `capabilityGap` and, when appropriate, `needsDccRerig` with
`ready=false`; never claim acceptance from a Build result.
Record and read back the runtime baseline around every pose; restore it after
the sweep, and block acceptance if restoration readback fails.

Only outside the community route and after separate diagnostic authorization
may attachment analysis measure ring center, normal, radius, pivot, and bone
roll. The default head route uses Unity-first fitting and layered visible-seam
repair; concealment is allowed only when the user explicitly chooses it.
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
a separately authorized diagnostic, never as an acceptance gate. When authorized,
record finite vertices, Rest/world AABB, distance
percentiles, and reconstructed skin-matrix metrics at Rest, then at AFK,
head-down, left-turn, and right-turn. Rest and AFK must use the exact same
camera position, target, projection, crop, and close-root framing across Front,
both Sides, Back, and Bottom. Ear-root drift, a gap/seam that grows or
shrinks between poses, sudden AABB inflation/collapse/translation, non-finite
vertices, or an unexplained skin-matrix scale/translation jump is reported as an
authorized diagnostic anomaly, not visual pass/fail evidence.
