# VRCForge 1.7.2

VRCForge 1.7.2 is the next stable patch release and supersedes the 1.7.1
refresh. It carries the verified 1.7 Agentic closeout, the approved
file-backed multi-colour theme extension, the Memory Dreaming convergence and
the goal semantic verification against Claude Code and Codex CLI. It is a
documentation-corrected package whose public identity and release evidence
track the 1.7.2 source and assets together.

## Corrected packaging evidence

- Packaged README, User Manual and About metadata now report version 1.7.2
  instead of the previous release identity.
- The release badge, portable archive name, MCP target and provenance-gate
  wording use the same package version.
- App, Runtime, Tauri, Cargo and localized About metadata report 1.7.2.

## Supersedes 1.7.1

The `v1.7.2` tag and package replace the `v1.7.1` refresh as the current stable
release. The `v1.7.1` tag and Release page remain untouched; users should
download the 1.7.2 assets below.

## What is new in 1.7.2

- **Memory Dreaming convergence.** Memory Settings exposes exactly two user
  controls: **Enable Memory** and **Remember and use Memory across
  conversations**. Dreaming only organizes already-saved Memory, reuses the
  configured BYOK Provider/Model, never reads raw chat transcripts and never
  rewrites Memory text. Consolidation is two-pass: the first model call
  proposes duplicates, and a separate second call re-reads the same bounded
  Memory batch plus the proposal to drop false positives and add missed
  groups. No deletion happens before that review, and the final deletion
  batch is restorable.
- **Background coverage modes.** A selected background image can cover the
  center workspace only (default) or the entire App including both sidebars,
  with the existing 0–100% visibility control unchanged.
- **Custom theme colour editor.** Custom mode exposes accent and background
  base seeds through a palette, editable HEX/RGB/HSL fields and a
  capability-gated screen eyedropper, with automatic light/dark derivation
  and exactly three deduplicated recent colours retained across **Restore
  defaults**. Colour fields keep raw partial text while typing and commit
  only on valid blur/non-composing Enter, so IME input cannot repeat or
  normalize early.
- **Goal semantic verification.** The Goal surface was verified against
  Claude Code and Codex CLI implementations: `/goal <objective>` sets,
  bare `/goal` views, `/goal pause|resume|clear` controls; an active Goal is
  injected as persistent context; pause finishes the current delivery round
  rather than hard-interrupting; elapsed active time survives pause and
  restart; no token budget or cost conversion is present.

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

- `VRCForge.unitypackage`: `pending`
- `VRCForge_Windows_x64_1.7.2.zip`: `pending`
- `VRCForge_Offline_Installer_x64.exe`: `pending`
- `VRCForge_Web_Installer_x64.exe`: `pending`
- `release-manifest.json`: `pending`
