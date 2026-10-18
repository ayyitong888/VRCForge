"""Bind an explicit checkpoint opt-out to one owner-held user turn and write."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def bind_user_checkpoint_skip(
    authority: object,
    choice: object,
    *,
    session_id: str,
    turn_id: str,
    client_turn_id: str,
    project_root: str,
    tool: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Accept only an exact quote from this host-bound user message and scope."""
    if not isinstance(authority, Mapping) or authority.get("source") != "app_user_message":
        return None
    if not isinstance(choice, Mapping) or choice.get("mode") != "skip":
        return None
    quote = choice.get("quote")
    message = authority.get("message")
    if not isinstance(quote, str) or not quote.strip() or not isinstance(message, str) or quote not in message:
        return None
    for key, expected in (("sessionId", session_id), ("turnId", turn_id),
                          ("clientTurnId", client_turn_id), ("projectRoot", project_root)):
        if str(authority.get(key) or "") != str(expected or ""):
            return None
    return {
        "schema": "vrcforge.user_checkpoint_policy.v1",
        "status": "skipped_by_user",
        "source": "user_message",
        "sourceQuote": quote,
        "sessionId": session_id,
        "turnId": turn_id,
        "clientTurnId": client_turn_id,
        "projectRoot": project_root,
        "tool": tool,
        "argumentsSha256": _digest(arguments),
    }


def checkpoint_skip_matches(policy: object, task_context: object, *, tool: str,
                             arguments: Mapping[str, Any], plan_digest: str = "") -> bool:
    """Revalidate the bound write identity before skipping checkpoint creation."""
    if not isinstance(policy, Mapping) or not isinstance(task_context, Mapping):
        return False
    if policy.get("schema") != "vrcforge.user_checkpoint_policy.v1" or policy.get("status") != "skipped_by_user":
        return False
    request_arguments = task_context.get("requestedArguments")
    if not isinstance(request_arguments, Mapping):
        return False
    return (
        str(policy.get("tool") or "") == str(task_context.get("tool") or "")
        and str(policy.get("targetTool") or "") == tool
        and str(policy.get("requestedArgumentsSha256") or "") == _digest(request_arguments)
        and str(policy.get("argumentsSha256") or "") == _digest(request_arguments)
        and str(policy.get("approvedArgumentsSha256") or "") == _digest(arguments)
        and str(policy.get("approvedPlanDigest") or "") == str(plan_digest or "")
        and str(policy.get("actionId") or "") == str(task_context.get("actionId") or "")
        and all(str(policy.get(key) or "") == str(task_context.get(key) or "")
                for key in ("sessionId", "turnId", "clientTurnId", "projectRoot"))
    )


def bind_approved_write(policy: object, task_context: object, *, target_tool: str,
                        requested_arguments: Mapping[str, Any], approved_arguments: Mapping[str, Any],
                        approved_project_root: str = "", plan_digest: str = "") -> dict[str, Any] | None:
    """Bind the user choice to the server-prepared immutable approval payload."""
    if not isinstance(policy, Mapping) or not isinstance(task_context, Mapping):
        return None
    if policy.get("schema") != "vrcforge.user_checkpoint_policy.v1" or policy.get("status") != "skipped_by_user":
        return None
    requested_root = str(task_context.get("projectRoot") or "")
    if requested_root:
        if not approved_project_root:
            return None
        try:
            if Path(requested_root).expanduser().resolve() != Path(approved_project_root).expanduser().resolve():
                return None
        except (OSError, RuntimeError, ValueError):
            return None
    if (
        str(policy.get("tool") or "") != str(task_context.get("tool") or "")
        or str(policy.get("argumentsSha256") or "") != _digest(requested_arguments)
        or _digest(requested_arguments) != _digest(task_context.get("requestedArguments") or {})
        or any(str(policy.get(key) or "") != str(task_context.get(key) or "")
               for key in ("sessionId", "turnId", "clientTurnId", "projectRoot"))
    ):
        return None
    return {
        **dict(policy),
        "targetTool": target_tool,
        "requestedArgumentsSha256": _digest(requested_arguments),
        "approvedArgumentsSha256": _digest(approved_arguments),
        "approvedPlanDigest": str(plan_digest or ""),
        "actionId": str(task_context.get("actionId") or ""),
    }
