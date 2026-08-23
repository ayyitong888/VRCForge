"""Process-local, one-use authority for managed Unity Core writes.

The gateway owns construction and binding of :class:`ApprovedUnityExecutionPlan`.
The JSON helpers deliberately serialize only an auditable *plan specification*;
they never deserialize an arbitrary request dictionary into a ContextVar
capability.  A bound capability is an in-memory object with a private lock and
per-call execution identifiers, and may only advance in the frozen order.

Internal Agent writes use the ``approved_write`` lane and bind an approval plus
checkpoint. External MCP writes use the ``external_mcp_write`` lane and bind an
operation id instead. Both lanes share only this exact Core-call capability;
the external lane does not inherit the internal Agent approval transaction.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from unity_mcp_core_client import canonical_arguments_sha256


FROZEN_PLAN_SCHEMA = "vrcforge.approved-unity-execution-plan.v1"
MAX_APPROVED_UNITY_EXECUTION_CALLS = 64


class ApprovedUnityExecutionError(RuntimeError):
    """The active write authority is unavailable or does not match the call."""


class ApprovedUnityExecutionClaimError(ApprovedUnityExecutionError):
    """A Core call cannot consume the next exact one-use plan entry."""


@dataclass(frozen=True)
class ApprovedUnityExecutionCall:
    """One exact Core invocation allowed by an approved handler."""

    tool_name: str
    arguments_sha256: str

    def as_json(self) -> dict[str, str]:
        return {"toolName": self.tool_name, "argumentsSha256": self.arguments_sha256}


@dataclass(frozen=True)
class FrozenApprovedUnityExecutionPlan:
    """Validated approval-time call specification; not a runtime capability."""

    calls: tuple[ApprovedUnityExecutionCall, ...]
    plan_digest: str

    def as_json(self) -> dict[str, Any]:
        return {
            "schema": FROZEN_PLAN_SCHEMA,
            "calls": [call.as_json() for call in self.calls],
            "planDigest": self.plan_digest,
        }


@dataclass(frozen=True)
class ApprovedUnityExecutionClaim:
    """A claimed transport envelope; complete or uncertain must be reported."""

    _plan: "ApprovedUnityExecutionPlan"
    _index: int
    execution_context: Mapping[str, Any]

    def complete(self) -> None:
        self._plan._complete_claim(self._index)

    def uncertain(self) -> None:
        self._plan._mark_uncertain(self._index)


class ApprovedUnityExecutionPlan(Mapping[str, Any]):
    """Bound in-process capability with ordered, exact, one-use Core calls.

    The Mapping surface is read-only diagnostic compatibility.  It intentionally
    exposes no mutation or claim creation path; callers must use :meth:`claim`.
    """

    def __init__(self, context: Mapping[str, Any], frozen: FrozenApprovedUnityExecutionPlan) -> None:
        self._context = MappingProxyType(_validate_context(context))
        self._calls = frozen.calls
        self._plan_digest = frozen.plan_digest
        self._execution_ids = tuple(secrets.token_urlsafe(24) for _ in self._calls)
        self._lock = threading.Lock()
        self._owner_thread_id = threading.get_ident()
        self._next_index = 0
        self._active_index: int | None = None
        self._uncertain = False

    @property
    def plan_digest(self) -> str:
        return self._plan_digest

    @property
    def project_root(self) -> Path:
        return _canonical_project_root(str(self._context["projectRoot"]))

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def uncertain_state(self) -> bool:
        with self._lock:
            return self._uncertain

    @property
    def consumed(self) -> bool:
        with self._lock:
            return (
                not self._uncertain
                and self._active_index is None
                and self._next_index == len(self._calls)
            )

    def frozen(self) -> FrozenApprovedUnityExecutionPlan:
        return FrozenApprovedUnityExecutionPlan(self._calls, self._plan_digest)

    def diagnostic_context(self) -> dict[str, Any]:
        """Return a copy for logs/tests, never a transport authority."""
        with self._lock:
            context = _json_copy(dict(self._context))
            if self._next_index < len(self._calls):
                call = self._calls[self._next_index]
                context.update(
                    {
                        "executionId": self._execution_ids[self._next_index],
                        "unityToolName": call.tool_name,
                        "argumentsSha256": call.arguments_sha256,
                        "planDigest": self._plan_digest,
                    }
                )
            return context

    def claim(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        project_root: str | Path,
        *,
        now_unix_ms: int | None = None,
    ) -> ApprovedUnityExecutionClaim:
        """Atomically claim the next exact call before one transport attempt."""
        if not isinstance(tool_name, str) or not tool_name:
            raise ApprovedUnityExecutionClaimError("Unity tool name is invalid.")
        try:
            actual_hash = canonical_arguments_sha256(arguments)
        except ValueError as exc:
            raise ApprovedUnityExecutionClaimError("Unity tool arguments are invalid.") from exc
        actual_root = _canonical_project_root(project_root)
        now_ms = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
        if not isinstance(now_ms, int) or isinstance(now_ms, bool):
            raise ApprovedUnityExecutionClaimError("Execution time is invalid.")
        if threading.get_ident() != self._owner_thread_id:
            raise ApprovedUnityExecutionClaimError("Approved Unity execution cannot cross its handler thread.")

        with self._lock:
            if self._uncertain:
                raise ApprovedUnityExecutionClaimError("Approved Unity execution is uncertain and closed.")
            if self._active_index is not None:
                raise ApprovedUnityExecutionClaimError("Approved Unity execution already has an active call.")
            if self._next_index >= len(self._calls):
                raise ApprovedUnityExecutionClaimError("Approved Unity execution plan is already consumed.")
            if now_ms < int(self._context["issuedAtUnixMs"]) or now_ms >= int(self._context["expiresAtUnixMs"]):
                raise ApprovedUnityExecutionClaimError("Approved Unity execution has expired.")
            if actual_root != self.project_root:
                raise ApprovedUnityExecutionClaimError("Approved Unity execution project root drifted.")
            expected = self._calls[self._next_index]
            if expected.tool_name != tool_name:
                raise ApprovedUnityExecutionClaimError("Approved Unity execution tool does not match the frozen plan.")
            if expected.arguments_sha256 != actual_hash:
                raise ApprovedUnityExecutionClaimError("Approved Unity execution arguments do not match the frozen plan.")

            index = self._next_index
            self._active_index = index
            envelope = _json_copy(dict(self._context))
            envelope.update(
                {
                    "executionId": self._execution_ids[index],
                    "gatewayTargetTool": str(self._context["targetTool"]),
                    "targetTool": expected.tool_name,
                    "unityToolName": expected.tool_name,
                    "argumentsSha256": expected.arguments_sha256,
                    "planDigest": self._plan_digest,
                }
            )
            return ApprovedUnityExecutionClaim(self, index, MappingProxyType(envelope))

    def burn(self) -> None:
        """Permanently close every unconsumed call in this in-process capability."""
        with self._lock:
            self._active_index = None
            self._uncertain = True

    def _complete_claim(self, index: int) -> None:
        with self._lock:
            if self._active_index != index:
                raise ApprovedUnityExecutionClaimError("Approved Unity execution claim is no longer active.")
            self._active_index = None
            self._next_index += 1

    def _mark_uncertain(self, index: int) -> None:
        with self._lock:
            if self._active_index == index:
                self._active_index = None
            self._uncertain = True

    # Mapping keeps observability callers from receiving a mutable dict.
    def __getitem__(self, key: str) -> Any:
        return self.diagnostic_context()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.diagnostic_context())

    def __len__(self) -> int:
        return len(self.diagnostic_context())


_APPROVED_UNITY_EXECUTION: ContextVar[ApprovedUnityExecutionPlan | None] = ContextVar(
    "vrcforge_approved_unity_execution",
    default=None,
)


def freeze_approved_unity_execution_plan(
    calls: Sequence[ApprovedUnityExecutionCall | Mapping[str, Any] | tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Create the auditable approval-time exact call plan.

    Checkpoint identity, TTL, project root, and execution ids are deliberately
    absent: those values only exist after approval and checkpoint creation.
    """
    normalized_calls = tuple(_normalize_call(call) for call in calls)
    if not normalized_calls:
        raise ValueError("approved Unity execution plan must contain at least one call.")
    if len(normalized_calls) > MAX_APPROVED_UNITY_EXECUTION_CALLS:
        raise ValueError("approved Unity execution plan contains too many calls.")
    digest_input = {
        "schema": FROZEN_PLAN_SCHEMA,
        "calls": [call.as_json() for call in normalized_calls],
    }
    digest = _canonical_json_sha256(digest_input)
    return {**digest_input, "planDigest": digest}


def validate_frozen_approved_unity_execution_plan(value: Mapping[str, Any]) -> FrozenApprovedUnityExecutionPlan:
    """Validate a persisted plan specification; no runtime authority is bound."""
    if not isinstance(value, Mapping) or value.get("schema") != FROZEN_PLAN_SCHEMA:
        raise ValueError("approved Unity execution plan schema is invalid.")
    raw_calls = value.get("calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("approved Unity execution plan calls are invalid.")
    if len(raw_calls) > MAX_APPROVED_UNITY_EXECUTION_CALLS:
        raise ValueError("approved Unity execution plan contains too many calls.")
    calls = tuple(_normalize_call(call) for call in raw_calls)
    expected_digest = _canonical_json_sha256(
        {"schema": FROZEN_PLAN_SCHEMA, "calls": [call.as_json() for call in calls]}
    )
    supplied_digest = value.get("planDigest")
    if not isinstance(supplied_digest, str) or not secrets.compare_digest(supplied_digest, expected_digest):
        raise ValueError("approved Unity execution plan digest is invalid.")
    return FrozenApprovedUnityExecutionPlan(calls, expected_digest)


def create_approved_unity_execution_plan(
    context: Mapping[str, Any],
    calls: (
        Mapping[str, Any]
        | Sequence[ApprovedUnityExecutionCall | Mapping[str, Any] | tuple[str, dict[str, Any]]]
    ),
) -> ApprovedUnityExecutionPlan:
    """Gateway-only factory for an in-process execution capability."""
    frozen = (
        validate_frozen_approved_unity_execution_plan(calls)
        if isinstance(calls, Mapping)
        else validate_frozen_approved_unity_execution_plan(freeze_approved_unity_execution_plan(calls))
    )
    return ApprovedUnityExecutionPlan(context, frozen)


@contextmanager
def bind_approved_unity_execution(plan: ApprovedUnityExecutionPlan) -> Iterator[None]:
    if not isinstance(plan, ApprovedUnityExecutionPlan):
        raise ValueError("approved Unity execution requires a gateway-owned plan capability")
    token = _APPROVED_UNITY_EXECUTION.set(plan)
    try:
        yield
    finally:
        _APPROVED_UNITY_EXECUTION.reset(token)


def current_approved_unity_execution() -> ApprovedUnityExecutionPlan | None:
    return _APPROVED_UNITY_EXECUTION.get()


def _normalize_call(value: ApprovedUnityExecutionCall | Mapping[str, Any] | tuple[str, dict[str, Any]]) -> ApprovedUnityExecutionCall:
    if isinstance(value, ApprovedUnityExecutionCall):
        tool_name, arguments_sha256 = value.tool_name, value.arguments_sha256
    elif isinstance(value, tuple) and len(value) == 2:
        tool_name, arguments = value
        if not isinstance(arguments, dict):
            raise ValueError("approved Unity execution call arguments must be an object.")
        arguments_sha256 = canonical_arguments_sha256(arguments)
    elif isinstance(value, Mapping):
        tool_name = value.get("toolName")
        arguments_sha256 = value.get("argumentsSha256")
    else:
        raise ValueError("approved Unity execution call is invalid.")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("approved Unity execution call toolName is invalid.")
    if not isinstance(arguments_sha256, str) or len(arguments_sha256) != 64:
        raise ValueError("approved Unity execution call argumentsSha256 is invalid.")
    try:
        int(arguments_sha256, 16)
    except ValueError:
        raise ValueError("approved Unity execution call argumentsSha256 is invalid.") from None
    return ApprovedUnityExecutionCall(tool_name, arguments_sha256.lower())


def _validate_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("approved Unity execution context is invalid.")
    context = _json_copy(dict(value))
    lane = context.get("lane")
    if lane == "approved_write":
        required_strings = ("approvalId", "checkpointId", "targetTool", "projectRoot")
    elif lane == "external_mcp_write":
        required_strings = ("operationId", "targetTool", "projectRoot")
        if "approvalId" in context or "checkpointId" in context:
            raise ValueError("approved Unity execution context is invalid.")
    else:
        raise ValueError("approved Unity execution context is invalid.")
    for key in required_strings:
        if not isinstance(context.get(key), str) or not context[key].strip():
            raise ValueError("approved Unity execution context is invalid.")
    issued, expires = context.get("issuedAtUnixMs"), context.get("expiresAtUnixMs")
    if (
        not isinstance(issued, int)
        or isinstance(issued, bool)
        or not isinstance(expires, int)
        or isinstance(expires, bool)
        or issued < 0
        or expires <= issued
    ):
        raise ValueError("approved Unity execution context expiry is invalid.")
    context["projectRoot"] = str(_canonical_project_root(context["projectRoot"]))
    return context


def _canonical_project_root(value: str | Path) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        raise ApprovedUnityExecutionError("Approved Unity execution project root is invalid.") from None
    if not root.is_dir():
        raise ApprovedUnityExecutionError("Approved Unity execution project root is invalid.")
    return root


def _json_copy(value: Any) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("approved Unity execution data must be JSON-compatible.") from None
    if not isinstance(copied, dict):
        raise ValueError("approved Unity execution data must be an object.")
    return copied


def _canonical_json_sha256(value: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("approved Unity execution data must be JSON-compatible.") from None
    return hashlib.sha256(encoded).hexdigest()
