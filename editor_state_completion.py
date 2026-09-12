"""Bounded independent readback for asynchronous Editor state changes."""
from contextlib import contextmanager
from uuid import uuid4
from execution_target import validate_runtime_execution_target, execution_target_digest
from operation_context import bind_operation_context, current_operation_context


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
