import asyncio

from test_agent_runtime_run_ledger import make_ledger

from channel_runtime_recovery import ChannelRuntimeRecovery


def run(coro):
    return asyncio.run(coro)


def test_active_run_blocks_new_submit_after_restart():
    async def case():
        class Ledger:
            def list_runs(self, **kwargs):
                return {"runs": [{"clientTurnId": "c1", "turnId": "t1", "status": "running", "sessionId": "s1"}]}

        decision = await ChannelRuntimeRecovery(Ledger()).check("s1", "c1", "P")
        assert not decision.allowed and decision.status == "active"

    run(case())


def test_terminal_same_id_is_not_reexecuted():
    async def case():
        recovery = ChannelRuntimeRecovery(lambda **kwargs: {"runs": [{"clientTurnId": "c1", "status": "completed"}]})
        decision = await recovery.check("s1", "c1")
        assert not decision.allowed and decision.status == "terminal"

    run(case())


def test_absent_record_allows_new_request_and_unknown_blocks():
    async def case():
        recovery = ChannelRuntimeRecovery(lambda **kwargs: {"runs": []})
        assert (await recovery.check("s", "new")).allowed
        unknown = ChannelRuntimeRecovery(lambda **kwargs: {"runs": [{"clientTurnId": "c", "status": "future_state"}]})
        decision = await unknown.check("s", "c")
        assert not decision.allowed and decision.status == "unknown"

    run(case())


def test_old_active_different_client_id_blocks_new_submit():
    async def case():
        recovery = ChannelRuntimeRecovery(lambda **kwargs: {"runs": [{"clientTurnId": "old", "status": "running"}]})
        decision = await recovery.check("s", "new")
        assert not decision.allowed and decision.status == "active"

    run(case())


def test_general_scope_is_explicit_and_control_filters_session_and_empty_project():
    async def case():
        rows = [
            {"sessionId": "s1", "projectRoot": "", "clientTurnId": "general", "status": "running"},
            {"sessionId": "s1", "projectRoot": "P", "clientTurnId": "project", "status": "running"},
            {"sessionId": "s2", "projectRoot": "", "clientTurnId": "foreign", "status": "running"},
        ]
        recovery = ChannelRuntimeRecovery(lambda **kwargs: {"runs": rows})
        general = await recovery.check("s1", "general", "")
        assert not general.allowed and general.status == "active"
        foreign = await ChannelRuntimeRecovery(lambda **kwargs: {"runs": [rows[2]]}).check("s1", "foreign", "")
        assert foreign.allowed and foreign.status == "absent"
        control = await recovery.control("s1", "", "")
        assert control.allowed and control.run["clientTurnId"] == "general"
        project_control = await recovery.control("s1", "", "P")
        assert project_control.allowed and project_control.run["clientTurnId"] == "project"
    run(case())


def test_project_selection_checkpoint_is_skipped_for_new_general_turn_but_replay_is_denied():
    async def case():
        row = {"sessionId": "s1", "projectRoot": "", "clientTurnId": "before-selection",
               "status": "blocked", "nextStep": "needs_user_action",
               "skillTool": "vrcforge_request_project_selection", "skillStatus": "needs_user_action",
               "approvalIds": [],
               "steps": [{"tool": "vrcforge_request_project_selection", "status": "needs_user_action",
                          "outcome": {"status": "needs_user_action"},
                          "result": {"projectSelectionRequested": True}}]}
        recovery = ChannelRuntimeRecovery(lambda **kwargs: {"runs": [row]})
        fresh = await recovery.check("s1", "new-general-turn", "")
        assert fresh.allowed and fresh.status == "absent"
        replay = await recovery.check("s1", "before-selection", "")
        assert not replay.allowed and replay.status == "terminal"
        other = dict(row, skillTool="other_tool")
        blocked = await ChannelRuntimeRecovery(lambda **kwargs: {"runs": [other]}).check("s1", "new", "")
        assert not blocked.allowed and blocked.status == "active"
    run(case())


def test_completed_continuations_use_real_ledger_shape_and_strict_scope(tmp_path):
    async def case():
        ledger, _lock = make_ledger(tmp_path)
        valid = {
            "schema": "vrcforge.runtime_turn_event.v1",
            "continuationSource": "foreground_completed",
            "sessionId": "s1", "turnId": "t1", "clientTurnId": "c1",
            "plan": {"reply": "latest"},
        }
        replacement = {**valid, "turnId": "t1-replacement", "plan": {"reply": "replacement"}}
        ledger.append({"event": "runtime_turn_completed", "status": "completed", "sessionId": "s1",
                       "turnId": "t1", "clientTurnId": "c1", "projectRoot": "P", "continuationEvent": valid})
        ledger.append({"event": "runtime_turn_completed", "status": "completed", "sessionId": "s1",
                       "turnId": "t1-replacement", "clientTurnId": "c1", "projectRoot": "P", "continuationEvent": replacement})
        ledger.append({"event": "runtime_turn_completed", "status": "blocked", "sessionId": "s1",
                       "turnId": "blocked", "clientTurnId": "blocked-client", "projectRoot": "P",
                       "continuationEvent": {**valid, "turnId": "blocked", "clientTurnId": "blocked-client"}})
        ledger.append({"event": "runtime_turn_completed", "status": "completed", "sessionId": "s1",
                       "turnId": "foreign-project", "clientTurnId": "foreign-project-client", "projectRoot": "Q",
                       "continuationEvent": {**valid, "turnId": "foreign-project", "clientTurnId": "foreign-project-client"}})
        ledger.append({"event": "runtime_turn_completed", "status": "completed", "sessionId": "s2",
                       "turnId": "foreign-session", "clientTurnId": "foreign-session-client", "projectRoot": "P",
                       "continuationEvent": {**valid, "sessionId": "s2", "turnId": "foreign-session", "clientTurnId": "foreign-session-client"}})
        recovery = ChannelRuntimeRecovery(ledger)
        result = await recovery.completed_continuations("s1", "P")
        assert result == [replacement]
        assert result[0]["plan"]["reply"] == "replacement"

    run(case())
