# VRCForge 1.7.5

VRCForge 1.7.5 is a documentation-corrected package and Windows x64 corrective
release for workspace wallpaper continuity and General-project approval
safety. It supersedes 1.7.4; the
`v1.7.4` tag and Release page remain available and unchanged.

## Workspace corrections

- Whole-App wallpapers now share one continuous scrim across the left sidebar,
  center workspace and right environment rail. Static borders no longer form
  visible vertical seams; resize handles remain visible while hovered or used.
- The redundant permission, Core status and pending-approval chips were removed
  from the workspace header. Permission remains visible in the chat composer;
  Core and approval state remain visible in the right environment rail.

## General-project approval safety

- Editing, overwriting, patching, moving and deleting files always require
  manual approval in Auto Approve mode.
- Creating a file outside the current General project also requires manual
  approval. The request is allowed to proceed to approval rather than being
  rejected solely for crossing the project boundary.
- Eligible new-file creation inside the current General project can be
  auto-approved only after a distinct lightweight model available from the
  user's configured provider and API key returns a strict allow decision.
  Missing reviewer models, provider failures, malformed replies and uncertainty
  all fall back to manual approval.
- General-project approval cards identify the operation and file name. Windows
  notifications identify the operation type without exposing file contents or
  full local paths.
- Manual cards retain **Allow once**, **Reject**, and **Allow this kind**. A
  remembered project/category rule does not let the executing model self-approve:
  each future match still goes through the separate provider review request.

## Carried-forward release gates

- The first-party Unity MCP Core remains MCP 2.0 (`2026-07-28`) with the fixed
  64-tool catalogue and zero bundled third-party MCP provenance.
- General tools remain unable to write into registered Unity project roots;
  Unity writes retain their approval, checkpoint, validation and restore path.
- The Windows installers are not code-signed. Download only official VRCForge
  assets and verify their published SHA-256 digests.

## SHA-256

The verified hashes for the Unity package, Windows payload, both installers and
release manifest are added after the strict release build.
