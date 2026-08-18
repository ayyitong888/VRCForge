# VRCForge 1.7.3

VRCForge 1.7.3 is a corrective patch that makes the already-visible Goal
surface usable through both its user command and the Agent Runtime. It
is a documentation-corrected package and supersedes 1.7.2; the `v1.7.2` tag
and Release page remain untouched.

## Goal control correction

- Users retain `/goal <objective>`, bare `/goal`, and `/goal
  pause|resume|clear`.
- The Agent Runtime exposes exactly `get_goal`, `create_goal` and `update_goal`.
- Agent creation is valid only after an explicit user request and cannot
  silently replace an unfinished Goal.
- Agent update is limited to evidence-backed completion or blocked evidence.
  The same blocking reason must recur in three distinct consecutive Goal turns
  before the Goal becomes blocked; user resume resets that audit.
- Pause, resume and clear remain user controls. Goal has no token budget,
  provider-price lookup or cost conversion.
- Runtime-owned chat, session, project and turn scope overrides any
  model-supplied scope fields.

## Carried-forward release gates

- The first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with the fixed
  64-tool catalogue and zero bundled third-party MCP provenance.
- The Unity Editor-only approval-receipt guard remains in the package so the
  integration does not leak `UnityEditor` APIs into player/build compilation.
- The 1.7.2 Memory Dreaming, background coverage and custom-colour behavior is
  unchanged.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify their published SHA-256 digests.

## Supersedes 1.7.2

The `v1.7.3` tag and package replace 1.7.2 as the current stable release. The
1.7.2 tag and Release page remain available and unchanged; users should use
the 1.7.3 assets below.

## SHA-256

- `VRCForge.unitypackage`: `pending`
- `VRCForge_Windows_x64_1.7.3.zip`: `pending`
- `VRCForge_Offline_Installer_x64.exe`: `pending`
- `VRCForge_Web_Installer_x64.exe`: `pending`
- `release-manifest.json`: `pending`
