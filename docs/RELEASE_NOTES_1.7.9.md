# VRCForge 1.7.9

VRCForge 1.7.9 is a Windows x64 corrective release target for causal Agent
tool results and deterministic avatar neck-seam review. It supersedes 1.7.8;
the published `v1.7.8` tag and Release remain unchanged until formal
publication of this version.

## Exact Agent result causality

- The internal Agent loop and external MCP boundary now project the same
  canonical success, readiness, failure, verification, evidence, and recovery
  facts from one raw tool result.
- A read operation may execute successfully while the requested domain remains
  not ready. That state is no longer collapsed into either a generic success or
  a transport failure: `ready`, `blockingReasons`, the exact cause chain,
  observed/expected deltas, evidence, next action, and recovery facts remain
  available to both Agent paths.
- Structured tool failures retain their originating layer, phase, root cause,
  bounded evidence, and retry/recovery guidance instead of leaving an Agent to
  infer the reason from a wrapper message.

## Head and part transplant workflows

- Two built-in workflow skills now cover avatar head transplantation (with or
  without face tracking) and cross-avatar part transplantation. Each workflow
  separates inspection, approved atomic writes, rollback points, and visual
  acceptance instead of treating a successful tool call as proof of a usable
  avatar.
- Rigid accessories, dynamic accessories, and clothes follow different paths:
  rigid accessories use a target bone and local transform; dynamic accessories
  additionally preserve their complete bone chain, PhysBone root, colliders,
  and unit root scale; clothes use armature/bone remapping and deformation
  checks.
- Head and dynamic-part acceptance requires unobstructed Rest and Gesture
  Manager motion evidence from the same local camera frame. Front, back, both
  sides, and top/bottom close-ups must reject open seams, scale changes,
  floating roots, clipping, or pose-dependent drift.

## Deterministic free-camera and deformation inspection

- Screenshot requests support a true free Scene-view camera with explicit
  camera position, target, up vector, perspective/orthographic projection, and
  orthographic size. Named angles and free-camera coordinates are mutually
  exclusive, and returned camera facts are checked against the approved request.
- The built-in SkinnedMeshRenderer diagnostics report finite Rest/Play/world
  vertices, AABBs, distance percentiles, and reconstructed used-bone skin
  matrices. Exact used-bone remapping and reference closure are available as
  separate inspected/approved atoms for diagnosing pose-dependent deformation.
- Head-swap acceptance still requires front, both sides, back, and Bottom
  evidence. An open rim, visible gap, overlap, or geometric offset at the neck
  is a hard visual failure even when other readiness checks pass.

## VRChat SDK readiness boundary

- Avatar upload readiness remains read-only. When blocked, it reports the exact
  Play Mode, SDK authentication/ownership, platform, pipeline, validation, or
  builder-state facts that caused the block and gives the corresponding next
  action without starting a mutation.
- A real build/upload remains separately approval-gated and must bind the exact
  avatar, SDK owner, platform, metadata, and readiness digest. No upload retry,
  rollback, tag, Release mutation, or public publication is inferred from a
  readiness result.

## Packaging and compatibility

- Unity Core product identity is `1.7.9`; contract 82 exposes 80
  VRCForge-owned Unity tools through MCP 2.0 (`2026-07-28`).
- The Windows payload remains self-contained and must pass the clean import,
  compile/load, provenance, paired-Core, result-parity, and stable-readiness
  gates before publication.
- The Windows binaries are not code-signed, so Windows may warn. The public package must not contain a
  third-party Unity MCP runtime or private-addon implementation.

Release artifact names, SHA-256 values, and live Unity/package evidence are
published only after the final artifacts have been built and verified; source
tests alone are not release proof.
