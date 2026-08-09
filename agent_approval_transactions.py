from __future__ import annotations

import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import AbstractContextManager
from typing import Any, Callable, Mapping

from agent_command_safety import is_path_within, looks_like_absolute_path, normalize_filesystem_path
from agent_tool_result_contract import normalize_agent_tool_result
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
    bind_approved_unity_execution,
    capture_unity_mcp_core_call_audits,
    create_approved_unity_execution_plan,
    ensure_dict,
    ensure_string_list,
    extract_approval_id,
    extract_project_root,
    freeze_approved_unity_execution_plan,
    iter_param_leaf_values,
    normalize_execution_mode,
    normalize_exposure_layer,
    normalize_risk_level,
    parse_iso_datetime,
    redact_sensitive,
    stable_hash,
    summarize_params,
    summarize_text,
    tool_usage_description,
    utc_now_iso,
    validate_frozen_approved_unity_execution_plan,
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
        "_scoped_approval_reviewer",
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
        self._scoped_approval_reviewer: Callable[[dict[str, Any]], str] | None = None

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
        requires_approved_execution_context: bool = False,
        approved_execution_plan_builder: ApprovedUnityExecutionPlanBuilder | None = None,
        approval_category: str = "",
        allow_future_category: bool = False,
    ) -> None:
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
            requires_approved_execution_context=requires_approved_execution_context,
            approved_execution_plan_builder=approved_execution_plan_builder,
            approval_category=str(approval_category or "").strip(),
            allow_future_category=bool(allow_future_category),
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
                    }
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
        if approval:
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
        if approval:
            payload["approval_id"] = approval
            payload["approvalId"] = approval
        if outcome.get("error"):
            payload["error"] = outcome["error"]
        return payload

    def create_apply_request(
        self,
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
        include_arguments_digest: bool = False,
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
        requires_explicit_for_mode = False if full_permission_auto else (
            never_auto_approve
            or requires_explicit_approval
            or (execution_mode == "auto" and bool(auto_policy_reason or risk_escalation_reason))
        )
        explicit_approval_reason = str(
            mandatory_manual_approval_reason
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
        if full_permission_auto and (
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
        if self.auto_approval_enabled(config) and not requires_explicit_for_mode:
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
        if execution_mode == "approval" and not requires_explicit_for_mode:
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
        if bool(approval.get("requiresExplicitApproval")):
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

            if self._ports.state.background_project_read_leases:
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

            in_flight_writes = [dict(entry) for entry in self._ports.state.in_flight_apply_writes.values()]
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

            active_recoveries = self._ports.checkpoint.active_apply_recoveries()
            if active_recoveries and target_tool not in APPLY_RECOVERY_EXEMPT_WRITE_TARGETS:
                self._ports.append_audit(
                    {
                        "event": "approval_blocked_by_interrupted_apply_recovery",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "recoveries": active_recoveries,
                    }
                )
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
                "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
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
            classification = ensure_dict(arguments.get("classification_snapshot"))
            requires_checkpoint = not (
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
                        with capture_unity_mcp_core_call_audits() as core_call_audits:
                            result = self._call_write_handler(
                                write_handler,
                                target_tool,
                                approval_id,
                                checkpoint,
                                arguments,
                                handler_arguments_digest,
                                ensure_dict(approval.get("approvedUnityExecutionPlan")),
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
                with capture_unity_mcp_core_call_audits() as core_call_audits:
                    result = self._call_write_handler(
                        write_handler,
                        target_tool,
                        approval_id,
                        checkpoint,
                        arguments,
                        handler_arguments_digest,
                        ensure_dict(approval.get("approvedUnityExecutionPlan")),
                    )
            if core_call_audits:
                request_trace = {
                    "approvalId": approval_id,
                    "targetTool": target_tool,
                    "executionId": execution_id,
                    "unityCoreCallAudits": [dict(audit) for audit in core_call_audits],
                }
            if isinstance(result, dict) and result.get("ok") is False:
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
            completion_status = str(completion_outcome["status"])
            if completion_status == "failed":
                raise AgentGatewayError(str(completion_outcome["summary"]))
            self._observe_apply_lifecycle(
                "handler_returned", approval, checkpoint=checkpoint, result=result
            )
            with self._ports.state.shared_state_lock:
                approval["status"] = "applied"
                approval["appliedAt"] = utc_now_iso()
                approval["completionOutcome"] = completion_outcome
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
            with self._ports.state.shared_state_lock:
                approval["status"] = "failed"
                approval["failedAt"] = utc_now_iso()
                approval["error"] = str(exc)
                self._ports.state.approvals[approval_id] = approval
                permission_context = self.permission_audit_context()
                failed_audit = {"event": "approval_failed", "approval": approval, **permission_context}
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
                    }
                )
            if recovery:
                self._finish_apply_recovery(
                    recovery,
                    status="not_applied" if no_write_conflict else "needs_recovery",
                    resolution="no_write_snapshot_conflict" if no_write_conflict else "write_failed_after_checkpoint",
                    error=str(exc),
                )
            payload = {"ok": False, "status": "failed", "approval": approval, "error": str(exc)}
            if request_trace is not None:
                payload["requestTrace"] = request_trace
            if checkpoint:
                payload["checkpoint"] = checkpoint
            return self._goal.attach_terminal_resolution(payload, approval)
        finally:
            with self._ports.state.shared_state_lock:
                self._ports.state.in_flight_apply_writes.pop(approval_id, None)

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
    ) -> Any:
        handler_arguments = dict(arguments)
        handler_arguments.pop("_vrcforge_approved_execution", None)
        if not write_handler.requires_approved_execution_context:
            return write_handler.handler(handler_arguments)
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
        if not execution_plan.consumed:
            raise AgentGatewayError(
                "The approved Unity execution plan was not consumed exactly.",
                status_code=409,
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

    def try_acquire_background_project_read(self, token: str) -> bool:
        normalized = str(token or "").strip()
        if not normalized:
            return False
        with self._ports.state.shared_state_lock:
            if self.has_in_flight_project_write() or self._ports.state.background_project_read_leases:
                return False
            self._ports.state.background_project_read_leases.add(normalized)
            return True

    def release_background_project_read(self, token: str) -> bool:
        normalized = str(token or "").strip()
        if not normalized:
            return False
        with self._ports.state.shared_state_lock:
            if normalized not in self._ports.state.background_project_read_leases:
                return False
            self._ports.state.background_project_read_leases.remove(normalized)
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
    ) -> dict[str, Any]:
        text = " ".join([str(error or ""), str(note or ""), str(recovery.get("targetTool") or "")])
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
            "incidentKind": self._ports.checkpoint.classify_apply_recovery_incident(text, str(recovery.get("targetTool") or "")),
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
                if key_lower.endswith("projectroot") or key_lower.endswith("project_root") or key_lower.endswith("projectpath"):
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
    ) -> dict[str, Any]:
        self._ports.signal_background_activity("pending_approval")
        now = datetime.now(timezone.utc)
        config = self._ports.ensure_config()
        permission_context = self.permission_audit_context(config)
        approval = {
            "id": f"appr_{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}",
            "createdAt": now.isoformat(),
            "expiresAt": (now + timedelta(seconds=config.approval_timeout_seconds)).isoformat(),
            "agentName": agent_name,
            "targetTool": target_tool,
            "reason": reason,
            "riskLevel": risk_level,
            "status": "pending",
            "arguments": arguments,
            "paramsSummary": summarize_params(arguments),
            "preview": preview if preview is not None else summarize_params(arguments),
            "permissionMode": permission_context["permissionMode"],
            "fullPermission": permission_context["fullPermission"],
            "permissionLabel": permission_context["permissionLabel"],
        }
        if requires_explicit_approval:
            approval["requiresExplicitApproval"] = True
            approval["autoApprovalBlocked"] = True
            approval["explicitApprovalReason"] = explicit_approval_reason or "This write request requires explicit user approval."
        if goal_delivery_id:
            approval["goalDeliveryId"] = goal_delivery_id
        if approved_execution_plan is not None:
            approval["approvedUnityExecutionPlan"] = approved_execution_plan
        project_root = self._approval_project_root(approval)
        if project_root:
            approval["projectRoot"] = project_root
        if allow_future_eligible:
            approval["allowFutureEligible"] = True
        if user_constraints and user_constraints.content:
            approval["userConstraintsApplied"] = True
            approval["userConstraintsPath"] = str(user_constraints.path)
        with self._ports.state.shared_state_lock:
            self._ports.state.approvals[approval["id"]] = approval
            self._ports.append_audit({"event": "approval_requested", "approval": approval, **permission_context})
        return redact_sensitive(dict(approval))

    def _approval_project_root(self, approval: dict[str, Any]) -> str:
        arguments = ensure_dict(approval.get("arguments"))
        for key in ("projectRoot", "project_root", "projectPath", "project_path"):
            value = str(arguments.get(key) or approval.get(key) or "").strip()
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
            if approval.get("status") != "pending" and status == "rejected":
                return {"ok": False, "approval": approval, "message": f"Approval is {approval.get('status')}."}
            if approval.get("status") == "expired":
                return {"ok": False, "approval": approval, "message": "Approval has expired."}
            approval["status"] = status
            approval[f"{status}At"] = utc_now_iso()
            self._ports.state.approvals[approval_id] = approval
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
            approval["status"] = "revision_requested"
            approval["revisionRequestedAt"] = utc_now_iso()
            approval["revisionReason"] = reason.strip()
            approval["revisionNote"] = note.strip()
            self._ports.state.approvals[approval_id] = approval
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
        if approval.get("status") != "pending":
            return approval
        expires_at = parse_iso_datetime(str(approval.get("expiresAt") or ""))
        if expires_at and expires_at < datetime.now(timezone.utc):
            approval["status"] = "expired"
            self._ports.state.approvals[str(approval.get("id"))] = approval
        return approval

    def _load_approval_from_audit(self, approval_id: str) -> dict[str, Any] | None:
        return None

    def _reconcile_unrecoverable_linked_approval(self, approval_id: str) -> bool:
        linked = self._goal.delivery_for_approval(approval_id)
        if linked is None:
            return False
        self._goal.reconcile_missing_approvals(set(self._ports.state.approvals))
        return True
