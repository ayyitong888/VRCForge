# VRCForge 1.7.1

VRCForge 1.7.1 is the documentation-corrected package of the 1.7 Agentic
closeout. It carries the verified 1.7 runtime and UI behavior without adding,
removing, or redesigning product features.

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
- General Settings retains the optional custom accent and local background
  extension while default light and dark appearance remains unchanged.

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
