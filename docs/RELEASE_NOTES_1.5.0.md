# VRCForge 1.5.0

## Highlights

- VRCForge's desktop, runtime, approval, checkpoint, Unity workflow, Skill,
  project, and diagnostic domains now use explicit typed owners instead of the
  former Dashboard and Gateway compatibility facades. The 1.5 migration seam
  inventory is empty; user-facing routes and supervised write contracts remain
  in place.
- Startup now restores the last confirmed Unity project without automatically
  reopening an old conversation. The center workbench appears first, while the
  project and environment sidebars finish loading asynchronously.
- The environment rail focuses on project, backend, MCP package, MCP bridge,
  Unity instance, VRCForge Unity tools, and pending-approval readiness. Runtime
  history and review surfaces remain available on demand in the conversation.
- Closing the main window hides VRCForge to the system tray and keeps its owned
  backend work alive. The tray Quit action remains the explicit termination
  path.
- Active Computer Use state remains visible across conversation changes without
  loading historical desktop actions during startup.
- App-mediated writes still require the existing approval, checkpoint,
  readback, validation, and separately approved restore flow. The release does
  not add a direct or unauthenticated Unity write path.
- The project Core continues to use only MCP 2.0 (`2026-07-28`) and the fixed
  64-tool VRCForge contract. MCP 1.x edge compatibility and outbound federation
  are not included in 1.5.0.

## Before you install

- `1.4.0` remains the latest published stable release until the 1.5.0 Draft,
  strict artifacts, and acceptance evidence have been verified and the release
  owner explicitly publishes it.
- The intended 1.5 update target is an existing `1.4.0` installation. Final
  support is contingent on the clean 1.4-to-1.5 packaged upgrade gate. Back up
  the Unity project before importing the 1.5 Unity package, and preserve
  `%LOCALAPPDATA%\VRCForge\agentic-app` so chats, settings, memories, Skills,
  and checkpoints remain available.
- The breaking `1.3.6` to `1.4.0` boundary is unchanged. Do not overwrite a
  `1.3.6` App or Unity integration with 1.5.0; follow the fresh 1.4+ migration
  boundary documented in the user manual.
- The Windows installers are not code-signed. Download only assets attached to
  the VRCForge GitHub Release and review Windows prompts before continuing.
- Back up the Unity project before any operation that can write project data.
  Unknown or user-modified files are preserved and reported rather than being
  treated as automatic cleanup targets.

## Included release assets

- `VRCForge.unitypackage`
- `VRCForge_Windows_x64_1.5.0.zip`
- `VRCForge_Offline_Installer_x64.exe`
- `VRCForge_Web_Installer_x64.exe`

These source-bound notes do not claim that release assets, hashes, a tag, a
Draft, or publication already exist. Those claims become valid only after the
strict build and GitHub Draft readback gates complete.
