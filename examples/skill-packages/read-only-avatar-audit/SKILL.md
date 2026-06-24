---
name: read-only-avatar-audit
title: Read Only Avatar Audit
description: Scan the current avatar and summarize safe, read-only findings.
permission-mode: read_only
risk-level: low
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_avatar_scan
  - vrcforge_optimization_plan
entrypoint-tool: vrcforge_avatar_scan
test-command: python -m pytest tests/test_skill_packages.py -q
---

Use this skill when a user wants an avatar audit without changing Unity assets.
Do not write files, add components, change materials, or request package
installation. Return a concise report with avatar identity, renderer count,
parameter pressure, shader inventory, and recommended next checks.
