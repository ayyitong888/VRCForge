"""Bounded independent readback for asynchronous Editor state changes."""
from contextlib import contextmanager
import os
from uuid import uuid4
from collections.abc import Callable, Mapping
from execution_target import validate_runtime_execution_target, execution_target_digest
from operation_context import bind_operation_context, current_operation_context


def fresh_play_mode_readback_arguments(
    arguments: Mapping[str, object],
    discover_project_targets: Callable[[dict[str, object]], Mapping[str, object]],
) -> dict[str, object]:
    """Refresh only the project-level identity after an approved Play transition."""

    original = arguments.get("executionTarget")
    if not isinstance(original, Mapping):
        return dict(arguments)
    project_path = str(
        arguments.get("projectPath")
        or arguments.get("project_path")
        or arguments.get("projectRoot")
        or arguments.get("project_root")
        or ""
    ).strip()
    if not project_path:
        return dict(arguments)
    original_project = dict(original.get("project") or {})
    original_editor = dict(original.get("editor") or {})
    try:
        discovered = discover_project_targets(
            {"projectPath": project_path, "scope": "project", "maxItems": 8}
        )
    except Exception:
        return dict(arguments)
    targets = discovered.get("targets")
    if (
        discovered.get("ok") is False
        or not isinstance(targets, list)
        or len(targets) != 1
        or discovered.get("candidateCount", len(targets)) != 1
        or discovered.get("totalCandidateCount", 1) != 1
        or discovered.get("hasMore") is True
        or discovered.get("complete") is False
        or discovered.get("ambiguous") is True
    ):
        return dict(arguments)
    for candidate in targets:
        if not isinstance(candidate, Mapping):
            continue
        if (
            str(candidate.get("scope") or "").casefold() != "project"
            or candidate.get("ambiguous") is True
            or candidate.get("resolutionCandidateCount") not in (None, 1)
        ):
            continue
        project = dict(candidate.get("project") or {})
        editor = dict(candidate.get("editor") or {})
        if (
            os.path.normcase(os.path.abspath(str(project.get("root") or "")))
            != os.path.normcase(os.path.abspath(project_path))
            or str(project.get("projectId") or "") != str(original_project.get("projectId") or "")
            or str(editor.get("unityPid") or "") != str(original_editor.get("unityPid") or "")
            or str(editor.get("processStartTime") or "") != str(original_editor.get("processStartTime") or "")
        ):
            continue
        refreshed = dict(arguments)
        refreshed["executionTarget"] = dict(candidate)
        return refreshed
    return dict(arguments)


@contextmanager
def bound_editor_readback(arguments):
    target = arguments.get("executionTarget")
    if target is None:  # Existing local UI calls have no external namespace envelope.
        yield {"verified": False, "status": "not_bound"}
        return
    project = arguments.get("projectPath") or arguments.get("project_path") or arguments.get("projectRoot")
    if not project:
        raise ValueError("Editor readback requires the original explicit project path.")
    verified = validate_runtime_execution_target(target, project_root=project, required_scope="project")
    avatar = arguments.get("avatarPath") or arguments.get("avatar_path")
    bound_avatar = (target.get("avatar") or {}).get("exactHierarchyPath")
    if avatar and bound_avatar and avatar.strip("/") != bound_avatar.strip("/"):
        raise ValueError("Editor readback Avatar does not match ExecutionTarget.")
    operation = current_operation_context() or {}
    evidence = {
        "verified": False,
        "beforeExecutionTargetDigest": execution_target_digest(verified),
        "editor": {key: verified.get("editor", {}).get(key) for key in ("unityPid", "processStartTime", "coreInstanceId")},
    }
    with bind_operation_context(operation.get("operationId") or "editor_readback_" + uuid4().hex, verified):
        yield evidence
    after = validate_runtime_execution_target(target, project_root=project, required_scope="project")
    after_digest = execution_target_digest(after)
    if after_digest != evidence["beforeExecutionTargetDigest"]:
        raise ValueError("Editor identity changed during independent readback.")
    evidence.update(verified=True, afterExecutionTargetDigest=after_digest)


def verified_editor_receipt(facts, initial, identity_verification=None):
    return {
        **facts, "schema": "vrcforge.editor_state_completion.v1", "ok": True,
        "verified": True, "readback": dict(facts), "persistent": False,
        "sceneDirty": False, "mutationStarted": initial.get("mutationStarted", True),
        "committed": True,
        "transitionScheduled": False, "verificationRequired": False,
        "identityVerification": dict(identity_verification or {"verified": False, "status": "not_bound"}),
        "commitState": "no_change" if initial.get("mutationStarted") is False else "runtime_connected" if facts.get("moduleConnected") else "runtime_state_verified",
    }
