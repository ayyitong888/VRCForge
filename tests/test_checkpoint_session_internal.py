from __future__ import annotations

import threading

from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from checkpoint_user_policy import bind_session_checkpoint_skip, bind_user_checkpoint_choice


def _state() -> AgentRuntimeSessionState:
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))


def test_session_choice_survives_turns_and_resets_with_session() -> None:
    state = _state()
    quote = "Turn off checkpoints for this session."
    choice = {
        "mode": "skip", "quote": quote, "source": "user_message",
        "sessionId": "s1", "clientTurnId": "c1", "projectRoot": "P",
    }
    assert state.checkpoint_choice("s1") is None  # New sessions default to required checkpoints.
    state.set_checkpoint_choice("s1", choice)
    state.append_turn("s1", now="now", updated_at="later", turn={"id": "t2"})
    assert state.checkpoint_choice("s1") == choice
    assert state.checkpoint_choice("s2") is None
    state.discard_session("s1")
    assert state.checkpoint_choice("s1") is None


def test_switch_requires_exact_current_trusted_user_quote_and_can_reenable() -> None:
    authority = {
        "source": "app_user_message", "message": "Please turn checkpoints off now.",
        "sessionId": "s1", "turnId": "t1", "clientTurnId": "c1", "projectRoot": "P",
    }
    for mode in ("skip", "require"):
        result = bind_user_checkpoint_choice(
            authority, {"mode": mode, "quote": "turn checkpoints off"},
            session_id="s1", turn_id="t1", client_turn_id="c1", project_root="P",
        )
        assert result == {
            "mode": mode, "quote": "turn checkpoints off", "source": "user_message",
            "sessionId": "s1", "turnId": "t1", "clientTurnId": "c1", "projectRoot": "P",
        }
    assert bind_user_checkpoint_choice(
        authority, {"mode": "skip", "quote": "not in message"},
        session_id="s1", turn_id="t1", client_turn_id="c1", project_root="P",
    ) is None
    assert bind_user_checkpoint_choice(
        {**authority, "source": "model_text"}, {"mode": "skip", "quote": "checkpoints off"},
        session_id="s1", turn_id="t1", client_turn_id="c1", project_root="P",
    ) is None


def test_session_skip_is_rebound_to_each_new_write() -> None:
    setting = {
        "mode": "skip", "quote": "Turn off checkpoints for this session.",
        "source": "user_message", "sessionId": "s1", "turnId": "t1",
        "clientTurnId": "c1", "projectRoot": "P",
    }
    first = bind_session_checkpoint_skip(
        setting, session_id="s1", turn_id="t2", client_turn_id="c2",
        project_root="P", tool="write", arguments={"path": "a"},
    )
    second = bind_session_checkpoint_skip(
        setting, session_id="s1", turn_id="t3", client_turn_id="c3",
        project_root="P", tool="write", arguments={"path": "b"},
    )
    assert first and second
    assert first["sourceQuote"] == second["sourceQuote"] == setting["quote"]
    assert first["turnId"] == "t2" and second["turnId"] == "t3"
    assert first["argumentsSha256"] != second["argumentsSha256"]
    assert bind_session_checkpoint_skip(
        setting, session_id="other", turn_id="t2", client_turn_id="c2",
        project_root="P", tool="write", arguments={"path": "a"},
    ) is None


def test_internal_session_switch_persists_then_reenables_default(monkeypatch) -> None:
    from test_agent_loop_p0 import AgentLoopP0Tests
    from agent_approval_transactions import AgentApprovalTransactionService

    fixture = AgentLoopP0Tests("test_session_checkpoint_switch_covers_corrected_write_transaction")
    fixture.setUp()
    try:
        gateway = fixture.gateway
        project = fixture._unity_project()
        session_id = "switch-session"
        skip_quote = "Turn off checkpoints for this session."
        require_quote = "Turn checkpoints back on for this session."
        plans = iter([
            {"planner": "llm", "summary": "Checkpoints are off.", "reply": "Checkpoints are off.",
             "nextStep": "done", "checkpointChoice": {"mode": "skip", "quote": skip_quote}},
            {"planner": "llm", "summary": "Write one value.", "writeNeeded": True,
             "writeTool": "vrcforge_set_gameobject_active", "writeParams": {
                 "projectPath": str(project), "gameObjectPath": "SampleAvatar_Default", "active": True,
                 "executionTarget": fixture._fixture_execution_target(project),
             }, "continueLoop": False, "nextStep": "call_tool"},
            {"planner": "llm", "summary": "Checkpoints are on.", "reply": "Checkpoints are on.",
             "nextStep": "done", "checkpointChoice": {"mode": "require", "quote": require_quote}},
            {"planner": "llm", "summary": "Write one value.", "writeNeeded": True,
             "writeTool": "vrcforge_set_gameobject_active", "writeParams": {
                 "projectPath": str(project), "gameObjectPath": "SampleAvatar_Default", "active": True,
                 "executionTarget": fixture._fixture_execution_target(project),
             }, "continueLoop": False, "nextStep": "call_tool"},
        ])
        observed_policies = []
        monkeypatch.setattr(gateway.runtime_planner, "plan_agent_turn", lambda *_a, **_k: next(plans))

        def capture(_self, _tool, _args, _agent, **kwargs):
            observed_policies.append(kwargs["task_context"].get("checkpointUserPolicy"))
            return {"ok": True, "status": "needs_approval", "outcome": {"status": "needs_approval"}}

        monkeypatch.setattr(AgentApprovalTransactionService, "_execute_write_request", capture)

        def turn(client_turn_id, message, *, source=False):
            params = {"message": message, "projectPath": str(project), "projectRoot": str(project),
                      "session_id": session_id, "client_turn_id": client_turn_id}
            if source:
                params["_humanUserMessageSource"] = {
                    "source": "app_user_message", "message": message, "sessionId": session_id,
                    "clientTurnId": client_turn_id, "projectRoot": str(project),
                }
            return gateway.runtime_message(params)

        turn("switch-1", skip_quote, source=True)
        assert gateway.runtime_sessions.checkpoint_choice(session_id)["mode"] == "skip"
        turn("write-2", "Write it")
        assert observed_policies[-1]["sourceQuote"] == skip_quote
        assert gateway.runtime_sessions.checkpoint_choice(session_id)["mode"] == "skip"
        turn("switch-3", require_quote, source=True)
        assert gateway.runtime_sessions.checkpoint_choice(session_id)["mode"] == "require"
        turn("write-4", "Write it")
        assert observed_policies[-1] is None
    finally:
        fixture.tearDown()
