# VRCForge 1.7.10

VRCForge 1.7.10 is a Windows x64 release target focused on trustworthy Unity
write results, bounded asynchronous Editor work, and portable official Skills.
It supersedes 1.7.9; the published `v1.7.8` release and the existing `v1.7.9`
Draft remain unchanged until this version is formally published.

## Trustworthy Unity write results

- Synchronous single-asset writes now return the real value observed before
  mutation and a fresh saved/read-back value after mutation instead of merely
  echoing request parameters or reporting a generic success flag.
- Deferred-save operations explicitly return their current in-memory result
  with `pending=true`; transaction-oriented operations report bounded per-asset
  outcomes without changing their existing save or transaction boundaries.
- Affected-item summaries remain bounded while retaining exact counts and a
  handle for full follow-up inspection.

## Asynchronous Editor jobs

- Long-running Editor operations can register durable queued, running, done,
  failed, and expired job states.
- The read-only `vrc_poll_job` tool exposes completion and failure evidence
  without forcing the initiating request to block through an Editor refresh or
  domain reload.

## Materials, assets, and avatar authoring

- Material texture-slot assignment validates the target shader property,
  texture asset, approval context, checkpoint, saved readback, and affected
  renderer references.
- Project asset copy supports material assets, texture import exposes
  `streamingMipmaps`, and lilToon-oriented shader properties are available
  through the same supervised write boundary.
- Blendshape preview/post-write verification, per-project write locking,
  managed screenshot artifacts, and checkpoint cache responsiveness improve
  deterministic authoring and recovery evidence.

## Skills and external integrations

- Official Skill signing keys can be exported and imported through an
  encrypted, passphrase-protected migration format; unencrypted private-key
  material is never written to the migration document.
- External installed Skills have a deterministic registry and stdio exposure,
  while official identity remains separate from package availability or
  enabled state.
- Seven VRCForge-owned Avatar Skill packages are now tracked as portable source
  packages for audit, animation, expression menus, hairstyle, wardrobe,
  accessory switching, and physics review.

## Runtime and desktop reliability

- Runtime project binding remains scoped to the exact selected project, Unity
  process discovery avoids slow broad scans, and Editor reload confirmation is
  an explicit approval-gated ephemeral action.
- Background and interactive work share a fixed capacity while retaining
  interactive headroom; checkpoint and protection workspaces remain responsive
  during longer operations.
- Screenshot-only writes are identified as managed local artifact overwrites
  rather than claiming a Unity-project rollback checkpoint.

## Packaging and compatibility

- Unity Core product identity is `1.7.10`; contract 84 exposes 82
  VRCForge-owned Unity tools through MCP 2.0 (`2026-07-28`).
- The Windows payload remains self-contained and must pass clean build,
  Unity compile/load, provenance, paired-Core, result-contract, and packaged
  backend gates before publication.
- The Windows binaries are not code-signed, so Windows may warn. The public
  package must not contain a third-party Unity MCP runtime or private-addon
  implementation.

Release artifact names, SHA-256 values, and live package evidence are published
only after the final artifacts have been built and verified; source tests alone
are not release proof.
