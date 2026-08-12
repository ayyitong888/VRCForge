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

- Contract: each step is `plan -> admit/approve -> execute -> canonical result
  -> refeed -> verify -> next plan`. Action identity survives approval,
  execution, restart, result delivery and verification.
- Forbidden regression: no unlimited call chain, missing result refeed,
  completed-action replay or hidden retry of an ambiguous side effect.
- Acceptance: task-loop, continuation, cancellation, background and full-tree
  tests; the per-turn tool budget remains enforced.
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
  status list, not user checkboxes. Active items keep a colored marker and
  reduced-motion-aware breathing effect; completed items keep the colored
  marker while text becomes muted and struck through.
- Forbidden regression: do not replace TODO with execution history, editable
  checkboxes or a duplicate center card.
- Acceptance: TODO skill/API and layout/style contract tests.
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

- Contract: editing occurs inline on the latest sent user bubble. Copy copies
  only visible Agent natural-language reply text. Copy success/failure receives
  a dismissible transient notice.
- Forbidden regression: no editing in the new-message composer, no Planner/
  tool/result JSON in copied text and no response-rating thumbs UI.
- Acceptance: chat timeline and clipboard failure tests.
- [首次实现: 待考证] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-006 — Transient failure notice

- Contract: visual, upload, chat-send and clipboard failures use a lower-center
  rectangular card with a short upward entrance, close control and about three
  seconds total lifetime. The card contains a useful bounded summary.
- Forbidden regression: no silent failure, raw error dump, upper-corner
  placement or modal interaction lock.
- Acceptance: toast timing/style/i18n tests and packaged visual review.
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
  compatible protocol for the same Provider, exact model and canonical
  endpoint; explicit protocol pinning never silently falls back.
- Forbidden regression: no cross-Provider/model/origin fallback, retry after
  output/ambiguous side effect, or capability claim from model list alone.
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

## Latency and lifecycle contracts

### LAT-001 — Startup is a release-blocking hard gate

- Contract: static startup shell paints before App loading. Warm startup shell,
  backend invoke and cached bootstrap each remain at or below 100 ms. Center is
  usable before sidebars mount/hydrate; FCP is recorded.
- Forbidden regression: no eager sidebars, sub-agent detail or historical
  runtime data in the critical path; thresholds must not be weakened to pass.
- Acceptance: manifest-bound isolated cold/warm pair, normal Quit and zero
  residue. Verified 1.5.1 warm sample: shell 100 ms, backend invoke 30 ms,
  cached bootstrap 9 ms and FCP 176 ms.
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
  and connection guidance are immediately available.
- Forbidden regression: no third-party MCP runtime/code/GUID residue, missing
  meta/assets, duplicate upgrade files or more than three guided actions to
  connect and list tools.
- Acceptance: archive/provenance scan, clean import, compile and connection.
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

### CMP-001 — External Agent request boundary

- Contract: external Agents/connectors are authenticated requesters. VRCForge
  remains the only Unity/project write authority and owns the entire write
  transaction.
- Forbidden regression: no direct connector write, broad Shell authority or
  removal of supported compatibility without packaged proof and approval.
- Acceptance: connector negatives, interop matrix and package boundary scan.
- [首次实现: 1.3.0] [强化/修复: 1.5.0] [最近验证: 1.5.1]

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
