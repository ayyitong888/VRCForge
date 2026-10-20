from __future__ import annotations

import sys
from contextlib import nullcontext
from types import SimpleNamespace

from starlette.requests import Request


def test_source_task_loop_shell_failure_plan_has_explicit_field():
    from agent_task_loop import AgentTaskLoop, prepare_shell_task_continuation

    requested = {"command": "fixture-timeout", "cwd": "fixture", "timeout_seconds": 1}
    loop = AgentTaskLoop("run controlled shell fixture", session_id="fixture-session", turn_id="fixture-turn")
    seed = loop.approval_seed(requested_tool="shell", requested_arguments=requested)
    prepared = prepare_shell_task_continuation(seed, {
        "shellSessionId": "fixture-shell",
        "status": "finished",
        "exitCode": 1,
        "timedOut": True,
        "cancelled": False,
        "terminationFailed": False,
    })
    assert prepared is not None
    plan = prepared["taskContinuation"]["terminalPlan"]
    assert plan["nextStep"] == "tool_failed"
    assert plan["shellNeeded"] is False


def test_real_subprocess_timeout_producer_response_is_saved_and_not_replayed(tmp_path, monkeypatch):
    import agent_shell_service
    import dashboard_server as server
    from agent_gateway import AgentGateway
    from agent_task_loop import AgentTaskLoop
    from dashboard_api_models import ChatTranscriptsRequest
    from runtime_planner_service import PlannerCatalogSnapshot, PlannerTurnMetadata, RuntimePlannerService

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    planner = RuntimePlannerService(
        catalog=SimpleNamespace(read=lambda *_args, **_kwargs: PlannerCatalogSnapshot()),
        desktop=SimpleNamespace(summarize_action_result=lambda _value: ""),
        turn=SimpleNamespace(
            bind=lambda *_args, **_kwargs: nullcontext(
                PlannerTurnMetadata(verified_context_limit=None, planner_label="fixture")
            )
        ),
    )
    gateway.bind_runtime_planner(planner)
    monkeypatch.setattr(
        agent_shell_service,
        "native_shell_argv",
        lambda _command: [sys.executable, "-c", "import time; time.sleep(5)"],
    )
    requested = {"command": "fixture-timeout", "cwd": str(tmp_path), "timeout_seconds": 1}
    try:
        with gateway.shell._execution_admission() as admission_id:
            result = gateway.shell._run_command(
                "fixture-timeout",
                tmp_path,
                admission_id=admission_id,
                timeout_seconds=1,
            )
        assert result["ok"] is False
        assert result["timedOut"] is True
        assert result["cancelled"] is False
        assert result["exitCode"] != 0
        assert gateway.shell.active_process_count == 0

        loop = AgentTaskLoop(
            "run controlled shell fixture",
            session_id="fixture-session",
            turn_id="fixture-turn",
            client_turn_id="fixture-client",
        )
        loop.require_action(kind="shell", tool="shell", arguments=requested)
        seed = loop.approval_seed(
            requested_tool="shell",
            requested_arguments=requested,
            continue_after_approval=True,
        )
        terminal = {
            "shellSessionId": "fixture-shell-timeout",
            "status": "finished",
            "exitCode": result["exitCode"],
            "timedOut": result["timedOut"],
            "cancelled": result["cancelled"],
            "terminationFailed": False,
            "result": result,
        }
        assert gateway.runtime_runs.stage_shell_continuation(
            shell_session_id=terminal["shellSessionId"],
            task_seed=seed,
            terminal_event=terminal,
        ) is True
        claimed = gateway.runtime_runs.claim_shell_continuation(terminal["shellSessionId"])
        assert claimed is not None
        resumed_event = {**claimed["terminalEvent"], "taskSeed": claimed["taskSeed"]}
        response = gateway.resume_runtime_task_after_shell(resumed_event)
        assert response["plan"]["nextStep"] == "tool_failed"
        assert response["plan"]["shellNeeded"] is False
        assert response["plan"]["task"]["actions"][-1]["status"] == "failed"
        assert gateway.runtime_runs.deliver_shell_continuation(terminal["shellSessionId"]) is True
        assert gateway.runtime_runs.claim_shell_continuation(terminal["shellSessionId"]) is None

        monkeypatch.setattr(server, "chat_transcripts_path", lambda: tmp_path / "chats.json")
        monkeypatch.setattr(server, "chat_project_index_path", lambda: tmp_path / "projects.json")
        chat = {
            "id": "fixture-shell-timeout-chat",
            "sessionId": "fixture-session",
            "items": [{"id": "agent-1", "type": "agent", "response": response}],
        }
        saved, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
        loaded = server.read_chat_transcripts(Request({"type": "http", "query_string": b""}))
        assert saved["ok"] is True
        assert loaded["chats"] == [chat]
        assert loaded["chats"][0]["items"][0]["response"]["plan"]["nextStep"] == "tool_failed"
    finally:
        gateway.shell.shutdown()
