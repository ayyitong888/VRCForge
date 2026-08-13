# VRCForge Product Regression Contract

This is the durable product-level no-regression contract for VRCForge. It
covers user-visible features, Agent behavior, approvals, UI/UX, Providers,
vision, persistence, latency, compatibility and release evidence. It is not a
session log or a replacement for implementation planning.

## Authority and use

- Current executable behavior plus this contract define the product boundary.
  Screenshots and other products may provide interaction references, but they
  never override VRCForge safety, ownership or completion rules.
- `AGENTS.md` owns engineering behavior rules. `ROADMAP.md` owns future work.
  `PROJECT_STATUS.md` owns current evidence. This document owns behavior that
  later work must not silently remove or reinterpret.
- Every changed contract needs a failure-first regression test. Manual visual
  review supplements executable proof; it never replaces a failed automated
  gate.
- `待考证` means the first exact implementation version cannot be established
  from current evidence. It must not be guessed.

Each item ends with its version history in this exact form:
`[首次实现: vx] [强化/修复: vx] [最近验证: vx]`.

## Agent and feature contracts

### AGT-001 — Provider-only planning

- Contract: production turns require a configured model Provider. The model
  selects the next tool/action; VRCForge does not execute a keyword-based local
  plan when the Provider is absent or fails.
- Forbidden regression: `shell_command`, slash-like phrasing or a tool keyword
  must not bypass a missing/failed Provider and create a call, approval or
  write. Return typed `provider_not_configured` or a bounded Provider failure
  with zero action execution.
- Acceptance: planner, Gateway and agentic smoke tests cover ordinary,
  attachment, tool-name and caller-supplied Shell cases.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-002 — Bounded Agent tool loop

- Priority: P0.
- Contract: an ordinary multi-step loop continues until the Runtime reaches a
  terminal outcome; it has no hard stop after three calls. A per-turn budget of
  25 remains a safety ceiling, not a normal completion rule. Desktop bootstrap
  work has its own session budget/account and cannot consume or reset the
  ordinary loop budget. Each step is `plan -> admit/approve -> execute ->
  canonical result -> refeed -> verify -> next plan`; retries after repeated
  failure, pending approval, and completion verification remain explicit,
  identity-bound Runtime states.
- Forbidden regression: no unlimited chain, arbitrary three-call cutoff,
  missing result refeed, completed-action replay, hidden retry of an ambiguous
  side effect, or budget sharing between desktop bootstrap and the ordinary
  loop.
- Acceptance: failure-first tests prove a safe four-step loop, the 25-call
  ceiling, separate desktop accounting, repeated-failure/approval/completion
  states, cancellation and full-tree identity preservation.
- [首次实现: 1.2.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-003 — Runtime-owned completion

- Contract: model text may request completion but cannot certify it. Runtime
  releases a task only after every required action and external verifier is
  terminal, identity-bound and passed.
- Forbidden regression: assistant `done`, stop/task hooks, selection receipts
  or a canonical tool envelope cannot satisfy missing Unity/visual/readback
  evidence.
- Acceptance: completion inversion tests reject missing, forged, expired,
  reused, cross-task and failed verifier evidence.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-004 — Tool triggering and exposure

- Contract: descriptions include when-to-use, when-NOT-to-use and negative
  examples. Planning exposes read/inspect tools; supervised writes become
  visible only in execution exposure.
- Forbidden regression: explanation, quotation, hypothetical, status check or
  keyword mention must not trigger a tool. A write must never be labelled or
  dispatched as a read skill.
- Acceptance: positive/negative selection matrix, action-kind tests and
  execution exposure tests.
- [首次实现: 1.4.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-005 — Modular ownership

- Contract: chat timeline, project workbench, sub-agent detail, Provider
  configuration, vision transport, task loop and notification parsing remain
  coherent modules with one owner per failure mode.
- Forbidden regression: do not regrow a second runtime in UI code, a second
  result envelope, dynamic write authority or a local planning platform.
- Acceptance: seam gate, import/build tests and main-thread review of delegated
  changes.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-006 — Honest Path-to-Skill recovery

- Contract: only a Runtime run terminally blocked as `completion_unverified`
  may expose its already successful, replayable structured actions for
  Path-to-Skill capture. The resulting summary is
  `structured_actions_completed`; it never claims that the blocked task
  completed.
- Forbidden regression: failed or pending actions, Shell, question, progress,
  desktop, error-bearing or duplicate-pollution steps cannot enter the capture.
  Only exact Runtime control steps `phase`, `exposure_layer` and
  `entered_execution` are omitted.
- Acceptance: `tests/test-path-to-skill-context.mjs` covers the positive special
  case and fail-closed negatives; release evidence includes the exact packaged
  Skill/Path-to-Skill report.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-007 — Non-CoT runtime phase presentation

- Priority: P0.
- Contract: user-visible progress uses a fixed allowlisted phase set and a
  spinner while work is active, with short safe commentary only. Runtime phase
  labels are not model text and never expose reasoning, chain-of-thought,
  hidden traces or arbitrary caller-supplied labels.
- Forbidden regression: no `reasoning`, `chain_of_thought`, hidden trace,
  arbitrary `label`, or raw model scratchpad may appear in timeline,
  notification, sidebar or composer progress.
- Acceptance: phase allowlist, spinner-in-every-active-state and adversarial
  label/CoT redaction tests, including empty and mid-tool states.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-008 — Same-turn steer and bounded follow-up queue

- Priority: P0.
- Contract: an accepted steer is captured by CAS identity and injected after
  the current tool batch, before the next LLM request. Frozen write authority,
  approval, checkpoint and verification semantics do not change. Follow-ups
  preserve FIFO order, cap at eight, expose an explicit user button, and are
  cancelled and cleaned up on terminal/cancelled runs.
- Forbidden regression: no steer injection during a tool batch, write-scope
  widening, reordered/uncapped follow-ups, hidden auto-send, duplicate replay,
  or orphaned queue after cancellation.
- Acceptance: CAS race tests cover batch-boundary injection, frozen write
  identity, FIFO/8-cap, explicit-button gating and cancellation cleanup.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Approval, write and recovery contracts

### APR-001 — Durable non-expiring approval

- Contract: a pending approval remains until approve, reject, modify or cancel.
  Restart restores only valid pending records with the same identity.
- Forbidden regression: no UI/server timeout may silently remove or decide a
  pending approval. Approval must not prevent scrolling or reading history.
- Acceptance: persistence/restart tests plus manual bottom-card/history review.
- [首次实现: 1.2.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### APR-002 — Once and eligible-similar choices

- Contract: every write offers allow-once/reject. An allow-similar choice
  appears only when the canonical approval marks the exact project and bounded
  category eligible.
- Forbidden regression: never hide an eligible choice, broaden it across
  projects/categories, or enable it for destructive/high-risk/no-project work.
- Acceptance: policy, notification and scoped approval UI tests.
- [首次实现: 1.2.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### APR-003 — Checkpoint, apply, readback and restore

- Contract: Unity/project writes require approval and a successful pre-write
  checkpoint, atomic apply and declared readback/verifier. Restore is a
  separately approved action.
- Forbidden regression: no direct UI write, silent overwrite, missing
  checkpoint, model-only validation or automatic destructive recovery.
- Acceptance: transaction lifecycle, identity binding, readback and restore
  tests.
- [首次实现: 1.1.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### APR-004 — Approval notifications are bounded request surfaces

- Contract: Windows notifications show a safe action gist. Main approvals may
  expose bounded once/reject actions; sub-agent review notifications only open
  the App and never decide.
- Forbidden regression: never include raw prompts, keys, commands, paths,
  hierarchy, checkpoint IDs or complete arguments. A deep link re-fetches and
  revalidates exact task/chat/revision/pending state before opening actions.
- Acceptance: Rust parser/validation, frontend deep-link and notification
  payload tests.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Project workbench and conversation contracts

### UX-001 — Exact project right-rail composition

- Contract: a project conversation right rail contains exactly, in order:
  Agent TODO, Sub Agents, VRCForge Environment Information, User Attachment
  Sources. Quick Chat retains the generic environment surface.
- Forbidden regression: do not put Runtime history/Run Ledger, Context, Memory,
  Skills, file-change history or approval decision buttons into this four-part
  project rail.
- Acceptance: static order/exclusion tests and packaged visual review.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-002 — Agent-owned TODO presentation

- Contract: Agent skills own list/replace/create/update/delete. The rail is a
  compact ordered status list, not user checkboxes. Pending items use a
  theme-colored hollow circle with their display-order number. Active items use
  a filled theme-colored numbered circle and a reduced-motion-aware breathing
  title. Completed items replace the number with a check inside the same filled
  theme circle while the title becomes muted and struck through.
- Forbidden regression: do not replace TODO with execution history, editable
  checkboxes, visible status words, status-color traffic lights, second-line
  error/summary text, status grouping or a duplicate center card. Preserve the
  received order and derive the visible number only from that display order.
- Acceptance: TODO skill/API and layout/style contract tests.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-002A — Environment rows disclose truncated text on hover

- Contract: the project Environment section remains compact, but each row,
  title and truncated detail exposes its complete label/value through hover.
- Forbidden regression: no permanently clipped status with no disclosure, and
  no eager tooltip framework or startup request for static text.
- Acceptance: RuntimeInfoRow source contract and packaged visual review.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-003 — Sub-agent summary and detail workspace

- Contract: the rail shows a compact active/completed summary. Opening it uses
  an independent large, scrollable surface with Active/Open and Completed
  groups, event/history detail and status-derived Cancel/Retry/Adopt/Dismiss/
  Use-next-action controls.
- Forbidden regression: do not embed full detail in the narrow rail,
  auto-adopt output, lock history during review or eagerly load detail during
  startup.
- Acceptance: workbench, notification deep-link, lazy-load and startup tests.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-004 — Chronological conversation timeline

- Contract: user messages, Agent natural-language replies and every tool call/
  result render in source time order. Tool rows may appear between replies and
  repeated calls remain separate facts.
- Forbidden regression: do not collect all calls above/below chat, collapse
  repeated calls into the last result, or attach tool JSON to copied prose.
- Acceptance: timeline extraction/order tests and conversation visual review.
- [首次实现: 1.2.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-005 — Edit and copy behavior

- Contract: user and Agent prose each expose copy of their visible prose only;
  Planner/tool/result JSON, hidden traces and attachments are excluded. Editing
  remains available on the latest sent user bubble through a usable full-width
  editing surface, then re-submits with the original turn identity rules.
  Copy success/failure receives a dismissible transient notice.
- Forbidden regression: no Agent-only copy restriction, unusable narrow edit
  field, Planner/tool/result JSON in copied text or response-rating thumbs UI.
- Acceptance: timeline tests cover user+Agent copy payloads, full-width edit
  interaction, clipboard failure and exclusion of non-prose content.
- [首次实现: 待考证] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-006 — Transient failure notice

- Contract: visual, upload, chat-send and clipboard failures use a lower-center
  rectangular card with a short upward entrance, close control and about three
  seconds total lifetime. The card contains a useful bounded summary.
- Forbidden regression: no silent failure, raw error dump, upper-corner
  placement or modal interaction lock.
- Acceptance: toast timing/style/i18n tests and packaged visual review.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-007 — Timeline-owned execution evidence

- Priority: P0.
- Contract: execution evidence remains in the chronological timeline and its
  bounded approval/checkpoint/rollback surfaces. Neither the chat center nor
  the project right rail exposes a central Run Ledger. Save-as-Skill is allowed
  only when `response.clientTurnId` has exactly one match in the current
  `runtimeRuns` and the bounded summary extraction succeeds; missing,
  duplicate, stale or cross-conversation mappings fail closed.
- Forbidden regression: no central ledger, detached history that changes event
  order, approval/checkpoint/rollback removal, or ambiguous skill capture.
- Acceptance: static UI exclusion, timeline ordering, approval/checkpoint/
  rollback and exact-identity Save-as-Skill negative tests.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-008 — Unified command palette

- Priority: P1.
- Contract: `+` and `/` use the same compact command-palette container. Each
  row has an icon, short title and one-line description; unavailable actions
  retain their reason and permission boundary. The list has a fixed maximum
  height with scrolling, mouse hover/click and ArrowUp/ArrowDown selection,
  Enter execution and Escape dismissal. Only real VRCForge capabilities are
  listed; absent plugins, targets or plans are never invented.
- Forbidden regression: no separate large explanation cards or developer-only
  command list, unbounded input/menu growth, hidden disabled reason, or
  unsupported capability claim.
- Acceptance: failure-first UI contract covers shared container and row
  association, action/command filtering, disabled reasons, max-height scroll,
  mouse/keyboard behavior, attach fallback and dismissal/reopen behavior.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Vision contracts

### VIS-001 — Provider-neutral image channel

- Contract: use an enabled Vision Profile; otherwise use the main model through
  its Provider image request shape. A configured unknown/new model is sent the
  image and its real response decides capability.
- Forbidden regression: no model-name allowlist, Provider-name pre-rejection,
  implicit Provider/model switch or text-channel substitution.
- Acceptance: route/shape tests including unknown models and canonical error
  refeed.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### VIS-002 — Permanent versus retryable image failure

- Contract: explicit 4xx/unsupported-image rejection keeps only a bounded
  Provider/model/source/error summary and discards raw images. Timeout,
  connection, 429 and 5xx keep identity-bound images behind a fresh one-use
  retry receipt.
- Forbidden regression: no raw image retention after permanent refusal, image
  loss on retryable failure or replay of a consumed capture receipt.
- Acceptance: classification, attachment-retention and task-loop tests.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### VIS-003 — Multi-angle integrity and completion

- Contract: fixed-angle capture is an approved write. Managed bytes, MIME,
  action, approval and checkpoint remain bound through audit and refeed.
  Byte-identical angle frames fail before Provider audit as camera-switching
  failure.
- Forbidden regression: no local-path audit, partial set, old receipt reuse,
  silent duplicate frame acceptance or visual self-certification.
- Acceptance: selector, duplicate-byte, receipt and verifier tests; release
  requires a package-paired successful live completion receipt.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Provider and context contracts

### PRV-001 — Per-Provider credential persistence

- Contract: every Provider retains its saved credential when the user switches
  Providers. Main and Vision lanes preserve their own values and may reuse the
  same Provider credential only through the bounded lookup rule.
- Forbidden regression: no plaintext key projection, cross-Provider key reuse
  or requirement to re-enter a saved key after switching.
- Acceptance: configuration round-trip, masking, lane-switch and upgrade tests.
- [首次实现: 待考证] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### PRV-002 — Explicit protocol selection and bounded negotiation

- Contract: protocol selection is observable. `Auto` negotiates an ordered
  compatible protocol for the same Provider, explicitly selected concrete
  model and canonical endpoint. The opt-in `deepseek-auto` virtual alias is the
  sole exception: before any output, an explicit protocol-incompatibility
  response may advance through its declared same-Provider Pro then Flash
  candidates. Explicit protocol or concrete-model pinning never changes model.
- Forbidden regression: no cross-Provider/origin fallback; no fallback on auth,
  rate-limit, timeout, 5xx, ambiguous side effect or after output. DeepSeek
  Messages stays on the same origin normalized to `/anthropic`.
- Acceptance: protocol/cache/origin/error-class tests and Provider Test status.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### PRV-003 — Custom endpoint boundary

- Contract: custom endpoints use an explicitly selected supported protocol and
  validated canonical endpoint. Credentials stay local and redirects cannot
  carry them across origins.
- Forbidden regression: no userinfo/query/fragment endpoint, unapproved remote
  plaintext HTTP, authenticated cross-origin redirect or silent protocol swap.
- Acceptance: endpoint policy, redirect and custom protocol tests.
- [首次实现: 待考证] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### PRV-004 — User-controlled effective context window

- Contract: Model settings expose a synchronized slider and numeric input in K
  tokens plus Auto. The saved cap may shrink a known/detected model limit but
  never expand it.
- Forbidden regression: no 1M-only assumption, stale value after Provider
  switch, unsynchronized controls or compaction budget ignoring the cap.
- Acceptance: UI, persistence, turn budgeting and compaction tests.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### PRV-005 — Bounded Provider transport lifecycle

- Priority: P0.
- Contract: OpenRouter and every supported OpenAI-compatible endpoint use
  request-scoped bounded connect, first-byte, idle and overall timeouts with
  bounded retry policy. Retry never replays an ambiguous side effect or
  crosses Provider/origin/model boundaries. Reasoning activity may be shown
  only through the fixed safe phase contract; chain-of-thought and arbitrary
  labels never enter user-visible output. HTTP 200 streams that emit a
  midstream error are terminal errors, and a terminal EOF without a complete
  result is not success. Stop actively closes that request, releases its
  watcher and worker, and preserves capacity for later turns.
- Forbidden regression: no unbounded connect/read/idle/overall wait, retry
  storm, false success on midstream error or terminal EOF, leaked request after
  Stop, orphan watcher/worker, or capacity starvation.
- Acceptance: fake transports exercise short connect/first-byte/idle/overall
  thresholds, retryable versus non-retryable failures, reasoning-activity
  redaction, HTTP-200 midstream error, terminal EOF, request-scoped Stop
  closure, watcher/worker cleanup and post-cancel capacity reuse.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Latency and lifecycle contracts

### LAT-001 — Startup is a release-blocking hard gate

- Contract: static startup shell paints before App loading. Warm startup shell,
  backend invoke and cached bootstrap each remain at or below 100 ms. Center is
  usable before sidebars mount/hydrate; FCP is recorded.
- Forbidden regression: no eager sidebars, sub-agent detail or historical
  runtime data in the critical path; thresholds must not be weakened to pass.
- Acceptance: manifest-bound isolated cold/warm pair, normal Quit and zero
  residue. Concrete release-candidate timings belong in `PROJECT_STATUS.md`,
  not this durable threshold contract.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### LAT-002 — Background and stop lifecycle

- Contract: Stop/cancel writes a terminal fence before worker ownership is
  released. Late success remains audit evidence and cannot restart execution.
- Forbidden regression: no restart after Stop, duplicate queued turn, mid-turn
  recap injection or replay after ambiguous dispatch.
- Acceptance: cancellation race, queue ownership, restart and ledger tests.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Package, persistence and compatibility contracts

### PKG-001 — Self-contained Unity package

- Contract: Import All needs no dependency fetch, environment variable or
  manual code edit; compile has zero errors and no unexpected warnings; menu
  and connection guidance are immediately available. Import never pins,
  installs, upgrades or downgrades VRChat SDK, Gesture Manager, AAO or another
  third-party VCC/VPM package.
- Forbidden regression: no third-party MCP runtime/code/GUID residue, missing
  meta/assets, duplicate upgrade files or more than three guided actions to
  connect and list tools.
- Acceptance: archive/provenance scan, zero packaged `Packages/` paths, clean
  import with unchanged project package manifests, compile and connection.
- [首次实现: 1.4.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### PKG-002 — Safe upgrade and user-data preservation

- Contract: upgrades preserve credentials, chats, memory, instructions,
  checkpoints and unrelated Unity content. Cleanup deletes only exact owned
  filename plus exact hash matches.
- Forbidden regression: no broad cleanup, renamed/modified user-file deletion,
  GUID conflict, duplicate install or lost saved credentials.
- Acceptance: disposable-Windows install/upgrade/uninstall and official Unity
  upgrade project; this remains a release-paired manual gate.
- [首次实现: 1.4.0] [强化/修复: 1.5.0] [最近验证: 待考证]

### PKG-003 — Compatibility-first third-party dependencies

- Contract: an absent optional package is reported as missing. Installation is
  opt-in and supervised; the exact available version is frozen only into that
  approval transaction. An already installed version is tried first without a
  forced upgrade.
- Forbidden regression: no global version pin, import-time install, silent
  downgrade or speculative upgrade. If the installed package proves
  incompatible at runtime, preserve the original error and direct the user to
  check available newer versions. Until Runtime owns a short-lived,
  single-use incompatibility receipt, caller-supplied failure claims may not
  create an upgrade approval; any future upgrade remains a separate approved
  transaction.
- Acceptance: dependency-doctor, optimizer skill, package-plan, version
  selection, diagnostic and prepared-install tests.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### CMP-001 — External Agent request boundary

- Contract: external Agents/connectors are authenticated requesters. VRCForge
  remains the only Unity/project write authority and owns the entire write
  transaction.
- Forbidden regression: no direct connector write, broad Shell authority or
  removal of supported compatibility without packaged proof and approval.
- Acceptance: connector negatives, interop matrix and package boundary scan.
- [首次实现: 1.3.0] [强化/修复: 1.5.0] [最近验证: 1.5.1]

### CMP-002 — DeepSeek Harness and host-side multi-MCP compatibility

- Contract: the App-side external-Agent edge prefers VRCForge MCP 2026
  (`2026-07-28`). A client that begins the published standard MCP initialize
  lifecycle may select a pinned supported MCP 1.x revision from the first valid
  frame. The selected profile is recorded and frozen for that connection; no
  mid-connection switch or silent catalogue downgrade is allowed. The Unity
  package/Core stays VRCForge MCP 2026-only.
- Contract: the DeepSeek Harness connector is generated in its official Cordis
  patch-list shape as one stable, uniquely namespaced stdio MCP row. It contains
  no credential, exposes planning tools by default, preserves unrelated MCP
  rows, and fails closed on duplicate ids, namespace conflicts or modified
  managed entries. The developer-preview Harness source used for compatibility
  verification is commit-pinned in evidence.
- Forbidden regression: no DSH-only protocol fork, token in generated YAML,
  direct external Unity write, replacement of another MCP row, guessed
  fallback after discovery, or claim that host-side multi-MCP coexistence is
  VRCForge outbound MCP federation.
- Acceptance: failure-first router/installer/connector tests; build the pinned
  official DSH repository; load VRCForge plus an independent MCP server in the
  same official DSH client; prove both namespaces can be called; then run an
  official DSH Agent turn that selects a VRCForge read tool and reaches a live
  Unity Editor. Preserve the separate built-in Provider Agent prompt replay as
  an honest comparison, including a rejected or failed final status.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Release contracts

### REL-001 — Strict artifact identity

- Contract: release artifacts come from clean, pushed source with exact version
  and commit binding, pinned dependency downloads and `releaseEligible=true`.
- Forbidden regression: local-acceptance, dirty, unpushed, version-mismatch or
  unpinned builds cannot be uploaded as formal release assets.
- Acceptance: strict manifest, four asset hashes, provenance and sensitive scan.
- [首次实现: 1.3.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### REL-002 — Package-paired external acceptance

- Contract: publication requires the exact candidate's clean/upgrade Unity
  evidence, one successful managed visual audit with authenticated Runtime
  completion receipt, installer lifecycle and user-data preservation.
- Forbidden regression: older package, source test, honest Provider failure or
  selection receipt cannot be promoted into release proof.
- Acceptance: evidence binds exact commit, hash, project, action, approval,
  checkpoint and verifier receipt.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 待考证]

### REL-003 — Publication remains user-owned

- Contract: tag, release mutation, upload and Latest/public state require
  explicit user authority. Remote asset sizes and hashes are read back.
- Forbidden regression: no implied publication or completion claim before
  remote readback.
- Acceptance: publication preflight and GitHub release readback.
- [首次实现: 1.1.2] [强化/修复: 1.5.0] [最近验证: 1.5.0]

## Change procedure

1. Identify affected stable IDs before implementation.
2. Add a failure-first test for every P0/P1 behavior change.
3. Preserve unaffected contracts and startup/lifecycle gates.
4. Update an item's version suffix only after executable verification.
5. Record package evidence in `PROJECT_STATUS.md`, future work in `ROADMAP.md`,
   and never use this contract as a session dump.
