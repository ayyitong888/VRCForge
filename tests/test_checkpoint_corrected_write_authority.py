from checkpoint_user_policy import (
    bind_checkpoint_skip_for_correction,
    bind_user_checkpoint_skip,
    same_resolved_execution_target,
)
import pytest

from execution_target import ExecutionTargetBindingRegistry, ExecutionTargetError


PROJECT = "E:/unity/VRCForge_SampleAvatar_Test"
AVATAR_ID = "GlobalObjectId_V1-2-15e15a4449a726f4b937a9f328f50cc5-2852913921261062763-1380987042"
BRA_ID = "GlobalObjectId_V1-2-15e15a4449a726f4b937a9f328f50cc5-1861238682686342914-1380987042"
DRESS_ID = "GlobalObjectId_V1-2-15e15a4449a726f4b937a9f328f50cc5-671292009101761630-1380987042"
SOURCE_QUOTE = "�������ֶ������κμ��㣬��ȡ��ͼ���Ĳ��ʺͱ���ʱ��һ����"


def _target(*, scope="object", object_id="", avatar_id=AVATAR_ID, revision="r1"):
    target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": scope,
        "project": {"root": PROJECT, "projectId": "project-id-from-host-resolver"},
        "editor": {"unityPid": 4321, "processStartTime": "100.0", "coreInstanceId": "core-instance"},
        "scene": {"guid": "scene-guid-from-host-resolver", "revision": revision, "digest": f"digest-{revision}"},
        "avatar": {"globalObjectId": avatar_id} if avatar_id else {},
        "object": {"globalObjectId": object_id} if object_id else {},
        "component": {},
    }
    return target


def _failed_action(tmp_path, *, action_id, arguments, target, quote):
    policy = bind_user_checkpoint_skip(
        {
            "source": "app_user_message",
            "message": quote,
            "sessionId": "session-1",
            "turnId": "turn-1",
            "clientTurnId": "client-turn-1",
            "projectRoot": str(tmp_path),
        },
        {"mode": "skip", "quote": quote},
        session_id="session-1",
        turn_id="turn-1",
        client_turn_id="client-turn-1",
        project_root=str(tmp_path),
        tool="vrcforge_set_gameobject_active",
        arguments=arguments,
    )
    assert policy is not None
    return {
        "actionId": action_id,
        "kind": "write",
        "tool": "vrcforge_set_gameobject_active",
        "outcome": {"status": "failed"},
        "_checkpointUserPolicy": policy,
        "_checkpointWriteArguments": arguments,
        "_checkpointResolvedExecutionTarget": target,
    }


def _context(tmp_path):
    return {
        "taskId": "task-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "clientTurnId": "client-turn-1",
        "projectRoot": str(tmp_path),
    }


def test_replays_real_bra_and_dress_ref_corrections_with_stable_identity(tmp_path):
    # These are the exact write arguments and references in the retained
    # native trace: failed bra action_af675... and dress action_877151....
    # The replacement object IDs come from the corresponding recorded host
    # bind_execution_target calls; only the ref changed in each correction.
    cases = [
        {
            "id": "action_af6754115e7b9570851c2774",
            "path": "SampleAvatar_DefaultBody/SampleAvatar_top",
            "active": True,
            "old_ref": "vrcforge-target-m61x_fMGqri0vKiy99AOcMKEMOTv2u0W",
            "new_ref": "vrcforge-target-DePFzGIJJnrdhY1gdjZN9zoNMnXllo-m",
            "old_target": _target(scope="avatar", avatar_id=AVATAR_ID),
            "new_target": _target(scope="object", avatar_id=AVATAR_ID, object_id=BRA_ID, revision="r2"),
            "expect_rebind": True,
        },
        {
            "id": "action_877151af8854cdb908f49b76",
            "path": "SampleAvatar_DefaultBody/SampleAvatar_dress",
            "active": False,
            "old_ref": "vrcforge-target-UobXeuFBMki3fFV4ByLqyNpW0-Yx0z2r",
            "new_ref": "vrcforge-target-bar9wIK6skx9yLKdhK_qmESpKsKFUvRr",
            "old_target": _target(scope="object", object_id=DRESS_ID, revision="r1"),
            "new_target": _target(scope="object", object_id=DRESS_ID, revision="r2"),
            "expect_rebind": True,
        },
    ]
    quote = SOURCE_QUOTE
    for case in cases:
        original = {
            "projectPath": PROJECT,
            "gameObjectPath": case["path"],
            "active": case["active"],
            "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": case["old_ref"]},
        }
        corrected = {
            **original,
            "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": case["new_ref"]},
        }
        failed_action = _failed_action(
            tmp_path, action_id=case["id"], arguments=original,
            target=case["old_target"], quote=quote,
        )
        assert same_resolved_execution_target(case["old_target"], case["new_target"]) is case["expect_rebind"]
        rebound = bind_checkpoint_skip_for_correction(
            failed_action,
            case["id"],
            _context(tmp_path),
            tool="vrcforge_set_gameobject_active",
            arguments=corrected,
            original_execution_target=case["old_target"],
            corrected_execution_target=case["new_target"],
        )
        if case["expect_rebind"]:
            assert rebound is not None
            assert rebound["status"] == "skipped_by_user"
            assert rebound["argumentsSha256"] != failed_action["_checkpointUserPolicy"]["argumentsSha256"]
        else:
            # A correction must not inherit a skip across unrelated targets.
            assert rebound is None


def test_corrected_skip_rejects_other_target_project_and_write_parameters(tmp_path):
    quote = SOURCE_QUOTE
    original_target = _target(scope="object", object_id=BRA_ID)
    corrected_target = _target(scope="object", object_id=BRA_ID, revision="r2")
    original = {
        "projectPath": PROJECT,
        "gameObjectPath": "SampleAvatar_DefaultBody/SampleAvatar_top",
        "active": True,
        "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": "old-ref"},
    }
    corrected = {**original, "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": "new-ref"}}
    failed = _failed_action(tmp_path, action_id="action-1", arguments=original,
                            target=original_target, quote=quote)

    def bind(args=corrected, target=corrected_target, *, action="action-1", context=None):
        return bind_checkpoint_skip_for_correction(
            failed, action, context or _context(tmp_path),
            tool="vrcforge_set_gameobject_active", arguments=args,
            original_execution_target=original_target,
            corrected_execution_target=target,
        )

    assert bind(target=_target(scope="object", object_id=DRESS_ID)) is None
    assert bind(target=_target(scope="object", object_id=BRA_ID, avatar_id="different-avatar")) is None
    assert bind(target=_target(scope="object", object_id=BRA_ID, revision="r2")) is not None
    different_scene = _target(scope="object", object_id=BRA_ID, revision="r2")
    different_scene["scene"]["guid"] = "different-scene-guid"
    assert bind(target=different_scene) is None
    different_editor = _target(scope="object", object_id=BRA_ID, revision="r2")
    different_editor["editor"]["unityPid"] = 9876
    assert bind(target=different_editor) is None
    assert bind(target=_target(scope="object", object_id=BRA_ID, revision="r2",),
                context={**_context(tmp_path), "projectRoot": "E:/other-project"}) is None
    assert bind(args={**corrected, "active": False}) is None
    assert bind(args={**corrected, "gameObjectPath": "SampleAvatar_DefaultBody/other"}) is None
    assert bind(args={**corrected, "projectPath": "E:/other-project"}) is None
    assert bind(action="different-action") is None
    assert bind(context={**_context(tmp_path), "sessionId": "other-session"}) is None
    assert bind(target=_target(scope="avatar", avatar_id=AVATAR_ID)) is None
    assert bind(target=_target(scope="object", object_id=BRA_ID),
                context={**_context(tmp_path), "turnId": "other-turn"}) is None


def test_avatar_target_narrowing_preserves_skip_only_inside_same_avatar(tmp_path):
    original_target = _target(scope="avatar", avatar_id=AVATAR_ID)
    current = _target(scope="object", object_id=BRA_ID, avatar_id=AVATAR_ID, revision="r2")
    assert same_resolved_execution_target(original_target, current)
    assert not same_resolved_execution_target(
        original_target, _target(scope="object", object_id=BRA_ID, avatar_id="other-avatar"),
    )
    assert not same_resolved_execution_target(
        _target(scope="avatar", avatar_id=""), current,
    )
    assert not same_resolved_execution_target(current, original_target)


def test_bound_snapshot_reads_host_owned_identity_without_live_scene_resolution(monkeypatch):
    import execution_target

    target = _target(scope="object", object_id=DRESS_ID, revision="scene-before-write")
    monkeypatch.setattr(execution_target, "validate_runtime_execution_target", lambda *_a, **_k: target)
    registry = ExecutionTargetBindingRegistry()
    bound = registry.bind(target, project_root=PROJECT, session_context="runtime:session-1")
    reference = bound["executionTarget"]

    def must_not_live_resolve(*_args, **_kwargs):
        raise AssertionError("snapshot lookup must not revalidate the stale scene")

    monkeypatch.setattr(execution_target, "validate_runtime_execution_target", must_not_live_resolve)
    snapshot = registry.bound_target_snapshot(
        reference, project_root=PROJECT, session_context="runtime:session-1",
    )
    assert snapshot == target
    with pytest.raises(ExecutionTargetError):
        registry.bound_target_snapshot(
            reference, project_root=PROJECT, session_context="runtime:other-session",
        )
    with pytest.raises(ExecutionTargetError):
        registry.bound_target_snapshot(
            reference, project_root="E:/other-project", session_context="runtime:session-1",
        )
