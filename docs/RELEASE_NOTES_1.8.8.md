# VRCForge 1.8.8

Release candidate. Publication requires the release validation gates to pass.

## Agent and Model Connections

- Keep native tool definitions stable while loading tool descriptions through tool results.
- Preserve native conversation history and scan snapshots for later result paging.
- Revalidate supported scan references before reuse; preserve old history when results change.
- Improve Responses streaming, completion receipts, rejected tool-call recovery, and reported token usage.
- Add optional ChatGPT account sign-in with locally retained login state and model selection.
- Keep OAuth integration separate from the other provider connections.
- Improve steering during active model requests while retaining tool approval boundaries.

Cache reuse depends on the provider and workload. These changes do not guarantee a fixed cache hit rate.

## Unity Connection and Updates

- Preserve strict App and Unity Core version matching.
- Show both versions when they differ, identifying whether to update the App or import its matching Core package.
- Show actionable guidance for compilation errors, missing Core and connection failures without requiring Developer Options.
- Add the Unity menu `VRCForge > Check Core Updates` for checking the latest official release and opening its download page.
- Checking for updates does not import packages or modify project assets automatically.

## Runtime and Support

- Scope MCP discovery and tool access to owned sessions.
- Preserve existing Avatar designs during supported skill workflows.
- Add redacted support exports and improve checkpoint metadata preservation.
- Page oversized memory sources with durable cursors.

## Downloads and Upgrade

The release provides an Offline Installer, a Web Installer, a Windows x64 ZIP,
`VRCForge.unitypackage`, and `release-manifest.json`.

Use the App and Unity package from the same release. Import the matching Core
package into the Unity project, wait for compilation to finish, then refresh the
App connection. Check the manifest hashes before installing downloaded files.

Protocol: MCP 2.0 (`2026-07-28`). Windows binaries are not code-signed; verify the release manifest hashes.
