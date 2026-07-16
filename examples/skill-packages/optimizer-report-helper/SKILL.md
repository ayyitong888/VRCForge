---
name: optimizer-report-helper
title: Optimizer Report Helper
description: Format one read-only optimizer plan into a concise decision report.
permission-mode: preview
risk-level: medium
allowed-tools:
  - vrcforge_optimization_plan
entrypoint-tool: vrcforge_optimization_plan
support-files:
  - workflows/optimizer-report-helper.json
test-command: python -m pytest tests/test_example_skill_packages.py -q
---

Use this skill when a user wants optimizer scan results explained without
applying optimization. Invoke `vrcforge_optimization_plan` exactly once, then
format its returned plan into `Summary`, `Highest-impact findings`, `PC/Quest
differences`, `Recommended order`, and `Approval-required follow-ups`.

Keep all reported counts, ranks, dependencies, and blockers tied to the tool
result. Do not run an apply-request tool, modify the Unity project, or present
an advisory recommendation as an already completed change.
