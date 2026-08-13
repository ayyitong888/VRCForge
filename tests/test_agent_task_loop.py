from __future__ import annotations

from agent_task_loop import (
    AgentTaskLoop,
    TASK_APPROVAL_CONTEXT_SCHEMA,
    approval_completion,
    approval_task_context,
    canonical_action_id,
    prepare_approval_task_continuation,
    prepare_sub_agent_task_continuation,
    rejected_approval_completion,
)


def ok_outcome(summary: str = "done") -> dict:
    return {
        "status": "ok",
        "summary": summary,
        "verification": {"state": "not_required", "checks": []},
    }


def test_failed_sub_agent_terminal_result_returns_to_exact_required_action() -> None:
    arguments = {
        "role": "project_index_review",
        "task": "find relevant prefabs",
        "displayName": "Manuka",
    }
    loop = AgentTaskLoop(
        "delegate the project review",
        session_id="owner-session",
        client_turn_id="owner-turn",
    )
    requirement = loop.require_action(
        kind="skill",
        tool="vrcforge_delegate_subagent",
        arguments=arguments,
    )
    seed = loop.approval_seed(
        tool_calls_used=1,
        requested_kind="skill",
        requested_tool="vrcforge_delegate_subagent",
        requested_arguments=arguments,
        continue_after_approval=True,
    )

    prepared = prepare_sub_agent_task_continuation(
        seed,
        {
            "subAgentTaskId": "sub-task-failed",
            "status": "failed",
            "error": "specialist failed",
        },
    )

    assert prepared is not None
    continuation = prepared["taskContinuation"]
    completion = continuation["completion"]
    assert completion["status"] == "failed"
    assert completion["actionId"] == requirement["actionId"]
    assert continuation["terminalPlan"] is None
    resumed = AgentTaskLoop.from_approval_context(continuation["context"], completion)
    gated = resumed.gate_terminal(
        {"planner": "llm", "nextStep": "done", "reply": "finished"}
    )
    assert gated["nextStep"] == "tool_failed"
    assert gated["task"]["actions"][0]["actionId"] == requirement["actionId"]


def test_cancelled_sub_agent_is_an_honest_terminal_not_a_tool_failure() -> None:
    arguments = {"role": "validation_triage", "task": "inspect the avatar"}
    loop = AgentTaskLoop("delegate validation", session_id="session", client_turn_id="turn")
    seed = loop.approval_seed(
        tool_calls_used=1,
        requested_kind="skill",
        requested_tool="vrcforge_delegate_subagent",
        requested_arguments=arguments,
        continue_after_approval=True,
    )

    prepared = prepare_sub_agent_task_continuation(
        seed,
        {
            "subAgentTaskId": "sub-task-cancelled",
            "status": "cancelled",
            "error": "Cancelled by the user.",
        },
    )

    assert prepared is not None
    continuation = prepared["taskContinuation"]
    assert continuation["completion"]["status"] == "cancelled"
    assert continuation["terminalPlan"]["nextStep"] == "cancelled"
    resumed = AgentTaskLoop.from_approval_context(
        continuation["context"],
        continuation["completion"],
    )
    gated = resumed.gate_terminal(continuation["terminalPlan"])
    assert gated["nextStep"] == "cancelled"
    assert gated["task"]["status"] == "cancelled"


def test_completed_sub_agent_with_failed_result_is_not_completion_evidence() -> None:
    arguments = {"role": "project_index_review", "task": "inspect the project"}
    loop = AgentTaskLoop("delegate review", session_id="session", client_turn_id="turn")
    seed = loop.approval_seed(
        tool_calls_used=1,
        requested_kind="skill",
        requested_tool="vrcforge_delegate_subagent",
        requested_arguments=arguments,
        continue_after_approval=True,
    )

    prepared = prepare_sub_agent_task_continuation(
        seed,
        {
            "subAgentTaskId": "sub-task-false-success",
            "status": "completed",
            "summary": "worker returned an error envelope",
            "result": {"ok": False, "error": "index failed"},
        },
    )

    assert prepared is not None
    continuation = prepared["taskContinuation"]
    assert continuation["completion"]["status"] == "failed"
    assert continuation["terminalPlan"] is None


def test_async_task_seed_preserves_bounded_parent_history_for_resampling() -> None:
    loop = AgentTaskLoop(
        "continue the referenced task",
        session_id="session",
        client_turn_id="turn",
        history=[
            {"role": "user", "text": "Use the avatar discussed above."},
            {"role": "agent", "text": "I will inspect the selected avatar."},
        ],
    )
    seed = loop.approval_seed(
        requested_kind="skill",
        requested_tool="vrcforge_delegate_subagent",
        requested_arguments={"role": "validation_triage", "task": "inspect it"},
    )

    prepared = prepare_sub_agent_task_continuation(
        seed,
        {
            "subAgentTaskId": "history-task",
            "status": "completed",
            "result": {"ok": True},
            "summary": "inspection completed",
        },
    )

    assert prepared is not None
    assert prepared["params"]["history"] == [
        {"role": "user", "text": "Use the avatar discussed above."},
        {"role": "agent", "text": "I will inspect the selected avatar."},
    ]


def test_redacted_durable_shell_seed_preserves_the_pre_execution_action_identity() -> None:
    arguments = {
        "command": "Write-Output ok",
        "env": {"API_KEY": "secret-value"},
        "background": True,
    }
    loop = AgentTaskLoop("run the host check", session_id="session", client_turn_id="turn")
    requirement = loop.require_action(kind="shell", tool="shell", arguments=arguments)
    seed = loop.approval_seed(
        requested_kind="shell",
        requested_tool="shell",
        requested_arguments=arguments,
    )
    assert seed["requestedActionId"] == requirement["actionId"]

    redacted_seed = {
        **seed,
        "requestedArguments": {
            **arguments,
            "env": {"API_KEY": "<redacted>"},
        },
    }
    context = approval_task_context(
        redacted_seed,
        tool="shell",
        arguments=redacted_seed["requestedArguments"],
    )

    assert context is not None
    assert context["requestedActionId"] == requirement["actionId"]
    assert context["priorRequirements"][0]["actionId"] == requirement["actionId"]


def test_sub_agent_continuation_rejects_a_seed_for_a_different_parent_session() -> None:
    loop = AgentTaskLoop(
        "delegate review",
        session_id="victim-session",
        client_turn_id="victim-turn",
    )
    seed = loop.approval_seed(
        requested_kind="skill",
        requested_tool="vrcforge_delegate_subagent",
        requested_arguments={"role": "project_index_review", "task": "inspect"},
    )

    prepared = prepare_sub_agent_task_continuation(
        seed,
        {
            "subAgentTaskId": "foreign-task",
            "parentSessionId": "attacker-session",
            "status": "completed",
            "result": {"ok": True},
        },
    )

    assert prepared is None


def test_llm_completion_requires_exact_completed_action_evidence() -> None:
    loop = AgentTaskLoop("inspect materials")
    action = loop.record_action(
        kind="skill",
        tool="vrcforge_scan_materials",
        arguments={"avatarPath": "Avatar"},
        raw_result={"ok": True, "status": "executed"},
        outcome=ok_outcome("materials scanned"),
    )

    missing = loop.gate_terminal({"planner": "llm", "nextStep": "done", "reply": "done"})
    unknown = loop.gate_terminal(
        {
            "planner": "llm",
            "nextStep": "done",
            "reply": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": ["action_not_executed"],
            },
        }
    )
    accepted = loop.gate_terminal(
        {
            "planner": "llm",
            "nextStep": "done",
            "reply": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": [action["actionId"]],
            },
        }
    )

    assert missing["nextStep"] == "completion_unverified"
    assert unknown["nextStep"] == "completion_unverified"
    assert accepted["nextStep"] == "done"
    assert accepted["taskCompletion"]["evidenceActionIds"] == [action["actionId"]]


def test_background_shell_running_cannot_be_claimed_complete() -> None:
    loop = AgentTaskLoop("run the worker")
    action = loop.record_action(
        kind="shell",
        tool="shell",
        arguments={"command": "python worker.py"},
        raw_result={
            "ok": True,
            "status": "running",
            "session": {"sessionId": "shell-1", "status": "running"},
        },
        outcome=ok_outcome("process started"),
    )

    gated = loop.gate_terminal(
        {
            "planner": "llm",
            "nextStep": "done",
            "reply": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": [action["actionId"]],
            },
        }
    )

    assert action["status"] == "running"
    assert gated["nextStep"] == "waiting_for_tool"
    assert gated["completionGate"] == {
        "status": "running",
        "reason": "action_not_terminal",
    }


def test_same_background_action_can_finish_before_the_completion_claim() -> None:
    loop = AgentTaskLoop("run the worker", session_id="session-shell")
    arguments = {"command": "python worker.py"}
    running = loop.record_action(
        kind="shell",
        tool="shell",
        arguments=arguments,
        raw_result={
            "ok": True,
            "status": "running",
            "session": {"sessionId": "shell-1", "status": "running"},
        },
        outcome=ok_outcome("worker started"),
    )
    finished = loop.record_action(
        kind="shell",
        tool="shell",
        arguments=arguments,
        raw_result={
            "ok": True,
            "status": "finished",
            "result": {"exitCode": 0, "stdout": "done", "stderr": ""},
        },
        outcome=ok_outcome("worker completed"),
    )

    gated = loop.gate_terminal(
        {
            "planner": "llm",
            "reply": "done",
            "nextStep": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": [finished["actionId"]],
            },
        }
    )

    assert finished["actionId"] == running["actionId"]
    assert finished["attempts"] == 2
    assert gated["nextStep"] == "done"
    assert gated["task"]["status"] == "completed"


def test_shell_completion_requires_the_runtime_owned_zero_exit_verifier() -> None:
    loop = AgentTaskLoop("run the host check")
    arguments = {"command": "Write-Output ok"}
    requirement = loop.require_action(kind="shell", tool="shell", arguments=arguments)

    passed = loop.record_action(
        kind="shell",
        tool="shell",
        arguments=arguments,
        raw_result={"ok": True, "status": "finished", "result": {"exitCode": 0}},
        outcome=ok_outcome("host check completed"),
    )

    assert requirement["verificationProfile"] == "shell_exit_zero"
    assert passed["status"] == "completed"
    assert passed["outcome"]["verification"] == {
        "state": "passed",
        "checks": [{"kind": "exitCode", "state": "passed"}],
    }


def test_shell_zero_exit_verifier_does_not_turn_a_running_process_terminal() -> None:
    loop = AgentTaskLoop("run the worker")
    arguments = {"command": "python worker.py"}
    loop.require_action(kind="shell", tool="shell", arguments=arguments)

    running = loop.record_action(
        kind="shell",
        tool="shell",
        arguments=arguments,
        raw_result={
            "ok": True,
            "status": "running",
            "session": {"sessionId": "shell-1", "status": "running"},
        },
        outcome=ok_outcome("worker started"),
    )

    assert running["status"] == "running"


def test_corrected_action_supersedes_the_failed_branch_and_keeps_the_requirement() -> None:
    loop = AgentTaskLoop("inspect the selected avatar")
    bad_args = {"avatarPath": "Missing"}
    good_args = {"avatarPath": "AvatarRoot"}
    loop.require_action(kind="skill", tool="vrcforge_scan_materials", arguments=bad_args)
    failed = loop.record_action(
        kind="skill",
        tool="vrcforge_scan_materials",
        arguments=bad_args,
        raw_result={"ok": False, "status": "failed", "error": "missing avatar"},
        outcome={"status": "failed", "summary": "missing avatar"},
    )
    corrected = loop.record_action(
        kind="skill",
        tool="vrcforge_scan_materials",
        arguments=good_args,
        raw_result={"ok": True, "status": "executed", "result": {"materials": []}},
        outcome=ok_outcome("materials scanned"),
        correction_for_action_id=failed["actionId"],
    )

    gated = loop.gate_terminal(
        {
            "planner": "llm",
            "nextStep": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": [corrected["actionId"]],
            },
        }
    )

    assert gated["nextStep"] == "done"
    actions = gated["task"]["actions"]
    assert actions[0]["status"] == "superseded"
    assert actions[0]["supersededBy"] == corrected["actionId"]
    assert gated["task"]["requirements"][0]["actionId"] == corrected["actionId"]


def test_unrelated_diagnostic_action_cannot_supersede_a_failed_requirement() -> None:
    loop = AgentTaskLoop("inspect the selected avatar")
    bad_args = {"avatarPath": "Missing"}
    loop.require_action(kind="skill", tool="vrcforge_scan_materials", arguments=bad_args)
    failed = loop.record_action(
        kind="skill",
        tool="vrcforge_scan_materials",
        arguments=bad_args,
        raw_result={"ok": False, "status": "failed", "error": "missing avatar"},
        outcome={"status": "failed", "summary": "missing avatar"},
    )
    diagnostic = loop.record_action(
        kind="skill",
        tool="vrcforge_health",
        arguments={},
        raw_result={"ok": True, "status": "executed"},
        outcome=ok_outcome("runtime healthy"),
        correction_for_action_id=failed["actionId"],
    )

    snapshot = loop.snapshot()
    assert snapshot["actions"][0]["status"] == "failed"
    assert "correctedActionId" not in diagnostic
    assert snapshot["requirements"][0]["actionId"] == failed["actionId"]
    gated = loop.gate_terminal(
        {
            "planner": "llm",
            "nextStep": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": [diagnostic["actionId"]],
            },
        }
    )
    assert gated["nextStep"] == "tool_failed"


def test_structured_failure_survives_the_task_projection_for_the_next_plan() -> None:
    loop = AgentTaskLoop("inspect materials")
    loop.record_action(
        kind="skill",
        tool="vrcforge_scan_materials",
        arguments={},
        raw_result={"ok": False, "status": "failed"},
        outcome={
            "status": "failed",
            "summary": "Unity Core is still starting.",
            "error": {
                "type": "unity_core",
                "code": "unity_core_not_ready",
                "likelyCauses": ["Unity is compiling"],
                "nextActions": ["Wait for compilation"],
                "retryable": True,
            },
        },
    )

    observation = loop.planner_observations()[0]

    assert observation["outcome"]["error"] == {
        "type": "unity_core",
        "code": "unity_core_not_ready",
        "likelyCauses": ["Unity is compiling"],
        "nextActions": ["Wait for compilation"],
        "retryable": True,
    }


def test_create_gameobject_requires_persisted_scene_readback_and_stable_console() -> None:
    loop = AgentTaskLoop("create one object")
    failed = loop.record_action(
        kind="write",
        tool="vrcforge_create_gameobject",
        arguments={"name": "Probe"},
        raw_result={"ok": True, "status": "applied", "gameObjectPath": "Probe"},
        outcome=ok_outcome("created"),
    )

    assert failed["status"] == "needs_user_action"
    assert failed["outcome"]["verification"]["state"] == "needs_user_action"
    assert loop.gate_terminal({"planner": "runtime", "nextStep": "done"})["nextStep"] == "needs_user_action"

    console_failed_loop = AgentTaskLoop("create one object")
    console_failed = console_failed_loop.record_action(
        kind="write",
        tool="vrcforge_create_gameobject",
        arguments={"name": "Probe"},
        raw_result={
            "ok": True,
            "status": "applied",
            "persistedReadback": True,
            "sceneSaved": True,
            "consoleVerified": False,
        },
        outcome=ok_outcome("created"),
    )
    assert console_failed["status"] == "needs_user_action"
    assert console_failed["outcome"]["verification"]["checks"][-1] == {
        "kind": "consoleVerified",
        "state": "failed",
    }

    verified_loop = AgentTaskLoop("create one object")
    verified = verified_loop.record_action(
        kind="write",
        tool="vrcforge_create_gameobject",
        arguments={"name": "Probe"},
        raw_result={
            "ok": True,
            "status": "applied",
            "gameObjectPath": "Probe",
            "persistedReadback": True,
            "sceneSaved": True,
            "consoleVerified": True,
        },
        outcome=ok_outcome("created"),
    )

    assert verified["status"] == "completed"
    assert verified["outcome"]["verification"]["state"] == "passed"


def test_declared_verification_profile_is_executed_for_the_required_action() -> None:
    loop = AgentTaskLoop("apply a persisted Unity change")
    arguments = {"target": "Avatar"}
    loop.require_action(
        kind="write",
        tool="fixture_unity_write",
        arguments=arguments,
        verification_profile="persisted_scene_write",
    )

    failed = loop.record_action(
        kind="write",
        tool="fixture_unity_write",
        arguments=arguments,
        raw_result={"ok": True, "persistedReadback": True, "sceneSaved": False},
        outcome=ok_outcome("applied"),
    )

    assert failed["status"] == "needs_user_action"
    assert failed["outcome"]["verification"]["checks"] == [
        {"kind": "persistedReadback", "state": "passed"},
        {"kind": "sceneSaved", "state": "failed"},
    ]


def test_multi_angle_visual_requires_managed_capture_evidence_verification() -> None:
    loop = AgentTaskLoop("visually verify the managed capture")
    arguments = {"captureReceipt": "opaque-receipt"}
    loop.require_action(
        kind="skill",
        tool="vrcforge_vision_audit_multi",
        arguments=arguments,
        verification_profile="multi_angle_visual",
    )
    missing = loop.record_action(
        kind="skill",
        tool="vrcforge_vision_audit_multi",
        arguments=arguments,
        raw_result={"ok": True, "visualVerified": True, "coverageComplete": True},
        outcome=ok_outcome("visual audit passed"),
    )

    assert missing["status"] == "needs_user_action"
    assert missing["outcome"]["verification"]["checks"][-1] == {
        "kind": "captureEvidenceVerified",
        "state": "failed",
    }


def test_unknown_declared_verification_profile_fails_closed() -> None:
    loop = AgentTaskLoop("verify the requested action")
    loop.require_action(
        kind="skill",
        tool="vrcforge_health",
        arguments={},
        verification_profile="typo_profile",
    )
    action = loop.record_action(
        kind="skill",
        tool="vrcforge_health",
        arguments={},
        raw_result={"ok": True, "status": "executed"},
        outcome=ok_outcome("health checked"),
    )

    assert action["status"] == "needs_user_action"
    assert action["outcome"]["error"]["code"] == "verification_profile_unknown"
    gated = loop.gate_terminal({"planner": "llm", "nextStep": "done", "reply": "done"})
    assert gated["nextStep"] == "needs_user_action"


def test_approval_context_binds_identity_and_verifies_terminal_result() -> None:
    loop = AgentTaskLoop(
        "create one object",
        session_id="session-1",
        turn_id="turn-1",
        client_turn_id="client-1",
        project_root="D:/Unity/Project",
    )
    arguments = {"name": "Probe", "projectRoot": "D:/Unity/Project"}
    context = approval_task_context(
        loop.approval_seed(),
        tool="vrcforge_create_gameobject",
        arguments=arguments,
    )

    assert context is not None
    assert context["schema"] == TASK_APPROVAL_CONTEXT_SCHEMA
    assert context["actionId"] == canonical_action_id(
        "write", "vrcforge_create_gameobject", arguments
    )
    assert context["sessionId"] == "session-1"

    incomplete = approval_completion(
        context,
        raw_result={"ok": True, "gameObjectPath": "Probe"},
        outcome=ok_outcome("created"),
    )
    complete = approval_completion(
        context,
        raw_result={
            "ok": True,
            "gameObjectPath": "Probe",
            "persistedReadback": True,
            "sceneSaved": True,
            "consoleVerified": True,
        },
        outcome=ok_outcome("created"),
    )

    assert incomplete is not None and incomplete["status"] == "needs_user_action"
    assert complete is not None and complete["status"] == "completed"


def test_approval_continuation_restores_prior_actions_budget_and_identity() -> None:
    loop = AgentTaskLoop(
        "inspect then create",
        session_id="session-1",
        turn_id="turn-1",
        client_turn_id="client-1",
        project_root="D:/Unity/Project",
        agent_name="desktop-agent",
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    prior = loop.record_action(
        kind="skill",
        tool="vrcforge_scan_materials",
        arguments={"avatarPath": "Avatar"},
        raw_result={"ok": True, "status": "executed"},
        outcome=ok_outcome("materials scanned"),
    )
    loop.activate_skill_policy(
        name="fixture-skill",
        instructions="Inspect first, then create the requested object.",
        allowed_tools=["vrcforge_create_gameobject"],
        disallowed_tools=["vrcforge_shell_execute"],
    )
    requested = {"name": "Probe", "projectRoot": "D:/Unity/Project"}
    context = approval_task_context(
        loop.approval_seed(
            tool_calls_used=2,
            exposure_layer="execution",
            requested_tool="vrcforge_create_gameobject",
            requested_arguments=requested,
        ),
        tool="vrcforge_create_gameobject",
        arguments={**requested, "prepared": True},
    )
    assert context is not None
    completion = approval_completion(
        context,
        raw_result={"ok": True, "persistedReadback": True, "sceneSaved": True},
        outcome=ok_outcome("created"),
    )
    assert completion is not None

    resumed = AgentTaskLoop.from_approval_context(context, completion)
    observations = resumed.planner_observations()

    assert resumed.task_id == loop.task_id
    assert resumed.tool_calls_used == 2
    assert resumed.exposure_layer == "execution"
    assert [item["actionId"] for item in observations if item.get("actionId")] == [
        prior["actionId"],
        completion["actionId"],
    ]
    assert observations[0] == {
        "kind": "skill_context",
        "status": "loaded",
        "synthetic": True,
        "skillContext": {
            "name": "fixture-skill",
            "instructions": "Inspect first, then create the requested object.",
            "allowedTools": ["vrcforge_create_gameobject"],
            "disallowedTools": ["vrcforge_shell_execute"],
        },
    }
    assert context["requestedArguments"] == requested
    assert resumed.skill_policy_block_reason("vrcforge_create_gameobject") == ""
    assert resumed.skill_policy_block_reason("vrcforge_shell_execute") == "skill_tool_disallowed"


def test_capture_approval_continuation_returns_only_bounded_receipt_to_planner() -> None:
    loop = AgentTaskLoop(
        "capture and visually verify",
        session_id="session-visual",
        client_turn_id="turn-visual",
    )
    arguments = {"angles": ["front", "back"]}
    context = approval_task_context(
        loop.approval_seed(
            requested_tool="vrcforge_capture_multi_screenshot",
            requested_arguments=arguments,
        ),
        tool="vrcforge_capture_multi_screenshot",
        arguments=arguments,
    )
    assert context is not None
    completion = approval_completion(
        context,
        raw_result={
            "ok": True,
            "evidence": [{"ref": "visual_123", "kind": "managed_visual_capture"}],
        },
        outcome=ok_outcome("captured"),
    )
    assert completion is not None
    prepared = prepare_approval_task_continuation(
        {"id": "approval-visual", "taskContext": context},
        {
            "status": "applied",
            "taskCompletion": completion,
            "result": {
                "data": {
                    "captureReceipt": "opaque-capability",
                    "captureEvidenceId": "visual_123",
                    "angles": ["front", "back"],
                },
                "privatePath": "D:/private/vision_front.png",
            },
        },
    )

    assert prepared is not None
    assert prepared["taskContinuation"]["source"] == "approval_finished"
    observation = prepared["taskContinuation"]["plannerObservation"]
    assert observation["result"] == {
        "captureReceipt": "opaque-capability",
        "captureEvidenceId": "visual_123",
        "angles": ["front", "back"],
    }
    assert "privatePath" not in str(observation)


def test_skill_context_is_bounded_in_task_and_approval_continuations() -> None:
    loop = AgentTaskLoop("follow the loaded skill", session_id="session-1")
    loop.activate_skill_policy(
        name="n" * 200,
        instructions="i" * 7_000,
        allowed_tools=["allowed", "allowed", *(f"allow-{index}" for index in range(40))],
        disallowed_tools=["denied", "denied", *(f"deny-{index}" for index in range(40))],
    )

    seed = loop.approval_seed(
        requested_tool="vrcforge_create_gameobject",
        requested_arguments={"name": "Probe"},
    )
    context = approval_task_context(
        seed,
        tool="vrcforge_create_gameobject",
        arguments={"name": "Probe"},
    )

    assert context is not None
    assert seed["skillContext"] == context["skillContext"]
    skill_context = context["skillContext"]
    assert len(skill_context["name"]) == 160
    assert len(skill_context["instructions"]) == 6_000
    assert 1 <= len(skill_context["allowedTools"]) <= 32
    assert 1 <= len(skill_context["disallowedTools"]) <= 32
    assert len(skill_context["allowedTools"]) == len(set(skill_context["allowedTools"]))
    assert len(skill_context["disallowedTools"]) == len(set(skill_context["disallowedTools"]))

    completion = approval_completion(
        context,
        raw_result={"ok": True, "persistedReadback": True, "sceneSaved": True},
        outcome=ok_outcome("created"),
    )
    assert completion is not None
    resumed = AgentTaskLoop.from_approval_context(context, completion)
    synthetic = resumed.planner_observations()[0]

    assert synthetic["synthetic"] is True
    assert synthetic["skillContext"] == skill_context
    assert "result" not in synthetic
    assert resumed.skill_policy_block_reason("allowed") == ""
    assert resumed.skill_policy_block_reason("denied") == "skill_tool_disallowed"


def test_rejected_approval_is_a_terminal_needs_user_action_outcome() -> None:
    loop = AgentTaskLoop("create one object", session_id="session-1")
    context = approval_task_context(
        loop.approval_seed(
            tool_calls_used=1,
            exposure_layer="execution",
            requested_tool="vrcforge_create_gameobject",
            requested_arguments={"name": "Probe"},
        ),
        tool="vrcforge_create_gameobject",
        arguments={"name": "Probe"},
    )

    completion = rejected_approval_completion(context)

    assert completion is not None
    assert completion["status"] == "needs_user_action"
    assert completion["outcome"]["status"] == "needs_user_action"


def test_read_only_skill_requirement_defaults_to_canonical_verification() -> None:
    loop = AgentTaskLoop("inspect the desktop", session_id="session-1")

    requirement = loop.require_action(
        kind="skill",
        tool="vrcforge_agent_desktop_action",
        arguments={"action": "list_windows"},
    )

    assert requirement["verificationProfile"] == "canonical_tool_result"


def test_resumed_task_projects_bounded_historical_action_identity() -> None:
    loop = AgentTaskLoop("inspect then continue", session_id="session-1")
    arguments = {"action": "computer_use"}
    requirement = loop.require_action(
        kind="skill",
        tool="vrcforge_agent_desktop_action",
        arguments=arguments,
    )
    loop.record_action(
        kind="skill",
        tool="vrcforge_agent_desktop_action",
        arguments=arguments,
        raw_result={"ok": True},
        outcome=ok_outcome("desktop ready"),
        action_id=requirement["actionId"],
        pre_provider=True,
    )

    steps = loop.historical_steps()

    assert steps == [
        {
            "index": 0,
            "kind": "skill",
            "tool": "vrcforge_agent_desktop_action",
            "status": "completed",
            "actionId": requirement["actionId"],
            "historical": True,
            "preProvider": True,
        }
    ]


def test_provider_request_count_survives_an_async_task_boundary() -> None:
    loop = AgentTaskLoop("continue after approval", session_id="session-1")
    seed = loop.approval_seed(
        requested_tool="vrcforge_capture_multi_screenshot",
        requested_arguments={"angles": ["front", "back"]},
        provider_request_count=2,
    )
    context = approval_task_context(
        seed,
        tool="vrcforge_capture_multi_screenshot",
        arguments={"angles": ["front", "back"]},
    )
    assert context is not None
    completion = approval_completion(
        context,
        raw_result={"ok": True},
        outcome=ok_outcome("captured"),
    )
    assert completion is not None

    resumed = AgentTaskLoop.from_approval_context(context, completion)

    assert resumed.provider_request_count == 2
    assert resumed.approval_seed()["providerRequestCount"] == 2


def test_tool_call_count_survives_an_async_task_boundary_beyond_three() -> None:
    loop = AgentTaskLoop("continue after approval", session_id="session-1")
    seed = loop.approval_seed(
        requested_tool="vrcforge_apply_shader_tuning",
        requested_arguments={"avatarPath": "Avatar"},
        tool_calls_used=7,
    )
    context = approval_task_context(
        seed,
        tool="vrcforge_apply_shader_tuning",
        arguments={"avatarPath": "Avatar"},
    )
    assert context is not None
    completion = approval_completion(
        context,
        raw_result={"ok": True},
        outcome=ok_outcome("applied"),
    )
    assert completion is not None

    resumed = AgentTaskLoop.from_approval_context(context, completion)

    assert resumed.tool_calls_used == 7
    assert resumed.approval_seed()["toolCallsUsed"] == 7


def test_managed_capture_identity_survives_beyond_the_bounded_action_window() -> None:
    loop = AgentTaskLoop("capture, inspect, then audit", session_id="session-visual")
    capture_arguments = {"angles": ["front", "back"]}
    capture_requirement = loop.require_action(
        kind="write",
        tool="vrcforge_capture_multi_screenshot",
        arguments=capture_arguments,
    )
    capture = loop.record_action(
        kind="write",
        tool="vrcforge_capture_multi_screenshot",
        arguments=capture_arguments,
        raw_result={"ok": True},
        outcome={
            **ok_outcome("captured"),
            "evidence": [{"ref": "visual-1", "kind": "managed_visual_capture"}],
        },
        action_id=capture_requirement["actionId"],
    )
    for index in range(3):
        arguments = {"probe": index}
        requirement = loop.require_action(
            kind="skill",
            tool=f"vrcforge_read_probe_{index}",
            arguments=arguments,
        )
        loop.record_action(
            kind="skill",
            tool=f"vrcforge_read_probe_{index}",
            arguments=arguments,
            raw_result={"ok": True},
            outcome=ok_outcome(f"probe {index}"),
            action_id=requirement["actionId"],
        )

    seed = loop.approval_seed()
    context = approval_task_context(
        seed,
        tool="vrcforge_vision_audit_multi",
        arguments={"captureReceipt": "opaque"},
    )

    assert capture["actionId"] not in {
        item["actionId"] for item in seed["actions"]
    }
    assert seed["managedVisualCaptureActionIds"] == [capture["actionId"]]
    assert context is not None
    assert context["managedVisualCaptureActionIds"] == [capture["actionId"]]


def test_visual_capture_and_audit_use_registered_completion_verifiers() -> None:
    loop = AgentTaskLoop("capture and visually audit", session_id="session-visual")
    capture = loop.require_action(
        kind="write",
        tool="vrcforge_capture_multi_screenshot",
        arguments={"angles": ["front", "back"]},
    )
    visual = loop.require_action(
        kind="skill",
        tool="vrcforge_vision_audit_multi",
        arguments={"captureReceipt": "opaque"},
    )

    assert capture["verificationProfile"] == "canonical_tool_result"
    assert visual["verificationProfile"] == "multi_angle_visual"

    incomplete = loop.record_action(
        kind="skill",
        tool="vrcforge_vision_audit_multi",
        arguments={"captureReceipt": "opaque"},
        raw_result={
            "ok": True,
            "visualVerified": True,
            "coverageComplete": True,
        },
        outcome=ok_outcome("partial audit"),
        action_id=visual["actionId"],
    )
    assert incomplete["status"] == "needs_user_action"
    assert incomplete["outcome"]["verification"]["state"] == "needs_user_action"
