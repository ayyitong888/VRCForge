# VRCForge 1.7.4

VRCForge 1.7.4 is a corrective release for App Update, personal appearance
settings, checkpoint explanations and three constrained-width workspaces. It
is a documentation-corrected package and supersedes 1.7.3; the `v1.7.3` tag
and Release page remain untouched.

## Update and personal settings

- Startup update checks remain background-only and silent unless a newer
  version is available.
- The tray **Check for updates** action performs a fresh check and reports one
  clear result: update available, already current, or check failed.
- The update button opens the validated official GitHub Release in the default
  browser. It does not download or install an update.
- Users can disable future automatic startup checks from the update dialog.
  That preference, themes, custom colours and managed background images survive
  App upgrades.

## UI corrections

- Optimization, Protection and Skills now lay out from the actual center
  workspace width, including when both sidebars are visible. Cards and fields
  stack cleanly instead of squeezing labels character by character.
- Visible labels on those pages are shorter and explain the control or state
  directly.
- Checkpoint archives distinguish **Latest retained** from **Recovery in
  progress** while keeping both protected from deletion.

## Carried-forward release gates

- The first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with the fixed
  64-tool catalogue and zero bundled third-party MCP provenance.
- The Unity Editor-only approval-receipt guard remains in the package so the
  integration does not leak `UnityEditor` APIs into player/build compilation.
- Goal, Memory Dreaming, theme/background coverage and custom-colour input
  behavior remain unchanged.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify their published SHA-256 digests.

## Supersedes 1.7.3

The `v1.7.4` tag and package replace 1.7.3 as the current stable release. The
1.7.3 tag and Release page remain available and unchanged; users should use
the 1.7.4 assets below.

## SHA-256

- `VRCForge.unitypackage`: `pending`
- `VRCForge_Windows_x64_1.7.4.zip`: `pending`
- `VRCForge_Offline_Installer_x64.exe`: `pending`
- `VRCForge_Web_Installer_x64.exe`: `pending`
- `release-manifest.json`: `pending`
