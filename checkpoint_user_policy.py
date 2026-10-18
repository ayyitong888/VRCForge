"""Bind an explicit checkpoint opt-out to one owner-held user turn and write."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_CHECKPOINT_SKIP_AUTHORITY_SCHEMA = "vrcforge.user_checkpoint_skip_authority.v1"


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


def checkpoint_skip_authority_from_policy(
    policy: object,
    task_context: object,
) -> dict[str, Any] | None:
    """Promote a trusted, task-seeded user policy to continuation authority.

    The returned capability deliberately omits the original write tool and
    argument digest. Those values authorize only the write that created the
    policy and must never be reused for a later action.
    """
    if not isinstance(policy, Mapping) or not isinstance(task_context, Mapping):
        return None
    if (
        policy.get("schema") != "vrcforge.user_checkpoint_policy.v1"
        or policy.get("status") != "skipped_by_user"
        or policy.get("source") != "user_message"
    ):
        return None
    source_quote = policy.get("sourceQuote")
    original_tool = policy.get("tool")
    original_digest = policy.get("argumentsSha256")
    if (
        not isinstance(source_quote, str) or not source_quote.strip()
        or not isinstance(original_tool, str) or not original_tool.strip()
        or not isinstance(original_digest, str) or len(original_digest) != 64
        or any(char not in "0123456789abcdef" for char in original_digest.lower())
    ):
        return None
    task_id = str(task_context.get("taskId") or "")
    session_id = str(task_context.get("sessionId") or "")
    turn_id = str(task_context.get("turnId") or "")
    client_turn_id = str(task_context.get("clientTurnId") or "")
    project_root = str(task_context.get("projectRoot") or "")
    if not task_id or not session_id or not turn_id or not client_turn_id:
        return None
    if any(
        str(policy.get(key) or "") != expected
        for key, expected in (
            ("sessionId", session_id),
            ("turnId", turn_id),
            ("clientTurnId", client_turn_id),
            ("projectRoot", project_root),
        )
    ):
        return None
    return {
        "schema": _CHECKPOINT_SKIP_AUTHORITY_SCHEMA,
        "status": "active",
        "source": "checkpoint_user_policy",
        "sourceQuote": source_quote,
        "taskId": task_id,
        "sessionId": session_id,
        "projectRoot": project_root,
        "originTurnId": turn_id,
        "originClientTurnId": client_turn_id,
    }


def checkpoint_skip_authority_for_context(
    authority: object,
    task_context: object,
) -> dict[str, Any] | None:
    """Return only a live task-scoped capability matching the trusted context."""
    if not isinstance(authority, Mapping) or not isinstance(task_context, Mapping):
        return None
    if (
        authority.get("schema") != _CHECKPOINT_SKIP_AUTHORITY_SCHEMA
        or authority.get("status") != "active"
        or authority.get("source") != "checkpoint_user_policy"
    ):
        return None
    source_quote = authority.get("sourceQuote")
    if not isinstance(source_quote, str) or not source_quote.strip():
        return None
    expected = {
        "taskId": str(task_context.get("taskId") or ""),
        "sessionId": str(task_context.get("sessionId") or ""),
        "projectRoot": str(task_context.get("projectRoot") or ""),
        "originTurnId": str(task_context.get("turnId") or ""),
        "originClientTurnId": str(task_context.get("clientTurnId") or ""),
    }
    if not expected["taskId"] or not expected["sessionId"]:
        return None
    if any(str(authority.get(key) or "") != value for key, value in expected.items()):
        return None
    return {
        "schema": _CHECKPOINT_SKIP_AUTHORITY_SCHEMA,
        "status": "active",
        "source": "checkpoint_user_policy",
        "sourceQuote": source_quote,
        **expected,
    }


def bind_continued_checkpoint_skip(
    authority: object,
    choice: object,
    *,
    task_context: object,
    tool: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Bind inherited owner authority to this new write's current arguments."""
    normalized = checkpoint_skip_authority_for_context(authority, task_context)
    if normalized is None or not isinstance(choice, Mapping):
        return None
    if choice.get("mode") != "skip" or choice.get("quote") != normalized["sourceQuote"]:
        return None
    source = {
        "source": "app_user_message",
        "message": normalized["sourceQuote"],
        "sessionId": normalized["sessionId"],
        "turnId": normalized["originTurnId"],
        "clientTurnId": normalized["originClientTurnId"],
        "projectRoot": normalized["projectRoot"],
    }
    return bind_user_checkpoint_skip(
        source,
        choice,
        session_id=normalized["sessionId"],
        turn_id=normalized["originTurnId"],
        client_turn_id=normalized["originClientTurnId"],
        project_root=normalized["projectRoot"],
        tool=tool,
        arguments=arguments,
    )


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
