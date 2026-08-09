from __future__ import annotations

from agent_task_loop import (
    AgentTaskLoop,
    TASK_APPROVAL_CONTEXT_SCHEMA,
    approval_completion,
    approval_task_context,
    canonical_action_id,
    rejected_approval_completion,
)


def ok_outcome(summary: str = "done") -> dict:
    return {
        "status": "ok",
        "summary": summary,
        "verification": {"state": "not_required", "checks": []},
    }


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


def test_deterministic_single_read_completes_from_its_exact_result() -> None:
    loop = AgentTaskLoop("list avatars")
    action = loop.record_action(
        kind="skill",
        tool="vrcforge_list_avatars",
        arguments={},
        raw_result={"ok": True, "status": "executed", "result": {"avatars": []}},
        outcome=ok_outcome("avatar list read"),
    )

    gated = loop.gate_terminal(
        {"planner": "deterministic-local", "nextStep": "call_skill", "reply": "listed"}
    )

    assert gated["nextStep"] == "done"
    assert gated["taskCompletion"]["evidenceActionIds"] == [action["actionId"]]


def test_create_gameobject_requires_persisted_scene_readback() -> None:
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
        },
        outcome=ok_outcome("created"),
    )

    assert verified["status"] == "completed"
    assert verified["outcome"]["verification"]["state"] == "passed"


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
    assert [item["actionId"] for item in observations] == [
        prior["actionId"],
        completion["actionId"],
    ]
    assert context["requestedArguments"] == requested


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
