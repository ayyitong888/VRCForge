# VRCForge 1.5.1

VRCForge 1.5.1 strengthens the Agent tool loop, durable approval continuity,
visual-review routing, and user-visible Runtime state while preserving the
existing supervised Unity write boundary.

The Unity integration remains the self-contained VRCForge MCP 2.0 (`2026-07-28`)
Core with its fixed 64-tool contract.

## Source changes

- Added a lightweight end-to-end Agent task loop that keeps objective identity,
  tool selection, argument validation, permission and approval decisions,
  execution results, correction attempts, external verification, and the final
  completion decision in one bounded runtime-owned chain.
- Kept long-running Shell and delegated work attached to the original task and
  conversation. Never-dispatched terminal continuations can resume after a
  restart; a continuation already claimed at the crash boundary is marked
  interrupted instead of risking duplicate side effects. Results are delivered
  only to the owning conversation.
- Preserved structured, bounded error causes and next actions so the model can
  correct a failed call without treating an unrelated successful tool as proof
  that the task is complete.
- Added shallow tool input contracts at both planning and execution boundaries.
  Invalid model arguments are returned as bounded correction evidence without
  invoking the target handler.
- Added process-authenticated, one-use runtime journey receipts so release-gate
  evaluation can distinguish a real App tool/result/completion loop from
  caller-supplied or selection-only evidence.
- Added declared verification profiles for successful Shell exit, stable Unity
  Console diagnostic deltas, persisted scene writes, and complete multi-angle
  visual review. Console and visual evidence are required only by actions that
  explicitly declare those profiles.
- Bound managed visual evidence to the exact Runtime task, approved capture
  action, immutable image bytes, and one-use receipt. Link, junction, reparse,
  hard-link, changed-byte, local-path, and cross-task inputs fail closed; the
  visual verifier remains internal to the App task loop rather than appearing
  as a directly callable external tool.
- Persisted authenticated terminal receipts in the existing bounded Runtime
  continuation ledger so release evaluation can follow an approval or
  background task to completion after reconnect. Polling and Unity Console
  readback now pass their remaining deadline to each underlying read instead
  of allowing one retrying call to exceed the declared wait.
- Preserved the canonical `skill` or supervised `write` identity across
  deterministic routing and exposure changes. Planning can classify a hidden
  write target without exposing it to the model, while execution still routes
  that target through the normal approval transaction instead of a Skill
  executor.
- Split single-view capture, capture-status inspection, and multi-angle capture
  intent before the generic screenshot fallback. Named angles and coverage
  requests now select the approved fixed-angle capture path, while one current
  view remains a separate approved action.
- Made approved multi-angle capture continuation stage-aware. A capture-only
  request now finishes from its verified approval result; when the original
  request explicitly asks for visual review, the same task consumes the managed
  capture receipt exactly once, runs the multi-angle verifier, and binds both
  action IDs before Runtime can mark the task complete. The write is never
  replayed while transitioning to visual review.
- Made visual review Provider-neutral. An enabled independent Vision Profile
  is preferred; otherwise the configured main model receives the image through
  its Provider channel. VRCForge does not reject DeepSeek or another configured
  route from a model-name allowlist, does not silently switch Provider, and
  returns the selected Provider's real error when image input is rejected.
- Split visual failures by Provider outcome. Explicit 4xx/unsupported-image
  responses discard the raw image and keep a bounded error summary; timeout,
  connection, 429, and 5xx failures preserve only the exact bound image receipt
  for a controlled retry. Upload, chat-send, and visual failures also surface
  in a dismissible lower-center notification.
- Persisted API credentials per Provider across main/Vision switches without
  projecting key values back to the App. A dedicated Vision Profile remains
  optional, and the same saved Provider key can be reused across lanes when no
  lane-specific override exists.
- Restored pending approvals as durable, non-expiring decisions. Restart keeps
  the exact bottom conversation card without carrying an execution capability;
  eligible fixed-angle capture approvals retain the explicit allow-similar
  split action, while destructive or unscoped writes remain ineligible.
- Restored the project conversation's upper-right work rail as a lightweight,
  collapsible Progress / project / Context surface. Agent-owned TODO items,
  Runtime run history, sub-agent work, project and Unity status, changes,
  memories, and Skills stay beside the conversation instead of consuming the
  center transcript. The rail remains lazy-mounted after the usable center and
  adds no startup fetch or polling path; TODO list/create/update/delete/replace
  Skill operations remain separate from Runtime execution history.
- Added an effective context-window cap in Model settings with a synchronized
  slider and K-token numeric input. A manual value can reduce the working
  window to keep attention focused, but it cannot expand a smaller detected
  Provider limit; Auto restores Provider/model detection.
- Expanded the lightweight selection Harness to 40 positive/negative cases and
  made action kind plus exposure layer part of each selection contract. The App
  projects action kind from host-owned tool metadata and binds it into the
  one-use selection receipt, so a correct tool name with the wrong execution
  lane no longer passes acceptance.

## Safety and scope

- The model may select and retry tools, but it cannot certify completion by
  itself. Runtime-owned action identity and declared verification decide whether
  a task is completed, failed, waiting, or needs user action.
- Full host Shell remains available according to the user-selected permission
  mode. Unity-project writes continue through the existing approval,
  checkpoint, readback, and rollback path.
- The implementation reuses the existing planner, tool-result, approval,
  Shell, and sub-agent owners. It does not add a database, generic hook platform,
  second event bus, or a second result envelope.
- The Windows installers are not code-signed. Download only assets attached to
  the official VRCForge Release and verify published hashes when available.
