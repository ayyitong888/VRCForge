# Skill Package Examples

These directories are source trees that can be exported with
`SkillPackageService.export_dev(...)` or the desktop Skill Manager export flow.
They intentionally do not commit generated `.vsk` archives.

- `read-only-avatar-audit`: a low-risk, read-only package that demonstrates the
  minimum manifest, `SKILL.md`, workflow, permission, and dry-run shape.
- `validation-report-extension`: one read-only validation call with additional
  presentation-only report sections.
- `material-preset-pack`: one approval request bound to the existing
  `vrcforge_apply_shader_tuning` write channel and bundled semantic presets.
- `outfit-naming-helper`: a scan/preview helper whose final primitive requests
  one existing atomic object-or-parameter reference migration through the
  supervised Unity write wrapper.
- `optimizer-report-helper`: one read-only optimizer-plan call formatted as a
  PC/Quest decision report.

Every workflow remains primitive: one declared tool call per execution. Write
examples request approval and rely on VRCForge for checkpoint, apply,
validation, readback, and rollback handling; they never expose their underlying
write handlers as directly callable Skill tools.

## Skill SDK quick start

Generate a reviewable source tree with one static tool call:

```powershell
python tools/vrcforge_cli.py skill init .\my-avatar-report `
  --id community.example.my-avatar-report `
  --tool vrcforge_run_validation_report `
  --permission read_project `
  --permission unity_run_validation `
  --permission unity_scan_scene
```

The generator writes `manifest.json`, `SKILL.md`, one workflow JSON, a README,
and a pytest smoke fixture. It refuses any non-empty output directory unless
`--force` is explicit; forced generation replaces the tree so unrelated files
cannot leak into a later package. After exporting the source through the
desktop Skill Manager or `SkillPackageService`, validate the package lock and
every declared file digest locally:

```powershell
python tools/vrcforge_cli.py --json skill lock-validate .\my-avatar-report.vsk
```

These two commands are local SDK operations; they do not connect to the
VRCForge runtime or apply Unity changes.
