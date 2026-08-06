# VRCForge 1.4.0

## Highlights

- The Unity package is self-contained: import `VRCForge.unitypackage` into a
  project, open that project, and connect VRCForge App directly to its
  project-owned MCP Core.
- The bundled Core uses only MCP 2.0 (`2026-07-28`) and exposes the 64
  VRCForge product tools provided by this release.
- Requests from clients older than protocol `2026-07-28` fail with an explicit
  update instruction instead of entering a compatibility path.
- Windows approval notifications now use the VRCForge name and icon in both
  popup banners and the Action Center application group. Settings > General
  includes localized version, MCP, safety, and license information.
- Unity logs a conflict warning when a known third-party MCP package is still
  declared or present in the project; VRCForge does not remove it automatically.
- DeepSeek `deepseek-v4-flash` uses the stateless Responses adapter, and provider
  fields containing non-visible or non-ASCII characters now fail locally with a
  clear validation error instead of reaching a model-list request.
- Normal startup through `VRCForge.exe` and the project-owned Unity Core does not
  open a separate command-console window. The manual `start_dashboard.cmd`
  compatibility/debug entry remains available in the portable payload.
- App-driven write operations remain protected by the approval, checkpoint,
  readback, and separately approved restore workflow.
- Approval requests appear in the conversation composer without hiding chat
  history. The primary action allows the request once; eligible future-category
  approval is available from its chevron menu. Windows notifications identify
  VRCForge by name and icon and expose the same one-use allow/reject decision.
- Pinned projects stay first; other projects are ordered by their latest chat
  activity.
- Unity projects can remove the integration from
  `VRCForge > Uninstall VRCForge...`; the command stops the Core, clears the
  versioned auto-connect preference, and removes the product-owned asset root.
- The package contains VRCForge-owned command, input, and result contracts and
  no third-party MCP runtime, protocol asset, residual GUID, or notice. Known
  third-party package identifiers appear only in conflict detection.
- Release builds scan the actual packaging inputs before build and all four
  generated assets afterward for private keys, credentials, tokens, and local
  machine paths. A finding stops the build without printing the matched value.

## Before you install

- `1.4.0` is a breaking install boundary. Overwrite installation or Unity
  package import over `1.3.6` is unsupported. Close VRCForge and Unity, remove
  the old VRCForge App/runtime files, and remove the old project integration
  with its provided uninstall command before installing and importing `1.4.0`
  fresh.
- Do not delete `%LOCALAPPDATA%\VRCForge\agentic-app` or unrelated Unity
  project content during that migration. Preserve configured API keys,
  user-owned `AGENTS.md`, chats, memories, checkpoints, and unrelated assets.
  Unknown or user-modified files are not an automatic cleanup target.
- Back up the Unity project before any operation that can write project data.
- A write operation must be initiated and approved through VRCForge App; the
  Unity package does not grant unauthenticated direct writes.
- Tool availability is not a guarantee that every avatar, asset, or project
  can be changed successfully. Review the plan and readback for the selected
  project.
- The Windows installers in this release are not code-signed. Download only
  the four assets attached to this release and verify their published SHA-256
  values when your environment requires it.
- Administrator-mode install, update, and uninstall execution was explicitly
  deferred for this release cycle. Installer construction and non-privileged
  package checks do not replace that UAC runtime gate.

## Included release assets

- `VRCForge.unitypackage`
- `VRCForge_Windows_x64_1.4.0.zip`
- `VRCForge_Offline_Installer_x64.exe`
- `VRCForge_Web_Installer_x64.exe`
