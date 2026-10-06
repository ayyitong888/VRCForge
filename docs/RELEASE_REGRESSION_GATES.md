# Release Regression Gates

This document records the regression classes used for VRCForge releases. A
passing source or unit test proves the stated contract in its test environment.
It does not prove that every tool, Unity project, avatar, or provider has been
live exercised. The 1.8.0 release includes hosted Windows evidence for the
fresh-install, upgrade, install-preservation, and uninstall path in run
34987883889; that evidence is separate from the repository test suite.

The normative product rules live in
[PRODUCT_REGRESSION_CONTRACT.md](PRODUCT_REGRESSION_CONTRACT.md). This page is
their release evidence supplement.

## VRC tool audit classes

These classes are the recurring failure surfaces found in the VRC tool audit:

| Class | Required contract | Evidence |
| --- | --- | --- |
| Exact object identity | Same-name objects, Animator layers/states, and material slots must be resolved by stable scoped identity; a name match alone must fail closed or require disambiguation. | `tests/test_animation_reader_identity_runtime.py`; `tests/test_ensure_animator_identity_runtime.py`; `tests/test_add_component_identity_scope.py`; `tests/test_material_property_edit.py` |
| Field forwarding | Tool input fields must reach the owning handler unchanged, with schema defaults and structured result fields preserved across Gateway and MCP boundaries. | `tests/test_agent_tool_result_contract.py`; `tests/test_dashboard_cli_forwarding.py`; `tests/test_external_core_failure_receipt.py`; `tests/test_execution_target_gateway_contract.py` |
| Scoped persistence | Reads and writes remain bound to the requested project/object scope; persisted state cannot silently widen to another project or user-data root. | `tests/test_checkpoint_requested_project_scope.py`; `tests/test_checkpoint_isolation.py`; `tests/test_ensure_animator_persistence_runtime.py`; `tests/test_agent_runtime_run_ledger.py` |
| Paging and completeness | Paged scans expose continuation and completeness state; truncation is explicit and cannot be presented as a complete inventory. | `tests/test_material_scan_index_paging.py`; `tests/test_asset_info_object_listing.py`; `tests/test_asset_text_resources.py`; `tests/test_general_agent_tools.py` |
| Failure and pending commit receipts | Failed, pending, rejected, scheduled, or unverified actions retain their state and cannot be promoted to committed success. | `tests/test_agent_completion_verifier.py`; `tests/test_authoritative_write_receipt_projection.py`; `tests/test_agent_approval_transaction_service.py`; `tests/test_agent_tool_result_contract.py` |
| Rollback context | A rollback receipt retains project, operation, checkpoint, and failure context; rollback failure preserves the current state and recovery path. | `tests/test_agent_checkpoint_recovery_service.py`; `tests/test_checkpoint_asset_memory_recovery.py`; `tests/test_atomic_reference_rename_undo_runtime.py` |
| Preview parity | Preview/readback uses the same target, parameters, and capability decisions as apply; a preview cannot claim a different object or result than the eventual write. | `tests/test_blendshape_preview_validation_live.py`; `tests/test_dashboard_screenshot_approval_policy.py`; `tests/test_checkpoint_preview_responsiveness.py` |
| Shader capability typing | Shader and material tools select by declared capability/property type and report unsupported properties; they must not hard-code one vendor or shader family as universal. | `tests/test_appearance_shader_schema_contract.py`; `tests/test_material_shader_assignment.py`; `tests/test_material_scalar_property_edit_runtime.py`; `tests/test_material_property_edit.py` |
| Internal/external permissions | Internal tool blocks, external MCP callers, and Unity writes use separate permission gates; external discovery/read access does not grant internal or project mutation authority. | `tests/test_internal_tool_blocks.py`; `tests/test_external_mcp_full_permission_policy.py`; `tests/test_agent_gateway_integrity.py`; `tests/test_authoritative_unity_writes.py` |

## Release and installer classes

| Class | Required contract | Evidence |
| --- | --- | --- |
| Exact target | Project and install targets must be explicit, scoped, and rejected when ambiguous or outside the allowed root. | `tests/test_web_payload_helper.py`; `tests/test_release_build_policy.py`; `installer/VRCForge_WebPayload.ps1` destination validators |
| Persistence | Successful state is persisted only after the operation reaches its committed boundary; failed or incomplete work remains distinguishable. | `tests/test_agent_runtime_run_ledger.py`; `tests/test_agent_checkpoint_recovery_service.py`; `tests/test_agent_completion_verifier.py` |
| Pending and interrupted work | Pending approval, paused work, and interrupted execution must not be reported as completed or executed. | `tests/test_agent_approval_transaction_service.py`; `tests/test_agent_runtime_followup_queue.py`; `tests/test-path-to-skill-context.mjs` |
| Errors | Invalid input, unavailable services, helper failures, and protocol errors produce structured failure state and preserve the prior valid state. | `tests/test_agent_completion_verifier.py`; `tests/test_release_build_policy.py`; `tests/test_web_payload_helper.py` |
| Permissions | Read, write, approval, and external-tool boundaries are enforced by the tool contract and execution layer. | `tests/test_agent_gateway_action_identity.py`; `tests/test_avatar_encryption_addon.py`; `tests/test_release_build_policy.py` |
| Rollback | Approved writes have a recoverable checkpoint; rollback failure preserves the current state and exposes recovery information. | `tests/test_agent_checkpoint_recovery_service.py`; `tests/test_agent_approval_transaction_service.py` |
| Installer target and preservation | Install and upgrade operate on the exact VRCForge leaf, preserve the prior installation when activation fails, and keep uninstall cleanup scoped. | `installer/VRCForge_WebPayload.ps1`; `tests/test_web_payload_helper.py`; hosted Windows run 34987883889 |
| Running-app retry | Detect exact installed App/backend processes as well as file locks before activation. Interactive users can close the App and Retry in the same setup; Cancel preserves the old installation. Late activation retries also recreate consumed Web download state. | `tests/test_release_build_policy.py`; `scripts/test_installer_retry_ui.py`; Hotfix1 real Windows evidence below |
| Silent installer behavior | Silent mode defaults Retry/Cancel dialogs to Cancel and exits nonzero without blocking. It never defaults to Retry or loops unattended. | `installer/VRCForge_Offline_Installer_x64.nsi`; `installer/VRCForge_Web_Installer_x64.nsi`; `tests/test_release_build_policy.py`; `scripts/test_installer_retry_ui.py` |
| PowerShell module resolution | Installer-launched Windows PowerShell children receive the native Windows PowerShell module path at process scope; registry state and the caller environment are unchanged. | The two NSIS `.onInit` entrypoints; `tests/test_release_build_policy.py` |
| ZIP layout safety | The archive entry count and byte limits bound extraction without rejecting the 1.8.0 payload: 5,989 entries are accepted and 8,193 are rejected against the 8,192 limit. | `installer/VRCForge_WebPayload.ps1`; `tests/test_web_payload_helper.py` |

## MCP and Unity evidence boundary

MCP contract tests cover handshake, tool discovery, loaded tool-block
invocation, read/write permission separation, structured receipts, and error
classification. They establish protocol and policy behavior; they do not claim
that all 235 tools were exercised against a live Unity project. Live hosted or
desktop evidence must identify the actual project scope, selected tool, result
state, readback, and any approval or rollback transition.

For Unity-facing changes, compile and package checks establish that the package
loads and that the declared Core contract is present. A live Unity result is
required to claim an actual scene, avatar, material, animation, or project
mutation. Pending, scheduled, rejected, failed, and unrouted receipts remain
non-success until independent readback verifies the intended result.

## Gate maintenance

Every P0/P1 release defect adds a focused regression test or a documented hosted
reproduction. Keep source/unit evidence, packaged evidence, and live hosted
evidence separately named in release reports. Do not convert a static test into
a live-coverage claim, and do not count a listening port or successful process
start as proof of an MCP or Unity workflow.

## 1.8.0 closeout references

### Installer Hotfix1

The original fresh-install/upgrade gate did not cover interactive Retry. Hotfix1
adds real Windows UI tests for both installers: locked-file Cancel/preservation,
unlocked Retry, an actual running App that stays alive until the test closes it,
late activation failure followed by Retry, and bounded silent failure. Each case
reads back installed hashes and a user-data sentinel. Running images must be
detected by exact process identity: Windows can permit an exclusive read of an
executable while it is running, so a file-lock check alone is insufficient.

- [Offline job](https://github.com/ayyitong888/VRCForge/actions/runs/34996314930/job/104473337259)
  passed at `332fe12`. The Web job in that older run failed and was superseded.
- [Web run](https://github.com/ayyitong888/VRCForge/actions/runs/34997563193)
  passed at `0455473`, including deletion of the consumed state descriptor before
  a later retry calls `Prepare` again.
- The published standard-name installers contain the tested Hotfix1 bytes; superseded installers have been removed. Their hashes,
  source commits and case exit codes are in
  [release-manifest.json](https://github.com/ayyitong888/VRCForge/releases/download/v1.8.0/release-manifest.json).
  App/backend payload, Unity package and original release tag remain unchanged.

The installer and package fixes were reviewed against these exact commits:

- `a9e5c25`: installer silent-dialog defaults.
- `3299c7d`: process-scoped PowerShell module resolution.
- `e9118e4`: ZIP entry-limit correction for the 1.8.0 payload and its
  build-time smoke gate.

The build gate references `packaging/build_release.ps1` and
`scripts/smoke_packaged_backend.py`, `tests/test_release_build_policy.py`, and
`tests/test_installer_archive_budget_gate.py`. The helper and archive boundary
gate references `installer/VRCForge_WebPayload.ps1` and
`tests/test_web_payload_helper.py`. Hosted lifecycle evidence is bound to the
published candidate by the release report; source and unit results do not
substitute for that hosted install, upgrade, preservation, or uninstall run.

## Agent runtime and UI regression gates

These contracts preserve the defects covered during the 1.8.6 work. Source
tests establish their specified behavior; packaged and live evidence remain
separate and must not be inferred from test counts.

### Native provider history and continuations

- Every admitted tool call receives exactly one result with its original ID.
  Invalid, invisible, unsupported parallel and cancelled calls must not execute
  handlers or leave orphan calls in a subsequent provider request.
- Native history preserves the provider's required reasoning and continuation
  fields. Omit empty assistant `tool_calls` arrays, but retain real calls,
  result IDs and provider state. Legacy responses need no native-only fields.
- An explicit model-turn budget pauses the same session without discarding its
  native calls, results or private reasoning. Continuing with the same provider
  binding appends the user's message and must not replay a successful handler.
- Import visible legacy conversation history once. Keep durable private replay
  in the session owner; public approvals, questions, sessions, observations and
  sub-agent summaries must not expose it. Public redaction must not mutate it.
- Accepted steering reaches the next request. Questions and approvals retain
  and settle their exact original call. Stop settles only the owning pending
  call; repeated decisions and stale continuations cannot rerun completed work.
- Background Shell and sub-agent completion settle their original call once.
  Changing provider, model, credentials or thinking during approval preserves
  the executed receipt and pauses without replaying a completed write.
- Context limits measure the native request including history and schemas.
  Compaction atomically replaces only a completed prefix, excludes private
  reasoning and preserves current calls, results and steering. Cancellation,
  races, failed compaction and current-turn overflow send no oversized request.
- Provider requests and compaction share cancellation, deadline and shutdown
  ownership. Thinking-only events count as activity; cleanup failures must not
  replace the primary error. Stop closes the owned request and remains terminal.
- Live acceptance must distinguish a gap between tool results from provider
  inactivity. Record phase and activity timing for the exact session and turn,
  including zero-activity waits; exclude message bodies and credentials. Open
  observation before the request and close it on completion, error or deadline.
  Activity alone is not evidence of task success or model capability.
- Decision probes with synthetic history must validate every recorded tool
  argument against its advertised schema before calling the provider. Label
  reconstructed history and reduced catalogs explicitly; they do not establish
  full-task acceptance or reproduce unavailable private provider history.
- Exact configured DeepSeek model names and thinking settings reach the selected
  transport unchanged. Metadata and adapters share the supported model set.

Regression: `tests/test_native_runtime_gateway.py`,
`tests/test_native_budget_resume.py`,
`tests/test_native_runtime_continuations.py`,
`tests/test_native_async_continuations.py`,
`tests/test_native_context_compaction.py`, `tests/test_native_context_guard.py`,
`tests/test_native_approval_privacy.py`, `tests/test_native_external_privacy.py`,
`tests/test_native_subagent_privacy.py`, `tests/test_provider_lifecycle.py`,
`tests/test_provider_protocol_clients.py`, `tests/test_model_provider_adapters.py`,
`tests/test_reasoning_effort.py` and `tests/test_deepseek_responses_adapter.py`.

### Tool discovery, results and completion

- Discovery, visibility, load and invocation share the callable catalog policy.
  Diagnostic Core entries are not advertised as callable runtime tools. Reject
  aliases and implicit loads with the actual current discovery/load recipe.
- Structured observations preserve action identity, kind, status and validated
  `vrcforge:` execution identities. Sanitize credentials and private content.
- Only corrected admission of the same task and route may supersede an older
  loader-admission failure. Unrelated success cannot erase an executed failure.
- Preserve full completion obligations through approval, including older failed,
  pending and unexecuted requirements. Reject oversized persistence explicitly.
  Missing independent readback must not become a success or completion claim.
- Instruction Skill scope has an explicit exact-name exit. Refuse exit with
  pending work, restore the enclosing policy and preserve failures, approvals
  and task evidence. Exiting a Skill does not complete the original task.
- Native internal execution must reject unsupported preview requests before
  approval or handler invocation; the external read-only preview path remains.
- The native control action schema explains that `enter_execution` exposes
  supervised write tools but performs no write and bypasses no approval. A
  phase change settles its original tool-call ID and replans without invoking
  a write handler. Test this at the provider request boundary; a successful
  decision with a reduced catalog is not full natural-task acceptance.
- When planning requests a registered write that is hidden by the planning
  exposure layer, return the typed `execution_mode_required` failure with the
  exact block, `requiredExposureLayer=execution`, and an `enter_execution`
  retry path. Do not expose its write schema, execute it, or widen permissions.
  Unknown and cross-block names remain `internal_tool_selection_invalid`.
- This rejected selection is a no-mutation outcome: preserve
  `mutationStarted=false` and `commitState=not_started`; canonical result
  projection must not invent recovery work. After entering execution, the
  explicit `project_environment/assets_packages` load may expose the approved
  write path. Route-only fixtures do not prove a natural-model repair or a
  successful repair-cost sample.
- Bounded source reads support inclusive line ranges and preserve line numbers
  during redaction. Report truncation and preserve existing access boundaries.

Regression: `tests/test_runtime_callable_catalog.py`,
`tests/test_native_tool_discovery_recovery.py`,
`tests/test_planner_structured_tool_evidence.py`,
`tests/test_agent_gateway_action_identity.py`, `tests/test_agent_task_loop.py`,
`tests/test_internal_installed_skill_discovery.py`,
`tests/test_internal_tool_blocks.py`, `tests/test_general_agent_tools_runtime.py`
and `tests/test_result_reader_completion_recovery.py`.

### Approval and question ownership

- Questions and approvals in chat require the exact non-empty session and
  normalized project identity. Other sessions' requests remain available only
  through explicit project confirmation UI. Closing that UI preserves requests.
- Revision/result delivery must not fall back to the active chat when ownership
  is unknown. Pending questions take priority over history; Stop requires the
  owning turn ID and cancelling is transitional until actual termination.
- Waiting for an answer replaces the ordinary composer with the complete
  question, wrapping options and an editable multiline custom response. Answered
  questions settle and leave the active dock. Reject oversize text explicitly
  instead of truncating it. Preserve options in the shared tool schema.
- Automatic approval uses one independent no-tools/no-history provider request
  with the configured request owner/key and bounded redacted evidence. Only an
  exact `allow_auto` permits execution; failure or uncertainty stays pending.
  Caller decisions are ignored. Preserve the owner-held execution identity until
  reviewer redaction, while keeping public approval responses summarized.
- Restricted, automatic and full-permission paths retain their distinct policy;
  no mode bypasses required write approval, readback or rollback boundaries.

Regression: `tests/test_runtime_scope.mjs`, `tests/test_approval_revision_ui.mjs`,
`tests/test_chat_question_dock.mjs`, `tests/test_question_continuation_ui.mjs`,
`tests/test_agent_question_service.py`,
`tests/test_agent_question_runtime_continuation.py`,
`tests/test_native_permission_modes.py`, `tests/test_approval_auto_review.py`
and `tests/test_approval_reviewer_provider_lifecycle.py`.

### Conversation display and recovery

- Show a spinner on the owning running sidebar row. Background completion shows
  one theme-colored unread dot. Viewing that chat clears it durably; later
  background completion may relight it. Failed, cancelled and waiting turns
  must not look successfully completed.
- Elapsed time advances locally every second and stops on settlement. Genuine
  commentary stays visible in order, interleaved with groups of consecutive
  tool calls. Use one group/count without extra indentation or duplicate layers.
  Completed history remains expandable; historical fallback steps do not replay.
- Render keys include runtime and chat ownership. Legacy duplicate item IDs
  cannot retain old approval DOM when switching chats. Ordinary reply IDs must
  not be mistaken for secret strings.
- A known IPC timeout may recover the same session/client turn using owned,
  abort-aware read-only polling after exactly one initial POST. Keep the complete
  result and pending approvals; never resend the write. This is not proof of
  restart durability.
- Enforce the chat size limit per store, preserve compare-and-swap conflicts and
  accept valid aggregate responses up to the explicit transport limit. Reject
  larger responses without truncating or deleting stored history.

Regression: `tests/test_sidebar_view.mjs`, `tests/test_chat_runtime_streaming.mjs`,
`tests/test_chat_render_keys.mjs`, `tests/test_chat_render_regression.mjs`,
`tests/test_chat_timeline_ux_ui.mjs`, `tests/test_historical_step_projection.mjs`,
`tests/test_chat_persistence_dedup.mjs`, `tests/test_chat_save_retry.mjs`,
`tests/test_agent_runtime_recovery.mjs`, `tests/test_agent_runtime_recovery_entry.mjs`
and the Rust `backend::app_api_response_tests` cases.

### Shared readiness and installed user tools

- Onboarding, settings, status and diagnostics consume the same readiness and
  verified provider configuration. A responding old assembly does not prove
  that the current on-disk plugin compiled successfully.
- Diagnose official incomplete or mixed-version Core files before proposing
  the existing bundled repair route. Preserve exact-project approval, backup
  and independent fresh compilation/readiness checks. Do not silently replace
  unrelated, unknown or third-party files.
- Official tool updates preserve user tool ownership and package compatibility
  boundaries. Compile the emitted installed sources and invoke a real user tool;
  descriptor validation must not require whole-assembly discovery.
- Preserve `VRCForgeToolResult.Waiting` as successful pending work, including
  `_mcp_status`, continuation interval and payload. Do not project it as completed.
  Failures and terminal outcomes must reach the existing result owner unchanged.

Regression: `tests/test_unity_status_service.py`,
`tests/test_doctor_readiness_report_service.py`,
`tests/test_provider_configuration_service.py`,
`tests/test_unity_mcp_tool_registry_runtime.py`,
`tests/test_user_tool_commands_runtime.py`, `tests/test_user_unity_tool_gateway.py`,
`tests/test_user_unity_tool_service.py` and `tests/test_skill_packages.py`.

### Build-time manifest encoding

- Read bundled package manifests as UTF-8 explicitly. Windows PowerShell must
  parse non-ASCII package names from UTF-8 files without a byte-order mark,
  independent of the machine's legacy code page.
- Exercise the manifest-reading statement used by the packaging script;
  parsing the same fixture through Python alone does not cover this boundary.

Regression: `tests/test_bundled_skill_delivery.py`.

### Reply language

- Native and JSON planning use one concise reply-language rule: follow the
  user's language established in the conversation; an explicit request for
  another language takes precedence. A brief language switch does not require
  an immediate reply-language switch and is not itself a regression.
- Tool results and internal runtime feedback do not set the reply language.
  Preserve the original conversation and tool roles; do not translate the
  transcript or use interface locale to select the model's response language.
- Request-contract tests prove the rule reaches both planner paths. Model
  behavior needs separate real-provider samples: mixed-language tool results,
  natural language switching and an explicit language override. Such samples
  do not substitute for App acceptance or guarantee model compliance.

Regression: `tests/test_runtime_planner_service.py`.

### MCP current-context age

- Prompt admission rejects identity/captured context at the 300-second boundary;
  this does not expire historical resources/read or revoke execution permissions.
- Explicit recapture can renew live state by publishing a new revision. Historical
  operation/checkpoint evidence retries remain idempotent, and catalogue listing
  cannot renew an identity via only-if-absent publication.
- Verify the real Prompt/router/registry path rejects expired references and
  accepts newly captured context while old resource content remains readable.

Regression: `tests/test_mcp_resource_context_expiry.py`.

### Resource session isolation and deliberate sharing

- Interleaved sessions must not overwrite each other's current identity,
  discover each other's private current resources, or reuse each other's cursor.
- Explicit authenticated history reads remain available across sessions and
  cannot promote the shared resource into current Prompt context.
- Server-issued HTTP contexts, automatic STDIO negotiation and accepted runtime
  sessions must use the same registry boundary. Thread hops inherit the context;
  nested calls and exceptions restore it. Unscoped requests have no shared current
  identity. Catalogs and projectless General capability remain available.
- Unknown, ended and credential-mismatched sessions fail without replaying calls.
  Session lifetime and current-context TTL do not expire historical evidence.
- Runtime-state paging must generate and validate its page URIs in the same
  context as publication, with repeat reads and repeated projection preserved.
- After a confirmed unknown/expired-session rejection, clear only the stale
  bridge session. Do not replay the rejected tool; the next independent request
  may negotiate a new session. Ordinary tool-not-found errors must not do this.

Regression: `tests/test_mcp_resource_session_context.py`,
`tests/test_gateway_resource_session_context.py`, `tests/test_mcp_request_sessions.py`.

### Manual-adjustment runtime handoff

- A verified committed prepare receipt must stop planning at
  `await_user_instruction` while retaining the successful prepare outcome.
- Late continuations cannot bypass the barrier with another handoff's
  finalize/abort receipt, including after the original handoff was closed.
- Failure to stop owned background work must block manual adjustment and
  must not report that the user can safely start dragging. Retry the original
  owner, then obtain a fresh verified preview before planning can resume.
- Exercise the production approval-resume entry point as well as direct
  runtime tool completion. Scripted receipts and cancellation requests do not
  prove live Unity readback or actual background-task termination.

- The managed execution envelope's nested `payload` must preserve the verified
  handoff receipt through approval resume. An outer failure or nonzero process
  exit must never expose a nested success as a committed handoff.

Regression: `tests/test_user_adjustment_runtime_boundary.py`,
`tests/test_user_adjustment_transport_receipt.py`.

### User-tool authoring descriptor parity

- Update compatibility must retain the existing availability decision and preserve
  user package bytes. Classify baseline drift from typed hash evidence and Core
  conflicts from fixed reason codes, never from translated error text.
- Compatibility does not establish repair effectiveness or upstream absorption.
  Absorption requires the original failure regression to pass against the exact
  official build with the local repair disabled; version changes alone are not proof.
- Reopening the same package store under a newer App version must retain package
  identity and enabled state. Source integration coverage is not installer/Core
  import acceptance.
- A compiled Core installation stamp must not override the package store's
  disabled state. A disabled package stays unavailable and dispatches no Core
  invocation; re-enabling must restore the ordinary valid package path.
- The effective App-facing catalog must expose one consistent state: a package
  with a disabled store entry reports `enabled=false` and `available=false`,
  and each child tool reports `available=false` with the structured
  `reason="disabled"`. Preserve the Core catalog's original state only in
  explicit `coreEnabled` and `coreReason` fields; do not leave the Core
  `enabled=true` or child `reason="ready"` values in the effective fields.
- A typed package-store disablement must preserve the structured source field
  `disabledBy="vrcforge_package_store"` through native and legacy observation
  projections. Core-only validation failures, missing entries, integrity or
  signature failures, and other generic errors must not claim App package-store
  disablement or synthesize `disabledBy`.
- A missing package-store entry remains a generic security failure and must
  not be inferred as `disabled`. A real disabled-store write must prevent
  dispatch, while re-enabling must restore the ordinary available/ready path.
- Validate the disabled-to-enabled round trip against the deployed Gateway;
  bind the live result to its source and deployed bytecode hashes.

Regression: `tests/test_user_tool_repair_compatibility.py`,
`tests/test_user_unity_tool_gateway.py`.

### Repair cost evidence

- Bind model usage, elapsed time, and terminal reason to the same runtime task.
  Approval and background Shell continuations may report cumulative task usage;
  do not sum those inherited totals with their original provider receipts.
  Deduplicate copied action history by action ID. Missing usage stays unknown
  rather than becoming zero.
- Count a successful repair only after the requested repair and independent
  readback succeed. A bounded stop, denied operation, manual restoration, or a
  later unrelated successful turn is not a successful repair cost sample.

### Complete model-facing tool descriptions

- Treat missing or truncated callable-tool instructions as a release blocker.
- Preserve the complete registered usage description in both native function
  schemas and legacy planner prompts, including positive conditions, exclusions
  and negative examples. Do not enforce character caps on these instructions.
- Control context growth through the existing tool selection/loading boundary;
  never remove repair conditions or permission limits from a loaded tool.
- Verify the descriptions actually received at the model request boundary, with
  long tails and the real Core installation contract, not just section labels.
- Preserve every input-schema description, including root, parameter, nested
  array, named-definition and composition-branch instructions. Do not cap either
  their length or the number of descriptions retained.

Regression: `tests/test_planner_tool_description_integrity.py`,
`tests/test_planner_schema_description_integrity.py`,
`tests/test_tool_usage_contract.py`, `tests/test_runtime_planner_service.py`.

### Tool discovery and continuation names

- Internal Agent registry names must match the existing planner catalog's
  callable names. External Gateway/MCP registry names remain unchanged.
- Runtime-owned project and exposure scope override model-supplied registry
  parameters. Registry entries, counts, categories and digest describe the
  same filtered view; tools outside the active profile are not advertised.
- Initial retained-result hints and subsequent page hints use the advertised
  result-reader name in native and legacy observations. Projection leaves the
  original receipt, arguments, result reference and isolation intact.
- Acceptance drivers must wait for owned background work before declaring a
  missing approval. A driver cancellation is not proof of model incapability.
- Continuation probes must send the client's durable visible chat history, as
  `buildChatHistory()` and `sendAgentMessage()` do. A session ID or the new
  process's session snapshot alone does not reconstruct that history after a
  backend restart. Compare the probe payload with the actual frontend history
  projection; reject a context-free continuation before making a model call.
  Such a malformed probe is not evidence of a product or model failure.

Regression: `tests/test_runtime_registry_profile_binding.py`,
`tests/test_runtime_planner_dashboard_wiring.py`,
`tests/test_result_reader_planner_boundary.py`,
`tests/test_agent_tool_result_reader.py`.

- Compile the documented C# example and execute its descriptor-generation
  snippet against the actual tool registry. Compare the complete generated
  descriptor with the documented JSON, including descriptions and required
  parameter order; do not weaken runtime validation to accept a stale example.
- Generate handler metadata and input schema through the existing registry
  instead of maintaining a second hand-written definition.
- Increment the bundled guide version when changing delivered content and
  verify an installed older guide upgrades through the normal import path.

Regression: `tests/test_bundled_skill_delivery.py`.

### Shared execution and tuning policies

- Gateway and Shell must reference the same execution-mode normalizer;
  preserve existing aliases, defaults and unknown-value behavior.
- Dashboard tuning helpers must reference the tuning store's normalization
  and retention rules. Preserve locked-item field precedence, avatar grouping,
  retention bounds and existing Shader-preset behavior.
- Shared presentation formatting must retain null, string, structured and
  unserializable-value behavior without becoming a runtime policy source.

Regression: `tests/test_agent_shell_service.py`,
`tests/test_avatar_tuning_state_service.py` and existing skill/sub-agent UI checks.

### Masked texture edits and precise importer checkpoints

- Reject colored or incorrectly sized PNG masks. Preview must not write, and a
  changed mask must invalidate approval. Preserve protected pixels, source alpha,
  source bytes, and existing targets; verify saved output independently.
- Texture importer checkpoint scope includes the exact texture and its meta.
  Restore those bytes without reverting unrelated assets or changing user Git
  state. Keep broader checkpoint scope where safe output cleanup is not bound.

Regression: `tests/test_texture_patch_mask_runtime.py`,
`tests/test_texture_patch_cleanup_runtime.py`,
`tests/test_checkpoint_archive_file_dates.py`,
`tests/test_checkpoint_git_user_state.py`.

### Reviewed support feedback and complete export publication

- Typed desktop IPC preserves already-redacted review text without weakening the
  general response sanitizer. Omitted export options serialize to false/200,
  never null for strict backend bool/int fields.
- Reading a bundle never checks the user's review confirmation. Editing input
  or refreshing a bundle invalidates the draft and any older pending response.
- Mark excerpts truncated at both input-byte and displayed-character limits.
- Publish a support ZIP only after it closes successfully; concurrent previews
  cannot see partial ZIPs. Failed export removes only its owned temporary file.
- Opening the feedback page must not submit text or attach a bundle.

Regression: `tests/test_support_feedback.py`, `tests/test_diagnostic_logging.py`,
`tests/test_settings_diagnostics_ui.mjs`, and Rust support projection/export body
tests. Desktop acceptance must exercise the actual export button and typed IPC.


## Normal task execution exposure

- A normal App task exposes execution tools on its first provider request without a model-generated phase transition. Tool visibility does not authorize dispatch: supervised writes still produce pending approvals and do not invoke Unity before approval.
- Continuations retain their recorded exposure layer. Explicit planning-layer tests must opt into that layer, not rely on the normal chat default.
- For cost measurement across approval continuations, use the final exact provider-backed task total once when available; excluding all continuation totals undercounts real calls. Never add cumulative totals to their predecessors.

Regression: `tests/test_agent_loop_p0.py::AgentLoopP0Tests::test_normal_app_turn_starts_execution_but_write_waits_for_real_approval`, `tests/test_native_runtime_gateway.py`.


## Explicit user-selected Plan mode

- Plan is off by default and independent from approval policy. Only explicit selection locks a task to read-only planning; the model cannot leave that mode itself.
- Preserve the selected mode through questions, queued input and retries. Different-mode queued input must not become a steer for the current task.
- Verify no write handler or Shell/delegated execution runs in Plan even under full permissions. Normal execution must still use its original approval rules.
- Check the plus-menu toggle, visible exit badge and per-chat isolation in the actual App separately from component-handler and transport unit tests.

Regression: `tests/test_explicit_plan_mode.py`, `tests/test_explicit_plan_mode_ui.mjs`, Rust `agent_message_transport_tests`.
# Explicit Plan queued replay

- Tool discovery observations must preserve complete metadata and description tails for the selected navigation level in both native provider messages and legacy prompts. Default canonical discovery returns roots only; category expansion returns direct children without the global tree. Every tool remains reachable, with complete definitions after loading. Unknown custom directories retain their metadata. Keep secret redaction and private-schema exclusion. Regression: `tests/test_tool_directory_observation_integrity.py`.

- Native runtime control rejections (including invalid correction arguments) retain kind=control. A subsequent valid final response may recover the rejected control proposal; real tool failures and task requirements must still block unsupported completion. Regression: `test_plan_can_finish_after_rejected_control_correction` plus native gateway failure-retention cases.

- A persisted queued turn owns its selected Plan mode. After process restart, claiming and replaying it must retain that mode even if the replay request omits the field or carries the current composer's different mode.
- Regression: `tests/test_explicit_plan_mode.py::test_queued_replay_uses_persisted_plan_mode` covers saved Plan with absent/false replay input and saved execution with true replay input; the durable queue is reopened before dispatch.

## Unified progressive tool loading

- A single model-visible loader browses roots when block is omitted, expands categories without loading, and loads exact leaf tools. Do not reject a valid category or expose competing list/load operations to the model. Keep existing list integrations callable.
- Optional tool selection requires a leaf; invalid and cross-block names remain rejected and Plan/write approval boundaries remain intact.
- Native provider system and the complete tools array stay fixed across interleaved block loads. Loaded definitions remain in their original loader result in history, and the fixed dispatcher executes them. Preserve full definitions and saved ordering across approval resume/compaction; revoked tools remain unavailable.
- Regression: `tests/test_internal_tool_blocks.py`, `tests/test_native_progressive_directory.py`, `tests/test_native_tool_discovery_recovery.py`, `tests/test_native_session_tool_order.py`. Provider acceptance must be reported separately from offline gateway tests.

- Exposed diagnostic tool descriptions and legacy navigation prompts must refer to the same visible loader; never direct the model to a hidden discovery entry. Regression: `test_diagnostic_description_does_not_route_to_hidden_discovery_entry`, `test_legacy_navigation_uses_same_loader_for_browsing_and_loading`.

- Bundled Skill allowed-tool metadata must not reintroduce hidden internal discovery names through model-visible Skill reads or activation. Keep the unified loader allowed and preserve existing direct compatibility handlers. Regression: `test_bundled_guide_does_not_reintroduce_hidden_directory_entry`, `test_previous_directory_handler_remains_directly_callable`.

### Retained text continuation

- Selecting an exact non-identity text field must return all sanitized text that fits the page, not another shortened preview.
- Oversized text must expose a progressing `textOffset` continuation and reconstruct the complete sanitized value within the existing serialized page budget.
- Collection `offset` and `limit` retain item semantics. Existing collection continuations remain valid.
- Redact before chunking; preserve ownership, private-field and exact-identity checks, including escaped JSON pointer segments.

Regression: `tests/test_result_reader_long_scalar.py`, `tests/test_agent_tool_result_reader.py`, `tests/test_result_reader_completion_recovery.py`.

### Native multi-call ownership

- Multiple reads from one assistant response must receive separate results by call ID without an extra model request between dispatches. Execution remains serial through the existing dispatcher.
- A response may propose at most one write; approval and post-approval authority checks remain unchanged. Resume must not replay completed reads or attach the write result to another call.
- Loading a tool cannot authorize a name that was absent from the original response's advertised set. Recheck current permissions before each queued dispatch.
- Stop, terminal scope denial and user steering must settle remaining proposals without executing them. Questions suspend the queue until answered; cancelling a question also closes remaining proposals.
- Queued dispatch is not another model turn. Clearing a queue must not bypass an explicit model-turn budget.

Regression: `tests/test_native_multi_call_admission.py`.

### Complete planner tool contracts

- Native and legacy planner lanes must preserve every registered tool-description section and nested schema description, including description tails and fields beyond former count limits.
- Literal schema constraints remain unchanged; preserve existing private-schema exclusions without introducing description-length budgets.

Regression: `tests/test_planner_tool_description_integrity.py`, `tests/test_planner_schema_description_integrity.py`.


## Provider cache usage accounting

- Extract nested provider cache counters without overriding an explicit top-level zero. Missing counters must remain unknown.
- Preserve usage and cache coverage across approval continuations; partial or historical data must not yield a complete hit rate.
- Context Usage computes hit rate from cumulative cached input divided by cumulative input, independently of peak context occupancy. Invalid counts or incomplete request coverage show unknown/incomplete instead of a percentage.
- Regression checks: `tests/test_cache_usage_accounting.py` and `tests/test_context_cache_usage_ui.mjs`. Source checks do not replace live UI acceptance or prove cost savings.


## Host-bound completion evidence

- Successful model completion still requires an explicit satisfied claim and all existing host failure, pending, running and verification gates.
- The host binds the complete completed-action ledger. Model references may be omitted or a subset, but every supplied reference must identify a completed action; foreign or unexecuted references fail. The original model claim is not rewritten.
- Native final replies must not require an extra model request solely to copy all action IDs. Preserve exact-set behavior for non-model completion paths.
- Regression checks: `tests/test_agent_task_loop.py` and `tests/test_native_runtime_gateway.py`, including invalid-reference recovery and approval continuations.


## TODO tool input contracts

- Replace/create/update/delete advertise actual title, list and identity inputs through the shared schema in both project profiles and planning/execution layers. Creation does not promise a caller-selected ID.
- Missing/non-array replacement arguments or a nonempty list with no valid titled items fail before any progress event is written. Explicit empty lists remain supported; preserve existing aliases, precedence and mixed-entry filtering.
- Invalid replacement requests leave existing progress unchanged. Updating or deleting items retains existing session/project scope enforcement.
- Regression checks: `tests/test_progress_tool_contract.py` and existing progress HTTP tests in `tests/test_dashboard_server.py`. These checks establish contract correctness, not measured provider cost savings.


## Mode-specific runtime control descriptions

- Runtime control descriptions and action-schema explanations derive from the same allowed action enum. Explicit Plan describes reply/correct only; execution does not tell the model to enter execution again.
- Remove obsolete unconditional enter-execution instructions while retaining host permission and approval enforcement. Control tools never authorize Unity writes by themselves.
- Regression checks: `tests/test_native_control_description.py` and `tests/test_native_runtime_gateway.py`; native and explicit Plan behavior must retain existing authorization boundaries.


## Native multi-call instruction alignment

- Native guidance allows independent already-advertised reads together, at most one write per response, and runtime control actions alone. It must not retain contradictory one-tool-only guidance or promise parallel execution.
- Keep current admission/approval checks, serial dispatch, dependent-discovery boundaries, Stop and steering behavior unchanged.
- Regression checks: `tests/test_native_batch_instruction.py`, `tests/test_native_multi_call_admission.py`, and `tests/test_native_control_description.py`. Prompt correctness does not establish model selection or cost improvement without a separate live sample.


### Retained-result continuation tool names

- Initial continuation and subsequent result pages must name a tool advertised by the planner catalog, in native and legacy observations and with or without project context.
- Project only the continuation hint; retain the internal reader name, result reference, arguments, content and ownership checks unchanged. Do not mutate stored results.
- Regression coverage: `tests/test_result_reader_planner_boundary.py`, alongside the existing result-reader and tool-result contract tests.


### Disabled user-tool package metadata

- A package disabled in the installed store must report effective enabled=false and unavailable tools with a disabled reason; retain original Core state separately for diagnosis.
- Missing packages must not be mislabeled as disabled. Reenabling restores ordinary metadata without changing project files or source catalog objects.
- Native and legacy model observations must preserve this distinction. Invocation remains blocked before dispatch. Coverage: `tests/test_package_disabled_contract.py` and existing user-tool gateway/service tests.


### Capability-package discovery semantics

- Root, child and leaf discovery must describe installed VRCForge .vsk package state and enablement while retaining Unity assets, prefabs, packages, dependencies/imports and asset/package inventory semantics.
- Descriptions come from canonical routing metadata; root/child views must not include a global tool inventory. Full leaf tool metadata and approval boundaries remain unchanged.
- Coverage: `tests/test_native_progressive_directory.py` and `tests/test_internal_tool_blocks.py`. Model selection and cost improvements require separate live evidence.


### Background command failure recovery

- A normally finished background command with a nonzero integer exit code must return its failed result to the planner exactly once, preserving the original call identity without rerunning the process.
- Keep cancellation, timeout, termination failure, unknown process states and invalid exit codes terminal; a host Stop must prevent model restart. Successful completion and approval rejection behavior remain unchanged.
- Coverage: `tests/test_native_shell_failure_recovery.py`, existing native async continuation tests and task-loop approval tests. Live repair completion and cost savings require separate evidence.


### Installed package observation

- Installed package listings must prioritize installed state and governance in model observations; registry and audit history stay available in the retained result reader.
- Keep the existing untrusted-data envelope, redaction and observation budget. Large installed collections must retain a targeted continuation; error-only responses retain their diagnostics.
- Verify the raw result is unchanged, audit remains readable in its owning context, and both canonical and native aliases preserve the contract.


### User-defined tool directory routing

- Diagnostic root and compile/log leaf descriptions must advertise their existing user-defined Unity tool discovery and invocation capabilities.
- Directory wording must distinguish read-only planning from approved invocation; tool membership, write gating and approval enforcement stay unchanged.
- Regression: `tests/test_internal_tool_blocks_user_unity_routing.py`. Natural task completion and provider wait behavior require separate live evidence.


### Complete tool directory ownership

- Directory ownership must match the runtime catalog. Core-resident tools remain available without being advertised under a conflicting lazy leaf.
- Build/upload, scene lifecycle, play mode, performance and behavior operations must route by their actual operation contract rather than incidental name substrings.
- Root and leaf descriptions must both expose installed Skills, explicitly activated desktop actions, attachment inspection/import and execution-target/property reads; approval and activation boundaries remain authoritative.
- Compare all visible registered tools against directory reachability independently for planning/execution and project/non-project profiles. Do not derive the expected inventory from the tree under test.
- Coverage: `tests/test_tool_directory_routing_contract.py`. Running-backend directory/load evidence and repeated natural task acceptance are separate requirements.


### Pending approval after a recoverable failure

- A later genuine pending approval must remain the current user-action boundary even when an older failed tool receipt remains unresolved. Match the pending payload, approval identity and target; ordinary error text must not manufacture an approval.
- Retain earlier failed evidence, completion requirements, explicit denial and cancellation precedence. Waiting for approval is never proof of successful completion.
- Approval continuation must settle the intended action once without replaying its write. Regression: `tests/test_pending_approval_completion_boundary.py` and existing task-loop approval/completion tests.


### Pending approval preserves summarized loop metadata

- Apply the current approval boundary to the final summarized plan; retain multi-step metadata and cleared execution flags. Do not replace the whole plan with an intermediate step.
- Keep the existing multi-step approval regression and pending-after-failure regression passing together: `tests/test_agent_loop_p0.py` and `tests/test_pending_approval_completion_boundary.py`.


### Resident Skill exit and stable runtime context

- Keep the exit tool resident with unchanged complete schema, description and definition order. An inactive exit is a successful no-op, never task-completion evidence; active exits still require the exact name.
- Append host-owned Skill state after existing runtime context; preserve stable instructions, tools and history prefixes. Other unresolved failures must still block completion.
- Regression: `tests/test_exit_skill_resident_noop.py` and the existing task-loop Skill checks.


### Bounded retained-result page capacity

- Keep retained-result pages bounded while allowing medium lists to fit in one page. Preserve exact pointers, source constraints, redaction and raw retained data.
- Larger pages must survive planner observation intact; long-text fixtures must exceed the configured page size so continuation validation remains exercised.
- Regression: `tests/test_agent_tool_result_reader.py`, `tests/test_result_reader_long_scalar.py`, `tests/test_result_reader_planner_boundary.py`, `tests/test_result_reader_completion_recovery.py`.


### Selected-category tool-name discovery

- Root navigation must not emit a global tool-name directory. Expanding a selected category must retain the exact names of tools in its immediate leaf groups so callers can request a subset without first loading every schema.
- Browsing never loads tools; preserve whole-group and exact-subset loading, complete definitions and append-only exposure.
- Regression: `tests/test_tool_leaf_name_discovery.py`, `tests/test_internal_tool_blocks.py`, `tests/test_exact_tool_loading_review.py`.
