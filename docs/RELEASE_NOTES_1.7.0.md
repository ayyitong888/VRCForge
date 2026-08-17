# VRCForge 1.7.0

VRCForge 1.7.0 is the Agentic closeout release built on 1.6.2. It keeps the
accepted 1.6.2 UI/UX, Unity-package correction and protected-project behavior,
and closes four bounded product lanes without adding the deferred Workflow or
multi-agent comparison systems.

## What changed

- Memory Consolidation now gathers, deduplicates, consolidates and reviews
  scoped candidates in bounded phases. The Memory page shows review records,
  and accepted changes remain erasable and reversible.
- Request Changes accepts one visible rejection reason and permits exactly one
  revision in the same interactive run. A revised write still requires a new
  approval; background and repeated denials remain terminal.
- Goal is persistent context with start, cooperative drain-pause, resume and
  elapsed time. Pause finishes the current turn instead of hard-interrupting
  it. Goal token budgets and provider-cost conversion are intentionally absent.
- External Agents can initialize, discover and call bounded VRCForge tools
  through the existing inbound MCP edge. Authentication, cancellation,
  redaction and Unity approval boundaries remain authoritative.
- The bundled first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`);
  inbound external-Agent compatibility does not replace or downgrade it.
- Generic MCP setup now gives beginners an exact three-step configuration path,
  and connection-help questions route through the read-only Know Yourself Skill
  for evidence-backed guidance.
- General Settings now exposes a small theme extension surface for a custom
  accent, local background image and image visibility. Existing light/dark
  appearance remains unchanged until the user opts in.

## Deliberate non-goals

This release does not add multi-Sub-Agent scoring or comparison, `/delegate
compete`, Session branches or Handoff Inbox, Reviewer shadow promotion,
Workflow execution or recovery, token-price conversion, or Agent/Workflow
monitoring.

## Compatibility and verification

- The window title and package metadata report 1.7.0.
- The 1.6.2 Unity Editor-only hotfix guard remains part of the package and its
  provenance gate.
- General Mode retains Core + General tools; Unity Project Mode remains its
  capability superset. Generic writes cannot modify registered Unity roots.
- The Windows installers are not code-signed. Download only assets attached to
  the official VRCForge Release and verify their GitHub SHA-256 digests.
