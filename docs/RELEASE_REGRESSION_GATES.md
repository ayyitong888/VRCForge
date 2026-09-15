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
- The published `*_Hotfix1.exe` assets match the tested bytes. Their hashes,
  source commits and case exit codes are in
  [installer-hotfix1-manifest.json](https://github.com/ayyitong888/VRCForge/releases/download/v1.8.0/installer-hotfix1-manifest.json).
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
