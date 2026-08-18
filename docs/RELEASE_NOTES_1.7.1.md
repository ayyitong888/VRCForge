# VRCForge 1.7.1

VRCForge 1.7.1 carries the verified 1.7 Agentic closeout, corrected public
documentation and the approved file-backed multi-colour theme extension.

## Refreshed 1.7.1 package

The 2026-08-18 package refresh replaces the earlier 1.7.1 binaries while
retaining the same product version at the release owner's explicit direction.
The refreshed source commit, moved `v1.7.1` tag, release manifest and all
replacement assets are rebuilt and verified together; hashes from the earlier
1.7.1 package no longer identify the current downloads.

- Theme customization now offers Default, Ocean, Violet, Sakura, Forest,
  Sunset and Custom palettes instead of a single accent-only control.
- Background images are copied to the App-owned local theme directory instead
  of being stored as Base64 in browser storage. The former 2 MiB UI limit is
  removed, and an existing Base64 preference is migrated once on first use.
- Background visibility covers the complete 0–100% range. Replacing or
  removing a background deletes the prior VRCForge-managed file; files outside
  the managed naming boundary are preserved.
- The theme action is named **Restore defaults**. It restores the default
  palette and removes the managed background rather than presenting an
  ambiguous reset action.

## Corrected packaging evidence

- Packaged README and User Manual identity now match version 1.7.1 instead of
  describing the older 1.6.2 release.
- The release badge, portable archive name, MCP target, Unity reimport guidance
  and provenance-gate wording now use the same package version.
- App, Runtime, Tauri, Cargo and localized About metadata report 1.7.1.

## Carried forward from the 1.7 closeout

- Memory Consolidation provides bounded gather, deduplicate, consolidate,
  review, promotion, erase and rollback phases with review records.
- Request Changes records one visible rejection reason and permits exactly one
  revision; a revised write still requires fresh approval.
- Goal persists per project with start, cooperative drain-pause, resume and
  elapsed active time, without token budgets or cost conversion.
- External Agents can initialize, discover and call bounded VRCForge tools
  through the existing inbound MCP edge.
- Generic MCP setup includes the beginner-facing three-step configuration path,
  and connection-help questions route through the read-only Know Yourself
  Skill.
- General Settings retains optional multi-colour palettes and a local
  file-backed background while default light and dark appearance remains
  unchanged.

## Deliberate non-goals

This release does not add multi-Sub-Agent scoring or comparison, `/delegate
compete`, Session branches or Handoff Inbox, Reviewer shadow promotion,
Workflow execution or recovery, token-price conversion, or Agent/Workflow
monitoring.

## Compatibility and verification

- The bundled first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with
  the fixed 64-tool catalogue.
- The 1.6.2 Unity Editor-only hotfix guard remains part of the package and its
  provenance gate.
- General Mode retains Core + General tools; Unity Project Mode remains its
  capability superset. Generic writes cannot modify registered Unity roots.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify their published SHA-256 digests.

## SHA-256

- `VRCForge.unitypackage`: `ad14c1b7577c74e7ef6a9bf7751d677f8a9a24ad789d02e71e082f4538012a82`
- `VRCForge_Windows_x64_1.7.1.zip`: `265ed935bd0828d12933747ab57d8f76945189989f35faad63da00829cf50706`
- `VRCForge_Offline_Installer_x64.exe`: `25d8e1b2e0cc19c277aeac04d6554182f3e6c088633a65b05b714bcb1b09aa07`
- `VRCForge_Web_Installer_x64.exe`: `feaaf6436ffdd439da488acad1cdb459625b98d7fef0e91ed76c0cfd4eb9ee68`
- `release-manifest.json`: `9d1c1ea2408f549915f3f17e4b0270b8df42c2f06c8bdc7898404314052590c2`
