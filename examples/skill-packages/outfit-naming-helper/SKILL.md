---
name: outfit-naming-helper
title: Outfit Naming Helper
description: Propose normalized outfit names and request one supervised atomic reference migration after preview.
permission-mode: approval_required
risk-level: high
allowed-tools:
  - vrcforge_scan_animation_bindings
  - vrcforge_preview_atomic_reference_rename
  - vrcforge_request_apply
support-files:
  - workflows/outfit-naming-helper.json
test-command: python -m pytest tests/test_example_skill_packages.py -q
---

Use this skill when the user asks to normalize one outfit object name or one
expression parameter and the affected avatar, scene, and current binding
evidence are known. Do not use it for bulk renames, unrelated hierarchy
cleanup, or a request that has not identified the exact old and new names.

First call `vrcforge_scan_animation_bindings` in a read-only turn. Convert the
selected label to a stable `Outfit_<PascalCase>` name, preserve meaningful
alphanumeric tokens, keep it at most 32 characters, and report every affected
binding or reference. Then call `vrcforge_preview_atomic_reference_rename` in a
separate preview turn with exactly one `game_object` or `parameter` operation.

Only after the user chooses that exact preview, submit one
`vrcforge_request_apply` call. Set `target_tool` to
`vrcforge_unity_mcp_write`; set its `arguments` to the selected workflow
wrapper containing `projectPath`, `toolName: vrc_atomic_reference_rename`, and
the exact previewed operation arguments. Never call
`vrcforge_unity_mcp_write`, `vrc_atomic_reference_rename`, or a raw rename tool
directly. Never change the operation, target path, or new name after preview.
VRCForge owns approval, checkpoint creation, validation, readback, and the
separately approved checkpoint restore.
