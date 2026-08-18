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

- `VRCForge.unitypackage`: `b00e34d2658e7180f752f7f0a99dddebe11d87af1ba667f47d9ada40a118e182`
- `VRCForge_Windows_x64_1.7.4.zip`: `ebda1851b958ba21a72bcbd26f4298078ef38d579cf98eb2b5abb44de0a84780`
- `VRCForge_Offline_Installer_x64.exe`: `81fe5278c20bd10cb75916eed7c7c1dc19b949f49effedfbed01af7b2f923497`
- `VRCForge_Web_Installer_x64.exe`: `450a5c0bc6b128f626f370e62fa183bd7ebc6862d4db31e787c23a181fd27efb`
- `release-manifest.json`: `c1f625c9521a163f35694e271d831cab7b70f80813c385fd4296462bb11ea86b`
