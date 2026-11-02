# VRCForge 1.4.0

## Highlights

- The `v1.4.0` Unity package bundles the project Core and release integration files under `Assets/VRCForge`.
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
  versioned auto-connect preference, and requests removal of the product-owned
  asset root. If Unity cannot safely remove it, the command reports the failure
  and leaves the remaining files for manual review.

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
- The Windows installers in this release are not code-signed. Download only
  the four assets attached to this release and verify their published SHA-256
  values when your environment requires it.

## Included release assets

- `VRCForge.unitypackage`
- `VRCForge_Windows_x64_1.4.0.zip`
- `VRCForge_Offline_Installer_x64.exe`
- `VRCForge_Web_Installer_x64.exe`
