---
name: vrcforge-avatar-head-transplant
title: VRChat 换头
description: Transplant a VRChat avatar head onto another body, branching explicitly between face-tracked and gesture-only heads; use for full head replacement, not ordinary face tuning or outfit work.
permission-mode: approval_required
risk-level: high
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_materials
  - vrcforge_plan_shader_tuning
  - vrcforge_apply_shader_tuning
  - vrcforge_preview_material_texture_assignment
  - vrcforge_set_material_texture
  - vrcforge_preview_project_asset_duplicate
  - vrcforge_duplicate_project_asset
  - vrcforge_preview_texture_import_settings
  - vrcforge_set_texture_import_settings
  - vrcforge_read_vrchat_sdk_builder_alerts
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
  - vrcforge_preview_setup_outfit
  - vrcforge_setup_outfit
  - vrcforge_preview_add_modular_avatar_component
  - vrcforge_add_modular_avatar_component
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

## Uncertainty rule

If skeletons, neck seams, materials, accessories, PhysBones, Modular Avatar,
or VRCFury behavior is unclear, first consult relevant mature community
guides, asset-author instructions, and official documentation. Execute only
evidence-supported steps. If evidence or an exact atom is missing, stop and
report `capabilityGap=true`; never guess, invent a tool, or claim unverified
support.

Use this workflow only after identifying one exact donor head and one exact
target Avatar. First inspect whether the retained donor head has a complete
face-tracking contract. Read [references/workflow.md](references/workflow.md)
before planning or requesting any write.

Identify the one active final head renderer before choosing a branch. Read back
`Descriptor.VisemeSkinnedMesh`, lipsync mode, renderer active state, and the
complete expected viseme/gesture/expression animation-binding set. The active
renderer must be the actual viseme/lipsync target and every expected binding
must resolve to its path. An inactive old/donor renderer may remain recoverable
during staging, but it must not remain a Descriptor target, the Animator's sole
target, or the only path satisfying any expected expression binding.

This workflow requires VRCForge 1.7.9 or newer because true named `Bottom`
capture and the causal result contract are hard acceptance gates. On an older
runtime, stop with `ready=false` and the exact missing capability; never
degrade to a mislabeled view or discard the cause.

Select exactly one branch:

- `gesture-only`: retain compatible donor gestures and target body/wardrobe
  controls; copy no face-tracking assets.
- `face-tracked`: preserve Mesh/BlendShapes, FX/gesture animations, Expression
  Parameters, and Expressions Menu as one four-part contract.

For `face-tracked`, prove closure rather than asset presence: every expected
controller binding resolves to an existing BlendShape on the active renderer,
every retained Parameter and Menu control reaches that controller/renderer
consumer, and the Descriptor points to the same active renderer and merged
assets. Missing bindings, orphan Parameters/Menu controls, or an inactive/donor
renderer as the only consumer are `ready=false` hard failures.

The community-established, Unity-first head route is: match overall head/body scale; place
the head Prefab under the target Avatar; run MA Setup Outfit and Merge Armature
(`Match bone names to integration target` when required); then use MA
ReplaceObject to preserve the body-side Body path. VRCForge can preview/run
Setup Outfit and add Merge Armature, but if no exact ReplaceObject atom is
available, stop with `capabilityGap=true`, `ready=false` for that exact
manual/external step—never substitute a guessed reference rewrite. Mark the
original target head/face `EditorOnly`, reconfigure EyeBone, viseme LipSync,
Eyelids, and ViewPoint, then repair every changed FX animation path.

Diagnose the neck in order: visible geometry, pose-dependent attachment,
lighting/shader differences, exact renderer/material-slot ownership, and only
then the visible neck/front-jaw texture. Preserve the user's actually assigned
body material, body texture, masks, and details; never silently replace them
with an assumed default or a third-party candidate. Load the materials block,
match shader and Probe Anchor, and preview only minimal supported shadow or
lighting changes. Local UV painting and mesh editing are not VRCForge atoms:
when needed, stop with `capabilityGap=true`, `ready=false`, and
`failureCause=seam_authoring_atom_unavailable` until the user provides an
externally authored head-texture copy; then preview and approve its exact
material-slot assignment. Protect mouth, nose, eyes, heterochromia, alpha, and
every pixel outside the confirmed interface. Mesh overlap, MeshHoleShrinker,
a collar, or a color-matched primitive are concealment fallbacks only when the
user explicitly accepts them, not proof of a repaired seam. Do not manipulate
neck vertices in C#, match neck-ring diameter programmatically, or use a camera
occluder as proof. Accept only same-framing
Rest/AFK free-camera pixels from front/back/left/right (plus VRCForge's stricter
Bottom view when required) at roughly 1–2 m normal VRChat viewing distance.

Matrix, determinant, bindpose, transform-guessing, and sub-millimeter
comparisons are prohibited by default and never count as visual
acceptance. A diagnostic call requires a separate explicit user authorization
and remains diagnostic only. If any one step takes 20 minutes without a visible
candidate, stop, report the current state with a screenshot, and ask for
direction instead of continuing exploration.

Keep the target body, body rig, and clothing scale unchanged. Stage the donor
head inactive, and first fit the imported whole head with uniform local scale
and position in Unity. If the actual neck aperture/mesh shape still cannot fit,
stop for DCC mesh work instead of scaling the body. Read back world/local
transforms and renderer bone use, and keep the old target head recoverable until
all acceptance gates pass.

Treat donor cat ears/tails as `independent_skeleton_physbone`, not as rigid
glasses and not as clothing. Move the whole accessory plus its complete bounded
local sway chain. Create two distinct nodes: `installContainer` owns uniform
size/position fitting, while `swayChainRoot` and every descendant bone keep Rest
local scale `(1,1,1)`. Cat ears must set MA Bone Proxy target=`Head`, or directly parent
`installContainer` to the target Avatar's `Head` as the exact equivalent; tails
use their separately declared target such as `Hips`. Rebind and read back the
PhysBone Root Transform, Colliders, probe anchors, constraint sources, contacts,
and every other external object reference. No reference may remain in the donor
Avatar, and every non-zero renderer used bone must remain closed.

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
other unweighted null slots are informational and must not block. Outside the
default community route, and only after separate matrix authorization, a
single-slot remap may compare predicted skinning metrics against the donor-chain
baseline; execute only when the target is materially closer.
Otherwise return `needsDccRerig=true`, `ready=false`. Also require an unobstructed Rest/AFK/Upright/VelocityZ/
AngularY/body-extreme/face-extreme sweep across five views. Missing atoms must
return `capabilityGap` and, when appropriate, `needsDccRerig` with `ready=false`;
never simulate a pass.
Record the runtime baseline before the sweep, read it back after every pose,
and restore/read back the baseline before acceptance; failed restoration blocks.

Only outside the community route and after separate diagnostic authorization
may neck analysis measure ring center, outward normal, radius, attachment frame,
pivot, and bone roll. The default route is Unity-first fitting plus layered
visible-interface repair; overlap concealment requires explicit user choice.
Only the imported head's local transform may be fitted. Preserve target rest
bindposes, transfer only the minimal weighted chain, and preserve Basis,
BlendShape order, and exact ARKit names. PhysBone A/B isolation must record,
temporarily disable, capture, restore, and read back both components. Any
unreconciled ring, bindpose, weight, Shape Key, or restoration failure returns
`needsDccRerig=true`, `ready=false`, and a concrete `failureCause`.

For cat ears, `vrcforge_inspect_skinned_mesh_deformation` is diagnostic only and
must not run without separate explicit user authorization:
record finite-vertex, Rest/world AABB, distance percentile, and reconstructed
skin-matrix metrics before motion and after Rest, AFK, head-down, left-turn,
and right-turn poses. Rest and AFK must use the exact same camera position,
target, projection, crop, and close-root framing across Front, both Sides, Back,
and Bottom; pair later motion poses to that Rest framing too. Any ear-root
drift, pose-dependent gap/seam size, sudden AABB inflation/collapse/translation,
non-finite vertex, or unexplained skin-matrix scale/translation jump is reported
as an authorized diagnostic anomaly, not visual pass/fail evidence; do not
"repair matrices" as a normal user step.
