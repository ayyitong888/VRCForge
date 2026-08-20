# VRCForge 1.7.7

VRCForge 1.7.7 is a Windows x64 external-Agent and Unity persistence hotfix.
It fixes the four release-blocking workflows found during live MCP testing and
was formally published on 2026-08-20. It supersedes `v1.7.6`; the published
`v1.7.6` tag and Release remain available and unchanged.

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
  assets and verify the SHA-256 digests below.

## Release identity and SHA-256

- Release: <https://github.com/ayyitong888/VRCForge/releases/tag/v1.7.7>
- Release commit: `e9aa5a9b044c5451e70ebfff3a3647753122c090`
- Annotated tag object: `7f27ee529b11830026c4f3c6a30bd73428b09a6a`
- Build policy: `strict`, `releaseEligible=true`
- `VRCForge.unitypackage`:
  `74eb5c25f8a361314d4a9a4e4a723b55ffc76c0bd7e53ece5ed1f66dc95da222`
- `VRCForge_Windows_x64_1.7.7.zip`:
  `a9919a7ef76a7b53b9b76ed5bc6dc13687f034044f4ac86aad56b2a4c0da566a`
- `VRCForge_Offline_Installer_x64.exe`:
  `e2aaaa55f67630c2a88e4705d0db4727d4b5c39ec85509d31ab53b2bcda2f2ca`
- `VRCForge_Web_Installer_x64.exe`:
  `7610177ac7763a90f06e88ec161ec16f23e1945a810fe5e87927e7484f57d874`
- `release-manifest.json`:
  `b7e873ad048c99f63e406f7e3916a5332bd9b2f8bd21eddcf2e2c403583f441c`
