"""One-use authority for evidence-bound Runtime journey receipts.

The authority is intentionally process-local and read-only.  Its permission
scope is limited to validating an already bounded ``runtime_message`` response;
it owns no Provider, tool, filesystem, network, or approval capability.  Its
lifetime belongs to the creating process and authority instance.  Receipts are
authenticated with a per-instance HMAC secret, expire quickly, and can be
verified exactly once by that same live authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any


JOURNEY_SCHEMA = "vrcforge.agent_harness_journey.v1"
JOURNEY_RECEIPT_SCHEMA = "vrcforge.agent_harness_journey_receipt.v1"
_MAX_ACTIONS = 25
_MAX_ID_LENGTH = 180
_MAX_TOOL_LENGTH = 160
_MAX_TTL_SECONDS = 300
_TOOL_STEP_KINDS = frozenset({"shell", "skill", "write"})
_REJECTED_TERMINAL_STATUSES = frozenset(
    {
        "approval_pending",
        "cancelled",
        "failed",
        "needs_user_action",
        "pending",
        "running",
    }
)


class JourneyReceiptError(ValueError):
    """A fail-closed projection, issuance, or verification failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise JourneyReceiptError(code, message)


def _status(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("journey_invalid", f"{field} must be an object.")
    return value


def _list(value: Any, field: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        _fail("journey_invalid", f"{field} must be a list.")
    if not allow_empty and not value:
        _fail("journey_invalid", f"{field} must not be empty.")
    if len(value) > _MAX_ACTIONS:
        _fail("journey_too_large", f"{field} exceeds the bounded action limit.")
    return value


def _identifier(value: Any, field: str, *, limit: int = _MAX_ID_LENGTH) -> str:
    if not isinstance(value, str):
        _fail("journey_identity_missing", f"{field} is required.")
    bounded = value.strip()
    if not bounded or len(bounded) > limit:
        _fail("journey_identity_missing", f"{field} is required and must be bounded.")
    if any(character in bounded for character in ("\x00", "\r", "\n", "/", "\\")):
        _fail("journey_identity_invalid", f"{field} is not a valid Runtime identity.")
    return bounded


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("journey_invalid", f"{field} must be a non-negative integer.")
    return value


def _declared_verifications(
    action: Mapping[str, Any],
    requirements: list[Any],
) -> tuple[list[str], list[str]]:
    action_id = _identifier(action.get("actionId"), "task.actions.actionId", limit=80)
    action_kind = _identifier(action.get("kind"), "task.actions.kind", limit=32)
    action_tool = _identifier(action.get("tool"), "task.actions.tool", limit=_MAX_TOOL_LENGTH)
    matches: list[Mapping[str, Any]] = []
    for raw_requirement in requirements:
        requirement = _mapping(raw_requirement, "task.requirements[]")
        if (
            str(requirement.get("actionId") or "").strip() == action_id
            and str(requirement.get("kind") or "").strip() == action_kind
            and str(requirement.get("tool") or "").strip() == action_tool
        ):
            matches.append(requirement)
    if not matches:
        _fail(
            "journey_verification_undeclared",
            "Every completed action must have an action-bound verification requirement.",
        )
    profiles = [
        _identifier(
            requirement.get("verificationProfile"),
            "task.requirements.verificationProfile",
            limit=80,
        )
        for requirement in matches
    ]
    if len(set(profiles)) != len(profiles):
        _fail("journey_verification_undeclared", "Verification profiles must be unique per action.")
    outcome = _mapping(action.get("outcome"), "task.actions.outcome")
    if _status(outcome.get("status")) != "ok":
        _fail("journey_action_failed", "A completed journey contains a non-ok action outcome.")
    verification = _mapping(outcome.get("verification"), "task.actions.outcome.verification")
    state = _status(verification.get("state"))
    # The current Runtime emits one independently evaluated verification state
    # per action.  More than one profile on the same action would make it
    # impossible to prove which verifier passed, so fail closed instead of
    # copying one aggregate state across multiple declarations.
    if len(profiles) != 1:
        _fail(
            "journey_verification_ambiguous",
            "Each declared verification profile needs its own independently verified action.",
        )
    if _status(profiles[0]) == "canonical_tool_result" and state == "not_required":
        # The canonical result contract is the verifier for a successful read;
        # Runtime truthfully reports that no stronger postcondition was needed.
        state = "passed"
    states = [state]
    if any(item != "passed" for item in states):
        _fail(
            "journey_verification_failed",
            "Every completed action must have a passed declared verification state.",
        )
    return profiles, states


def _managed_visual_evidence_refs(action: Mapping[str, Any]) -> list[str]:
    outcome = _mapping(action.get("outcome"), "task.actions.outcome")
    raw_evidence = outcome.get("evidence")
    if raw_evidence in (None, []):
        return []
    evidence = _list(raw_evidence, "task.actions.outcome.evidence", allow_empty=True)
    refs: list[str] = []
    for raw_item in evidence:
        item = _mapping(raw_item, "task.actions.outcome.evidence[]")
        if _status(item.get("kind")) != "managed_visual_capture":
            continue
        ref = _identifier(
            item.get("ref"),
            "task.actions.outcome.evidence.ref",
            limit=160,
        )
        if ref not in refs:
            refs.append(ref)
    return refs


def project_runtime_journey(runtime_message: Mapping[str, Any]) -> dict[str, Any]:
    """Project and validate the safe receipt fields from one Runtime response.

    Re-feed count is only credited when the response proves the Runtime's
    sequential order: each ordered tool step has the same exact action identity
    as the task record, the final plan is LLM-owned, and there is one later
    Provider request available per result plus the initial tool-selection
    request.  One exact Runtime-owned desktop bootstrap may run before that
    first request and is counted separately.  No raw message, arguments,
    result, path, or secret is projected.
    """

    response = _mapping(runtime_message, "runtime_message")
    if response.get("ok") is not True:
        _fail("journey_runtime_failed", "Only a successful Runtime response can be receipted.")

    session_id = _identifier(response.get("sessionId"), "sessionId")
    turn_id = _identifier(response.get("turnId"), "turnId")
    client_turn_id = _identifier(response.get("clientTurnId"), "clientTurnId")

    plan = _mapping(response.get("plan"), "plan")
    if _status(plan.get("planner")) != "llm" or _status(plan.get("nextStep")) != "done":
        _fail(
            "journey_provider_resample_missing",
            "The terminal decision must come from a later LLM planner sample.",
        )
    completion = _mapping(plan.get("taskCompletion"), "plan.taskCompletion")
    if _status(completion.get("status")) != "completed":
        _fail("journey_not_completed", "The Runtime task completion is not completed.")

    task = _mapping(response.get("task"), "task")
    if _status(task.get("status")) != "completed":
        _fail("journey_not_completed", "The Runtime task is not completed.")
    task_schema = _identifier(task.get("schema"), "task.schema", limit=100)
    if str(completion.get("schema") or "").strip() != task_schema:
        _fail("journey_completion_mismatch", "Task and completion schemas do not match.")

    actions = _list(task.get("actions"), "task.actions")
    requirements = _list(task.get("requirements"), "task.requirements")
    completed_actions: list[dict[str, Any]] = []
    completed_action_ids: list[str] = []
    verification_profiles: list[str] = []
    verification_states: list[str] = []
    action_rows: list[tuple[str, str, str]] = []
    all_action_ids: list[str] = []
    superseded_action_ids: list[str] = []
    managed_capture_refs: set[str] = set()
    verified_visual_refs: set[str] = set()
    for action_index, raw_action in enumerate(actions):
        action = _mapping(raw_action, "task.actions[]")
        action_status = _status(action.get("status"))
        action_id = _identifier(action.get("actionId"), "task.actions.actionId", limit=80)
        kind = _identifier(action.get("kind"), "task.actions.kind", limit=32)
        tool = _identifier(action.get("tool"), "task.actions.tool", limit=_MAX_TOOL_LENGTH)
        if action_id in all_action_ids:
            _fail("journey_action_identity_invalid", "Task action IDs must be unique.")
        all_action_ids.append(action_id)
        action_rows.append((action_id, kind, tool))
        if action_status == "superseded":
            outcome = _mapping(action.get("outcome"), "task.actions.outcome")
            if _status(outcome.get("status")) not in {"failed", "needs_user_action"}:
                _fail(
                    "journey_superseded_invalid",
                    "A superseded action must retain its failed or needs-user-action outcome.",
                )
            superseded_by = _identifier(
                action.get("supersededBy"),
                "task.actions.supersededBy",
                limit=80,
            )
            later_matches = [
                _mapping(candidate, "task.actions[]")
                for candidate in actions[action_index + 1 :]
                if str(_mapping(candidate, "task.actions[]").get("actionId") or "").strip()
                == superseded_by
            ]
            if len(later_matches) != 1:
                _fail(
                    "journey_superseded_invalid",
                    "A superseded action must point to exactly one later correction action.",
                )
            correction = later_matches[0]
            if (
                _status(correction.get("status")) != "completed"
                or str(correction.get("kind") or "").strip() != kind
                or str(correction.get("tool") or "").strip() != tool
            ):
                _fail(
                    "journey_superseded_invalid",
                    "A superseded action must resolve to a completed action with the same kind and tool.",
                )
            superseded_action_ids.append(action_id)
            continue
        if action_status != "completed":
            code = (
                "journey_action_failed"
                if action_status in _REJECTED_TERMINAL_STATUSES
                else "journey_action_not_completed"
            )
            _fail(code, "Every task action must be completed before a receipt is issued.")
        if action_id in completed_action_ids:
            _fail("journey_action_identity_invalid", "Completed action IDs must be unique.")
        profiles, states = _declared_verifications(action, requirements)
        visual_refs = _managed_visual_evidence_refs(action)
        if tool == "vrcforge_capture_multi_screenshot":
            if len(visual_refs) != 1:
                _fail(
                    "journey_visual_evidence_missing",
                    "The managed capture action must retain exactly one visual evidence identity.",
                )
            managed_capture_refs.add(visual_refs[0])
        if "multi_angle_visual" in profiles:
            if tool != "vrcforge_vision_audit_multi":
                _fail(
                    "journey_visual_verifier_invalid",
                    "Multi-angle visual verification must come from the first-party visual audit tool.",
                )
            if len(visual_refs) != 1 or visual_refs[0] not in managed_capture_refs:
                _fail(
                    "journey_visual_evidence_mismatch",
                    "A visual verifier must consume a prior managed capture from the same task.",
                )
            verified_visual_refs.add(visual_refs[0])
        completed_action_ids.append(action_id)
        verification_profiles.extend(profiles)
        verification_states.extend(states)
        completed_actions.append(
            {
                "actionId": action_id,
                "kind": kind,
                "tool": tool,
                "verificationProfiles": profiles,
                "verificationStates": states,
            }
        )

    declared_profile_count = sum(
        1
        for raw_requirement in requirements
        if str(_mapping(raw_requirement, "task.requirements[]").get("verificationProfile") or "").strip()
    )
    if declared_profile_count != len(verification_profiles):
        _fail(
            "journey_verification_undeclared",
            "Every declared verification profile must bind to one completed action.",
        )
    if not any(_status(profile) != "canonical_tool_result" for profile in verification_profiles):
        _fail(
            "journey_verification_canonical_only",
            "A journey needs at least one declared verifier stronger than its canonical tool results.",
        )

    evidence_ids = _list(
        completion.get("evidenceActionIds"),
        "plan.taskCompletion.evidenceActionIds",
    )
    if any(action_id in evidence_ids for action_id in superseded_action_ids):
        _fail(
            "journey_evidence_mismatch",
            "Superseded actions cannot be used as completion evidence.",
        )
    if evidence_ids != completed_action_ids:
        _fail(
            "journey_evidence_mismatch",
            "Completion evidence IDs must exactly match completed action IDs in order.",
        )

    raw_steps = _list(response.get("steps"), "steps")
    execution_rows: list[tuple[str, str, str]] = []
    pre_provider_bootstrap_count = 0
    for position, raw_step in enumerate(raw_steps):
        step = _mapping(raw_step, "steps[]")
        step_index = _strict_count(step.get("index"), "steps.index")
        if step_index != position:
            _fail("journey_order_invalid", "Runtime steps must have a contiguous call order.")
        kind = _status(step.get("kind"))
        if kind not in _TOOL_STEP_KINDS:
            continue
        action_id = _identifier(step.get("actionId"), "steps.actionId", limit=80)
        tool = _identifier(step.get("tool"), "steps.tool", limit=_MAX_TOOL_LENGTH)
        if step.get("preProvider") is True:
            if (
                execution_rows
                or kind != "skill"
                or tool != "vrcforge_agent_desktop_action"
                or pre_provider_bootstrap_count
            ):
                _fail(
                    "journey_provider_order_invalid",
                    "Only the first fixed desktop bootstrap can run before Provider planning.",
                )
            pre_provider_bootstrap_count = 1
        step_status = _status(step.get("status"))
        if action_id in superseded_action_ids:
            if step_status not in {"failed", "needs_user_action"}:
                _fail(
                    "journey_superseded_invalid",
                    "A superseded Runtime step must retain its failed terminal state.",
                )
        elif step_status in _REJECTED_TERMINAL_STATUSES:
            _fail("journey_action_failed", "A Runtime tool step is not successfully terminal.")
        execution_rows.append((action_id, kind, tool))

    if execution_rows != action_rows:
        _fail(
            "journey_action_identity_mismatch",
            "Ordered Runtime tool steps must exactly match the recorded task actions.",
        )

    context_usage = _mapping(response.get("contextUsage"), "contextUsage")
    provider_request_count = _strict_count(
        context_usage.get("requestCount"),
        "contextUsage.requestCount",
    )
    actual_tool_execution_count = len(execution_rows)
    minimum_provider_requests = actual_tool_execution_count + 1 - pre_provider_bootstrap_count
    if provider_request_count < minimum_provider_requests:
        _fail(
            "journey_provider_resample_missing",
            "Provider request order does not prove a later result re-feed sample.",
        )

    return {
        "schema": JOURNEY_SCHEMA,
        "id": turn_id,
        "sessionId": session_id,
        "turnId": turn_id,
        "clientTurnId": client_turn_id,
        "actualToolExecutionCount": actual_tool_execution_count,
        "toolExecutions": actual_tool_execution_count,
        "providerRequestCount": provider_request_count,
        "preProviderBootstrapCount": pre_provider_bootstrap_count,
        "resultRefeedCount": actual_tool_execution_count,
        "managedVisualEvidenceCount": len(verified_visual_refs),
        "completed": True,
        "taskStatus": "completed",
        "nextStep": "done",
        "completedActionIds": list(completed_action_ids),
        "supersededActionIds": list(superseded_action_ids),
        "evidenceActionIds": list(evidence_ids),
        "verificationProfiles": verification_profiles,
        "verificationStates": verification_states,
        "completedActions": completed_actions,
    }


class RuntimeJourneyReceiptAuthority:
    """Issue and consume short-lived receipts inside one owning process."""

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        clock: Callable[[], float] = time.time,
        default_ttl_seconds: int = 120,
        max_outstanding: int = 256,
    ) -> None:
        key = secret if secret is not None else secrets.token_bytes(32)
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Journey receipt HMAC secret must contain at least 32 bytes.")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(default_ttl_seconds, bool)
            or not isinstance(default_ttl_seconds, int)
            or not 1 <= default_ttl_seconds <= _MAX_TTL_SECONDS
        ):
            raise ValueError("default_ttl_seconds must be between 1 and 300")
        if isinstance(max_outstanding, bool) or not isinstance(max_outstanding, int) or max_outstanding < 1:
            raise ValueError("max_outstanding must be a positive integer")
        self._secret = key
        self._clock = clock
        self._default_ttl_seconds = default_ttl_seconds
        self._max_outstanding = max_outstanding
        self._process_id = os.getpid()
        self._authority_id = secrets.token_hex(16)
        self._issued: dict[str, tuple[int, str]] = {}
        self._consumed: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def authority_id(self) -> str:
        return self._authority_id

    def _assert_owner(self) -> None:
        if os.getpid() != self._process_id:
            _fail("receipt_wrong_process", "The receipt authority is owned by another process.")

    def _now_ms(self) -> int:
        return int(float(self._clock()) * 1000)

    def _cleanup(self, now_ms: int) -> None:
        self._issued = {
            receipt_id: state
            for receipt_id, state in self._issued.items()
            if state[0] > now_ms
        }
        self._consumed = {
            receipt_id: expires_at
            for receipt_id, expires_at in self._consumed.items()
            if expires_at > now_ms
        }

    def _sign(self, unsigned_receipt: Mapping[str, Any]) -> str:
        return hmac.new(
            self._secret,
            _canonical_bytes(unsigned_receipt),
            hashlib.sha256,
        ).hexdigest()

    def issue(
        self,
        runtime_message: Mapping[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Validate a Runtime journey and issue a bounded authenticated receipt."""

        self._assert_owner()
        journey = project_runtime_journey(runtime_message)
        ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= _MAX_TTL_SECONDS:
            raise ValueError("ttl_seconds must be between 1 and 300")
        now_ms = self._now_ms()
        expires_at_ms = now_ms + (ttl * 1000)
        receipt_id = secrets.token_hex(16)
        unsigned = {
            "schema": JOURNEY_RECEIPT_SCHEMA,
            "authorityId": self._authority_id,
            "receiptId": receipt_id,
            "issuedAtMs": now_ms,
            "expiresAtMs": expires_at_ms,
            "journey": journey,
        }
        signature = self._sign(unsigned)
        with self._lock:
            self._cleanup(now_ms)
            if len(self._issued) >= self._max_outstanding:
                _fail("receipt_capacity_reached", "The process-local receipt ledger is full.")
            self._issued[receipt_id] = (expires_at_ms, signature)
        return {**unsigned, "signature": signature}

    def verify(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Authenticate and consume a receipt exactly once, returning its safe journey."""

        self._assert_owner()
        candidate = dict(_mapping(receipt, "receipt"))
        if candidate.get("schema") != JOURNEY_RECEIPT_SCHEMA:
            _fail("receipt_invalid", "The journey receipt schema is invalid.")
        if candidate.get("authorityId") != self._authority_id:
            _fail("receipt_wrong_authority", "The receipt belongs to another authority.")
        receipt_id = _identifier(candidate.get("receiptId"), "receiptId", limit=80)
        supplied_signature = candidate.pop("signature", None)
        if not isinstance(supplied_signature, str) or len(supplied_signature) != 64:
            _fail("receipt_invalid", "The journey receipt signature is invalid.")
        expected_signature = self._sign(candidate)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            _fail("receipt_invalid", "The journey receipt signature is invalid.")
        now_ms = self._now_ms()
        with self._lock:
            self._consumed = {
                stored_id: expires_at
                for stored_id, expires_at in self._consumed.items()
                if expires_at > now_ms
            }
            if receipt_id in self._consumed:
                _fail("receipt_replayed", "The journey receipt has already been consumed.")
            issued_state = self._issued.get(receipt_id)
            if issued_state is None:
                _fail("receipt_unknown", "The journey receipt was not issued by this live authority.")
            expires_at_ms, issued_signature = issued_state
            if expires_at_ms <= now_ms:
                self._issued.pop(receipt_id, None)
                self._cleanup(now_ms)
                _fail("receipt_expired", "The journey receipt has expired.")
            if not hmac.compare_digest(issued_signature, supplied_signature):
                _fail("receipt_invalid", "The journey receipt does not match the issued record.")
            self._issued.pop(receipt_id, None)
            self._consumed[receipt_id] = expires_at_ms
        journey = _mapping(candidate.get("journey"), "receipt.journey")
        return json.loads(json.dumps(journey, ensure_ascii=True))


__all__ = [
    "JOURNEY_SCHEMA",
    "JOURNEY_RECEIPT_SCHEMA",
    "JourneyReceiptError",
    "RuntimeJourneyReceiptAuthority",
    "project_runtime_journey",
]
