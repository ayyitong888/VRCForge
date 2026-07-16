---
name: outfit-naming-helper
title: Outfit Naming Helper
description: Propose normalized outfit names from read-only binding evidence while unsafe object and parameter renames stay blocked.
permission-mode: read_only
risk-level: medium
allowed-tools:
  - vrcforge_scan_animation_bindings
entrypoint-tool: vrcforge_scan_animation_bindings
support-files:
  - workflows/outfit-naming-helper.json
test-command: python -m pytest tests/test_example_skill_packages.py -q
---

Use this skill to inspect animation-binding paths and propose normalized outfit
object and parameter names. Convert labels to stable `Outfit_<PascalCase>`
names, preserve meaningful alphanumeric tokens, and keep proposals at most 32
characters. Return the old name, proposed name, and every binding-path warning.

This package is proposal-only. Do not call `vrcforge_rename_gameobject`,
`vrcforge_request_apply`, or any other write tool. Renaming a GameObject can
break AnimationClip binding paths and serialized component or constraint
references; renaming an expression parameter can break menus, FX conditions,
and animation bindings. Both writes remain blocked until VRCForge has an
atomic reference-migration primitive with validation and rollback proof.
