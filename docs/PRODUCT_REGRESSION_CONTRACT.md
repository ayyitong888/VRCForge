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

### AGT-002 — Natural Agent loop with explicit safety budgets

- Priority: P0.
- Contract: an ordinary interactive turn ends when the model returns a final
  assistant response without another admitted action. Tool calls are telemetry
  (`toolCallsUsed` is observational only), not conversation turns and not a
  normal completion boundary. Interactive turns have no fixed tool-call or
  model-turn count. Automation, unattended and background runs must explicitly
  declare finite budgets such as `maxAgenticTurns`, wall-clock time and/or cost.
  The foreground request must not inherit that background-only limit, and the
  background request carries its finite budget across HTTP and Tauri transport.
  Provider/context deadlines, repeated-semantic-failure guard, cancellation,
  approval and Runtime-owned completion verification remain independent safety
  boundaries. Hitting any declared budget pauses an incomplete task and
  preserves the remaining action; it never reports completion. Desktop
  bootstrap keeps its own per-session accounting and cannot consume or reset
  the ordinary loop.
  Each action remains `plan -> admit/approve -> execute -> canonical result ->
  refeed -> verify -> next plan`.
- Forbidden regression: no arbitrary three- or 25-call normal cutoff, tool-call
  count mislabeled as a model/conversation turn, budget exhaustion reported as
  completion, missing result refeed, completed-action replay, hidden retry of an
  ambiguous side effect, or bootstrap sharing the ordinary turn's accounting.
- Acceptance: failure-first tests execute 26 distinct real Gateway tools and
  then stop on the model's natural terminal response; separate tests verify
  `toolCallsUsed` remains telemetry, freeze explicit finite automation budgets
  (model turns, time and/or cost), and retain repeated-failure, approval
  continuation, cancellation, completion verification and full-tree identity
  guarantees.
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

### AGT-008 — Exact-turn steer and durable follow-up lane

- Priority: P0.
- Contract: text-only input targeting the exact active Runtime turn is captured
  by CAS identity and injected at the next safe model boundary, never inside an
  executing tool or frozen approval. Attachments, unavailable steer targets and
  the bounded 20-item hot mailbox fall through to a durable per-chat follow-up
  lane instead of rejecting input. The durable lane is FIFO and serialized per
  chat while different chats may run independently; it has no arbitrary message
  count cap. Backpressure is based on bounded message/attachment size and the
  private queue-file byte budget, while the already-persisted chat input remains
  visible and retryable. Queue envelopes are idempotent by chat lane plus client
  turn, atomically persisted inside the private user-data boundary (requesting
  user-only file mode where supported), claimed with a session-bound lease
  token, acknowledged or cancelled explicitly, and restored
  after restart/runner handoff. Stop cancels only active work and pauses pending
  follow-ups until Resume or Cancel all. Ambiguous accepted delivery is shown as
  unverified and is never replayed automatically; terminal tombstones retain
  identity only and remove user content.
- A steer accepted after the last model boundary is not durable until queue
  persistence succeeds. Persistence failure/backpressure returns a bounded
  identity-only outcome and leaves the input visibly retryable; it never
  remains stuck as an apparently active steer.
- Forbidden regression: no steer injection during a tool batch, write-scope
  widening, eight-message rejection, count-based dropping, attachment loss,
  reordered or concurrent execution within one chat lane, input existing only
  in React memory, hidden auto-send, ambiguous duplicate replay, cross-session
  claim/ack, or Stop/restart orphaning pending input.
- Acceptance: CAS race tests cover safe-boundary injection and frozen write
  identity; durable tests cover 32+ FIFO inputs, byte-based backpressure,
  attachment-reference redaction, restart/idempotency, lease reclaim,
  session/token binding, persistence-failure rollback, Stop/Resume/Cancel and
  final-model-boundary fallback plus interrupted-delivery non-replay.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### AGT-009 — Projectless and General-project Agent parity

- Priority: P0.
- Contract: a temporary conversation without a project and a project explicitly
  typed `general` run as an ordinary General Agent, not as a degraded Unity
  workflow and not as a Shell-only mode. The model autonomously selects among
  bounded directory listing, text read, file find and text search, ordinary
  permission-gated Shell/process work, Ask/TODO/Sub Agent, attachments, vision
  and connected MCP capabilities according to the request. General turns must
  not expose or invoke Unity readiness, Unity MCP, avatar or project-write tools.
  Tool results are re-fed to the model and the Runtime completion gate remains
  authoritative.
- General filesystem reads are bound to the explicitly selected General project
  root or an existing absolute path written by the user in that turn. The tool
  module enforces its own depth/count/byte/output ceilings, rejects symlink or
  reparse traversal and known credential files, and redacts secret-like content
  before it can enter Provider context. A model-proposed path cannot authorize
  itself.
- A successful top-level listing is an observation, not proof that an
  investigation is complete. Repeating a semantically equivalent directory
  observation through another tool spelling is suppressed before execution and
  recorded as no progress only while it is consecutive. A distinct successful
  action or new observation resets that boundary, so `A -> B -> A` may inspect
  A again. Two duplicate proposals are returned to the planner as correction
  opportunities; the third consecutive duplicate hard-stops with
  `planner_no_progress`. The Runtime neither loops forever nor fabricates
  completion.
- Forbidden regression: no automatic Unity-tool detour, keyword-selected local
  plan, fixed one-tool workflow, repeated `list`/`dir`/`Get-ChildItem` execution
  for the same target, unscoped or link-escaped filesystem read, secret-bearing
  result, or final answer that treats an uninspected directory listing as
  completion evidence.
- Acceptance: failure-first catalog/prompt/Gateway tests cover tool exposure,
  bounded arbitrary-directory reads, binary/path/size limits, consecutive
  equivalent-observation suppression, `A -> B -> A` re-observation, the exact
  two-correction/third-stop boundary and an exact real Provider prompt replay
  with zero Unity tool calls and no false completion. Release diff review uses
  `agent_gateway.py` line growth only as a diagnostic: every net increase needs
  a per-hunk rationale proving Gateway-owned coordination or trust-boundary
  responsibility, explaining why an existing focused module is not the right
  owner, and naming the regression test that binds it. Unexplained hub growth
  or domain/presentation/catalog/helper logic in the Gateway fails review;
  justified growth is never rejected by line count alone.
- [首次实现: 1.6.0] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### AGT-010 — Frozen-prompt comparative live acceptance

- Priority: P0.
- Contract: Agentic-loop acceptance uses one frozen Unicode prompt and one
  explicitly authorized General root in fresh chats. Each configured comparison
  model is run separately with the same project type, permission mode and input;
  a refusal, failure or incomplete result remains valid evidence and is never
  rewritten as success. The comparison records safe observable behavior:
  Provider/model identity, first safe activity and first tool latency, ordered
  tool/invocation sequence, duplicate/no-progress suppression, bounded failures,
  final Runtime status/reason, total duration and Unity-tool count. It does not
  compare or display chain-of-thought.
- The same acceptance pass presents VRCForge and the current Codex reference UI
  for user-attested live manual review in equivalent active, collapsed-batch,
  expanded-batch and terminal states.
  Review is behavioral rather than pixel-perfect: chronology, one invocation
  per call/result pair, batch disclosure, one major duration, no per-event
  timestamps, visible progress/error detail and right-rail placement must match
  the approved product contract. The frozen prompt remains local evidence and
  must be redacted before any public use; no screenshot artifact is required.
- Forbidden regression: no changed prompt between models, reused chat/context,
  hidden model/permission/project difference, backend-direct substitute for the
  WebView/Tauri path, Unity-tool detour from General, result-only comparison,
  state selected after the fact to hide a duplicate/error, secret/API key,
  raw Provider body, prompt-path publication or CoT capture.
- Acceptance: the local report binds prompt digest/code-point count, General
  root digest, App/build identity and each fresh run/session; includes the
  user's live manual acceptance result plus a structured behavior table; and states every refusal,
  failure, manual Stop and unverified completion honestly.
- [首次实现: 1.6.0] [强化/修复: 1.6.0] [最近验证: 1.6.0]

### AGT-011 — Bound AGENTS instruction layers

- Priority: P1.
- Contract: every model-planned turn receives the enabled App-global
  `AGENTS.md` rules and, when a General or Unity project root is explicitly
  bound, that root's regular UTF-8 `AGENTS.md`. The project file is read-only,
  root-scoped, link-rejecting and bounded to 64 KiB. Runtime safety, exposed
  capabilities and approval rules remain authoritative; global/project
  instructions cannot grant writes, reveal secrets or bypass supervision, and
  the current user request owns the concrete task.
- Forbidden regression: no Settings claim that global instructions affect
  planning while only tool parameters receive them; no project conversation
  that silently ignores an existing root `AGENTS.md`; no parent/sibling search,
  symlink traversal, unbounded prompt growth, instruction content in tool
  arguments/logs/timeline, or edit to either AGENTS file during loading.
- Acceptance: focused loader tests cover missing, invalid and oversized files;
  planner tests bind a General project and prove global then project rules occur
  before the current user request without changing the planning/execution tool
  exposure boundary. Live acceptance uses a read-only task in a project that
  has an observable root rule.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### AGT-012 — Profiled tools and minimal Unity path guard

- Priority: P0.
- Contract: one `ProfiledToolRegistry` projects one shared implementation set as
  `CoreToolSet`, `GeneralToolSet` and `UnityToolSet`. General Mode exposes Core
  plus General; Unity Project Mode exposes Core plus General plus Unity, so it
  remains a strict capability superset rather than a second workflow Runtime.
  General/Core tools use plain model-visible names; Unity-only tools use the
  `unity_` namespace. Profile selection controls model visibility, not safety.
- Registered Unity project roots form a minimal cooperative-Agent path guard.
  Read/List/Glob/Grep remain available inside them. Ordinary `edit_file`,
  `write_file`, `delete_path`, `move_path`, `apply_patch` and ordinary Shell
  must reject direct project mutation; ordinary Shell also rejects a registered
  project as cwd or a direct project-path reference. Outside registered Unity
  roots these shared tools retain normal OS-user access. In Unity Project Mode,
  Unity tools and `unity_shell` carry `unity_project_access`, which permits only
  the current registered Unity project while preserving existing approval,
  change recording, validation and rollback behavior.
- Scope boundary: this prevents cooperative Agent mistakes only. It is not an
  adversarial security boundary and must not grow into an OS sandbox, ACL,
  separate user, filesystem interception, network isolation, Shadow Workspace,
  subprocess/script escape detector, junction defense or active path-bypass
  system. Capability profiles never duplicate General and Unity implementations.
- Forbidden regression: no Unity Mode that loses a General tool; no second
  Agent Runtime or copied file-tool implementation; no profile-only write
  protection; no ordinary project write/Shell access; no guard applied to
  project reads or paths outside registered Unity roots; and no broad security
  infrastructure beyond this declared cooperative threat model.
- Acceptance: registry tests prove the exact profile set relationship and
  handler identity reuse. Path/tool/Shell tests prove General read access,
  ordinary write and cwd/direct-reference refusal, current-root-only
  `unity_project_access`, other-root refusal and unrestricted external paths.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

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

- Contract: Windows notifications show a safe action gist and are detail-first
  for both main-thread approvals and sub-agent review. Clicking a notification
  only wakes the App, re-fetches/revalidates the still-pending identity and
  opens the owning chat/detail card; approve, reject, adopt and dismiss remain
  explicit in-App actions.
- Forbidden regression: never include raw prompts, keys, commands, paths,
  hierarchy, checkpoint IDs or complete arguments. A deep link re-fetches and
  revalidates exact task/chat/revision/pending state before opening actions.
  A native-notification callback must never decide an approval or review.
- Acceptance: Rust parser/validation and frontend deep-link/payload tests are
  required automatically; manual Windows acceptance verifies wake, exact-card
  focus and stale-notification refusal for both main and sub-agent paths.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

## Project workbench and conversation contracts

### UX-001 — Exact project right-rail composition

- Contract: the right rail follows the compact Codex/Claude Cowork information
  hierarchy rather than exposing product-management cards. A General project
  contains, in order, `Progress`, `Sub Agents`, the named workspace, and a
  direct `Sources` section only while the current chat has user attachments;
  the workspace exposes local root, branch, changes and outputs. Memory and
  Skills remain in their dedicated product surfaces rather than being repeated
  in a synthetic `Context` section. A Unity project
  retains, in order, Agent TODO, Sub Agents, VRCForge Environment Information
  and User Attachment Sources because those rows carry Unity readiness and
  approval state. Each listed status section is independently collapsible and
  its collapsed state is isolated by project type. The optional `Sources`
  section is absent when the current chat has no attachments, so it never shows
  an empty disclosure control. Quick Chat retains the generic environment
  surface.
- Attachment Sources come only from the current chat's user-provided
  files/images, including compacted vault references. Locate appears only while
  the owning message remains in the transcript; Open appears only while the
  original inline image preview exists. Compacted metadata exposes neither
  inert control.
- Forbidden regression: no Goal-management or Workflow-management card, no
  central Run Ledger, no Unity Core/readiness card in General, and no loss of
  the Unity-specific readiness surface in Unity. Goal state belongs immediately
  above the composer only while an active Goal exists.
- Acceptance: `tests/test_general_cowork_right_rail_ui.mjs` and
  `tests/test_162_product_regression_ui.mjs` freeze composition and exclusions;
  the user manually compares equivalent General/Unity states in the launched
  build with the approved Codex/Claude Cowork references.
- [首次实现: 1.5.1] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-002 — Agent-owned Progress/TODO presentation

- Contract: Agent skills own list/replace/create/update/delete. General labels
  the section `Progress`; Unity retains `Agent TODO`. The rail is a
  compact ordered status list, not user checkboxes. Pending items use a muted
  gray hollow numbered circle. Active items use a theme-colored numbered circle
  and a reduced-motion-aware breathing title. Completed items keep the same
  numbered marker in the completed color while only the title becomes muted
  and struck through. Failed/blocked items remain visibly failed and never
  impersonate completion.
- Forbidden regression: do not replace TODO with execution history, editable
  checkboxes, visible status words, status-color traffic lights, second-line
  error/summary text, status grouping or a duplicate center card. Preserve the
  received order and derive the visible number only from that display order.
- Acceptance: TODO skill/API and layout/style contract tests.
- [首次实现: 1.5.1] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-002A — Environment rows disclose truncated text on hover

- Contract: the project Environment section remains compact, but each row,
  title and truncated detail exposes its complete label/value through hover.
- Forbidden regression: no permanently clipped status with no disclosure, and
  no eager tooltip framework or startup request for static text.
- Acceptance: RuntimeInfoRow source contract and user live manual review.
- [首次实现: 1.5.1] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-003 — Sub-agent summary and detail workspace

- Contract: the rail shows a compact active/completed summary. Opening it uses
  an independent large, scrollable surface with Active/Open and Completed
  groups, event/history detail and status-derived Cancel/Retry/Adopt/Dismiss/
  Use-next-action controls. The detail surface has its own scroll owner and may
  overlay the workspace, but the main chat remains mounted with its history,
  scroll position and composer intact.
- Forbidden regression: do not embed full detail in the narrow rail,
  auto-adopt output, unmount/replace the main chat, lock history during review
  or eagerly load detail during startup.
- Acceptance: workbench, notification deep-link, main-chat persistence,
  independent-scroll, lazy-load and startup tests plus manual scroll review.
- [首次实现: 1.5.0] [强化/修复: 1.5.1] [最近验证: 1.5.1]

### UX-004 — Chronological conversation timeline

- Priority: P0.
- Contract: user messages, safe Agent commentary/final replies, planner
  boundaries, tool calls/results, file edits, commands and Sub Agent
  created/started/completed/failed lifecycle events render as one durable source
  timeline ordered by authoritative sequence and timestamp. Every safe,
  user-facing planner update remains visible at its original position even if
  its language is mechanical; only reasoning/CoT is excluded. A single
  commentary/tool/command/Sub Agent invocation renders directly with its real
  summary or label, never behind a generic `Work segment` or `Used tools`
  wrapper. Only adjacent same-kind multiple invocations may form one compact
  batch, and any intervening commentary/status breaks that batch. One matching
  tool call/result pair becomes one invocation; repeated calls and lifecycle
  transitions remain separate facts. General Agent tool rows use their plain
  capability names without the internal `vrcforge_` namespace; Unity-specific
  tools retain that namespace so their product boundary stays explicit.
  Timeline summaries may remain safely bounded, but an expanded completed
  invocation renders the exact full result persisted in its owning
  `response.steps` record inside a bounded internally scrolling card. Reused
  action IDs consume those stored results in occurrence order, including
  `A -> B -> A`; display scrolling must never become payload truncation.
  Prompts, credentials and raw tool arguments never enter the projection.
- Sub Agent lifecycle is projected only from the durable task registry with its
  stable task revision. The Runtime delegate call must not synthesize a second
  created/started lifecycle stream. Delegation itself remains an ordinary
  Runtime tool and therefore keeps its own chronological `tool_call` and
  `tool_result`; registry lifecycle events supplement rather than replace
  those execution facts.
- Known terminal outcomes are projected from structured Runtime status/reason
  codes, never opaque assistant prose. `planner_no_progress` renders as a
  localized failed/not-complete Runtime card rather than an ordinary Agent
  answer. Stop settles the live projection into a durable cancelled Agent card
  while preserving accepted timeline evidence and one whole-turn duration;
  restored history and copy use the same localized projection.
- Forbidden regression: do not collect all calls above/below chat, hoist all
  planner updates into one category block, hide safe commentary, wrap a single
  invocation in a generic semantic accordion, collapse
  repeated calls into the last result, replace the timeline with a Run Ledger,
  expose `vrcforge_` on a General Agent tool row, strip it from a Unity-specific
  tool row,
  clip a completed expanded result at the timeline-summary limit, substitute a
  later occurrence's result for an earlier repeated action,
  append delayed Sub Agent terminal cards at discovery time, attach tool JSON
  to copied prose, display a known Runtime failure as completed prose, expose
  the raw terminal body, or erase the timeline on acknowledged cancellation.
- Acceptance: backend sequence/timestamp/sanitization tests and frontend
  materialization/fallback/copy/no-duplicate-lifecycle, Runtime-terminal and
  durable-Stop tests are automatic gates. A result longer than 1000 characters
  must retain its tail marker, and repeated action IDs must retain FIFO result
  association. Manual acceptance verifies live prose/tool/Sub Agent
  interleaving, complete expanded output, terminal presentation and internal
  scroll stability.
- [首次实现: 1.2.0] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-005 — Edit and copy behavior

- Contract: user and Agent prose each expose copy of their visible prose only;
  Planner/tool/result JSON, hidden traces and attachments are excluded. Editing
  remains available on the latest sent user bubble through a usable full-width
  editing surface, then re-submits with the original turn identity rules.
  Copy success/failure receives a dismissible transient notice. The
  conversation text-selection action toolbar exists only while its owning chat
  surface is active; changing view, chat, project or opening a project/sub-agent
  detail surface clears both the toolbar state and the browser selection.
- Forbidden regression: no Agent-only copy restriction, unusable narrow edit
  field, Planner/tool/result JSON in copied text, response-rating thumbs UI, or
  stale selection action toolbar over Settings or another conversation surface.
- Acceptance: timeline tests cover user+Agent copy payloads, full-width edit
  interaction, clipboard failure and exclusion of non-prose content. A focused
  navigation contract and live acceptance cover selection-toolbar retirement.
- [首次实现: 待考证] [强化/修复: 1.5.1] [最近验证: 1.6.0]

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
  Enter execution and Escape dismissal. The user slash surface is intentionally
  short and Agent-generic: `/compact`, `/goal`, `/memory`, `/delegate` and
  `/handoff`; `/desktop` may appear only with Developer Options and Computer
  Use enabled. MA, VRCFury and other domain skills remain Agent-callable but do
  not flood the user menu. `/handoff` opens the bounded handoff card only when
  invoked; there is no always-on handoff form above the conversation. Only real
  VRCForge capabilities are listed; absent plugins, targets or plans are never
  invented.
- Forbidden regression: no separate large explanation cards or developer-only
  command list, unbounded input/menu growth, hidden disabled reason, or
  unsupported capability claim.
- Acceptance: failure-first UI contract covers shared container and row
  association, action/command filtering, disabled reasons, max-height scroll,
  mouse/keyboard behavior, attach fallback and dismissal/reopen behavior;
  `tests/test_slash_menu_shrink_ui.mjs` freezes the compact generic list,
  domain-skill exclusion and slash-only handoff surface.
- [首次实现: 1.5.1] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-009 — Explicit General and Unity project types

- Priority: P0.
- Contract: every newly added project is explicitly typed exactly `general` or
  `unity`, and that type is carried through project preferences, catalogue,
  chat identity, Runtime request, durable follow-up envelope, HTTP and Tauri.
  A General project accepts an absolute existing directory and uses the General
  Agent surface. A Unity project must contain `Assets`, `Packages` and
  `ProjectSettings/ProjectVersion.txt`, binds the Unity-specific readiness and
  tool surface, and may be selected from a discovered/running Editor instance.
  Legacy entries are classified once from their validated shape.
- Opening or discovering a Unity Editor must not implicitly convert a temporary
  conversation or General project into Unity. Changing type is an explicit user
  selection; General paths never pass through Unity selection, install, launch
  or readiness APIs.
- The left project rail presents General and Unity projects in two visibly
  separate sections. Explicit General entries appear only under General;
  explicit Unity entries and legacy entries without a persisted type remain
  under Unity. Grouping preserves the incoming order within each type and does
  not duplicate, drop or re-parent nested chats or their selection, unread,
  pin, rename, menu and collapse state.
- Forbidden regression: no path-only guess after explicit type exists, hidden
  General-to-Unity promotion, arbitrary directory rejected solely for lacking
  Unity metadata, Unity root accepted without its required structure, or
  project type dropped by queue/restart/IPC transport. Do not mix General and
  Unity project rows under one undifferentiated heading or infer the group from
  a running/discovered Editor.
- Acceptance: executable frontend/backend contracts cover the exact two-choice
  UI, validation/migration, no-auto-conversion, chat/queue/runtime propagation
  and Tauri parity. A focused sidebar contract covers the two sections,
  order-preserving split and legacy-to-Unity behavior; packaged WebView review
  covers project/chat actions in both sections. A live General prompt proves
  zero Unity calls while a separately selected Unity project retains its
  supervised tool chain.
- [首次实现: 1.6.0] [强化/修复: 1.6.0] [最近验证: 1.6.0]

### UX-010 — Provider idle reconnect disclosure

- Priority: P1.
- Contract: while a turn is waiting for the Provider, safe Provider activity
  resets the idle clock and keeps the ordinary phase display. With no safe
  activity, the chat shows a compact `Reconnecting N/5` status after each
  consecutive 60-second interval. The status has a visible disclosure chevron;
  expanding it shows fixed localized product text, not Provider output or
  internal reasoning. Attempts 1-4 say that no Provider activity has arrived
  yet and that any activity resets the timer. At 5/5 the turn terminates and
  the detail directs the user to retry manually while stating that the request
  was not automatically retried or replayed.
- Forbidden regression: no absolute timeout while safe activity continues,
  hidden/missing disclosure affordance, automatic retry/replay at 5/5, raw
  error body, prompt, credential, CoT/reasoning text, or per-event timestamp in
  the reconnect presentation.
- Acceptance: failure-first streaming contracts cover the five 60-second
  steps, activity reset, bounded 5/5 result, visible rotating chevron, four
  locale keys and timer cleanup. A packaged WebView acceptance pass expands the
  status, injects safe activity before 5/5, and observes a real silent 5/5
  terminal result followed by a user-owned manual retry.
- [首次实现: 1.6.0] [强化/修复: 1.6.0] [最近验证: 1.6.0]

### UX-011 — Conditional Goal control surface

- Priority: P1.
- Contract: an active Goal renders one compact control bar immediately above
  the composer with its objective/state and bounded pause/resume/cancel
  controls. With no active Goal the bar consumes zero layout space and no Goal
  card or placeholder appears in the right rail. Users may create, inspect and
  control a Goal through `/goal`; the Agent may use the existing Goal tool/API
  path. Both paths operate on the same durable Goal identity and state.
- Forbidden regression: no permanent `Enable independent continuation` card,
  no empty Goal section, no user-only or Agent-only ownership, no second active
  Goal silently replacing the first, and no Workflow card substituted for Goal.
- Acceptance: `tests/test_162_product_regression_ui.mjs` freezes conditional
  placement and both entry paths. User live manual acceptance covers one
  inactive chat with no Goal surface and one equivalent active-Goal chat with
  the bar directly above the composer.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-012 — Complete provider/model identity and continuous context ring

- Priority: P1.
- Contract: the composer and conversation disclose the complete effective
  Provider and model using a Unicode-safe middle-dot separator. Full-width
  layout keeps identity and context usage on one line; narrower layouts move
  the complete identity to a second line instead of truncating it. Context
  usage is a single continuous circular meter: a complete base ring plus the
  used arc for known exact usage, and one complete solid neutral ring when
  usage is unknown. Known colour thresholds are `<60%` primary, `60-89%` amber
  and `>=90%` danger. Hover/focus retains percent and token detail.
- Forbidden regression: no replacement characters, mojibake, clipped model
  identity, segmented/dashed unknown ring, missing base circumference, old
  horizontal bar, or changed warning thresholds.
- Acceptance: `tests/test_chat_model_identity_ui.mjs` and
  `tests/test_162_product_regression_ui.mjs` freeze separator, full-name wrap,
  complete-ring structure and thresholds. User live manual acceptance covers
  full and constrained widths with a long model name plus known and unknown usage.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-013 — Single panel-collapse owner and jump to conversation end

- Priority: P1.
- Contract: only the right rail header owns the open-state collapse button; its
  collapsed rail owns the matching restore button. The left sidebar remains
  open and the center header has no duplicate right-rail toggle. When the user
  is away from the pinned conversation bottom, a floating downward control is
  visible and moves the conversation directly to its end; it disappears again
  at the bottom. Reaching the bottom with the mouse wheel only updates pinned
  state and must not start a competing smooth programmatic scroll. Automatic
  content-follow uses an immediate scroll only while already pinned; smooth
  scrolling is reserved for the explicit downward control.
- Forbidden regression: no left-sidebar collapse button, duplicate center/right
  collapse controls, stranded collapsed right rail, permanently visible jump
  control, composer-overlapping jump control, or first-arrival bottom bounce.
- Acceptance: `tests/test_panel_toggle_dedupe_ui.mjs` and
  `tests/test_162_product_regression_ui.mjs` freeze ownership and the scroll
  action; user live manual acceptance verifies open/collapsed layout, a
  wheel-driven no-bounce bottom arrival and a button-driven long-chat jump at
  representative narrow and full widths.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

### UX-014 — Diagnostic identity maps stay developer-only

- Priority: P1.
- Contract: the local identity map remains available only on the Developer
  page while Developer Options is enabled. Normal Settings keeps redaction, retention,
  log-level and support-bundle controls without rendering the potentially long
  alias-to-user/project/avatar list.
- Forbidden regression: no local identity map, alias list, Windows user,
  project identity or Avatar mapping in normal Settings.
- Acceptance: `tests/test_settings_diagnostics_ui.mjs` freezes the Developer
  page placement and Options gate; user live manual acceptance verifies the map
  is absent from General and visible only on the enabled Developer page.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

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
- `Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs` stays at its
  published GUID `c03999e57815100961016fab067f9c2b` and the entire packaged
  source remains wrapped by first-line `#if UNITY_EDITOR` and final-line
  `#endif`. Its `EditorUtility` and `GlobalObjectId` references must never enter
  `Assembly-CSharp.dll` or the Player/VRChat avatar build compile boundary.
- Forbidden regression: no third-party MCP runtime/code/GUID residue, missing
  meta/assets, duplicate upgrade files, an unguarded approval receipt, the
  `CS0103: EditorUtility does not exist` / `CS0103: GlobalObjectId does not
  exist` build failure chain, or more than three guided actions to connect and
  list tools.
- Acceptance: archive/provenance scan, zero packaged `Packages/` paths, clean
  import with unchanged project package manifests, Editor and Player compile,
  VRChat `Build & Test`, and connection. Automated archive acceptance extracts
  the exact receipt source and asserts its full guard and fixed GUID; the fresh
  Unity import/build remains a release-paired manual gate.
- [首次实现: 1.4.0] [强化/修复: 1.6.2] [最近验证: 1.6.2]

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

### REL-004 — Startup-only silent App Update check

- Priority: P1.
- Contract: once per App process, after startup is usable, start one background
  GitHub Releases version check in parallel with ordinary App work and compare
  the latest version with the version actually running. Only a successful
  response that proves a newer version opens one in-App update dialog. An
  up-to-date response, unreachable GitHub, timeout, malformed response or any
  other failure is silent.
- Forbidden regression: no startup delay, 30-second wait, periodic/six-hour
  polling, manual Settings check, system notification, notification-permission
  prompt, automatic download/install, error popup, or `no update` popup.
- Acceptance: `tests/test-app-update-ui.mjs`,
  `tests/test_app_update_dashboard.py` and `tests/test_app_update_service.py`
  freeze once-only startup wiring, fixed GitHub boundary, semantic comparison
  and silent negatives. Packaged acceptance covers newer, current and offline
  startup states without delaying first usable paint.
- [首次实现: 1.6.2] [强化/修复: 1.6.2] [最近验证: 1.6.2]

## Change procedure

1. Identify affected stable IDs before implementation.
2. Add a failure-first test for every P0/P1 behavior change.
3. Preserve unaffected contracts and startup/lifecycle gates.
4. Update an item's version suffix only after executable verification.
5. Record package evidence in `PROJECT_STATUS.md`, future work in `ROADMAP.md`,
   and never use this contract as a session dump.
