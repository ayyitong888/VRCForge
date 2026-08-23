# VRCForge 1.7.8

VRCForge 1.7.8 is a Windows x64 corrective release for desktop installation,
external-Agent MCP dogfooding, and supervised avatar-composition workflows. It
supersedes `v1.7.7`; the older tag and Release remain available unchanged.

## Windows installation and desktop UI

- Offline and web installers bind desktop and Start-menu shortcuts to the
  installed `VRCForge.ico` and use the installed directory as their working
  directory, preventing the generic browser-style icon seen after 1.7.7
  installs.
- The desktop WebView suppresses native browser context menus across text and
  non-text surfaces while preserving the App's own controls and shortcuts.

## External MCP boundary

- External MCP exposes composable tools while the connecting Agent retains its
  own planning, retry, and continuation loop; internal Agent permission modes
  are not projected onto that external loop.
- Shared Unity capabilities in the internal Agent loop and external MCP Agent
  use the same canonical input schema, handler, Core atom, and result contract.
  Display names and lazy-load trees may differ, but capability or validation
  drift between the two execution surfaces is a release failure.
- Ordinary scoped writes can execute directly with their declared atomic and
  checkpoint contracts. High-risk, destructive, restore, and advanced actions
  return a confirmation challenge that is bound to the exact tool and argument
  digest for the external Agent to present to the user.
- Write failures report the failing layer, whether mutation occurred, whether
  commit state is known, checkpoint identity, and bounded Unity Console before
  and after snapshots without inventing retry or rollback decisions. Bounded,
  redacted exception causes retain the original failure message and type, so an
  Agent is not left with only a generic wrapper error.
- Every external read, preview, and write tool returns its exact handler success
  or failure object under `result`, with error codes, reasons, details, and
  lower-level Core results preserved. Unity transport now preserves Core tool
  errors by default; only an explicitly marked internal call may request a
  compressed exception. Gateway-added failure facts, exception causes, request
  trace, Console snapshots, and sensitive-field redaction notice are sibling
  context; the Gateway does not replace tool error text or inject its own Agent
  loop into an external Agent connection.
- Core compatibility uses a protocol range rather than product-version or
  executable/content-hash equality. Pre-handshake `server/core-info` reports
  compile-time Core identity, instance id, path-derived `projectId`, tool
  contract/count, and compile status so a failed handshake remains diagnosable.
- Core upgrade readiness waits for the running assembly to report the target
  compile-time version and for the Console compile snapshot to remain clean;
  copied files or a newer disk manifest cannot produce a false ready state when
  Unity retained the old assembly after a compile failure.

## Project lifecycle tools

- External Agents can inspect, plan, create, and register Unity projects through
  backend-neutral project tools without assuming VRChat Creator Companion is
  installed. Detected VCC, ALCOM/vrc-get, and Unity Hub capabilities are
  reported as manager handoffs rather than silently editing their private
  catalogues.
- Project creation freezes the selected local template, stages in the target
  parent, commits by atomic rename, registers the VRCForge project catalogue,
  and returns a receipt-backed rollback contract.
- Project rollback remains a separate high-risk action and moves the exact
  unchanged created project to a visible recovery directory instead of deleting
  it silently.

## Avatar composition and live acceptance

- The external MCP catalogue now exposes the fixed 78-tool Unity Core through
  indexed, on-demand blocks. New primitives cover project-asset duplication,
  inbound-reference closure, editor state, current/new scene saving, Gesture
  Manager runtime inspection, VRChat constraint conversion, local Build & Test,
  and read-only SDK Builder alerts.
- Built-in VRCForge Skills keep the existing head-swap router and provide
  separate face-tracked and gesture-only head-swap workflows. Face tracking is
  treated as one Mesh, FX, Expression Parameters, and Menu contract; the
  gesture-only branch does not copy face-tracking assets.
- A separate source-avatar part-transplant workflow inventories object, bone,
  PhysBone, collider, animation, FX, menu, and parameter dependencies before a
  disabled staged copy is fitted and enabled on the target. The source remains
  unchanged and removal of an old target part stays a separate supervised task.
- Head/neck acceptance requires named front, side, and back captures plus a
  manually verified Bottom view with hair and collars hidden. An open rim,
  visible internal geometry, backface, overlap, or shading break is a hard
  failure; Transform alignment, request receipts, or a successful Build & Test
  cannot override the pixels.

## Packaging and SDK diagnostics

- Portable Core payloads retain the `Assets/VRCForge.meta` root GUID during an
  atomic upgrade instead of allowing Unity to regenerate the package root.
- SDK Builder alert readback distinguishes selected-avatar alerts from other
  project alerts and exposes the exact alert text and blocking state without
  selecting, refreshing, or invoking SDK fixes.
- Local VRChat Build & Test is an explicit supervised atom. Its result keeps
  `localOnly`, upload, publication, job, Console, and produced-bundle facts
  separate so local testing cannot be mistaken for an upload.

## Safety and compatibility

- The first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with the
  78-tool catalogue and zero bundled third-party MCP provenance.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify the published SHA-256 digests after publication.
