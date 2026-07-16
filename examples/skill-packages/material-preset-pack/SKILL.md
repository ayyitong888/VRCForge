---
name: material-preset-pack
title: Material Preset Pack
description: Apply one bundled semantic material preset through the supervised shader tuning channel.
permission-mode: approval_required
risk-level: high
allowed-tools:
  - vrcforge_request_apply
  - vrcforge_apply_shader_tuning
support-files:
  - workflows/material-preset-pack.json
  - presets/material-presets.json
test-command: python -m pytest tests/test_example_skill_packages.py -q
---

Use this skill only when the user selects one bundled preset and supplies a
current shader inventory plus explicit material targets. Resolve the selected
preset into `changes` for those targets without inventing material ids.

Submit exactly one `vrcforge_request_apply` call whose `target_tool` is
`vrcforge_apply_shader_tuning`. The request arguments must contain the current
project root, avatar path, inventory, and resolved changes. Never call the
write target directly, never combine presets, and never bypass the host's
approval, checkpoint, validation, or rollback path.
