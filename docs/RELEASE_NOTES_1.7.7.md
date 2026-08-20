# VRCForge 1.7.7

VRCForge 1.7.7 is a Windows x64 external-Agent and Unity persistence hotfix.
It targets the four release-blocking workflows found during live MCP testing;
the published `v1.7.6` tag and Release remain unchanged until publication is
separately authorized.

## External Agent and project binding

- External Agent Unity calls now keep the explicitly supplied project path
  authoritative instead of falling back to a previously selected project.
- Completion verification now reads MCP Core `structuredContent` recursively,
  so a valid structured result cannot be discarded because an outer envelope
  lacks the same fields.
- Missing-GameObject reads preserve the stable `gameobject_not_found` code
  through Core, App, and external-Agent result envelopes.

## Checkpoints and exact rollback

- Unity projects ignored by an enclosing repository use the project-scoped
  archive checkpoint path instead of attempting an unusable parent-repository
  git checkpoint.
- Scene-only restores avoid unnecessary asset refresh and re-establish the MCP
  Core main-thread pump before post-rollback verification.
- Archive restore keeps Unity compiler caches when no Packages file changed,
  preventing a scene-only rollback from needlessly invalidating the live Core.

## Persistent scene-object writes

- Create, rename, reparent, delete, and active-state changes save their scene,
  perform exact persisted readback, and restore the original scene on failure.
- Reparent rejects cross-scene parents before mutation, preventing a failed
  operation from leaving an object saved into a second scene.
- Delete rollback verifies the canonical resolved hierarchy path, including
  targets originally selected by a unique leaf name.
- SetActive now reports `sceneSaved` and `persistedReadback` only after the saved
  scene and active-self state match the requested result.

## Safety and compatibility

- Runtime replacement checks distinguish a busy runtime from a rejected or
  unavailable one, avoiding false replacement warnings during internal IPC.
- The first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with the fixed
  64-tool catalogue and zero bundled third-party MCP provenance.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify the published SHA-256 digests after publication.
