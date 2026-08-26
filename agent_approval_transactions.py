from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from contextlib import AbstractContextManager
from typing import Any, Callable, Mapping

from agent_command_safety import is_path_within, looks_like_absolute_path, normalize_filesystem_path
from agent_task_loop import TASK_APPROVAL_CONTEXT_SCHEMA, approval_completion, approval_task_context
from agent_tool_result_contract import normalize_agent_tool_result
from external_tool_result_contract import (
    build_external_tool_error,
    external_exception_details,
    external_exception_raw_result,
    external_write_failure_view,
)
from prepared_unity_execution import (
    PREPARED_EVIDENCE_KEY,
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
)
from agent_gateway import (
    APPLY_RECOVERY_ACTIVE_STATUSES,
    APPLY_RECOVERY_EXEMPT_WRITE_TARGETS,
    APPLY_RECOVERY_SCHEMA,
    AUTO_APPROVAL_MANUAL_WRITE_TOKENS,
    AgentGatewayConfig,
    AgentGatewayError,
    AgentWriteHandler,
    ApprovedUnityExecutionPlanBuilder,
    CHECKPOINT_RECORD_SCHEMA,
    CheckpointPrepareHandler,
    CompletionVerificationFinalizeHandler,
    CompletionVerificationPrepareHandler,
    EXPOSURE_LAYER_EXECUTION,
    LOCAL_STATE_CHECKPOINT_SCOPE,
    LOCAL_STATE_CHECKPOINT_TARGETS,
    ManualApprovalResolver,
    PROJECT_CHAT_CHECKPOINT_MEMBER,
    PROJECT_CHAT_CHECKPOINT_TARGET,
    ROLLBACK_COVERAGE_AUDIT_SCHEMA,
    ROLLBACK_POLICY_SCHEMA,
    RiskLevelResolver,
    ToolHandler,
    UNITY_PROJECT_CHECKPOINT_SCOPE,
    UserConstraintsSnapshot,
    WRAPPER_ONLY_WRITE_TARGETS,
    WRITE_PATH_KEY_MARKERS,
    WriteRequestPreparer,
    atomic_write_json,
    bind_approved_unity_execution,
    capture_unity_mcp_core_call_audits,
    create_approved_unity_execution_plan,
    ensure_dict,
    ensure_string_list,
    external_mcp_typed_wrapper_allowed,
    extract_approval_id,
    extract_project_root,
    freeze_approved_unity_execution_plan,
    iter_param_leaf_values,
    normalize_execution_mode,
    normalize_exposure_layer,
    normalize_risk_level,
    redact_sensitive,
    stable_hash,
    summarize_params,
    summarize_text,
    tool_usage_description,
    utc_now_iso,
    validate_frozen_approved_unity_execution_plan,
)


PENDING_APPROVAL_SNAPSHOT_SCHEMA = "vrcforge.pending-approvals.v1"
PENDING_APPROVAL_SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024
PENDING_APPROVAL_SNAPSHOT_MAX_ITEMS = 128
CHECKPOINT_RESTORE_MANUAL_APPROVAL_REASON = (
    "Checkpoint restore always requires manual user approval because it can remove "
    "or replace project files."
)
AVATAR_UPLOAD_MANUAL_APPROVAL_REASON = (
    "VRChat avatar upload always requires one exact user confirmation because remote metadata, "
    "visibility, thumbnail, or bundle changes cannot be undone by a local checkpoint."
)
ROLLBACK_MANUAL_APPROVAL_TOOLS = frozenset(
    {
        "vrcforge_restore_checkpoint",
        "vrcforge_rollback_parameters",
        "vrcforge_rollback_project_lifecycle",
        "vrcforge_rollback_project_catalog_registration",
    }
)
ROLLBACK_MANUAL_APPROVAL_REASON = "Rollback operations always require explicit manual user confirmation."
def _write_failure_facts(
    failure_result: Any,
    *,
    failure_layer: str,
    verification_baseline: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    recovery: Mapping[str, Any],
    no_write_conflict: bool,
) -> dict[str, Any]:
    """Compatibility projection from the one external error constructor."""

    result = dict(failure_result) if isinstance(failure_result, Mapping) else {}
    checkpoint_failed = checkpoint.get("ok") is False
    clean_pre_write_failure = bool(
        checkpoint_failed
        or no_write_conflict
        or (not checkpoint and failure_layer == "completion_verification_baseline")
    )
    after_console = result.get("consoleVerification")
    after_console = (
        redact_sensitive(dict(after_console)) if isinstance(after_console, Mapping) else {}
    )
    constructor_args: dict[str, Any] = {
        "failure_layer": str(failure_layer or "approved_write")[:80],
        "operation_kind": "write",
        "raw_result": result,
        "checkpoint_id": str(checkpoint.get("id") or "")[:180],
        "recovery_id": str(recovery.get("id") or "")[:180],
        "console_before": redact_sensitive(dict(verification_baseline)),
        "console_after": after_console,
    }
    if clean_pre_write_failure:
        constructor_args.update(
            {
                "tool_routing_started": False,
                "mutation_started": False,
                "committed": False,
                "commit_state": "not_started",
                "checkpoint_recovery_required": False,
                "temporary_cleanup_required": False,
            }
        )
    elif "checkpointRecoveryRequired" not in result:
        constructor_args["checkpoint_recovery_required"] = bool(recovery)
    error_object = build_external_tool_error(**constructor_args)
    return external_write_failure_view(error_object)


def _confirmed_no_write_failure(facts: Mapping[str, Any]) -> bool:
    return bool(
        facts.get("mutationStarted") is False
        and facts.get("committed") is False
        and facts.get("commitState") == "not_started"
        and facts.get("checkpointRecoveryRequired") is False
    )


def _temporary_cleanup_only_failure(facts: Mapping[str, Any]) -> bool:
    return bool(
        facts.get("committed") is True
        and facts.get("commitState") == "complete"
        and facts.get("checkpointRecoveryRequired") is False
        and facts.get("temporaryCleanupRequired") is True
    )


@dataclass(frozen=True, slots=True)
class ApprovalGoalPorts:
    """Goal resolution capabilities used by approval transactions."""

    deny_approval: Callable[..., dict[str, Any] | None]
    attach_terminal_resolution: Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]
    delivery_for_approval: Callable[[str], dict[str, Any] | None]
    reconcile_missing_approvals: Callable[[set[str]], list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ApprovalCheckpointRecoveryPorts:
    """Checkpoint capabilities consumed by approved-write transactions."""

    active_apply_recoveries: Callable[[], list[dict[str, Any]]]
    append_apply_recovery_entry: Callable[[dict[str, Any]], dict[str, Any]]
    append_checkpoint: Callable[[dict[str, Any]], None]
    build_checkpoint_rollback_coverage_audit: Callable[..., dict[str, Any]]
    classify_apply_recovery_incident: Callable[[str, str], str]
    create_archive_checkpoint: Callable[[Path, dict[str, Any]], dict[str, Any]]
    create_local_state_checkpoint: Callable[[dict[str, Any]], dict[str, Any]]
    create_project_chat_checkpoint: Callable[[Path, dict[str, Any]], dict[str, Any]]
    resolve_checkpoint_project_root: Callable[[dict[str, Any]], Path | None]
    prune_checkpoint_archives: Callable[..., dict[str, Any]]
    project_chat_checkpoint_lock: Callable[[], AbstractContextManager[object]]


@dataclass(frozen=True, slots=True)
class ApprovalSkillsPort:
    write_lock: AbstractContextManager[object]


@dataclass(frozen=True, slots=True)
class ApprovalTransactionState:
    shared_state_lock: AbstractContextManager[object]
    approvals: dict[str, dict[str, Any]]
    write_handlers: dict[str, AgentWriteHandler]
    in_flight_apply_writes: dict[str, dict[str, Any]]
    background_project_read_leases: set[str]
    checkpoint_storage_lock: AbstractContextManager[object]
    skill_package_write_lock: AbstractContextManager[object]
    skill_package_write_lock_bound: bool


@dataclass(frozen=True, slots=True)
class ApprovalTransactionPorts:
    """Exact state, callbacks, and peer capabilities required by approval flow."""

    state: ApprovalTransactionState
    checkpoint: ApprovalCheckpointRecoveryPorts
    audit_log_path: Callable[[], Path]
    skills: ApprovalSkillsPort
    shell_manual_approval_reason: Callable[[dict[str, Any]], str]
    shell_execute_payload: Callable[[dict[str, Any]], dict[str, Any]]
    checkpoint_pathspecs: Callable[[Path, Path], list[str]]
    is_unity_project_root: Callable[[Path], bool]
    normalize_project_category_allow_rules: Callable[[Any], list[dict[str, str]]]
    run_git: Callable[..., dict[str, Any]]
    signal_background_activity: Callable[[str], None]
    tool_params_audit: Callable[[str, dict[str, Any]], dict[str, Any]]
    validated_memory_evidence_for_applied_write: Callable[..., dict[str, Any] | None]
    with_user_constraints: Callable[..., Any]
    write_handler_allows_future_category: Callable[[AgentWriteHandler, dict[str, Any]], bool]
    write_handler_visible: Callable[..., bool]
    append_audit: Callable[[dict[str, Any]], None]
    authenticate: Callable[..., AgentGatewayConfig]
    call_tool: Callable[..., dict[str, Any]]
    ensure_config: Callable[[], AgentGatewayConfig]
    read_user_constraints: Callable[[], UserConstraintsSnapshot]
    roslyn_available: Callable[[AgentGatewayConfig | None], bool]
    save_config: Callable[[AgentGatewayConfig], None]


class AgentApprovalTransactionService:
    """Own supervised write requests, approvals, apply, and recovery handoff.

    The gateway supplies its existing registries, state, locks, and narrow peer
    capabilities through typed ports. This owner keeps only the three app-level
    approval hooks that belong to its lifecycle. It creates no process, task,
    lock, file handle, authorization identity, or communication endpoint.
    """

    __slots__ = (
        "_apply_lifecycle_observer",
        "_checkpoint_prepare_handler",
        "_goal",
        "_ports",
        "_runtime_run_append",
        "_auto_approval_reviewer",
        "_scoped_approval_reviewer",
        "_project_write_locks",
        "_project_write_locks_guard",
    )

    def __init__(
        self,
        ports: ApprovalTransactionPorts,
        goal: ApprovalGoalPorts,
        *,
        runtime_run_append: Callable[[dict[str, Any]], None],
    ) -> None:
        self._ports = ports
        self._goal = goal
        self._runtime_run_append = runtime_run_append
        self._apply_lifecycle_observer: Callable[[str, dict[str, Any]], Any] | None = None
        self._checkpoint_prepare_handler: Callable[[Path], dict[str, Any]] | None = None
        self._auto_approval_reviewer: Callable[[dict[str, Any]], str] | None = None
        self._scoped_approval_reviewer: Callable[[dict[str, Any]], str] | None = None
        # Project-scoped, non-blocking gates.  The gate is process-local and
        # shared by internal approvals and external MCP writes through this
        # service; unknown project scope deliberately uses the global key.
        self._project_write_locks: dict[str, threading.Lock] = {}
        self._project_write_locks_guard = threading.Lock()
        self._restore_pending_approvals()

    @staticmethod
    def _project_lock_key(project_root: Any) -> str:
        raw = str(project_root or "").strip()
        if not raw:
            return "__global__"
        try:
            return normalize_filesystem_path(str(Path(raw).resolve(strict=False))).casefold()
        except (OSError, RuntimeError, TypeError, ValueError):
            return "__global__"

    def _try_acquire_project_write(self, project_root: Any) -> tuple[str, bool]:
        key = self._project_lock_key(project_root)
        with self._project_write_locks_guard:
            lock = self._project_write_locks.setdefault(key, threading.Lock())
        return key, lock.acquire(blocking=False)

    def _release_project_write(self, key: str) -> None:
        with self._project_write_locks_guard:
            lock = self._project_write_locks.get(key)
        if lock is not None and lock.locked():
            lock.release()

    @staticmethod
    def _entry_project_key(entry: Mapping[str, Any]) -> str:
        return AgentApprovalTransactionService._project_lock_key(
            entry.get("projectRoot") or entry.get("projectPath")
        )

    def _project_has_in_flight_write(self, project_root: Any) -> bool:
        target = self._project_lock_key(project_root)
        return any(
            self._entry_project_key(entry) in {target, "__global__"}
            or target == "__global__"
            for entry in self._ports.state.in_flight_apply_writes.values()
            if isinstance(entry, Mapping)
        )

    def _project_has_background_read(self, project_root: Any) -> bool:
        target = self._project_lock_key(project_root)
        for raw in self._ports.state.background_project_read_leases:
            key = str(raw).split("\x00", 1)[0] if "\x00" in str(raw) else "__global__"
            if key in {target, "__global__"} or target == "__global__":
                return True
        return False

    def _pending_approval_snapshot_path(self) -> Path:
        """Return the host-private, pending-only approval proposal snapshot.

        The file lives beside the existing approval audit under the backend's
        user-data directory.  It is owned by the backend process, contains no
        authorization token, and grants no execution capability: the existing
        authenticated approval endpoint and exact approval transaction remain
        the only way to move a restored proposal out of ``pending``.
        """

        return self._ports.audit_log_path().with_name("pending-approvals.json")

    def _restore_pending_approvals(self) -> None:
        path = self._pending_approval_snapshot_path()
        if not path.exists():
            return
        try:
            if path.stat().st_size > PENDING_APPROVAL_SNAPSHOT_MAX_BYTES:
                raise ValueError("pending approval snapshot exceeds the size limit")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("pending approval snapshot must be an object")
            if payload.get("schema") != PENDING_APPROVAL_SNAPSHOT_SCHEMA:
                raise ValueError("pending approval snapshot schema is invalid")
            stored = payload.get("approvals")
            if not isinstance(stored, list):
                raise ValueError("pending approval snapshot approvals must be a list")
            if len(stored) > PENDING_APPROVAL_SNAPSHOT_MAX_ITEMS:
                raise ValueError("pending approval snapshot has too many items")
            restored: dict[str, dict[str, Any]] = {}
            for raw in stored:
                if not isinstance(raw, dict):
                    raise ValueError("pending approval snapshot item must be an object")
                approval = dict(raw)
                approval_id = str(approval.get("id") or "").strip()
                target_tool = str(approval.get("targetTool") or "").strip()
                created_at = str(approval.get("createdAt") or "").strip()
                if (
                    not approval_id.startswith("appr_")
                    or len(approval_id) > 160
                    or not target_tool
                    or len(target_tool) > 160
                    or not created_at
                    or approval.get("status") != "pending"
                    or not isinstance(approval.get("arguments"), dict)
                    or approval_id in restored
                ):
                    raise ValueError("pending approval snapshot item is invalid")
                if any(
                    key in approval
                    for key in (
                        "approvedAt",
                        "appliedAt",
                        "applyingAt",
                        "failedAt",
                        "rejectedAt",
                    )
                ):
                    raise ValueError("pending approval snapshot contains a terminal field")
                approval.pop("expiresAt", None)
                restored[approval_id] = approval
            self._ports.state.approvals.update(restored)
            if restored:
                self._ports.append_audit(
                    {
                        "event": "pending_approvals_restored",
                        "approvalIds": sorted(restored),
                        "count": len(restored),
                    }
                )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            # Corrupt or oversized state never becomes an executable proposal.
            # Preserve the file for Doctor/user inspection and keep startup live.
            self._ports.append_audit(
                {
                    "event": "pending_approval_snapshot_invalid",
                    "error": summarize_text(str(exc), 300),
                }
            )

    def _persist_pending_approvals_locked(self) -> None:
        pending = [
            dict(item)
            for item in self._ports.state.approvals.values()
            if item.get("status") == "pending"
        ]
        pending.sort(key=lambda item: str(item.get("createdAt") or ""))
        if len(pending) > PENDING_APPROVAL_SNAPSHOT_MAX_ITEMS:
            raise ValueError("too many pending approvals to persist safely")
        snapshot = {
            "schema": PENDING_APPROVAL_SNAPSHOT_SCHEMA,
            "updatedAt": utc_now_iso(),
            "approvals": pending,
        }
        if (
            len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
            > PENDING_APPROVAL_SNAPSHOT_MAX_BYTES
        ):
            raise ValueError("pending approval snapshot exceeds the size limit")
        atomic_write_json(self._pending_approval_snapshot_path(), snapshot)

    @property
    def apply_lifecycle_observer(self) -> Callable[[str, dict[str, Any]], Any] | None:
        return self._apply_lifecycle_observer

    @apply_lifecycle_observer.setter
    def apply_lifecycle_observer(self, callback: Callable[[str, dict[str, Any]], Any] | None) -> None:
        self._apply_lifecycle_observer = callback

    @property
    def checkpoint_prepare_handler(self) -> Callable[[Path], dict[str, Any]] | None:
        return self._checkpoint_prepare_handler

    @checkpoint_prepare_handler.setter
    def checkpoint_prepare_handler(self, callback: Callable[[Path], dict[str, Any]] | None) -> None:
        self._checkpoint_prepare_handler = callback

    @property
    def scoped_approval_reviewer(self) -> Callable[[dict[str, Any]], str] | None:
        return self._scoped_approval_reviewer

    @scoped_approval_reviewer.setter
    def scoped_approval_reviewer(self, callback: Callable[[dict[str, Any]], str] | None) -> None:
        self._scoped_approval_reviewer = callback

    @property
    def auto_approval_reviewer(self) -> Callable[[dict[str, Any]], str] | None:
        return self._auto_approval_reviewer

    @auto_approval_reviewer.setter
    def auto_approval_reviewer(self, callback: Callable[[dict[str, Any]], str] | None) -> None:
        self._auto_approval_reviewer = callback

    def _observe_apply_lifecycle(
        self,
        stage: str,
        approval: dict[str, Any],
        *,
        checkpoint: dict[str, Any] | None = None,
        result: Any = None,
        arguments_digest: str = "",
    ) -> None:
        callback = self._apply_lifecycle_observer
        if callback is None:
            return
        payload = {
            "approval": dict(approval),
            "checkpoint": dict(checkpoint) if isinstance(checkpoint, dict) else None,
            "result": result,
        }
        if arguments_digest:
            payload["argumentsDigest"] = arguments_digest
        callback(stage, payload)

    def register_write_handler(
        self,
        name: str,
        description: str,
        risk_level: str,
        handler: ToolHandler,
        advanced: bool = False,
        risk_level_resolver: RiskLevelResolver | None = None,
        request_preparer: WriteRequestPreparer | None = None,
        manual_approval_resolver: ManualApprovalResolver | None = None,
        checkpoint_prepare_handler: CheckpointPrepareHandler | None = None,
        verification_profile: str = "",
        verification_prepare_handler: CompletionVerificationPrepareHandler | None = None,
        verification_finalize_handler: CompletionVerificationFinalizeHandler | None = None,
        requires_approved_execution_context: bool = False,
        approved_execution_plan_builder: ApprovedUnityExecutionPlanBuilder | None = None,
        approval_category: str = "",
        allow_future_category: bool = False,
        external_mcp_capability: str = "",
        pre_write_checkpoint_required: bool = True,
    ) -> None:
        bounded_verification_profile = str(verification_profile or "").strip()[:80]
        if bounded_verification_profile and (
            verification_prepare_handler is None or verification_finalize_handler is None
        ):
            raise ValueError("A declared write verification profile requires prepare and finalize handlers.")
        self._ports.state.write_handlers[name] = AgentWriteHandler(
            name=name,
            description=description,
            risk_level=risk_level,
            handler=handler,
            advanced=advanced,
            risk_level_resolver=risk_level_resolver,
            request_preparer=request_preparer,
            manual_approval_resolver=manual_approval_resolver,
            checkpoint_prepare_handler=checkpoint_prepare_handler,
            verification_profile=bounded_verification_profile,
            verification_prepare_handler=verification_prepare_handler,
            verification_finalize_handler=verification_finalize_handler,
            requires_approved_execution_context=requires_approved_execution_context,
            approved_execution_plan_builder=approved_execution_plan_builder,
            approval_category=str(approval_category or "").strip(),
            allow_future_category=bool(allow_future_category),
            external_mcp_capability=str(external_mcp_capability or "").strip(),
            pre_write_checkpoint_required=bool(pre_write_checkpoint_required),
        )

    def registered_write_target_names(self) -> set[str]:
        with self._ports.state.shared_state_lock:
            return set(self._ports.state.write_handlers)

    def authenticate_approval(
        self,
        headers: dict[str, str],
        query_params: dict[str, str],
        client_host: str | None,
    ) -> AgentGatewayConfig:
        config = self._ports.authenticate(headers, query_params, client_host, allow_disabled=False)
        supplied = (
            headers.get("x-vrcforge-approval-token")
            or headers.get("X-VRCForge-Approval-Token")
            or query_params.get("approval_token")
            or ""
        )
        if not supplied or not hmac.compare_digest(supplied, config.approval_token):
            raise AgentGatewayError("Approval token is missing or invalid.", status_code=401)
        return config

    def auto_approval_enabled(self, config: AgentGatewayConfig | None = None) -> bool:
        config = config or self._ports.ensure_config()
        return normalize_execution_mode(config.execution_mode) in {"auto", "roslyn_full_auto"}

    def permission_audit_context(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self._ports.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        full_permission = mode == "roslyn_full_auto"
        return {
            "permissionMode": mode,
            "fullPermission": full_permission,
            "permissionLabel": "full permission" if full_permission else ("auto approval" if mode == "auto" else "step approval"),
            "perActionApproval": mode == "approval",
            "autoApprove": mode in {"auto", "roslyn_full_auto"},
            "autoApproveDangerousRequiresApproval": mode == "auto",
        }

    def _auto_approval_block_reason(self, approval: dict[str, Any], config: AgentGatewayConfig | None = None) -> str:
        config = config or self._ports.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        explicit_reason = str(approval.get("explicitApprovalReason") or "").strip()
        if str(approval.get("targetTool") or "") == "vrcforge_restore_checkpoint":
            return explicit_reason or CHECKPOINT_RESTORE_MANUAL_APPROVAL_REASON
        if mode == "roslyn_full_auto":
            return ""
        if approval.get("requiresExplicitApproval"):
            return explicit_reason or "This approval always requires manual confirmation."
        if mode != "auto":
            return "Current permission mode does not auto-approve."
        if str(approval.get("targetTool") or "") == "vrcforge_shell_execute":
            arguments = ensure_dict(approval.get("arguments"))
            classification = ensure_dict(arguments.get("classification_snapshot") or approval.get("preview"))
            return self._ports.shell_manual_approval_reason(classification)
        return ""

    def permission_state(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self._ports.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        permission_context = self.permission_audit_context(config)
        return {
            "executionMode": mode,
            "perActionApproval": permission_context["perActionApproval"],
            "autoApprove": permission_context["autoApprove"],
            "autoApproveDangerousRequiresApproval": permission_context["autoApproveDangerousRequiresApproval"],
            "roslynFullAuto": mode == "roslyn_full_auto",
            "fullPermission": permission_context["fullPermission"],
            "permissionLabel": permission_context["permissionLabel"],
            "roslynRiskAcknowledged": bool(config.roslyn_risk_acknowledged),
            "roslynFullAutoEverEnabled": bool(config.roslyn_risk_acknowledged),
            "allowWriteRequests": bool(config.allow_write_requests),
            "allowRoslynAdvanced": self._ports.roslyn_available(config),
            "legacyRoslynEnvEnabled": False,
        }

    def update_permission_state(
        self,
        execution_mode: str,
        acknowledge_roslyn_risk: bool = False,
    ) -> dict[str, Any]:
        with self._ports.state.shared_state_lock:
            config = self._ports.ensure_config()
            mode = normalize_execution_mode(execution_mode)
            entering_full_permission = mode == "roslyn_full_auto"

            previous = self.permission_state(config)
            config.execution_mode = mode
            if entering_full_permission:
                # Selecting Full Permission is the user's explicit global
                # choice to enable write capability; do not leave a hidden
                # legacy master switch silently disabling it.
                config.allow_write_requests = True
            if acknowledge_roslyn_risk and entering_full_permission:
                config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = False
            self._ports.save_config(config)
            updated = self.permission_state(config)
            self._ports.append_audit(
                {
                    "event": "permission_mode_updated",
                    "previous": previous,
                    "updated": updated,
                    **self.permission_audit_context(config),
                }
            )
            return {"ok": True, "permission": updated}

    def _execute_write_request(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
        *,
        goal_delivery_id: str = "",
        task_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Route an avatar/Unity write through the supervised tool path.

        The loop never auto-applies writes: write handlers are converted into an
        approval request, and approved execution later creates the pre-write
        checkpoint and calls the registered handler. We surface the approval id so
        the turn can stop and wait. Direct tools remain supported for legacy
        request wrappers, but write-handler ids must not be sent through
        `call_tool` because they are not direct tools.
        """
        if not tool_name:
            return {"ok": False, "status": "blocked", "tool": "", "error": "No write tool was resolved."}
        params_summary = self._ports.tool_params_audit(tool_name, params)
        try:
            if tool_name in self._ports.state.write_handlers:
                outcome = self.create_apply_request(
                    {
                        "target_tool": tool_name,
                        "arguments": params,
                        "reason": f"Agent proposed supervised write: {tool_name}",
                        "agent_name": agent_name,
                        "goalDeliveryId": goal_delivery_id,
                        "requires_explicit_approval": True,
                        "disable_auto_approval": True,
                        "explicit_approval_reason": (
                            "Agent-proposed Unity/project write requires explicit user approval."
                        ),
                        "preview": {
                            "summary": f"Agent proposed {tool_name}.",
                            "paramsSummary": params_summary,
                        },
                    },
                    task_context=task_context,
                )
            else:
                outcome = self._ports.call_tool(tool_name, params, agent_name=agent_name)
        except AgentGatewayError as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "tool": tool_name,
                "paramsSummary": params_summary,
                "error": str(exc),
            }
        outcome = ensure_dict(outcome)
        approval = extract_approval_id(outcome)
        if not approval:
            approval_record = ensure_dict(outcome.get("approval"))
            if not approval_record:
                approval_record = ensure_dict(ensure_dict(outcome.get("result")).get("approval"))
            approval = str(approval_record.get("id") or "").strip()
        approval_record = ensure_dict(outcome.get("approval"))
        if not approval_record:
            approval_record = ensure_dict(ensure_dict(outcome.get("result")).get("approval"))
        approval_status = str(approval_record.get("status") or "").strip().casefold()
        if approval and approval_status in {"pending", "approved", "applying"}:
            status = "approval_pending"
        elif outcome.get("ok"):
            status = "executed"
        else:
            status = "failed"
        payload: dict[str, Any] = {
            "ok": bool(outcome.get("ok")),
            "status": status,
            "tool": tool_name,
            "paramsSummary": params_summary,
            "result": outcome.get("result") if "result" in outcome else outcome,
        }
        nested_outcome = outcome.get("outcome")
        payload["outcome"] = (
            dict(nested_outcome)
            if isinstance(nested_outcome, Mapping)
            else normalize_agent_tool_result(
                outcome,
                fallback_summary=f"{tool_name} did not complete.",
                write=True,
            )
        )
        linked_task = ensure_dict(approval_record.get("taskContext"))
        task_completion = ensure_dict(outcome.get("taskCompletion"))
        if not task_completion:
            task_completion = ensure_dict(approval_record.get("taskCompletion"))
        if approval:
            payload["approval_id"] = approval
            payload["approvalId"] = approval
        if linked_task.get("actionId"):
            payload["taskActionId"] = str(linked_task.get("actionId"))
        if task_completion:
            payload["taskCompletion"] = task_completion
        if outcome.get("error"):
            payload["error"] = outcome["error"]
        return payload

    def create_apply_request(
        self,
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
        include_arguments_digest: bool = False,
        task_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = self._ports.ensure_config()
        if not config.allow_write_requests:
            raise AgentGatewayError("Agent Gateway write requests are disabled.", status_code=403)

        target_tool = str(params.get("target_tool") or params.get("targetTool") or "").strip()
        if not target_tool:
            raise AgentGatewayError("target_tool is required.")
        if target_tool in WRAPPER_ONLY_WRITE_TARGETS and not internal_wrapper:
            raise AgentGatewayError(
                f"{target_tool} can only be requested through its dedicated VRCForge request tool.",
                status_code=403,
            )

        write_handler = self._ports.state.write_handlers.get(target_tool)
        if not write_handler or not self._ports.write_handler_visible(write_handler, config):
            raise AgentGatewayError(f"Unknown or unavailable write target: {target_tool}", status_code=404)

        arguments = ensure_dict(params.get("arguments") or params.get("params") or {})
        user_constraints = self._ports.read_user_constraints()
        arguments = self._inject_user_constraints_for_apply(arguments, user_constraints)
        preview = params.get("preview")
        if write_handler.request_preparer is not None:
            try:
                prepared_arguments, prepared_preview = write_handler.request_preparer(
                    dict(arguments),
                    preview,
                )
            except AgentGatewayError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed authoritative preview must block the request.
                raise AgentGatewayError(
                    f"Could not prepare the supervised write request for {target_tool}.",
                    status_code=500,
                ) from exc
            if not isinstance(prepared_arguments, dict):
                raise AgentGatewayError(
                    f"Write request preparation returned invalid arguments for {target_tool}.",
                    status_code=500,
                )
            arguments = prepared_arguments
            preview = prepared_preview
        mandatory_manual_approval_reason = ""
        if write_handler.manual_approval_resolver is not None:
            try:
                mandatory_manual_approval_reason = str(
                    write_handler.manual_approval_resolver(dict(arguments), preview) or ""
                ).strip()
            except Exception as exc:  # noqa: BLE001 - policy failures must block the request.
                raise AgentGatewayError(
                    f"Could not determine the manual approval policy for {target_tool}.",
                    status_code=500,
                ) from exc
        base_risk_level = normalize_risk_level(write_handler.risk_level)
        effective_risk_level = base_risk_level
        risk_escalation_reason = ""
        if write_handler.risk_level_resolver is not None:
            try:
                resolved_risk_level = str(
                    write_handler.risk_level_resolver(dict(arguments)) or ""
                ).strip().lower()
            except Exception as exc:  # noqa: BLE001 - a failed classifier must block the write request.
                raise AgentGatewayError(
                    f"Could not determine write risk for {target_tool}: {exc}",
                    status_code=500,
                ) from exc
            if resolved_risk_level not in {"low", "medium", "high", "critical"}:
                raise AgentGatewayError(
                    f"Write risk resolver returned an invalid level for {target_tool}.",
                    status_code=500,
                )
            risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            if risk_rank[resolved_risk_level] > risk_rank[base_risk_level]:
                effective_risk_level = resolved_risk_level
                risk_escalation_reason = (
                    f"These arguments elevate the request from {base_risk_level} to "
                    f"{effective_risk_level} risk and require manual approval in Auto Approve mode."
                )
        requires_explicit_approval = bool(
            params.get("requires_explicit_approval")
            or params.get("requiresExplicitApproval")
            or params.get("disable_auto_approval")
            or params.get("disableAutoApproval")
        )
        never_auto_approve = bool(
            params.get("never_auto_approve")
            or params.get("neverAutoApprove")
            or mandatory_manual_approval_reason
        )
        execution_mode = normalize_execution_mode(config.execution_mode)
        full_permission_auto = execution_mode == "roslyn_full_auto"
        permission_context = self.permission_audit_context(config)
        auto_policy_reason = self._write_auto_manual_approval_reason(target_tool, arguments, preview)
        always_manual_reason = (
            CHECKPOINT_RESTORE_MANUAL_APPROVAL_REASON
            if target_tool == "vrcforge_restore_checkpoint"
            else ROLLBACK_MANUAL_APPROVAL_REASON
            if target_tool in ROLLBACK_MANUAL_APPROVAL_TOOLS
            else AVATAR_UPLOAD_MANUAL_APPROVAL_REASON
            if target_tool == "vrcforge_build_and_upload_avatar" and not full_permission_auto
            else ""
        )
        requires_explicit_for_mode = bool(always_manual_reason) or (
            False if full_permission_auto else (
                never_auto_approve
                or requires_explicit_approval
                or (execution_mode == "auto" and bool(auto_policy_reason or risk_escalation_reason))
            )
        )
        explicit_approval_reason = str(
            always_manual_reason
            or mandatory_manual_approval_reason
            or params.get("explicit_approval_reason")
            or params.get("explicitApprovalReason")
            or risk_escalation_reason
            or auto_policy_reason
            or "This write request requires explicit user approval."
        ).strip()
        if user_constraints.content and isinstance(preview, dict):
            preview = {
                **preview,
                "userConstraintsApplied": True,
                "userConstraintsPath": str(user_constraints.path),
            }
        approved_execution_plan: dict[str, Any] | None = None
        if write_handler.requires_approved_execution_context:
            if write_handler.approved_execution_plan_builder is None:
                raise AgentGatewayError(
                    f"Unity write target is not yet bound to an exact Core execution plan: {target_tool}",
                    status_code=409,
                )
            try:
                planned_calls = write_handler.approved_execution_plan_builder(dict(arguments))
                approved_execution_plan = freeze_approved_unity_execution_plan(planned_calls)
            except AgentGatewayError:
                raise
            except Exception as exc:  # noqa: BLE001 - malformed plans must fail before approval exists.
                raise AgentGatewayError(
                    f"Could not freeze the exact Core execution plan for {target_tool}.",
                    status_code=409,
                ) from exc
        approval = self._new_approval(
            agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent"),
            target_tool=target_tool,
            arguments=arguments,
            reason=str(params.get("reason") or ""),
            preview=preview,
            risk_level=effective_risk_level,
            user_constraints=user_constraints,
            requires_explicit_approval=requires_explicit_for_mode,
            explicit_approval_reason=explicit_approval_reason,
            goal_delivery_id=str(params.get("goalDeliveryId") or params.get("goal_delivery_id") or "").strip(),
            approved_execution_plan=approved_execution_plan,
            allow_future_eligible=self._ports.write_handler_allows_future_category(
                write_handler,
                {
                    "targetTool": target_tool,
                    "riskLevel": effective_risk_level,
                    "requiresExplicitApproval": requires_explicit_for_mode,
                },
            ),
            task_context=approval_task_context(
                task_context,
                tool=target_tool,
                arguments=arguments,
            ),
        )
        if include_arguments_digest:
            approval["argumentsDigest"] = stable_hash(
                json.dumps(
                    arguments,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        if full_permission_auto and not always_manual_reason and (
            never_auto_approve
            or requires_explicit_approval
            or auto_policy_reason
            or risk_escalation_reason
        ):
            self._ports.append_audit(
                {
                    "event": "approval_explicit_requirement_overridden_by_full_permission",
                    "approvalId": approval.get("id"),
                    "mode": execution_mode,
                    **permission_context,
                    "reason": explicit_approval_reason,
                    "targetTool": target_tool,
                }
            )
        auto_review_decision = "not_applicable"
        if execution_mode == "auto" and self._auto_approval_reviewer is not None:
            try:
                auto_review_decision = str(
                    self._auto_approval_reviewer(redact_sensitive(dict(approval))) or "manual"
                ).strip()
            except Exception:
                auto_review_decision = "manual"
            if auto_review_decision not in {"allow_auto", "manual", "not_applicable"}:
                auto_review_decision = "manual"
            if auto_review_decision != "not_applicable":
                self._ports.append_audit(
                    {
                        "event": "approval_independent_auto_review",
                        "approvalId": approval.get("id"),
                        "targetTool": target_tool,
                        "reviewerDecision": auto_review_decision,
                        "manualPolicyRequired": requires_explicit_for_mode,
                    }
                )
        if self.auto_approval_enabled(config) and not requires_explicit_for_mode:
            if auto_review_decision in {"allow_auto", "not_applicable"}:
                auto_payload = self._auto_execute_approval(approval)
                if auto_payload is not None:
                    return auto_payload
        if self.auto_approval_enabled(config) and requires_explicit_for_mode:
            self._ports.append_audit(
                {
                    "event": "approval_auto_approval_suppressed",
                    "approvalId": approval.get("id"),
                    "mode": execution_mode,
                    **permission_context,
                    "reason": explicit_approval_reason,
                    "targetTool": target_tool,
                }
            )
        if execution_mode == "approval":
            stored_approval = self._ports.state.approvals.get(str(approval.get("id") or ""), approval)
            scoped_rule = self._matching_project_category_allow_rule(stored_approval, write_handler, config)
            if scoped_rule is not None:
                reviewer = self._scoped_approval_reviewer
                decision = "manual"
                if reviewer is not None:
                    try:
                        decision = str(reviewer(redact_sensitive(dict(stored_approval))) or "manual").strip()
                    except Exception:
                        decision = "manual"
                if decision == "allow_auto":
                    auto_payload = self._scoped_rule_execute_approval(stored_approval, scoped_rule)
                    if auto_payload is not None:
                        return auto_payload
                self._ports.append_audit(
                    {
                        "event": "approval_scoped_rule_manual",
                        "approvalId": approval.get("id"),
                        "targetTool": target_tool,
                        "projectRoot": scoped_rule["projectRoot"],
                        "category": scoped_rule["category"],
                        "reviewerDecision": decision if decision == "allow_auto" else "manual",
                    }
                )
        return {
            "ok": True,
            "status": "pending",
            "approval": approval,
            "message": (
                "Apply request requires explicit user approval."
                if requires_explicit_for_mode
                else "Apply request is waiting for user approval."
            ),
        }

    def _auto_execute_approval(self, approval: dict[str, Any]) -> dict[str, Any] | None:
        """Auto-approve and apply an approval under the auto / full-permission tiers.

        Returns the execution payload, or None when auto-approval could not
        proceed (caller then falls back to the normal pending flow). The
        approval record and audit trail are still produced, so every auto
        decision stays reviewable.
        """
        approval_id = str(approval.get("id") or "").strip()
        if not approval_id:
            return None
        block_reason = self._auto_approval_block_reason(approval)
        permission_context = self.permission_audit_context()
        if block_reason:
            self._ports.append_audit(
                {
                    "event": "approval_auto_approval_suppressed",
                    "approvalId": approval_id,
                    "mode": permission_context["permissionMode"],
                    **permission_context,
                    "reason": block_reason,
                    "targetTool": approval.get("targetTool"),
                }
            )
            return None
        approved = self.approve(approval_id)
        if not approved.get("ok"):
            return None
        self._ports.append_audit(
            {
                "event": "approval_auto_approved",
                "approvalId": approval_id,
                "mode": permission_context["permissionMode"],
                **permission_context,
                "targetTool": approval.get("targetTool"),
                "agent": approval.get("agentName") or "",
                "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
            }
        )
        applied = self.apply_approved({"approval_id": approval_id})
        applied_status = str(applied.get("status") or "").strip()
        payload: dict[str, Any] = {
            "ok": bool(applied.get("ok")),
            "status": (
                "needs_user_action"
                if applied_status == "needs_user_action"
                else "executed" if applied.get("ok") else "failed"
            ),
            "autoApproved": True,
            "fullPermission": permission_context["fullPermission"],
            "permissionMode": permission_context["permissionMode"],
            "permissionLabel": permission_context["permissionLabel"],
            "approval": applied.get("approval") or approved.get("approval") or approval,
            "approval_id": approval_id,
            "approvalId": approval_id,
            "message": "Approval was auto-approved by the current permission mode.",
        }
        if applied.get("result") is not None:
            payload["result"] = applied.get("result")
        if applied.get("outcome") is not None:
            payload["outcome"] = applied.get("outcome")
        if not applied.get("ok"):
            payload["error"] = str(applied.get("error") or "Auto-approved execution failed.")
        return payload

    def _matching_project_category_allow_rule(
        self,
        approval: dict[str, Any],
        write_handler: AgentWriteHandler,
        config: AgentGatewayConfig | None = None,
    ) -> dict[str, str] | None:
        if not self._ports.write_handler_allows_future_category(write_handler, approval):
            return None
        project_root = self._approval_project_root(approval)
        project_key = normalize_filesystem_path(project_root) if project_root else ""
        if not project_key:
            return None
        active = config or self._ports.ensure_config()
        for rule in active.project_category_allow_rules:
            if (
                rule.get("projectRoot") == project_key
                and rule.get("category") == write_handler.approval_category
            ):
                return {"projectRoot": project_key, "category": write_handler.approval_category}
        return None

    def _scoped_rule_execute_approval(
        self, approval: dict[str, Any], rule: dict[str, str]
    ) -> dict[str, Any] | None:
        approval_id = str(approval.get("id") or "").strip()
        if not approval_id:
            return None
        approved = self.approve(approval_id)
        if not approved.get("ok"):
            return None
        self._ports.append_audit(
            {
                "event": "approval_scoped_rule_auto_approved",
                "approvalId": approval_id,
                "targetTool": approval.get("targetTool") or "",
                "projectRoot": rule["projectRoot"],
                "category": rule["category"],
            }
        )
        applied = self.apply_approved({"approval_id": approval_id})
        applied_status = str(applied.get("status") or "").strip()
        payload: dict[str, Any] = {
            "ok": bool(applied.get("ok")),
            "status": (
                "needs_user_action"
                if applied_status == "needs_user_action"
                else "executed" if applied.get("ok") else "failed"
            ),
            "scopedRuleAutoApproved": True,
            "approval": applied.get("approval") or approved.get("approval") or approval,
            "approval_id": approval_id,
            "approvalId": approval_id,
            "message": "Approval was auto-approved by the saved project category rule.",
        }
        if applied.get("result") is not None:
            payload["result"] = applied.get("result")
        if applied.get("outcome") is not None:
            payload["outcome"] = applied.get("outcome")
        if not applied.get("ok"):
            payload["error"] = str(applied.get("error") or "Scoped-rule execution failed.")
        return payload

    def apply_approved(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approval_id") or params.get("approvalId") or "").strip()
        if not approval_id:
            raise AgentGatewayError("approval_id is required.")
        self._ports.signal_background_activity("approved_write")
        project_lock_key = ""
        project_lock_acquired = False

        with self._ports.state.shared_state_lock:
            approval = self._ports.state.approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                if self._reconcile_unrecoverable_linked_approval(approval_id):
                    raise AgentGatewayError(
                        "Approval could not be recovered after the runtime restarted; the linked goal now needs review.",
                        status_code=409,
                    )
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)

            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") != "approved":
                return {
                    "ok": False,
                    "status": approval.get("status"),
                    "approval": approval,
                    "message": "Approval is not approved yet.",
                }

            target_tool = str(approval.get("targetTool") or "")
            if target_tool == "vrcforge_shell_execute":
                write_handler = AgentWriteHandler(
                    "vrcforge_shell_execute",
                    "Execute an approved high-risk shell command.",
                    "high",
                    self._ports.shell_execute_payload,
                )
            else:
                write_handler = self._ports.state.write_handlers.get(target_tool)
            if not write_handler:
                raise AgentGatewayError(f"Write target is no longer available: {target_tool}", status_code=404)

            project_root = self._approval_project_root(approval)
            if self._project_has_background_read(project_root):
                self._ports.append_audit(
                    {
                        "event": "approval_blocked_by_background_project_read",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "activeReadCount": len(self._ports.state.background_project_read_leases),
                    }
                )
                return {
                    "ok": False,
                    "status": "blocked_background_read",
                    "approval": approval,
                    "error": "A background project read is active. Retry this approved write after it finishes.",
                }

            in_flight_writes = [
                dict(entry)
                for entry in self._ports.state.in_flight_apply_writes.values()
                if isinstance(entry, Mapping)
                and (
                    self._entry_project_key(entry) in {self._project_lock_key(project_root), "__global__"}
                    or self._project_lock_key(project_root) == "__global__"
                )
            ]
            if in_flight_writes:
                self._ports.append_audit(
                    {
                        "event": "approval_blocked_by_in_flight_write",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "inFlightWrites": in_flight_writes,
                    }
                )
                return {
                    "ok": False,
                    "status": "blocked_concurrent_write",
                    "approval": approval,
                    "inFlightWrites": in_flight_writes,
                    "error": "Another approved write is still applying. Wait for it to finish (or fail into recovery) before running this write.",
                }

            project_lock_key, project_lock_acquired = self._try_acquire_project_write(project_root)
            if not project_lock_acquired:
                self._ports.append_audit(
                    {
                        "event": "approval_blocked_by_project_write_lock",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "projectRoot": project_root,
                    }
                )
                return {
                    "ok": False,
                    "status": "blocked_concurrent_write",
                    "approval": approval,
                    "inFlightWrites": [{"projectRoot": project_root}],
                    "error": "Another write for this Unity project is still applying. Retry after it finishes.",
                }

            active_recoveries = [
                recovery
                for recovery in self._ports.checkpoint.active_apply_recoveries()
                if self._entry_project_key(recovery) in {self._project_lock_key(project_root), "__global__"}
                or self._project_lock_key(project_root) == "__global__"
            ]
            if active_recoveries and target_tool not in APPLY_RECOVERY_EXEMPT_WRITE_TARGETS:
                self._ports.append_audit(
                    {
                        "event": "approval_blocked_by_interrupted_apply_recovery",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "recoveries": active_recoveries,
                    }
                )
                if project_lock_acquired:
                    self._release_project_write(project_lock_key)
                    project_lock_acquired = False
                return {
                    "ok": False,
                    "status": "blocked_recovery",
                    "approval": approval,
                    "recoveries": active_recoveries,
                    "recovery": active_recoveries[0],
                    "error": "A previous write did not finish cleanly. Restore or resolve the interrupted apply recovery before running another write.",
                }

            self._ports.state.in_flight_apply_writes[approval_id] = {
                "approvalId": approval_id,
                "targetTool": target_tool,
                "projectRoot": project_root,
                "projectLockKey": project_lock_key,
                "startedAt": utc_now_iso(),
            }
            try:
                approval["status"] = "applying"
                self._ports.state.approvals[approval_id] = approval
                permission_context = self.permission_audit_context()
                self._ports.append_audit({"event": "approval_applying", "approval": approval, **permission_context})
                self._runtime_run_append(
                    {
                        "event": "approval_applying",
                        "status": "applying",
                        "approvalId": approval_id,
                        "approvalIds": [approval_id],
                        **permission_context,
                        "targetTool": target_tool,
                        "agent": approval.get("agentName") or "",
                        "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                    }
                )
                self._observe_apply_lifecycle("approval_started", approval)
            except Exception as exc:  # noqa: BLE001 - restore a retryable approval on transition I/O failure.
                approval["status"] = "approved"
                self._ports.state.approvals[approval_id] = approval
                self._ports.state.in_flight_apply_writes.pop(approval_id, None)
                if project_lock_acquired:
                    self._release_project_write(project_lock_key)
                    project_lock_acquired = False
                try:
                    self._ports.append_audit(
                        {
                            "event": "approval_apply_transition_failed",
                            "approval": approval,
                            "approvalId": approval_id,
                            "targetTool": target_tool,
                            "error": str(exc),
                        }
                    )
                except Exception:  # noqa: BLE001 - the original failure may be the audit sink itself.
                    pass
                raise AgentGatewayError(f"Could not start approved write: {exc}", status_code=500) from exc

        checkpoint: dict[str, Any] | None = None
        recovery: dict[str, Any] | None = None
        no_write_conflict = False
        core_call_audits: list[dict[str, Any]] = []
        execution_id = f"exec_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}"
        request_trace: dict[str, Any] | None = None
        result: Any = None
        completion_outcome: dict[str, Any] = {}
        task_completion: dict[str, Any] | None = None
        verification_baseline: dict[str, Any] = {}
        failure_layer = "approval_transaction"
        try:
            user_constraints = self._ports.read_user_constraints()
            arguments = self._inject_user_constraints_for_apply(
                ensure_dict(approval.get("arguments") or {}),
                user_constraints,
            )
            handler_arguments_digest = stable_hash(
                json.dumps(
                    arguments,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            if write_handler.verification_prepare_handler is not None:
                # The pre-write verifier must finish before a recovery record
                # claims that handler execution may have mutated the project.
                # A failed/unstable baseline is therefore a clean no-write
                # failure, not an interrupted apply that blocks later writes.
                failure_layer = "completion_verification_baseline"
                verification_arguments = dict(arguments)
                verification_arguments.pop("_vrcforge_approved_execution", None)
                verification_baseline = ensure_dict(
                    write_handler.verification_prepare_handler(verification_arguments)
                )
            failure_layer = "checkpoint"
            classification = ensure_dict(arguments.get("classification_snapshot"))
            requires_checkpoint = write_handler.pre_write_checkpoint_required and not (
                target_tool == "vrcforge_shell_execute" and classification.get("readOnly") is True
            )
            if requires_checkpoint and target_tool == PROJECT_CHAT_CHECKPOINT_TARGET:
                # Keep the digest-bound checkpoint and the repair handler in
                # one writer-locked critical section. This prevents a newer
                # chat save from landing between snapshot verification and
                # quarantine, while preserving storage -> chat lock order.
                with self._ports.state.checkpoint_storage_lock:
                    with self._ports.checkpoint.project_chat_checkpoint_lock():
                        checkpoint = self._create_pre_write_checkpoint(approval, arguments)
                        if checkpoint:
                            approval["checkpoint"] = checkpoint
                            if checkpoint.get("ok") is not True:
                                checkpoint["blocking"] = True
                                raise AgentGatewayError(str(checkpoint.get("error") or "Pre-write checkpoint failed."))
                            self._observe_apply_lifecycle(
                                "checkpoint_created", approval, checkpoint=checkpoint
                            )
                            self._observe_apply_lifecycle(
                                "handler_starting",
                                approval,
                                checkpoint=checkpoint,
                                arguments_digest=handler_arguments_digest,
                            )
                            recovery = self._start_apply_recovery(approval, arguments, checkpoint)
                        if not checkpoint:
                            self._observe_apply_lifecycle(
                                "handler_starting",
                                approval,
                                checkpoint=checkpoint,
                                arguments_digest=handler_arguments_digest,
                            )
                        failure_layer = "approved_write_execution"
                        with capture_unity_mcp_core_call_audits() as core_call_audits:
                            result = self._call_write_handler(
                                write_handler,
                                target_tool,
                                approval_id,
                                checkpoint,
                                arguments,
                                handler_arguments_digest,
                                ensure_dict(approval.get("approvedUnityExecutionPlan")),
                                verification_baseline,
                                ensure_dict(approval.get("taskContext")),
                            )
            elif requires_checkpoint and target_tool in LOCAL_STATE_CHECKPOINT_TARGETS:
                if not self._ports.state.skill_package_write_lock_bound:
                    raise AgentGatewayError(
                        "Local-state approved writes require the shared skill-package lock.",
                        status_code=503,
                    )
                # Keep archive capture and the package/user-skill mutation in
                # one storage -> package -> user critical section. The
                # package and user locks are re-entrant because the typed
                # Controller/Projection owners enforce the same order.
                with self._ports.state.checkpoint_storage_lock:
                    with self._ports.state.skill_package_write_lock:
                        with self._ports.skills.write_lock:
                            checkpoint = self._create_pre_write_checkpoint(
                                approval,
                                arguments,
                            )
                            if checkpoint:
                                approval["checkpoint"] = checkpoint
                                if checkpoint.get("ok") is not True:
                                    checkpoint["blocking"] = True
                                    raise AgentGatewayError(
                                        str(
                                            checkpoint.get("error")
                                            or "Pre-write checkpoint failed."
                                        )
                                    )
                                self._observe_apply_lifecycle(
                                    "checkpoint_created",
                                    approval,
                                    checkpoint=checkpoint,
                                )
                                self._observe_apply_lifecycle(
                                    "handler_starting",
                                    approval,
                                    checkpoint=checkpoint,
                                    arguments_digest=handler_arguments_digest,
                                )
                                recovery = self._start_apply_recovery(
                                    approval,
                                    arguments,
                                    checkpoint,
                                )
                            if not checkpoint:
                                self._observe_apply_lifecycle(
                                    "handler_starting",
                                    approval,
                                    checkpoint=checkpoint,
                                    arguments_digest=handler_arguments_digest,
                                )
                            failure_layer = "approved_write_execution"
                            with capture_unity_mcp_core_call_audits() as core_call_audits:
                                result = self._call_write_handler(
                                    write_handler,
                                    target_tool,
                                    approval_id,
                                    checkpoint,
                                    arguments,
                                    handler_arguments_digest,
                                    ensure_dict(
                                        approval.get("approvedUnityExecutionPlan")
                                    ),
                                    verification_baseline,
                                    ensure_dict(approval.get("taskContext")),
                                )
            else:
                if requires_checkpoint:
                    with self._ports.state.checkpoint_storage_lock:
                        checkpoint = self._create_pre_write_checkpoint(approval, arguments)
                        if checkpoint:
                            approval["checkpoint"] = checkpoint
                            if checkpoint.get("ok") is not True:
                                checkpoint["blocking"] = True
                                raise AgentGatewayError(str(checkpoint.get("error") or "Pre-write checkpoint failed."))
                            self._observe_apply_lifecycle(
                                "checkpoint_created", approval, checkpoint=checkpoint
                            )
                            self._observe_apply_lifecycle(
                                "handler_starting",
                                approval,
                                checkpoint=checkpoint,
                                arguments_digest=handler_arguments_digest,
                            )
                            recovery = self._start_apply_recovery(approval, arguments, checkpoint)
                if not checkpoint:
                    self._observe_apply_lifecycle(
                        "handler_starting",
                        approval,
                        checkpoint=checkpoint,
                        arguments_digest=handler_arguments_digest,
                    )
                failure_layer = "approved_write_execution"
                with capture_unity_mcp_core_call_audits() as core_call_audits:
                    result = self._call_write_handler(
                        write_handler,
                        target_tool,
                        approval_id,
                        checkpoint,
                        arguments,
                        handler_arguments_digest,
                        ensure_dict(approval.get("approvedUnityExecutionPlan")),
                        verification_baseline,
                        ensure_dict(approval.get("taskContext")),
                    )
            if core_call_audits:
                request_trace = {
                    "approvalId": approval_id,
                    "targetTool": target_tool,
                    "executionId": execution_id,
                    "unityCoreCallAudits": [dict(audit) for audit in core_call_audits],
                }
            if isinstance(result, dict) and result.get("ok") is False:
                failure_layer = str(result.get("failureLayer") or "write_handler")[:80]
                no_write_conflict = bool(
                    target_tool == PROJECT_CHAT_CHECKPOINT_TARGET
                    and result.get("status") == "conflict"
                    and result.get("changed") is False
                )
                message = (
                    result.get("error")
                    or result.get("message")
                    or result.get("reason")
                    or f"{target_tool} returned ok=false."
                )
                raise AgentGatewayError(str(message))
            completion_outcome = ensure_dict(
                redact_sensitive(
                    normalize_agent_tool_result(
                        result,
                        fallback_summary=write_handler.description,
                        write=True,
                    )
                )
            )
            task_completion = approval_completion(
                ensure_dict(approval.get("taskContext")),
                raw_result=result,
                outcome=completion_outcome,
            )
            if task_completion is not None:
                completion_outcome = ensure_dict(task_completion.get("outcome"))
            completion_status = str(completion_outcome["status"])
            if completion_status == "failed":
                failure_layer = "result_verification"
                raise AgentGatewayError(str(completion_outcome["summary"]))
            failure_layer = "apply_lifecycle_observer"
            self._observe_apply_lifecycle(
                "handler_returned", approval, checkpoint=checkpoint, result=result
            )
            failure_layer = "approval_transaction_commit"
            with self._ports.state.shared_state_lock:
                approval["status"] = "applied"
                approval["appliedAt"] = utc_now_iso()
                approval["completionOutcome"] = completion_outcome
                if task_completion is not None:
                    approval["taskCompletion"] = task_completion
                approval["resultSummary"] = summarize_params(result if isinstance(result, dict) else {"result": result})
                self._ports.state.approvals[approval_id] = approval
                permission_context = self.permission_audit_context()
                applied_audit = {
                    "event": "approval_applied",
                    "approval": approval,
                    "completionStatus": completion_status,
                    **permission_context,
                }
                memory_evidence = self._ports.validated_memory_evidence_for_applied_write(
                    approval,
                    arguments,
                    result,
                )
                if memory_evidence is not None:
                    applied_audit["memoryEvidence"] = memory_evidence
                if request_trace is not None:
                    applied_audit["requestTrace"] = request_trace
                self._ports.append_audit(applied_audit)
                self._runtime_run_append(
                    {
                        "event": "approval_applied",
                        "status": (
                            "needs_user_action"
                            if completion_status == "needs_user_action"
                            else "applied"
                        ),
                        "transactionStatus": "applied",
                        "completionStatus": completion_status,
                        "approvalId": approval_id,
                        "approvalIds": [approval_id],
                        **permission_context,
                        "targetTool": target_tool,
                        "agent": approval.get("agentName") or "",
                        "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                        "checkpointId": ensure_dict(checkpoint).get("id") if checkpoint else "",
                        "checkpointIds": [ensure_dict(checkpoint).get("id")] if checkpoint and ensure_dict(checkpoint).get("id") else [],
                        "resultSummary": approval.get("resultSummary") or "",
                    }
                )
            if recovery:
                self._finish_apply_recovery(
                    recovery,
                    status="applied",
                    resolution="write_completed",
                    result_summary=summarize_params(result if isinstance(result, dict) else {"result": result}),
                )
            payload = {
                "ok": True,
                "status": "needs_user_action" if completion_status == "needs_user_action" else "applied",
                "approval": approval,
                "result": result,
                "outcome": completion_outcome,
            }
            if task_completion is not None:
                payload["taskCompletion"] = task_completion
            if request_trace is not None:
                payload["requestTrace"] = request_trace
            if checkpoint:
                payload["checkpoint"] = checkpoint
            return self._goal.attach_terminal_resolution(payload, approval)
        except Exception as exc:  # noqa: BLE001
            if core_call_audits and request_trace is None:
                request_trace = {
                    "approvalId": approval_id,
                    "targetTool": target_tool,
                    "executionId": execution_id,
                    "unityCoreCallAudits": [dict(audit) for audit in core_call_audits],
                }
            failure_result = result
            if failure_result is None:
                checkpoint_failure = ensure_dict(checkpoint)
                if checkpoint_failure.get("ok") is False:
                    failure_result = {
                        "ok": False,
                        "status": "failed",
                        "error": {
                            "type": "checkpoint",
                            "code": str(
                                checkpoint_failure.get("code")
                                or "unity_checkpoint_prepare_failed"
                            ),
                            "summary": str(
                                checkpoint_failure.get("error")
                                or "The pre-write checkpoint failed."
                            ),
                        },
                    }
                else:
                    failure_result = {
                        "ok": False,
                        "status": "failed",
                        "error": str(exc),
                    }
            write_failure = _write_failure_facts(
                failure_result,
                failure_layer=failure_layer,
                verification_baseline=verification_baseline,
                checkpoint=ensure_dict(checkpoint),
                recovery=ensure_dict(recovery),
                no_write_conflict=no_write_conflict,
            )
            redacted_failure_result = redact_sensitive(
                dict(failure_result)
                if isinstance(failure_result, Mapping)
                else {"error": str(exc)}
            )
            if str(completion_outcome.get("status") or "").casefold() != "failed":
                completion_outcome = ensure_dict(
                    redact_sensitive(
                        normalize_agent_tool_result(
                            failure_result,
                            fallback_summary=f"{write_handler.description} failed.",
                            write=True,
                        )
                    )
                )
            if str(completion_outcome.get("status") or "").casefold() != "failed":
                completion_outcome = {
                    "status": "failed",
                    "summary": f"{write_handler.description} failed.",
                    "verification": {"state": "failed", "checks": []},
                }
            if task_completion is None:
                task_completion = approval_completion(
                    ensure_dict(approval.get("taskContext")),
                    raw_result=failure_result,
                    outcome=completion_outcome,
                )
            with self._ports.state.shared_state_lock:
                approval["status"] = "failed"
                approval["failedAt"] = utc_now_iso()
                approval["error"] = str(exc)
                approval["completionOutcome"] = completion_outcome
                approval["writeFailure"] = write_failure
                approval["resultSummary"] = summarize_params(redacted_failure_result)
                if task_completion is not None:
                    approval["taskCompletion"] = task_completion
                self._ports.state.approvals[approval_id] = approval
                permission_context = self.permission_audit_context()
                failed_audit = {
                    "event": "approval_failed",
                    "approval": approval,
                    "writeFailure": write_failure,
                    **permission_context,
                }
                if request_trace is not None:
                    failed_audit["requestTrace"] = request_trace
                self._ports.append_audit(failed_audit)
                self._runtime_run_append(
                    {
                        "event": "approval_failed",
                        "status": "failed",
                        "approvalId": approval_id,
                        "approvalIds": [approval_id],
                        **permission_context,
                        "targetTool": target_tool,
                        "agent": approval.get("agentName") or "",
                        "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                        "checkpointId": ensure_dict(checkpoint).get("id") if checkpoint else "",
                        "checkpointIds": [ensure_dict(checkpoint).get("id")] if checkpoint and ensure_dict(checkpoint).get("id") else [],
                        "error": str(exc),
                        "writeFailure": write_failure,
                    }
                )
            if recovery:
                confirmed_no_write = _confirmed_no_write_failure(write_failure)
                cleanup_only = _temporary_cleanup_only_failure(write_failure)
                self._finish_apply_recovery(
                    recovery,
                    status=(
                        "not_applied"
                        if confirmed_no_write
                        else "applied"
                        if cleanup_only
                        else "needs_recovery"
                    ),
                    resolution=(
                        "no_write_snapshot_conflict"
                        if confirmed_no_write and no_write_conflict
                        else "confirmed_no_write"
                        if confirmed_no_write
                        else "write_completed_cleanup_pending"
                        if cleanup_only
                        else "write_failed_after_checkpoint"
                    ),
                    error=str(exc),
                    write_failure=write_failure,
                )
            payload = {
                "ok": False,
                "status": "failed",
                "approval": approval,
                "result": redacted_failure_result,
                "error": str(exc),
                "outcome": completion_outcome,
                "writeFailure": write_failure,
            }
            if task_completion is not None:
                payload["taskCompletion"] = task_completion
            if request_trace is not None:
                payload["requestTrace"] = request_trace
            if checkpoint:
                payload["checkpoint"] = checkpoint
            return self._goal.attach_terminal_resolution(payload, approval)
        finally:
            with self._ports.state.shared_state_lock:
                self._ports.state.in_flight_apply_writes.pop(approval_id, None)
            if project_lock_acquired:
                self._release_project_write(project_lock_key)

    def list_approvals(
        self,
        include_expired: bool = True,
        project_root: str = "",
        global_only: bool = False,
    ) -> list[dict[str, Any]]:
        normalized_project_root = normalize_filesystem_path(project_root) if project_root else ""

        def project_matches(approval: dict[str, Any]) -> bool:
            candidate = self._approval_project_root(approval)
            if global_only and not normalized_project_root:
                return not candidate
            if not normalized_project_root:
                return True
            if not candidate:
                return True
            return normalize_filesystem_path(candidate) == normalized_project_root

        with self._ports.state.shared_state_lock:
            approvals = [
                self._refresh_approval_expiry(dict(item))
                for item in self._ports.state.approvals.values()
                if project_matches(dict(item))
            ]
            if include_expired:
                return [
                    redact_sensitive(item)
                    for item in sorted(approvals, key=lambda item: str(item.get("createdAt") or ""), reverse=True)
                ]
            filtered = [
                item
                for item in sorted(approvals, key=lambda approval: str(approval.get("createdAt") or ""), reverse=True)
                if item.get("status") != "expired"
            ]
            return [redact_sensitive(item) for item in filtered]

    def approve(
        self,
        approval_id: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._set_approval_status(
            approval_id,
            "approved",
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def approve_with_project_category_rule(
        self,
        approval_id: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        """Persist one eligible project/category rule, then approve this item.

        The rule is intentionally created only through the human approval
        endpoint.  Automatic executions never create or widen remembered
        permissions, and every unsuitable write remains a normal pending item.
        """
        with self._ports.state.shared_state_lock:
            approval = self._ports.state.approvals.get(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
            self._ensure_approval_scope(
                approval,
                expected_project_root=expected_project_root,
                global_only=global_only,
            )
            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") != "pending":
                return {"ok": False, "approval": redact_sensitive(dict(approval)), "message": f"Approval is {approval.get('status')}."}
            target_tool = str(approval.get("targetTool") or "")
            write_handler = self._ports.state.write_handlers.get(target_tool)
            config = self._ports.ensure_config()
            rule = self._matching_project_category_allow_rule(approval, write_handler, config) if write_handler else None
            if write_handler is None or not self._ports.write_handler_allows_future_category(write_handler, approval):
                raise AgentGatewayError("This write target cannot be remembered for future approvals.", status_code=409)
            project_root = self._approval_project_root(approval)
            project_key = normalize_filesystem_path(project_root) if project_root else ""
            if not project_key:
                raise AgentGatewayError("A saved approval category requires an exact project root.", status_code=409)
            if rule is None:
                config.project_category_allow_rules.append(
                    {"projectRoot": project_key, "category": write_handler.approval_category}
                )
                config.project_category_allow_rules = self._ports.normalize_project_category_allow_rules(
                    config.project_category_allow_rules
                )
                self._ports.save_config(config)
                self._ports.append_audit(
                    {
                        "event": "approval_scoped_rule_granted",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "projectRoot": project_key,
                        "category": write_handler.approval_category,
                    }
                )
            return self._set_approval_status(
                approval_id,
                "approved",
                expected_project_root=expected_project_root,
                global_only=global_only,
            )

    def reject(
        self,
        approval_id: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._set_approval_status(
            approval_id,
            "rejected",
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def recent_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._ports.audit_log_path().exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._ports.audit_log_path().read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def _call_write_handler(
        self,
        write_handler: AgentWriteHandler,
        target_tool: str,
        approval_id: str,
        checkpoint: dict[str, Any] | None,
        arguments: dict[str, Any],
        handler_arguments_digest: str,
        frozen_execution_plan: dict[str, Any],
        verification_baseline: dict[str, Any],
        task_context: dict[str, Any],
    ) -> Any:
        handler_arguments = dict(arguments)
        handler_arguments.pop("_vrcforge_approved_execution", None)
        if not write_handler.requires_approved_execution_context:
            result = write_handler.handler(handler_arguments)
            if write_handler.verification_finalize_handler is not None:
                result = write_handler.verification_finalize_handler(
                    dict(handler_arguments),
                    dict(verification_baseline),
                    result,
                )
            return result
        checkpoint_id = str(ensure_dict(checkpoint).get("id") or "").strip()
        if not checkpoint or checkpoint.get("ok") is not True or not checkpoint_id:
            raise AgentGatewayError(
                "The Unity write cannot start without a successful bound checkpoint.",
                status_code=409,
            )
        project_root = str(ensure_dict(checkpoint).get("projectRoot") or "").strip()
        if not project_root:
            raise AgentGatewayError(
                "The Unity write checkpoint is missing its exact project binding.",
                status_code=409,
            )
        if write_handler.approved_execution_plan_builder is None:
            raise AgentGatewayError(
                "The Unity write no longer has an exact Core execution plan builder.",
                status_code=409,
            )
        try:
            persisted_plan = validate_frozen_approved_unity_execution_plan(frozen_execution_plan)
            recomputed_plan_json = freeze_approved_unity_execution_plan(
                write_handler.approved_execution_plan_builder(dict(handler_arguments))
            )
            recomputed_plan = validate_frozen_approved_unity_execution_plan(recomputed_plan_json)
        except Exception as exc:  # noqa: BLE001 - plan loss/drift is a hard write boundary.
            raise AgentGatewayError(
                "The approved Unity execution plan is unavailable or invalid.",
                status_code=409,
            ) from exc
        if persisted_plan.plan_digest != recomputed_plan.plan_digest:
            raise AgentGatewayError(
                "The approved Unity execution plan drifted after approval.",
                status_code=409,
            )
        now_ms = int(time.time() * 1000)
        execution_context = {
            "lane": "approved_write",
            "approvalId": str(approval_id),
            "checkpointId": checkpoint_id,
            "targetTool": str(target_tool),
            "projectRoot": project_root,
            "handlerArgumentsSha256": str(handler_arguments_digest),
            "issuedAtUnixMs": now_ms,
            "expiresAtUnixMs": now_ms + 60_000,
        }
        for key in ("taskId", "sessionId", "actionId"):
            value = str(task_context.get(key) or "").strip()
            if value:
                execution_context[
                    "requestedActionId" if key == "actionId" else key
                ] = value
        execution_plan = create_approved_unity_execution_plan(
            execution_context,
            frozen_execution_plan,
        )
        try:
            with bind_approved_unity_execution(execution_plan):
                result = write_handler.handler(handler_arguments)
        finally:
            if not execution_plan.consumed:
                execution_plan.burn()
        if (
            not execution_plan.consumed
            and not (isinstance(result, dict) and result.get("ok") is False)
        ):
            raise AgentGatewayError(
                "The approved Unity execution plan was not consumed exactly.",
                status_code=409,
            )
        if write_handler.verification_finalize_handler is not None:
            result = write_handler.verification_finalize_handler(
                dict(handler_arguments),
                dict(verification_baseline),
                result,
            )
        return result

    def _create_pre_write_checkpoint(self, approval: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
        with self._ports.state.checkpoint_storage_lock:
            return self._create_pre_write_checkpoint_locked(approval, arguments)

    def _create_pre_write_checkpoint_locked(
        self,
        approval: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        target_tool = str(approval.get("targetTool") or "")
        if not target_tool or target_tool in APPLY_RECOVERY_EXEMPT_WRITE_TARGETS:
            return None
        checkpoint_id = f"ckpt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        base_record = {
            "schema": CHECKPOINT_RECORD_SCHEMA,
            "id": checkpoint_id,
            "createdAt": utc_now_iso(),
            "approvalId": str(approval.get("id") or ""),
            "targetTool": target_tool,
            "status": "unavailable",
        }
        if target_tool in LOCAL_STATE_CHECKPOINT_TARGETS:
            return self._ports.checkpoint.create_local_state_checkpoint(base_record)
        project_root = self._ports.checkpoint.resolve_checkpoint_project_root(arguments)
        if project_root is None:
            record = {
                **base_record,
                "ok": False,
                "blocking": True,
                "status": "failed",
                "error": "No Unity project root was available for checkpointing.",
            }
            self._ports.checkpoint.append_checkpoint(record)
            return record
        project_root = project_root.resolve()
        record = {**base_record, "projectRoot": str(project_root)}
        if not self._ports.is_unity_project_root(project_root):
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "error": "Resolved checkpoint root is not a Unity project.",
                }
            )
            self._ports.checkpoint.append_checkpoint(record)
            return record
        if target_tool == PROJECT_CHAT_CHECKPOINT_TARGET:
            record["expectedSourceDigest"] = str(arguments.get("expectedDigest") or "").strip().lower()
            return self._ports.checkpoint.create_project_chat_checkpoint(project_root, record)

        write_handler = self._ports.state.write_handlers.get(target_tool)
        dedicated_checkpoint_prepare = (
            write_handler.checkpoint_prepare_handler
            if write_handler is not None
            else None
        )
        if dedicated_checkpoint_prepare is not None:
            try:
                prepare_result = ensure_dict(
                    dedicated_checkpoint_prepare(project_root, dict(arguments))
                )
            except Exception:  # noqa: BLE001 - dedicated preflight details stay internal.
                prepare_result = {
                    "ok": False,
                    "error": "The dedicated checkpoint preflight failed.",
                }
            record["unityPrepare"] = prepare_result
            if not prepare_result.get("ok"):
                record.update(
                    {
                        "ok": False,
                        "blocking": True,
                        "status": "failed",
                        "error": str(
                            prepare_result.get("error")
                            or "The dedicated checkpoint preflight rejected the write."
                        ),
                    }
                )
                self._ports.checkpoint.append_checkpoint(record)
                return record
        elif self._checkpoint_prepare_handler is not None:
            try:
                prepare_result = ensure_dict(self._checkpoint_prepare_handler(project_root))
            except Exception as exc:  # noqa: BLE001
                prepare_result = {"ok": False, "error": str(exc)}
            record["unityPrepare"] = prepare_result
            if not prepare_result.get("ok"):
                warning = str(prepare_result.get("error") or "Unity could not prepare a rollback checkpoint.")
                if prepare_result.get("blocking") is True:
                    record.update(
                        {
                            "ok": False,
                            "blocking": True,
                            "status": "failed",
                            "code": str(
                                prepare_result.get("code")
                                or "unity_checkpoint_prepare_blocked"
                            ),
                            "error": warning,
                        }
                    )
                    self._ports.checkpoint.append_checkpoint(record)
                    return record
                record["unityPrepareWarning"] = warning
                record["warnings"] = [
                    *ensure_string_list(record.get("warnings")),
                    "Unity prepare checkpoint failed; using file-level checkpoint fallback.",
                ]

        git_root_result = self._ports.run_git(project_root, ["rev-parse", "--show-toplevel"])
        if not git_root_result["ok"]:
            return self._ports.checkpoint.create_archive_checkpoint(project_root, record)
        git_root = Path(git_root_result["stdout"].strip()).resolve()
        pathspecs = self._ports.checkpoint_pathspecs(git_root, project_root)
        ignored_pathspecs = [
            pathspec
            for pathspec in pathspecs
            if self._ports.run_git(
                git_root,
                ["check-ignore", "--quiet", "--no-index", "--", pathspec],
            ).get("returncode") == 0
        ]
        if ignored_pathspecs:
            record["warnings"] = [
                *ensure_string_list(record.get("warnings")),
                "The enclosing Git repository ignores the Unity project; using file-level checkpoint fallback.",
            ]
            record["gitFallbackReason"] = "project_path_ignored_by_enclosing_repository"
            return self._ports.checkpoint.create_archive_checkpoint(project_root, record)
        base_commit_result = self._ports.run_git(git_root, ["rev-parse", "HEAD"])
        base_commit = base_commit_result["stdout"].strip() if base_commit_result["ok"] else ""

        status_before = self._ports.run_git(git_root, ["status", "--porcelain", "--", *pathspecs])
        add_result = self._ports.run_git(git_root, ["add", "-A", "--", *pathspecs], timeout_seconds=120)
        if not add_result["ok"]:
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "gitRoot": str(git_root),
                    "pathspecs": pathspecs,
                    "baseCommit": base_commit,
                    "error": add_result["error"] or "git add failed while creating checkpoint.",
                }
            )
            self._ports.checkpoint.append_checkpoint(record)
            return record

        staged_diff = self._ports.run_git(git_root, ["diff", "--cached", "--quiet", "--", *pathspecs])
        created_commit = False
        checkpoint_ref = base_commit
        if staged_diff["returncode"] == 1:
            message = f"chore(vrcforge): checkpoint before {target_tool} {checkpoint_id}"
            commit_result = self._ports.run_git(
                git_root,
                [
                    "-c",
                    "user.name=VRCForge",
                    "-c",
                    "user.email=vrcforge@example.invalid",
                    "commit",
                    "--no-verify",
                    "-m",
                    message,
                ],
                timeout_seconds=120,
            )
            if not commit_result["ok"]:
                record.update(
                    {
                        "ok": False,
                        "blocking": True,
                        "status": "failed",
                        "gitRoot": str(git_root),
                        "pathspecs": pathspecs,
                        "baseCommit": base_commit,
                        "error": commit_result["error"] or "git commit failed while creating checkpoint.",
                        "stdout": commit_result["stdout"],
                        "stderr": commit_result["stderr"],
                    }
                )
                self._ports.checkpoint.append_checkpoint(record)
                return record
            created_commit = True
            head_result = self._ports.run_git(git_root, ["rev-parse", "HEAD"])
            checkpoint_ref = head_result["stdout"].strip() if head_result["ok"] else base_commit
        elif staged_diff["returncode"] not in {0, 1}:
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "gitRoot": str(git_root),
                    "pathspecs": pathspecs,
                    "baseCommit": base_commit,
                    "error": staged_diff["error"] or "git diff failed while creating checkpoint.",
                }
            )
            self._ports.checkpoint.append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "git",
                "gitRoot": str(git_root),
                "pathspecs": pathspecs,
                "baseCommit": base_commit,
                "checkpointRef": checkpoint_ref,
                "createdCommit": created_commit,
                "statusBefore": [line for line in status_before["stdout"].splitlines() if line.strip()] if status_before["ok"] else [],
            }
        )
        record["rollbackCoverageAudit"] = self._ports.checkpoint.build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._ports.checkpoint.append_checkpoint(record)
        self._ports.append_audit({"event": "checkpoint_created", "checkpoint": record})
        self._ports.checkpoint.prune_checkpoint_archives(protected_checkpoint_ids={checkpoint_id})
        return record

    def has_in_flight_project_write(self) -> bool:
        """Return whether an approved project write is currently applying."""

        with self._ports.state.shared_state_lock:
            if self._ports.state.in_flight_apply_writes:
                return True
            return any(
                str(approval.get("status") or "").strip().casefold() == "applying"
                for approval in self._ports.state.approvals.values()
                if isinstance(approval, dict)
            )

    def try_acquire_background_project_read(self, token: str, project_root: str = "") -> bool:
        normalized = str(token or "").strip()
        if not normalized:
            return False
        key = self._project_lock_key(project_root)
        stored = f"{key}\x00{normalized}" if project_root else normalized
        with self._ports.state.shared_state_lock:
            if self._project_has_in_flight_write(project_root) or self._project_has_background_read(project_root):
                return False
            self._ports.state.background_project_read_leases.add(stored)
            return True

    def release_background_project_read(self, token: str, project_root: str = "") -> bool:
        normalized = str(token or "").strip()
        if not normalized:
            return False
        stored = f"{self._project_lock_key(project_root)}\x00{normalized}" if project_root else normalized
        with self._ports.state.shared_state_lock:
            if stored not in self._ports.state.background_project_read_leases:
                return False
            self._ports.state.background_project_read_leases.remove(stored)
            return True

    def _apply_recovery_blocks_writes(self, recovery: dict[str, Any]) -> bool:
        return str(recovery.get("status") or "") in APPLY_RECOVERY_ACTIVE_STATUSES

    def _start_apply_recovery(
        self,
        approval: dict[str, Any],
        arguments: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        target_tool = str(approval.get("targetTool") or "")
        recovery_id = f"recovery_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        error_text = " ".join(
            str(value or "")
            for value in (
                approval.get("reason"),
                checkpoint.get("error"),
                arguments.get("error"),
                arguments.get("message"),
            )
        )
        record = {
            "id": recovery_id,
            "status": "applying",
            "resolution": "",
            "createdAt": utc_now_iso(),
            "approvalId": str(approval.get("id") or ""),
            "targetTool": target_tool,
            "riskLevel": str(approval.get("riskLevel") or ""),
            "projectRoot": str(checkpoint.get("projectRoot") or arguments.get("projectRoot") or arguments.get("project_root") or ""),
            "avatarPath": str(arguments.get("avatarPath") or arguments.get("avatar_path") or ""),
            "checkpointId": str(checkpoint.get("id") or ""),
            "checkpoint": checkpoint,
            "argumentsSummary": summarize_params(arguments),
            "incidentKind": self._ports.checkpoint.classify_apply_recovery_incident(error_text, target_tool),
            "restoreTool": "vrcforge_restore_checkpoint",
            "resolveTool": "vrcforge_resolve_interrupted_apply_recovery",
            "blockingWrites": True,
        }
        saved = self._ports.checkpoint.append_apply_recovery_entry(record)
        self._ports.append_audit({"event": "apply_recovery_started", "recovery": saved})
        return saved

    def _finish_apply_recovery(
        self,
        recovery: dict[str, Any],
        *,
        status: str,
        resolution: str,
        error: str = "",
        note: str = "",
        result_summary: dict[str, Any] | None = None,
        write_failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = " ".join([str(error or ""), str(note or ""), str(recovery.get("targetTool") or "")])
        # incidentKind is the immutable first-failure classification.  Completion
        # and manual-resolution notes are state transitions, not new evidence;
        # never let them reclassify the original incident.
        incident_kind = str(recovery.get("incidentKind") or "").strip()
        if not incident_kind:
            incident_kind = self._ports.checkpoint.classify_apply_recovery_incident(
                text,
                str(recovery.get("targetTool") or ""),
            )
        record: dict[str, Any] = {
            "id": str(recovery.get("id") or ""),
            "status": status,
            "resolution": resolution,
            "resolvedAt": utc_now_iso() if status not in APPLY_RECOVERY_ACTIVE_STATUSES else "",
            "approvalId": str(recovery.get("approvalId") or ""),
            "targetTool": str(recovery.get("targetTool") or ""),
            "projectRoot": str(recovery.get("projectRoot") or ""),
            "avatarPath": str(recovery.get("avatarPath") or ""),
            "checkpointId": str(recovery.get("checkpointId") or ""),
            "checkpoint": ensure_dict(recovery.get("checkpoint")),
            "incidentKind": incident_kind,
            "restoreTool": "vrcforge_restore_checkpoint",
            "resolveTool": "vrcforge_resolve_interrupted_apply_recovery",
            "blockingWrites": status in APPLY_RECOVERY_ACTIVE_STATUSES,
        }
        if error:
            record["error"] = error
        if note:
            record["note"] = note
        if result_summary is not None:
            record["resultSummary"] = result_summary
        if write_failure is not None:
            record["writeFailure"] = write_failure
        saved = self._ports.checkpoint.append_apply_recovery_entry(record)
        self._ports.append_audit({"event": "apply_recovery_updated", "recovery": saved})
        return saved

    def _resolve_apply_recoveries_for_checkpoint(
        self,
        checkpoint_id: str,
        *,
        resolution: str,
        restore_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not checkpoint_id:
            return []
        resolved: list[dict[str, Any]] = []
        for recovery in self._ports.checkpoint.active_apply_recoveries():
            if str(recovery.get("checkpointId") or "") != checkpoint_id:
                continue
            resolved.append(
                self._finish_apply_recovery(
                    recovery,
                    status="restored",
                    resolution=resolution,
                    result_summary=summarize_params(restore_payload or {}),
                )
            )
        return resolved

    def visible_write_targets(
        self,
        config: AgentGatewayConfig | None = None,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> list[dict[str, Any]]:
        config = config or self._ports.ensure_config()
        exposure_layer = normalize_exposure_layer(exposure_layer)
        return [
            {
                "name": handler.name,
                "description": tool_usage_description(handler.name, handler.description, write=True),
                "riskLevel": handler.risk_level,
                "advanced": handler.advanced,
                "rollbackPolicy": self._write_handler_rollback_policy(handler),
            }
            for handler in self._ports.state.write_handlers.values()
            if self._ports.write_handler_visible(handler, config, exposure_layer)
            and handler.name not in WRAPPER_ONLY_WRITE_TARGETS
        ]

    def _write_handler_rollback_policy(self, handler: AgentWriteHandler) -> dict[str, Any]:
        if handler.name in {"vrcforge_create_project", "vrcforge_register_project"}:
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "handler_managed_atomic_receipt",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "vrcforge_rollback_project_lifecycle",
                "coverageAudit": "vrcforge.project_lifecycle_receipt.v1",
                "postRestoreValidationRequired": True,
                "note": (
                    "This handler writes only an absent project path or the VRCForge project "
                    "catalogue, publishes atomically, and returns a bound rollback receipt."
                ),
            }
        if handler.name == "vrcforge_register_project_catalog":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "manager_catalog_snapshot_receipt",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "vrcforge_rollback_project_catalog_registration",
                "coverageAudit": "vrcforge.project_catalog_registration_receipt.v1",
                "postRestoreValidationRequired": True,
                "note": "The exact VCC, ALCOM, or Unity Hub catalogue bytes are receipt-bound before registration.",
            }
        if handler.name == "vrcforge_install_unity_core":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "retained_core_tree_receipt",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "vrcforge_restore_unity_core",
                "coverageAudit": "vrcforge.unity_core_install.v1",
                "postRestoreValidationRequired": True,
                "note": (
                    "The previous Core tree is retained under the exact project backup root. "
                    "Restore is a separate receipt-bound high-risk write and is never automatic."
                ),
            }
        if handler.name == "vrcforge_restore_unity_core":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": False,
                "kind": "explicit_receipt_bound_rollback",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "",
                "coverageAudit": "vrcforge.unity_core_restore.v1",
                "postRestoreValidationRequired": True,
                "note": (
                    "This is itself an explicitly approved Core restore. The tool retains a pre-restore safety copy; "
                    "no automatic user-level rollback is advertised."
                ),
            }
        if handler.name == "vrcforge_select_project":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "active_project_selection",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "vrcforge_select_project",
                "coverageAudit": "vrcforge.project_select_result.v1",
                "postRestoreValidationRequired": True,
                "note": "The result returns previousProjectPath; reselect that validated project to reverse the selection.",
            }
        if handler.name in {
            "vrcforge_gesture_manager_enter_play_mode",
            "vrcforge_gesture_manager_set_parameter",
            "vrcforge_select_scene_object",
            "vrcforge_set_play_mode",
        }:
            restore_tool = (
                "vrcforge_set_play_mode"
                if handler.name == "vrcforge_gesture_manager_enter_play_mode"
                else handler.name
            )
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "ephemeral_editor_state_inverse",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": restore_tool,
                "coverageAudit": "vrcforge.ephemeral_editor_state_result.v1",
                "postRestoreValidationRequired": True,
                "note": (
                    "This operation changes only transient Unity Editor or Play Mode state. "
                    "Its result reports the observed state, and the declared atomic restore tool can explicitly "
                    "restore the prior value, selection, or Play Mode state; no project checkpoint or automatic "
                    "rollback is claimed."
                ),
            }
        if handler.name == "vrcforge_confirm_unity_reload_dialog":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": False,
                "kind": "irreversible_ephemeral_editor_reload",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "",
                "coverageAudit": "vrcforge.unity_reload_confirmation.v1",
                "postRestoreValidationRequired": False,
                "note": (
                    "This project-, process-, dialog-, and button-bound action confirms only the exact "
                    "Unity Editor Reload dialog. It does not claim an asset checkpoint or rollback; "
                    "unsaved in-memory scene or Editor changes may be discarded and cannot be restored."
                ),
            }
        if handler.name in {
            "vrcforge_capture_screenshot",
            "vrcforge_capture_multi_screenshot",
        }:
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": False,
                "kind": "local_artifact_overwrite",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "",
                "coverageAudit": "vrcforge.visual_capture_artifact.v1",
                "artifactRoots": ["dashboard/latest"],
                "postRestoreValidationRequired": False,
                "note": (
                    "This operation overwrites only VRCForge-managed dashboard screenshot PNG artifacts under "
                    "dashboard/latest. It does not mutate Assets, Packages, or ProjectSettings and does not claim "
                    "a Unity-project checkpoint or rollback."
                ),
            }
        if handler.name in {
            "vrcforge_rollback_project_lifecycle",
            "vrcforge_rollback_project_catalog_registration",
        }:
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": False,
                "kind": "explicit_receipt_bound_rollback",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "",
                "coverageAudit": (
                    "vrcforge.project_lifecycle_rollback_result.v1"
                    if handler.name == "vrcforge_rollback_project_lifecycle"
                    else "vrcforge.project_catalog_registration_rollback_result.v1"
                ),
                "postRestoreValidationRequired": True,
                "note": (
                    "This is itself an explicitly approved rollback. Created projects are moved to a visible recovery directory; "
                    "no automatic inverse rollback is advertised."
                ),
            }
        if not handler.pre_write_checkpoint_required:
            raise ValueError(f"Write handler {handler.name!r} has no truthful rollback policy.")
        if handler.name == "vrcforge_restore_checkpoint":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "checkpoint_restore",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [*UNITY_PROJECT_CHECKPOINT_SCOPE, *LOCAL_STATE_CHECKPOINT_SCOPE, PROJECT_CHAT_CHECKPOINT_MEMBER],
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "postRestoreValidationRequired": True,
                "note": "Restores a previously captured Unity project or VRCForge local-state checkpoint.",
            }
        if handler.name == "vrcforge_resolve_interrupted_apply_recovery":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "interrupted_apply_recovery_resolution",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "recoveryLedger": APPLY_RECOVERY_SCHEMA,
                "postRestoreValidationRequired": False,
                "note": "Marks a persisted interrupted-write recovery as manually resolved after the user confirms the Unity project state was handled.",
            }
        if handler.name in LOCAL_STATE_CHECKPOINT_TARGETS:
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "local_state_archive",
                "approvalRequired": True,
                "preWriteCheckpointRequired": True,
                "checkpointScope": list(LOCAL_STATE_CHECKPOINT_SCOPE),
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "stateRoots": ["VRCForge skill package store", "projected user skills"],
                "postRestoreValidationRequired": True,
                "note": "Community skill package writes are checkpointed as VRCForge local app state before mutation.",
            }
        if handler.name == PROJECT_CHAT_CHECKPOINT_TARGET:
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "project_chat_archive",
                "approvalRequired": True,
                "preWriteCheckpointRequired": True,
                "checkpointScope": [PROJECT_CHAT_CHECKPOINT_MEMBER],
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "postRestoreValidationRequired": True,
                "note": "Project chat repair snapshots only the exact transcript file and never adds hidden chat data to Git.",
            }
        return {
            "schema": ROLLBACK_POLICY_SCHEMA,
            "required": True,
            "kind": "unity_project_checkpoint",
            "approvalRequired": True,
            "preWriteCheckpointRequired": True,
            "checkpointScope": list(UNITY_PROJECT_CHECKPOINT_SCOPE),
            "restoreTool": "vrcforge_restore_checkpoint",
            "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "postRestoreValidationRequired": True,
            "generatedResidueAuditRequired": True,
            "ecosystemCoverageRequired": ["Modular Avatar", "VRCFury", "NDMF", "MA2BT-Pro"],
            "note": "Every Unity project write must be restorable through the approval-time checkpoint boundary.",
        }

    def _inject_user_constraints_for_apply(
        self,
        params: dict[str, Any],
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        if not snapshot.content:
            return dict(params)
        return self._ports.with_user_constraints(params, snapshot, include_content=False, append_instruction=False)

    def _write_auto_manual_approval_reason(self, target_tool: str, arguments: dict[str, Any], preview: Any = None) -> str:
        target_lower = str(target_tool or "").lower()

        def is_read_source_path(key_lower: str) -> bool:
            if target_lower == "vrcforge_import_outfit_package":
                leaf = key_lower.split(".")[-1].replace("_", "")
                return (
                    leaf in {"packagepath", "unitypackagepath", "actualpackagepath"}
                    or ".source." in key_lower
                    or ".queue." in key_lower
                    or ".materializations." in key_lower
                )
            if target_lower == "vrcforge_install_vpm_package":
                sealed_cli_identity_path = (
                    f"{PREPARED_UNITY_EXECUTION_ARGUMENT_KEY}."
                    f"{PREPARED_EVIDENCE_KEY}.binary.identity.path"
                ).lower()
                # The fixed CLI executable is a hash-bound read source created
                # by the trusted VPM preparer. It is not a project write target.
                return key_lower == sealed_cli_identity_path
            return False

        if target_lower == "vrcforge_export_vrm":
            return "VRM export requires manual confirmation of content rights in Auto Approve mode."
        if any(token in target_lower for token in AUTO_APPROVAL_MANUAL_WRITE_TOKENS):
            return "Delete, remove, restore, reset, or uninstall write requests require manual approval in Auto Approve mode."

        for key, value in iter_param_leaf_values(arguments):
            key_lower = key.lower()
            text_value = str(value or "").strip()
            value_lower = text_value.lower()
            if any(token in key_lower for token in AUTO_APPROVAL_MANUAL_WRITE_TOKENS) and value not in {False, None, "", 0, "false", "False"}:
                return "Delete, remove, restore, reset, or uninstall write requests require manual approval in Auto Approve mode."
            if key_lower.split(".")[-1] in {"action", "operation", "mode"} and value_lower in AUTO_APPROVAL_MANUAL_WRITE_TOKENS:
                return "Delete, remove, restore, reset, or uninstall write requests require manual approval in Auto Approve mode."

        project_root = extract_project_root(arguments)
        if project_root:
            for key, value in iter_param_leaf_values(arguments):
                key_lower = key.lower()
                if not any(marker in key_lower for marker in WRITE_PATH_KEY_MARKERS):
                    continue
                if (
                    target_lower == "vrcforge_create_project"
                    and key_lower.split(".")[-1].replace("_", "") == "templatepath"
                ):
                    # The explicit template is a frozen read source. Only the
                    # absent projectPath is a write target for project creation.
                    continue
                if key_lower.endswith("projectroot") or key_lower.endswith("project_root") or key_lower.endswith("projectpath"):
                    continue
                if is_read_source_path(key_lower):
                    continue
                text_value = str(value or "").strip()
                if looks_like_absolute_path(text_value) and not is_path_within(Path(text_value), project_root):
                    return "Write requests that reference paths outside the selected project require manual approval in Auto Approve mode."

        if isinstance(preview, dict):
            preview_root = project_root or extract_project_root(preview)
            if preview_root:
                for key, value in iter_param_leaf_values(preview):
                    key_lower = key.lower()
                    if not any(marker in key_lower for marker in WRITE_PATH_KEY_MARKERS):
                        continue
                    if (
                        target_lower == "vrcforge_create_project"
                        and key_lower.split(".")[-1].replace("_", "") == "templatepath"
                    ):
                        continue
                    if is_read_source_path(key_lower):
                        continue
                    text_value = str(value or "").strip()
                    if looks_like_absolute_path(text_value) and not is_path_within(Path(text_value), preview_root):
                        return "Write requests that reference paths outside the selected project require manual approval in Auto Approve mode."
        return ""

    def _new_approval(
        self,
        agent_name: str,
        target_tool: str,
        arguments: dict[str, Any],
        reason: str,
        preview: Any,
        risk_level: str,
        user_constraints: UserConstraintsSnapshot | None = None,
        requires_explicit_approval: bool = False,
        explicit_approval_reason: str = "",
        goal_delivery_id: str = "",
        approved_execution_plan: dict[str, Any] | None = None,
        allow_future_eligible: bool = False,
        task_context: Mapping[str, Any] | None = None,
        initial_status: str = "pending",
        approval_channel: str = "internal",
    ) -> dict[str, Any]:
        normalized_status = str(initial_status or "pending").strip().casefold()
        if normalized_status not in {"pending", "approved"}:
            raise ValueError("initial approval status must be pending or approved")
        normalized_channel = str(approval_channel or "internal").strip().casefold()
        external_mcp = normalized_channel == "external_mcp"
        self._ports.signal_background_activity(
            "external_mcp_write" if external_mcp else "pending_approval"
        )
        now = datetime.now(timezone.utc)
        permission_context = self.permission_audit_context()
        approval = {
            "id": f"appr_{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}",
            "createdAt": now.isoformat(),
            "agentName": agent_name,
            "targetTool": target_tool,
            "reason": reason,
            "riskLevel": risk_level,
            "status": normalized_status,
            "arguments": arguments,
            "paramsSummary": summarize_params(arguments),
            "preview": preview if preview is not None else summarize_params(arguments),
        }
        if external_mcp:
            approval["approvalChannel"] = "external_mcp"
        else:
            approval.update(
                {
                    "permissionMode": permission_context["permissionMode"],
                    "fullPermission": permission_context["fullPermission"],
                    "permissionLabel": permission_context["permissionLabel"],
                }
            )
        if normalized_status == "approved":
            approval["approvedAt"] = now.isoformat()
        if requires_explicit_approval:
            approval["requiresExplicitApproval"] = True
            approval["autoApprovalBlocked"] = True
            approval["explicitApprovalReason"] = explicit_approval_reason or "This write request requires explicit user approval."
        if goal_delivery_id:
            approval["goalDeliveryId"] = goal_delivery_id
        if approved_execution_plan is not None:
            approval["approvedUnityExecutionPlan"] = approved_execution_plan
        if isinstance(task_context, Mapping) and task_context:
            approval["taskContext"] = dict(task_context)
        project_root = self._approval_project_root(approval)
        if project_root:
            approval["projectRoot"] = project_root
        if allow_future_eligible and project_root:
            approval["allowFutureEligible"] = True
        if user_constraints and user_constraints.content:
            approval["userConstraintsApplied"] = True
            approval["userConstraintsPath"] = str(user_constraints.path)
        with self._ports.state.shared_state_lock:
            self._ports.state.approvals[approval["id"]] = approval
            try:
                self._persist_pending_approvals_locked()
            except (OSError, UnicodeError, TypeError, ValueError) as exc:
                self._ports.state.approvals.pop(approval["id"], None)
                raise AgentGatewayError(
                    "Pending approval could not be persisted safely.",
                    status_code=500,
                ) from exc
            audit = {
                "event": (
                    "external_mcp_write_authorized"
                    if external_mcp
                    else "approval_requested"
                ),
                "approval": approval,
            }
            if not external_mcp:
                audit.update(permission_context)
            self._ports.append_audit(audit)
        return redact_sensitive(dict(approval))

    def prepare_external_mcp_write(
        self,
        target_tool: str,
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Prepare one real MCP write under the user's selected permission policy."""

        config = self._ports.ensure_config()
        if not config.allow_write_requests:
            raise AgentGatewayError("Agent Gateway write requests are disabled.", status_code=403)

        normalized_target = str(target_tool or "").strip()
        if not normalized_target:
            raise AgentGatewayError("A write target is required.")
        write_handler = self._ports.state.write_handlers.get(normalized_target)
        if write_handler is None:
            raise AgentGatewayError(
                f"Unknown or unavailable write target: {normalized_target}",
                status_code=404,
            )
        if (
            normalized_target in WRAPPER_ONLY_WRITE_TARGETS
            and not external_mcp_typed_wrapper_allowed(write_handler)
        ):
            raise AgentGatewayError(
                f"{normalized_target} is not exposed as an external MCP write tool.",
                status_code=403,
            )

        arguments = ensure_dict(params or {})
        caller_requested_preview = arguments.get("preview") is True
        user_constraints = self._ports.read_user_constraints()
        arguments = self._inject_user_constraints_for_apply(arguments, user_constraints)
        preview: Any = None
        if write_handler.request_preparer is not None:
            try:
                prepared_arguments, preview = write_handler.request_preparer(dict(arguments), None)
            except AgentGatewayError:
                raise
            except Exception as exc:  # noqa: BLE001 - authoritative preparation fails closed.
                detail = str(exc).strip()
                suffix = f" Reason: {detail}" if detail else ""
                raise AgentGatewayError(
                    f"Could not prepare the external MCP write for {normalized_target}.{suffix}",
                    status_code=500,
                ) from exc
            if not isinstance(prepared_arguments, dict):
                raise AgentGatewayError(
                    f"Write request preparation returned invalid arguments for {normalized_target}.",
                    status_code=500,
                )
            arguments = prepared_arguments

        authoritative_preview_only = bool(
            caller_requested_preview
            and write_handler.request_preparer is not None
            and preview is not None
        )

        mandatory_confirmation_reason = ""
        if write_handler.manual_approval_resolver is not None:
            try:
                mandatory_confirmation_reason = str(
                    write_handler.manual_approval_resolver(dict(arguments), preview) or ""
                ).strip()
            except Exception as exc:  # noqa: BLE001 - safety policy failures fail closed.
                raise AgentGatewayError(
                    f"Could not determine the external confirmation policy for {normalized_target}.",
                    status_code=500,
                ) from exc

        base_risk_level = normalize_risk_level(write_handler.risk_level)
        effective_risk_level = base_risk_level
        if write_handler.risk_level_resolver is not None:
            try:
                resolved_risk_level = str(
                    write_handler.risk_level_resolver(dict(arguments)) or ""
                ).strip().lower()
            except Exception as exc:  # noqa: BLE001 - safety classification failures fail closed.
                raise AgentGatewayError(
                    f"Could not determine write risk for {normalized_target}: {exc}",
                    status_code=500,
                ) from exc
            if resolved_risk_level not in {"low", "medium", "high", "critical"}:
                raise AgentGatewayError(
                    f"Write risk resolver returned an invalid level for {normalized_target}.",
                    status_code=500,
                )
            risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            if risk_rank[resolved_risk_level] > risk_rank[base_risk_level]:
                effective_risk_level = resolved_risk_level

        destructive_reason = self._write_auto_manual_approval_reason(
            normalized_target,
            arguments,
            preview,
        )
        full_permission = normalize_execution_mode(config.execution_mode) == "roslyn_full_auto"
        confirmation_reason = str(
            CHECKPOINT_RESTORE_MANUAL_APPROVAL_REASON
            if normalized_target == "vrcforge_restore_checkpoint"
            else ROLLBACK_MANUAL_APPROVAL_REASON
            if normalized_target in ROLLBACK_MANUAL_APPROVAL_TOOLS
            else AVATAR_UPLOAD_MANUAL_APPROVAL_REASON
            if normalized_target == "vrcforge_build_and_upload_avatar" and not full_permission
            else (
                mandatory_confirmation_reason
                or destructive_reason
                or "This external MCP tool is declared high risk and requires user confirmation."
            )
            if effective_risk_level in {"high", "critical"} and not full_permission
            else ""
        ).strip()
        if authoritative_preview_only:
            confirmation_reason = ""

        approved_execution_plan: dict[str, Any] | None = None
        if (
            write_handler.requires_approved_execution_context
            and not authoritative_preview_only
        ):
            try:
                approved_execution_plan = self._build_external_mcp_execution_plan(
                    write_handler,
                    arguments,
                )
            except AgentGatewayError:
                raise
            except Exception as exc:  # noqa: BLE001 - malformed plans fail before any write.
                raise AgentGatewayError(
                    f"Could not freeze the exact Core execution plan for {normalized_target}.",
                    status_code=409,
                ) from exc

        arguments_digest = stable_hash(
            json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        )
        return {
            "targetTool": normalized_target,
            "arguments": arguments,
            "argumentsDigest": arguments_digest,
            "preview": preview if preview is not None else summarize_params(arguments),
            "riskLevel": effective_risk_level,
            "requiresUserConfirmation": bool(confirmation_reason),
            "confirmationReason": confirmation_reason,
            "approvedUnityExecutionPlan": approved_execution_plan,
            "authoritativePreviewOnly": authoritative_preview_only,
            "userConstraints": user_constraints,
        }

    def _build_external_mcp_execution_plan(
        self,
        write_handler: AgentWriteHandler,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Freeze the exact Core calls for the external handler invocation.

        Internal approved-write builders intentionally canonicalize their plan
        to the apply form (``preview=false``). External MCP may legitimately
        invoke the same atomic tool with ``preview=true``. Preserve that
        caller-selected no-write form without widening any other Core argument.
        """

        if write_handler.approved_execution_plan_builder is None:
            raise AgentGatewayError(
                f"Unity write target is not yet bound to an exact Core execution plan: {write_handler.name}",
                status_code=409,
                cause_code="external_mcp_execution_plan_missing",
                failure_layer="external_mcp_write_preparation",
                failure_phase="execution_plan_binding",
                operation_kind="write",
                tool=write_handler.name,
                tool_routing_started=False,
                mutation_started=False,
                committed=False,
                commit_state="not_started",
            )
        planned_calls = list(
            write_handler.approved_execution_plan_builder(dict(arguments))
        )
        if arguments.get("preview") is True:
            adjusted_calls: list[tuple[str, dict[str, Any]]] = []
            for tool_name, core_arguments in planned_calls:
                adjusted_arguments = dict(core_arguments)
                if "preview" in adjusted_arguments:
                    adjusted_arguments["preview"] = True
                adjusted_calls.append((tool_name, adjusted_arguments))
            planned_calls = adjusted_calls
        return freeze_approved_unity_execution_plan(planned_calls)

    def _call_external_mcp_write_handler(
        self,
        write_handler: AgentWriteHandler,
        target_tool: str,
        operation_id: str,
        arguments: dict[str, Any],
        handler_arguments_digest: str,
        frozen_execution_plan: Mapping[str, Any],
    ) -> Any:
        """Run one external handler with only the shared exact Core authority."""

        handler_arguments = dict(arguments)
        handler_arguments.pop("_vrcforge_approved_execution", None)
        if not write_handler.requires_approved_execution_context:
            return write_handler.handler(handler_arguments)

        project_root = extract_project_root(handler_arguments)
        if project_root is None or not project_root.is_dir():
            raise AgentGatewayError(
                "The external Unity write is missing its exact project binding. Pass the exact existing Unity project root as arguments.projectPath on this tool call.",
                status_code=409,
                cause_code="external_mcp_project_binding_missing",
                failure_layer="external_mcp_project_binding",
                failure_phase="before_unity_core_call",
                operation_kind="write",
                tool=target_tool,
                tool_routing_started=False,
                mutation_started=False,
                committed=False,
                commit_state="not_started",
                details={
                    "requiredArgument": "projectPath",
                    "acceptedAliases": ["projectPath", "projectRoot", "project_path", "project_root"],
                    "selectedProjectIsNotAuthority": True,
                },
            )
        try:
            persisted_plan = validate_frozen_approved_unity_execution_plan(
                frozen_execution_plan
            )
            recomputed_plan_json = self._build_external_mcp_execution_plan(
                write_handler,
                handler_arguments,
            )
            recomputed_plan = validate_frozen_approved_unity_execution_plan(
                recomputed_plan_json
            )
        except AgentGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001 - plan loss/drift fails before Core mutation.
            raise AgentGatewayError(
                "The external Unity execution plan is unavailable or invalid.",
                status_code=409,
                cause_code="external_mcp_execution_plan_invalid",
                failure_layer="external_mcp_execution_plan",
                failure_phase="before_unity_core_call",
                operation_kind="write",
                tool=target_tool,
                tool_routing_started=False,
                mutation_started=False,
                committed=False,
                commit_state="not_started",
            ) from exc
        if persisted_plan.plan_digest != recomputed_plan.plan_digest:
            raise AgentGatewayError(
                "The external Unity execution plan drifted after preparation.",
                status_code=409,
                cause_code="external_mcp_execution_plan_drifted",
                failure_layer="external_mcp_execution_plan",
                failure_phase="before_unity_core_call",
                operation_kind="write",
                tool=target_tool,
                tool_routing_started=False,
                mutation_started=False,
                committed=False,
                commit_state="not_started",
            )

        now_ms = int(time.time() * 1000)
        execution_plan = create_approved_unity_execution_plan(
            {
                "lane": "external_mcp_write",
                "operationId": operation_id,
                "targetTool": target_tool,
                "projectRoot": str(project_root),
                "handlerArgumentsSha256": handler_arguments_digest,
                "issuedAtUnixMs": now_ms,
                "expiresAtUnixMs": now_ms + 60_000,
            },
            frozen_execution_plan,
        )
        result: Any = None
        try:
            with bind_approved_unity_execution(execution_plan):
                result = write_handler.handler(handler_arguments)
        finally:
            if not execution_plan.consumed:
                execution_plan.burn()
        if (
            not execution_plan.consumed
            and not (isinstance(result, dict) and result.get("ok") is False)
        ):
            raise AgentGatewayError(
                "The external Unity execution plan was not consumed exactly.",
                status_code=409,
            )
        return result

    def execute_prepared_external_mcp_write(
        self,
        prepared: Mapping[str, Any],
        *,
        agent_name: str = "mcp-agent",
    ) -> dict[str, Any]:
        """Execute one prepared MCP operation without the internal Agent loop."""

        target_tool = str(prepared.get("targetTool") or "").strip()
        arguments = ensure_dict(prepared.get("arguments"))
        if not target_tool or target_tool not in self._ports.state.write_handlers:
            raise AgentGatewayError("The prepared external MCP write target is unavailable.", status_code=404)
        expected_digest = str(prepared.get("argumentsDigest") or "").strip()
        actual_digest = stable_hash(
            json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        )
        if not expected_digest or not hmac.compare_digest(expected_digest, actual_digest):
            raise AgentGatewayError(
                "The prepared external MCP write arguments no longer match their binding.",
                status_code=409,
            )

        if prepared.get("authoritativePreviewOnly") is True:
            preview_value = prepared.get("preview")
            preview_result = (
                dict(preview_value)
                if isinstance(preview_value, Mapping)
                else {"data": preview_value}
            )
            preview_result.setdefault("ok", True)
            preview_result.setdefault("preview", True)
            self._ports.append_audit(
                {
                    "event": "external_mcp_preview_completed",
                    "targetTool": target_tool,
                    "agent": str(agent_name or "mcp-agent")[:120],
                    "projectRoot": str(extract_project_root(arguments) or ""),
                    "argumentsDigest": actual_digest,
                }
            )
            return {
                "ok": True,
                "status": "preview",
                "result": redact_sensitive(preview_result),
            }

        write_handler = self._ports.state.write_handlers[target_tool]
        frozen_execution_plan = ensure_dict(prepared.get("approvedUnityExecutionPlan"))
        operation_id = (
            f"mcpwrite_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_"
            f"{secrets.token_hex(4)}"
        )
        self._ports.signal_background_activity("external_mcp_write")
        project_root = extract_project_root(arguments)
        project_lock_key = ""
        project_lock_acquired = False

        with self._ports.state.shared_state_lock:
            if self._project_has_background_read(project_root):
                raise AgentGatewayError(
                    "A background project read is active. Retry this external write after it finishes.",
                    status_code=409,
                )
            if self._project_has_in_flight_write(project_root):
                raise AgentGatewayError(
                    "Another Unity write is active. Retry this external write after it finishes.",
                    status_code=409,
                )
            project_lock_key, project_lock_acquired = self._try_acquire_project_write(project_root)
            if not project_lock_acquired:
                raise AgentGatewayError(
                    "Another Unity write is active for this project. Retry after it finishes.",
                    status_code=409,
                )
            self._ports.state.in_flight_apply_writes[operation_id] = {
                "operationId": operation_id,
                "targetTool": target_tool,
                "projectRoot": str(project_root or ""),
                "projectLockKey": project_lock_key,
                "startedAt": utc_now_iso(),
                "source": "external_mcp",
            }

        result: Any = None
        source_tool_result: Any = None
        verification_baseline: dict[str, Any] = {}
        completion_outcome: dict[str, Any] = {}
        request_trace: dict[str, Any] | None = None
        core_call_audits: list[dict[str, Any]] = []
        failure_layer = "external_mcp_transaction"
        handler_started = False
        checkpoint: dict[str, Any] | None = None
        try:
            if (
                write_handler.requires_approved_execution_context
                and write_handler.pre_write_checkpoint_required
                and arguments.get("preview") is not True
                and (extract_project_root(arguments) is not None)
                and extract_project_root(arguments).is_dir()
            ):
                failure_layer = "checkpoint"
                checkpoint = self._create_pre_write_checkpoint(
                    {
                        "id": operation_id,
                        "targetTool": target_tool,
                        "agentName": str(agent_name or "mcp-agent")[:120],
                        "arguments": arguments,
                    },
                    arguments,
                )
                if not checkpoint or checkpoint.get("ok") is not True:
                    raise AgentGatewayError(
                        str((checkpoint or {}).get("error") or "Pre-write checkpoint failed."),
                        status_code=409,
                    )
            if write_handler.verification_prepare_handler is not None:
                failure_layer = "completion_verification_baseline"
                verification_arguments = dict(arguments)
                verification_arguments.pop("_vrcforge_approved_execution", None)
                verification_baseline = ensure_dict(
                    write_handler.verification_prepare_handler(verification_arguments)
                )

            failure_layer = "external_mcp_write_execution"
            handler_started = True
            with capture_unity_mcp_core_call_audits() as core_call_audits:
                result = self._call_external_mcp_write_handler(
                    write_handler,
                    target_tool,
                    operation_id,
                    arguments,
                    actual_digest,
                    frozen_execution_plan,
                )
            source_tool_result = result
            if write_handler.verification_finalize_handler is not None:
                failure_layer = "completion_verification_finalize"
                verification_arguments = dict(arguments)
                verification_arguments.pop("_vrcforge_approved_execution", None)
                result = write_handler.verification_finalize_handler(
                    verification_arguments,
                    dict(verification_baseline),
                    result,
                )
            if core_call_audits:
                request_trace = {
                    "operationId": operation_id,
                    "targetTool": target_tool,
                    "unityCoreCallAudits": [dict(audit) for audit in core_call_audits],
                }
            if isinstance(result, dict) and result.get("ok") is False:
                failure_layer = str(result.get("failureLayer") or "write_handler")[:80]
                message = (
                    result.get("error")
                    or result.get("message")
                    or result.get("reason")
                    or f"{target_tool} returned ok=false."
                )
                raise AgentGatewayError(str(message))

            completion_outcome = ensure_dict(
                redact_sensitive(
                    normalize_agent_tool_result(
                        result,
                        fallback_summary=write_handler.description,
                        write=True,
                    )
                )
            )
            if str(completion_outcome.get("status") or "").casefold() == "failed":
                failure_layer = "result_verification"
                raise AgentGatewayError(str(completion_outcome.get("summary") or "Write failed."))

            payload: dict[str, Any] = {
                "ok": True,
                "status": (
                    "needs_user_action"
                    if completion_outcome.get("status") == "needs_user_action"
                    else "applied"
                ),
                "result": redact_sensitive(source_tool_result),
                "outcome": completion_outcome,
            }
            if isinstance(result, Mapping) and isinstance(
                result.get("consoleVerification"), Mapping
            ):
                payload["consoleVerification"] = redact_sensitive(
                    dict(result["consoleVerification"])
                )
            if request_trace is not None:
                payload["requestTrace"] = request_trace
            if checkpoint is not None:
                payload["checkpoint"] = checkpoint
            self._ports.append_audit(
                {
                    "event": "external_mcp_write_completed",
                    "operationId": operation_id,
                    "targetTool": target_tool,
                    "agent": str(agent_name or "mcp-agent")[:120],
                    "projectRoot": str(project_root or ""),
                    "argumentsDigest": actual_digest,
                    "resultSummary": summarize_params(
                        result if isinstance(result, dict) else {"result": result}
                    ),
                    **({"requestTrace": request_trace} if request_trace is not None else {}),
                }
            )
            return payload
        except Exception as exc:  # noqa: BLE001 - return facts; the external Agent owns replanning.
            if core_call_audits and request_trace is None:
                request_trace = {
                    "operationId": operation_id,
                    "targetTool": target_tool,
                    "unityCoreCallAudits": [dict(audit) for audit in core_call_audits],
                }
            exception_text = str(exc)
            legacy_exception_details = external_exception_details(exc)
            exception_raw_result = external_exception_raw_result(legacy_exception_details)
            if isinstance(source_tool_result, Mapping):
                # Preserve the Unity Core result as the external Agent's
                # authoritative failure object.  The Gateway may derive an
                # adjacent writeFailure/Console envelope, but must never
                # rewrite the lower-level code, reason, or payload.
                source_failure_result: dict[str, Any] = dict(source_tool_result)
                failure_result: dict[str, Any] = (
                    dict(result) if isinstance(result, Mapping) else dict(source_failure_result)
                )
                failure_result.setdefault("ok", False)
                failure_result.setdefault("status", "failed")
                failure_result.setdefault("error", exception_text)
                if handler_started and not any(
                    key in failure_result
                    for key in ("commitState", "committed", "mutationStarted")
                ):
                    failure_result["commitState"] = "unknown"
            elif isinstance(exception_raw_result, Mapping):
                source_failure_result = dict(exception_raw_result)
                failure_result = dict(source_failure_result)
                failure_result.setdefault("ok", False)
                failure_result.setdefault("status", "failed")
                failure_result.setdefault("error", exception_text)
                if handler_started and not any(
                    key in failure_result
                    for key in ("commitState", "committed", "mutationStarted")
                ):
                    failure_result["commitState"] = "unknown"
            elif isinstance(result, Mapping):
                source_failure_result = dict(result)
                failure_result = dict(source_failure_result)
                failure_result.setdefault("ok", False)
                failure_result.setdefault("status", "failed")
                failure_result.setdefault("error", exception_text)
                if handler_started and not any(
                    key in failure_result
                    for key in ("commitState", "committed", "mutationStarted")
                ):
                    failure_result["commitState"] = "unknown"
            else:
                failure_result = {
                    "ok": False,
                    "status": "failed",
                    "error": exception_text,
                    "mutationStarted": False if not handler_started else None,
                    "committed": False if not handler_started else None,
                    "commitState": "not_started" if not handler_started else "unknown",
                    "checkpointRecoveryRequired": False,
                }
                source_failure_result = dict(failure_result)
            if (
                write_handler.verification_finalize_handler is not None
                and verification_baseline
                and not isinstance(failure_result.get("consoleVerification"), Mapping)
            ):
                verification_arguments = dict(arguments)
                verification_arguments.pop("_vrcforge_approved_execution", None)
                try:
                    failure_result = ensure_dict(
                        write_handler.verification_finalize_handler(
                            verification_arguments,
                            dict(verification_baseline),
                            failure_result,
                        )
                    )
                except Exception as verification_exc:  # noqa: BLE001 - preserve the primary write failure.
                    failure_result["consoleVerification"] = {
                        "schema": "vrcforge.unity_console_verification.v1",
                        "status": "failed",
                        "code": "unity_console_after_capture_failed",
                        "summary": str(verification_exc)[:400],
                    }
            console_after = failure_result.get("consoleVerification")
            error_constructor_args: dict[str, Any] = {
                "error": failure_result.get("error") or exception_text,
                "failure_layer": failure_layer,
                "failure_phase": "before_write_handler" if not handler_started else "",
                "operation_kind": "write",
                "tool": target_tool,
                "tool_routing_started": False if not handler_started else None,
                "raw_result": source_failure_result,
                "exception": exc,
                "console_before": verification_baseline,
                "console_after": (
                    console_after if isinstance(console_after, Mapping) else {}
                ),
            }
            if not handler_started:
                error_constructor_args.update(
                    {
                        "mutation_started": False,
                        "committed": False,
                        "commit_state": "not_started",
                        "checkpoint_recovery_required": False,
                        "temporary_cleanup_required": False,
                    }
                )
            error_object = build_external_tool_error(**error_constructor_args)
            write_failure = external_write_failure_view(error_object)
            completion_outcome = ensure_dict(
                redact_sensitive(
                    normalize_agent_tool_result(
                        failure_result,
                        fallback_summary=f"{write_handler.description} failed.",
                        write=True,
                    )
                )
            )
            if str(completion_outcome.get("status") or "").casefold() != "failed":
                completion_outcome = {
                    "status": "failed",
                    "summary": f"{write_handler.description} failed.",
                    "verification": {"state": "failed", "checks": []},
                }
            payload = {
                "ok": False,
                "status": "failed",
                "result": redact_sensitive(source_failure_result),
                "error": str(source_failure_result.get("error") or exception_text),
                "outcome": completion_outcome,
                "writeFailure": write_failure,
                "errorDetails": redact_sensitive(error_object),
            }
            if isinstance(failure_result.get("consoleVerification"), Mapping):
                payload["consoleVerification"] = redact_sensitive(
                    dict(failure_result["consoleVerification"])
                )
            if request_trace is not None:
                payload["requestTrace"] = request_trace
            if checkpoint is not None:
                payload["checkpoint"] = checkpoint
            self._ports.append_audit(
                {
                    "event": "external_mcp_write_failed",
                    "operationId": operation_id,
                    "targetTool": target_tool,
                    "agent": str(agent_name or "mcp-agent")[:120],
                    "projectRoot": str(project_root or ""),
                    "argumentsDigest": actual_digest,
                    "writeFailure": write_failure,
                    **({"requestTrace": request_trace} if request_trace is not None else {}),
                }
            )
            return payload
        finally:
            with self._ports.state.shared_state_lock:
                self._ports.state.in_flight_apply_writes.pop(operation_id, None)
            if project_lock_acquired:
                self._release_project_write(project_lock_key)

    def _approval_project_root(self, approval: dict[str, Any]) -> str:
        arguments = ensure_dict(approval.get("arguments"))
        task_context = ensure_dict(approval.get("taskContext"))
        for key in ("projectRoot", "project_root", "projectPath", "project_path"):
            value = str(
                arguments.get(key)
                or approval.get(key)
                or task_context.get(key)
                or ""
            ).strip()
            if value:
                return value
        return ""

    def _ensure_approval_scope(
        self,
        approval: dict[str, Any],
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> None:
        candidate = self._approval_project_root(approval)
        if global_only and candidate:
            raise AgentGatewayError("Approval does not belong to the current no-project context.", status_code=409)
        expected_key = normalize_filesystem_path(expected_project_root) if expected_project_root else ""
        if expected_key and candidate and normalize_filesystem_path(candidate) != expected_key:
            raise AgentGatewayError("Approval belongs to a different project.", status_code=409)

    def _set_approval_status(
        self,
        approval_id: str,
        status: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        self._ports.signal_background_activity("approval_transition")
        with self._ports.state.shared_state_lock:
            approval = self._ports.state.approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                if self._reconcile_unrecoverable_linked_approval(approval_id):
                    raise AgentGatewayError(
                        "Approval could not be recovered after the runtime restarted; the linked goal now needs review.",
                        status_code=409,
                    )
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
            self._ensure_approval_scope(
                approval,
                expected_project_root=expected_project_root,
                global_only=global_only,
            )
            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") not in {"pending", "approved"} and status == "approved":
                return {"ok": False, "approval": approval, "message": f"Approval is {approval.get('status')}."}
            if approval.get("status") not in {"pending", "approved"} and status == "rejected":
                return {"ok": False, "approval": approval, "message": f"Approval is {approval.get('status')}."}
            if approval.get("status") == "expired":
                return {"ok": False, "approval": approval, "message": "Approval has expired."}
            previous = dict(approval)
            approval["status"] = status
            approval[f"{status}At"] = utc_now_iso()
            self._ports.state.approvals[approval_id] = approval
            try:
                self._persist_pending_approvals_locked()
            except (OSError, UnicodeError, TypeError, ValueError) as exc:
                self._ports.state.approvals[approval_id] = previous
                raise AgentGatewayError(
                    "Approval decision could not be persisted safely.",
                    status_code=500,
                ) from exc
            permission_context = self.permission_audit_context()
            self._ports.append_audit({"event": f"approval_{status}", "approval": approval, **permission_context})
            self._runtime_run_append(
                {
                    "event": f"approval_{status}",
                    "status": status,
                    "approvalId": approval_id,
                    "approvalIds": [approval_id],
                    **permission_context,
                    "targetTool": approval.get("targetTool") or "",
                    "agent": approval.get("agentName") or "",
                    "projectRoot": self._approval_project_root(approval),
                    "messageSummary": summarize_text(str(approval.get("reason") or "")),
                }
            )
            payload: dict[str, Any] = {"ok": True, "approval": redact_sensitive(dict(approval))}
            if status == "rejected" and str(approval.get("goalDeliveryId") or "").strip():
                denied = self._goal.deny_approval(approval_id, reason="approval_denied")
                if denied is not None:
                    payload["goalDelivery"] = denied
            return payload

    def request_approval_revision(
        self,
        approval_id: str,
        *,
        reason: str = "",
        deny_reason_code: str = "",
        note: str = "",
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        self._ports.signal_background_activity("approval_transition")
        with self._ports.state.shared_state_lock:
            approval = self._ports.state.approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
            self._ensure_approval_scope(
                approval,
                expected_project_root=expected_project_root,
                global_only=global_only,
            )
            approval = self._refresh_approval_expiry(approval)
            status = str(approval.get("status") or "")
            if status != "pending":
                return {"ok": False, "approval": redact_sensitive(dict(approval)), "message": f"Approval is {status}."}
            task_context = ensure_dict(approval.get("taskContext"))
            goal_delivery_id = str(approval.get("goalDeliveryId") or "").strip()
            if not goal_delivery_id and task_context.get("schema") != TASK_APPROVAL_CONTEXT_SCHEMA:
                return {
                    "ok": False,
                    "approval": redact_sensitive(dict(approval)),
                    "message": "Request Changes is available only for an approval linked to an active Agent task.",
                }
            if task_context.get("approvalRevisionUsed") is True:
                return {
                    "ok": False,
                    "approval": redact_sensitive(dict(approval)),
                    "message": "This Agent task already used its single approval revision.",
                }
            if not str(reason or "").strip():
                return {
                    "ok": False,
                    "approval": redact_sensitive(dict(approval)),
                    "message": "A change reason is required.",
                }
            if str(approval.get("revisionRequestedAt") or "").strip():
                return {"ok": False, "approval": redact_sensitive(dict(approval)), "message": "Revision already requested for this approval; exactly one revision is allowed."}
            previous = dict(approval)
            approval["status"] = "revision_requested"
            approval["revisionRequestedAt"] = utc_now_iso()
            approval["revisionReason"] = reason.strip()
            approval["denyReasonCode"] = deny_reason_code.strip()
            approval["revisionNote"] = note.strip()
            self._ports.state.approvals[approval_id] = approval
            try:
                self._persist_pending_approvals_locked()
            except (OSError, UnicodeError, TypeError, ValueError) as exc:
                self._ports.state.approvals[approval_id] = previous
                raise AgentGatewayError(
                    "Approval revision could not be persisted safely.",
                    status_code=500,
                ) from exc
            self._ports.append_audit({"event": "approval_revision_requested", "approval": approval})
            self._runtime_run_append(
                {
                    "event": "approval_revision_requested",
                    "status": "revision_requested",
                    "approvalId": approval_id,
                    "approvalIds": [approval_id],
                    "targetTool": approval.get("targetTool") or "",
                    "agent": approval.get("agentName") or "",
                    "projectRoot": self._approval_project_root(approval),
                    "messageSummary": summarize_text(note or reason),
                }
            )
            payload: dict[str, Any] = {"ok": True, "approval": redact_sensitive(dict(approval))}
            if str(approval.get("goalDeliveryId") or "").strip():
                denied = self._goal.deny_approval(approval_id, reason="approval_recovery_required")
                if denied is not None:
                    payload["goalDelivery"] = denied
            return payload

    def _refresh_approval_expiry(self, approval: dict[str, Any]) -> dict[str, Any]:
        if approval.get("status") == "pending" and "expiresAt" in approval:
            # Pending approval is a durable user decision point.  Legacy
            # records may still carry the old ten-minute deadline; remove it
            # instead of silently turning the visible card into an expiry.
            legacy_expiry = approval.get("expiresAt")
            approval.pop("expiresAt", None)
            self._ports.state.approvals[str(approval.get("id"))] = approval
            try:
                self._persist_pending_approvals_locked()
            except (OSError, UnicodeError, TypeError, ValueError) as exc:
                approval["expiresAt"] = legacy_expiry
                self._ports.state.approvals[str(approval.get("id"))] = approval
                raise AgentGatewayError(
                    "Pending approval migration could not be persisted safely.",
                    status_code=500,
                ) from exc
        return approval

    def _load_approval_from_audit(self, approval_id: str) -> dict[str, Any] | None:
        return None

    def _reconcile_unrecoverable_linked_approval(self, approval_id: str) -> bool:
        linked = self._goal.delivery_for_approval(approval_id)
        if linked is None:
            return False
        self._goal.reconcile_missing_approvals(set(self._ports.state.approvals))
        return True
