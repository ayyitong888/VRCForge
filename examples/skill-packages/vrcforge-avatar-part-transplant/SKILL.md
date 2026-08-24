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

Duplicate into an inactive target staging branch; keep the source unchanged.
Reparent, fit, and rebind only proven dependencies. Enable the staged copy only
after exact readback succeeds. Donor removal is a separate workflow with a new
closure scan and approval.

All writes remain supervised. Bind each smallest actual `vrcforge_*` write atom
to its accepted preview and let the runtime approval layer expose/invoke it.
Rollback is separately approved and never automatic. Keep tool-call success
separate from domain readiness, preserve exact causal fields, and never retry
an unknown-commit write before target readback.
