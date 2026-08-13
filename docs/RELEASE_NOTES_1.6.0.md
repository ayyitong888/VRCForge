# VRCForge 1.6.0

VRCForge 1.6.0 makes the built-in Agent useful both inside and outside Unity
projects, adds DeepSeek Harness and multi-MCP integration, and replaces the
old execution-history presentation with a live, chronological Agent timeline.

The Unity integration remains the self-contained VRCForge MCP 2.0 (`2026-07-28`) Core with its fixed 64-tool contract and supervised write path.

## Highlights

- Added explicit **General** and **Unity** project types. General projects work
  like ordinary Agent workspaces and are not routed through Unity or avatar
  tools merely because a Unity Editor is open. Unity projects retain their
  project-scoped Core, approval, checkpoint, readback, and rollback boundary.
  The left project rail now keeps General and Unity projects in separate
  sections without changing their nested chats or actions.
- Added bounded General file inspection for listing, finding, reading, and
  searching files within user-selected roots. Traversal, junction and symlink
  escapes, sensitive credential files, hidden VRCForge state, oversized
  outputs, and secret-shaped content fail closed or are redacted.
- Restored a natural interactive Agent loop without a fixed tool-call count.
  Runtime-owned completion verification, cancellation, repeated-failure
  protection, Provider deadlines, and finite background-task budgets remain in
  force.
- Added exact-turn steering and durable FIFO follow-up delivery. Inputs that
  cannot be steered safely remain recoverable instead of being silently
  dropped or executed out of order.
- Added a live chronological timeline for model phases, commands, file edits,
  tool calls and results, Agent replies, and Sub Agent lifecycle events. Related
  work is grouped behind expandable summaries, each invocation is shown once,
  and internal reasoning text is not shown to ordinary users.
- Runtime terminal states now stay distinct from ordinary Agent replies. A
  repeated-no-progress result is shown as a localized failed/not-complete card,
  while Stop settles the live turn into a durable cancelled card that preserves
  its accepted timeline and whole-turn duration.
- Added explicit DeepSeek Harness connector support and multi-MCP registration.
  VRCForge prefers its modern MCP profile and can use a pinned compatible edge
  profile when the peer does not support it. The Unity package itself remains
  MCP 2.0 only.
- Added deterministic MCP tool namespaces, collision-safe registration,
  per-server lifecycle isolation, bounded reconnect behavior, and local-only
  credential references for external MCP integrations.
- Added bounded OpenRouter and OpenAI-compatible Provider lifecycles, including
  safe reasoning activity, first-byte/idle/overall deadlines, mid-stream error
  handling, request-scoped cancellation, and worker cleanup.
- Added an expandable `Reconnecting 1/5` through `5/5` Provider-idle status.
  Safe activity resets the clock; sustained silence ends with a manual-retry
  instruction and never automatically replays the request. The disclosure uses
  fixed product text and does not expose internal reasoning or raw responses.
- Restored message copy and in-place editing, explicit send/queue controls
  while an Agent is running, and compact `+` and `/` command palettes.
- Removed the central Run Ledger and duplicate project-workspace Doctor card.
  Agent TODO, Sub Agents, environment state, and user attachment sources remain
  separate, purpose-specific workspace sections.
- Changed approval and Sub Agent notifications to open the exact current detail
  view before an action is accepted. Pending decisions no longer replace or
  lock the chat composer.
- Conversation text-selection actions now retire with their owning chat
  surface, including when the user opens Settings, changes chat or project, or
  opens a project/sub-agent detail surface. A stale selection toolbar no longer
  remains over the destination view.

## Safety and compatibility

- Models select tools, but Runtime-owned evidence decides whether work is
  completed, failed, cancelled, waiting, or still unverified.
- General tools cannot acquire Unity write authority. Unity-project writes
  continue through the existing approval, checkpoint, readback, and rollback
  path.
- Existing user data and saved Provider credentials remain local. Credentials,
  prompts, internal reasoning text, and private paths are not included in MCP
  discovery or public release metadata.
- The Windows installers are not code-signed. Download only assets attached to
  the official VRCForge Release and verify the published hashes.

## Post-release validation decision

Local comparative acceptance confirmed that the configured OpenRouter GLM-5.2
and DeepSeek V4 Pro models were both available and stayed on General tools with
zero Unity-tool calls. Both runs ended honestly as
`planner_no_progress` / not complete; neither was recorded as success. A
separate Stop run settled durably as cancelled. This validates the bounded
Runtime and terminal presentation, not completion of the frozen task or a
natural reconnect cycle.

The release owner chose a hot-fix-first publication for 1.6.0. Warm-start,
Golden Path, packaged Skill, visual completion, optimizer, external-Agent, and
clean-Windows install/upgrade/uninstall evidence will be refreshed after
publication against the exact published hashes. Those deferred probes are not
claimed as pre-release evidence here; a confirmed user-visible regression will
be handled by a versioned hot fix rather than by silently replacing history.
