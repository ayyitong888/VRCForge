---
name: validation-report-extension
title: Validation Report Extension
description: Add concise, read-only review sections to the standard validation report.
permission-mode: read_only
risk-level: medium
allowed-tools:
  - vrcforge_run_validation_report
entrypoint-tool: vrcforge_run_validation_report
support-files:
  - workflows/validation-report-extension.json
test-command: python -m pytest tests/test_example_skill_packages.py -q
---

Use this skill when a user wants the standard validation result reorganized
for review. Invoke `vrcforge_run_validation_report` exactly once and use that
returned report as the only source of project facts.

Preserve the original findings, then append three presentation-only sections:
`Release blockers`, `Cross-platform warnings`, and `Suggested next checks`.
Every added item must cite an existing finding or explicitly say that the
report contained no supporting evidence. Do not repair findings, write files,
rerun scanners, or request approval.
