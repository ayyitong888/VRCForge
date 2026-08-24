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
head inactive, fit it locally, read back world/local transforms and renderer
bone use, and keep the old target head recoverable until all acceptance gates
pass.

All Unity writes remain supervised. After an accepted preview, let the runtime
approval layer expose and invoke exactly one matching `vrcforge_*` write atom;
never combine unrelated mutations under one approval. Rollback is a separate
user decision bound to an exact checkpoint and reason.

Treat a tool call and the domain result separately. `success=true/status=ok`
can coexist with `ready=false`. Preserve exact blockers and cause fields; do
not retry a write while commit state is unknown. A Build & Test result cannot
override a visible neck gap or a missing dynamic control.
