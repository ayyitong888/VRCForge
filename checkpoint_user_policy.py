"""Bind an explicit checkpoint opt-out to one owner-held user turn and write."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_CHECKPOINT_SKIP_AUTHORITY_SCHEMA = "vrcforge.user_checkpoint_skip_authority.v1"


def bind_user_checkpoint_choice(
    authority: object, choice: object, *, session_id: str, turn_id: str,
    client_turn_id: str, project_root: str,
) -> dict[str, Any] | None:
    """Validate and normalize an explicit checkpoint on/off instruction."""
    if not isinstance(authority, Mapping) or authority.get("source") != "app_user_message":
        return None
    if not isinstance(choice, Mapping) or choice.get("mode") not in {"skip", "require"}:
        return None
    quote, message = choice.get("quote"), authority.get("message")
    if not isinstance(quote, str) or not quote.strip() or not isinstance(message, str) or quote not in message:
        return None
    expected = {
        "sessionId": session_id,
        "clientTurnId": client_turn_id,
        "projectRoot": project_root,
    }
    if any(str(authority.get(key) or "") != str(value or "") for key, value in expected.items()):
        return None
    return {
        "mode": choice["mode"],
        "quote": quote,
        "source": "user_message",
        "turnId": turn_id,
        **expected,
    }


def bind_session_checkpoint_skip(
    setting: object, *, session_id: str, turn_id: str, client_turn_id: str,
    project_root: str, tool: str, arguments: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Bind the session's previously user-authorized skip switch to one write."""
    if not isinstance(setting, Mapping) or setting.get("mode") != "skip":
        return None
    quote = setting.get("quote")
    if (
        setting.get("source") != "user_message"
        or not isinstance(quote, str) or not quote.strip()
        or str(setting.get("sessionId") or "") != str(session_id or "")
    ):
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


def bind_external_checkpoint_skip(
    choice: object, *, session_id: str, project_root: str, tool: str,
    arguments_digest: str, plan_digest: str,
) -> dict[str, Any]:
    """Bind the external host Agent's quoted user instruction to this write.

    MCP does not provide the host transcript. The host Agent is responsible
    for quoting an explicit user opt-out; this is not App-message attestation.
    The gateway supplies the authenticated session and prepared write scope.
    """
    if (
        not isinstance(choice, Mapping)
        or set(choice) != {"mode", "quote"}
        or choice.get("mode") != "skip"
        or not isinstance(choice.get("quote"), str)
        or not choice["quote"].strip()
    ):
        raise ValueError("checkpointChoice requires mode=skip and the user's exact explicit opt-out quote.")
    if not session_id or not project_root:
        raise ValueError("Checkpoint choice requires the current authenticated MCP session and exact project root.")
    return {
        "schema": "vrcforge.user_checkpoint_policy.v1",
        "status": "skipped_by_user",
        "source": "external_user_instruction",
        "sourceQuote": choice["quote"],
        "sessionId": session_id,
        "projectRoot": str(Path(project_root).expanduser().resolve()),
        "targetTool": tool,
        "approvedArgumentsSha256": arguments_digest,
        "approvedPlanDigest": plan_digest,
    }


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def same_resolved_execution_target(
    original: object,
    current: object,
) -> bool:
    """Compare host-resolved correction scope for checkpoint choice only.

    Scene revision and content digests may change after a failed operation. A
    The write arguments are independently required to remain unchanged. A
    failed Avatar-scoped reference can be narrowed to its resolved object in
    that same Avatar. This carries no write approval: the corrected action
    still goes through fresh target validation and the approval transaction.
    """
    if not isinstance(original, Mapping) or not isinstance(current, Mapping):
        return False

    def section(target: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = target.get(key)
        return value if isinstance(value, Mapping) else {}

    old_project, new_project = section(original, "project"), section(current, "project")
    old_editor, new_editor = section(original, "editor"), section(current, "editor")
    old_scene, new_scene = section(original, "scene"), section(current, "scene")
    old_avatar, new_avatar = section(original, "avatar"), section(current, "avatar")
    old_object, new_object = section(original, "object"), section(current, "object")
    old_component, new_component = section(original, "component"), section(current, "component")

    def same_nonempty(left: Mapping[str, Any], right: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
        return all(left.get(field) not in (None, "") and left.get(field) == right.get(field) for field in fields)

    if not same_nonempty(old_project, new_project, ("root", "projectId")):
        return False
    if not same_nonempty(old_editor, new_editor, ("unityPid", "processStartTime", "coreInstanceId")):
        return False
    if not same_nonempty(old_scene, new_scene, ("guid",)):
        return False

    old_avatar_id = str(old_avatar.get("globalObjectId") or "")
    new_avatar_id = str(new_avatar.get("globalObjectId") or "")
    old_object_id = str(old_object.get("globalObjectId") or "")
    new_object_id = str(new_object.get("globalObjectId") or "")
    old_scope = str(original.get("scope") or "")
    new_scope = str(current.get("scope") or "")
    if not new_object_id or new_scope not in {"object", "component"}:
        return False
    if old_object_id:
        if old_object_id != new_object_id:
            return False
    elif old_scope == "avatar":
        # An insufficient Avatar-scoped ref is exactly what this correction
        # replaces. Preserve the user's checkpoint choice inside the same
        # trusted Avatar while the write gate validates the new object ref.
        if not old_avatar_id or old_avatar_id != new_avatar_id:
            return False
    else:
        return False

    if old_avatar_id and old_avatar_id != new_avatar_id:
        return False
    old_component_id = str(old_component.get("globalObjectId") or "")
    if old_component_id and old_component_id != str(new_component.get("globalObjectId") or ""):
        return False
    return True


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


def bind_corrected_checkpoint_skip(
    policy: object,
    original_arguments: Mapping[str, Any],
    task_context: object,
    *,
    tool: str,
    arguments: Mapping[str, Any],
    original_execution_target: object = None,
    corrected_execution_target: object = None,
) -> dict[str, Any] | None:
    """Rebind only a host-verified retry with unchanged write scope."""
    if (not isinstance(policy, Mapping) or not isinstance(task_context, Mapping)
            or not isinstance(original_arguments, Mapping)):
        return None
    if (
        policy.get("schema") != "vrcforge.user_checkpoint_policy.v1"
        or policy.get("status") != "skipped_by_user"
        or policy.get("source") != "user_message"
        or str(policy.get("tool") or "") != str(tool)
        or str(policy.get("argumentsSha256") or "") != _digest(original_arguments)
    ):
        return None
    original_scope = dict(original_arguments)
    corrected_scope = dict(arguments)
    original_target = original_scope.pop("executionTarget", None)
    corrected_target = corrected_scope.pop("executionTarget", None)
    if original_target is not None or corrected_target is not None:
        if not (original_target is not None and corrected_target is not None
                and same_resolved_execution_target(original_execution_target, corrected_execution_target)):
            return None
    if original_scope != corrected_scope:
        return None
    authority = checkpoint_skip_authority_from_policy(policy, task_context)
    if authority is None:
        return None
    return bind_continued_checkpoint_skip(
        authority,
        {"mode": "skip", "quote": policy.get("sourceQuote")},
        task_context=task_context,
        tool=tool,
        arguments=arguments,
    )


def bind_checkpoint_skip_for_correction(
    failed_action: object,
    correction_for_action_id: str,
    task_context: object,
    *,
    tool: str,
    arguments: Mapping[str, Any],
    original_execution_target: object = None,
    corrected_execution_target: object = None,
) -> dict[str, Any] | None:
    """Use only an attached host grant for the exact failed write action."""
    if not isinstance(failed_action, Mapping):
        return None
    action_id = str(correction_for_action_id or "").strip()
    outcome = failed_action.get("outcome")
    outcome_status = str(outcome.get("status") or "") if isinstance(outcome, Mapping) else ""
    if (
        not action_id
        or str(failed_action.get("actionId") or "") != action_id
        or failed_action.get("kind") != "write"
        or failed_action.get("tool") != tool
        or outcome_status not in {"failed", "needs_user_action"}
    ):
        return None
    original_policy = failed_action.get("_checkpointUserPolicy")
    original_arguments = failed_action.get("_checkpointWriteArguments")
    return bind_corrected_checkpoint_skip(
        original_policy,
        original_arguments,
        task_context,
        tool=tool,
        arguments=arguments,
        original_execution_target=original_execution_target,
        corrected_execution_target=corrected_execution_target,
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
