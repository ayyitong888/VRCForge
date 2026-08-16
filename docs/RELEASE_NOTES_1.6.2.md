# VRCForge 1.6.2

VRCForge 1.6.2 replaces VRCForge 1.6.0 and supersedes the unpublished v1.6.1
tag. Use 1.6.2 for new installations and upgrades. The historical v1.6.0 tag
and assets remain unchanged.

## Corrections and no-regression closure

- Restores source, Unity package, desktop binary and release-manifest identity
  after the 1.6.0 Unity package hotfix evidence gap.
- Corrects General-Agent read-loop progress detection: only consecutive
  semantically equivalent observations are suppressed; new evidence permits a
  later re-check, while the third consecutive no-progress proposal still stops
  honestly without claiming completion.
- Freezes the reviewed VRCForge General workbench: cached General projects,
  Unity-only non-blocking discovery refresh, a compact conditional Sources
  section, no empty disclosure controls, the conditional Goal bar, single
  right-rail collapse owner, jump-to-bottom, compact generic slash commands
  with `/handoff`, complete Provider/model identity and the continuous
  context-usage ring.
- Preserves the real conversation sequence. Every safe non-CoT Agent update is
  shown where it occurred, single tool invocations use their concrete identity
  instead of a generic batch, and only adjacent same-kind multiple operations
  may be grouped. General Agent rows omit the internal namespace prefix while
  Unity-specific tool rows retain their explicit product identity.
- Uses one profiled Agent tool registry: General Mode exposes Core + General,
  while Unity Project Mode adds Unity tools without losing the General toolset.
  General Edit/Write/Web tools are first-class capabilities rather than Shell
  substitutes. General reads may inspect registered Unity projects, but shared
  mutation tools and ordinary Shell reject those registered roots; `unity_shell`
  and Unity tools receive the explicit capability for the current project.
  This is a cooperative path guard, not an OS sandbox or adversarial boundary.
- Restores instruction delivery to planning: enabled App-global rules and an
  explicitly bound project's root `AGENTS.md` reach the planner without
  changing Runtime permissions or entering the visible timeline.
- Replaces the previous App Update proposal with one non-blocking startup check.
  Only a successful newer-version result opens an in-App dialog; current,
  offline and failed checks stay silent.
- Keeps the first-party Unity MCP Core on MCP 2.0 (`2026-07-28`) while the
  external Agent edge retains its existing compatibility boundary.

## Windows binary SHA-256

The authoritative `VRCForge.exe` SHA-256 is inserted into the GitHub Release
notes from the strict manifest produced by the single clean v1.6.2 tag build.
The tagged source file is intentionally not rewritten after that build.

The Windows installers are not code-signed. Download only assets attached to
the official VRCForge Release and verify the published hashes.
