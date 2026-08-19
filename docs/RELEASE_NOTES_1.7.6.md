# VRCForge 1.7.6

VRCForge 1.7.6 is a Windows x64 visual hotfix. It supersedes 1.7.5 while
leaving the `v1.7.5` tag and Release page unchanged.

## Workspace wallpaper hotfix

- Whole-App wallpaper mode now composites the image and uniform scrim once on
  the shared App background. The left sidebar, center workspace, right rail,
  and both resize hit areas reveal that same composite.
- This removes the two bright vertical bands previously visible at the left
  and right sidebar drag zones in both light and dark themes.
- The wallpaper is anchored to the whole App. Resizing either sidebar changes
  only the pane width and never moves, recenters, rescales or recrops the image.
- Dragging remains available across the same hit areas, and the one-pixel
  indicator remains visible only while hovering or dragging.
- The theme contract now requires the two resize hit areas to remain
  transparent and forbids reintroducing a separate unscreened background.

## Carried-forward corrections

- The workspace header keeps the redundant permission, Core-status and
  pending-approval chips removed.
- General-project writes keep the 1.7.5 approval boundary, separate provider
  reviewer, manual approve/reject/allow-this-kind actions, and Windows
  notification presentation.
- The first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with the fixed
  64-tool catalogue and zero bundled third-party MCP provenance.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify their published SHA-256 digests.

## SHA-256

Artifact SHA-256 values are filled from the strict release manifest after the
clean Windows x64 build.
