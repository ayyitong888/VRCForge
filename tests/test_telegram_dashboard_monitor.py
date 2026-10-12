import asyncio

import pytest

from telegram_dashboard_monitor import TelegramDashboardMonitorMapper
from channel_session_controls import compact_chat_items


def run(coro):
    return asyncio.run(coro)


def test_mapper_resolves_stable_chat_and_maps_real_turn_and_unity_fields():
    async def case():
        calls = []

        def load_chats():
            return {"chats": [{"id": "tg_1", "sessionId": "session-1", "projectPath": "P"}]}

        def list_runs(**kwargs):
            calls.append(kwargs)
            return {"runs": [{"sessionId": "session-1", "projectRoot": "P", "turnId": "turn-1", "clientTurnId": "client-1", "status": "completed", "progressSummary": "safe"}]}

        async def unity_status(project):
            return {"connected": True}

        mapper = TelegramDashboardMonitorMapper(load_chats, list_runs, unity_status)
        result = await mapper.snapshot("P", "tg_1")
        assert result == {"runId": "turn-1", "clientTurnId": "client-1", "taskStatus": "completed", "unityStatus": "connected", "progressSummary": "safe"}
        assert calls == [{"session_id": "session-1", "project_root": "P"}]

    run(case())


def test_mapper_projects_latest_exact_context_usage_with_peak_occupancy():
    async def case():
        usage = {
            "schema": "vrcforge.context_usage.v1", "exact": True,
            "provider": "openai", "model": "gpt-test",
            "inputTokens": 120, "lastInputTokens": 100,
            "peakInputTokens": 120, "cumulativeInputTokens": 900,
            "cumulativeTotalTokens": 940, "requestCount": 4,
            "nativeContextGuard": {"contextLimit": 1000},
        }
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session", "projectPath": "P",
                      "settings": {"provider": "openai", "model": "gpt-test", "contextLimit": 1000},
                      "items": [{"type": "agent", "response": {"contextUsage": usage}}]}],
            lambda **kwargs: {"runs": [{"sessionId": "session", "projectRoot": "P", "turnId": "turn", "status": "completed"}]},
            lambda project: {"connected": True},
        )
        result = await mapper.snapshot("P", "chat")
        assert result["contextUsage"]["exact"] is True
        assert result["contextUsage"]["usedTokens"] == 120
        assert result["contextUsage"]["occupancySource"] == "peakInputTokens"
        assert result["contextUsage"]["limitTokens"] == 1000
        assert "cumulativeInputTokens" not in result["contextUsage"]

    run(case())


def test_mapper_invalidates_context_after_model_switch_and_compaction():
    async def case():
        old = {"schema": "vrcforge.context_usage.v1", "exact": True,
               "provider": "openai", "model": "new", "peakInputTokens": 120}
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session", "projectPath": "P",
                      "settings": {"provider": "openai", "model": "new"},
                      "compaction": {"status": "applied"},
                      "items": [{"type": "compact"}, {"type": "agent", "response": {"contextUsage": old}}]}],
            lambda **kwargs: {"runs": []}, lambda project: {"connected": True},
        )
        result = await mapper.snapshot("P", "chat")
        assert result["contextUsage"]["status"] == "unavailable"
        assert result["contextUsage"]["unavailableReason"] == "compaction_applied"

    run(case())


def test_mapper_invalidates_context_after_model_switch_even_without_compaction():
    async def case():
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session", "projectPath": "P",
                      "settings": {"provider": "openai", "model": "new"},
                      "items": [{"type": "agent", "response": {"contextUsage": {
                          "schema": "vrcforge.context_usage.v1", "exact": True,
                          "provider": "openai", "model": "old", "peakInputTokens": 120}}}]}],
            lambda **kwargs: {"runs": []}, lambda project: {"connected": True},
        )
        result = await mapper.snapshot("P", "chat")
        assert result["contextUsage"]["status"] == "unavailable"
        assert result["contextUsage"]["unavailableReason"] == "model_changed"

    run(case())


def test_mapper_does_not_use_cumulative_input_as_window_occupancy():
    async def case():
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session", "projectPath": "P",
                      "settings": {"provider": "openai", "model": "same"},
                      "items": [{"type": "agent", "response": {"contextUsage": {
                          "schema": "vrcforge.context_usage.v1", "exact": True,
                          "provider": "openai", "model": "same", "inputTokens": 900,
                          "requestCount": 3}}}]}],
            lambda **kwargs: {"runs": []}, lambda project: {"connected": True},
        )
        result = await mapper.snapshot("P", "chat")
        assert result["contextUsage"]["status"] == "unavailable"

    run(case())


def test_mapper_accepts_post_compaction_run_usage_instead_of_old_chat_usage():
    async def case():
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session", "projectPath": "P",
                      "settings": {"provider": "openai", "model": "same"},
                      "compaction": {"status": "applied"},
                      "items": [{"type": "compact"}, {"type": "agent", "response": {
                          "contextUsage": {"exact": True, "provider": "openai", "model": "same", "peakInputTokens": 900}}}]}],
            lambda **kwargs: {"runs": [{"sessionId": "session", "projectRoot": "P", "turnId": "new",
                                         "status": "completed", "contextUsage": {
                                             "exact": True, "provider": "openai", "model": "same",
                                             "peakInputTokens": 40}}]},
            lambda project: {"connected": True},
        )
        result = await mapper.snapshot("P", "chat")
        assert result["contextUsage"]["status"] == "available"
        assert result["contextUsage"]["usedTokens"] == 40

    run(case())


def test_mapper_accepts_post_compaction_chat_usage_when_session_identity_matches():
    async def case():
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session-new", "projectPath": "P",
                      "settings": {"provider": "openai", "model": "same"},
                      "items": [{"type": "compact"}, {"type": "agent", "response": {
                          "sessionId": "session-new", "contextUsage": {
                              "exact": True, "provider": "openai", "model": "same",
                              "peakInputTokens": 40}}}]}],
            lambda **kwargs: {"runs": []}, lambda project: {"connected": True},
        )
        result = await mapper.snapshot("P", "chat")
        assert result["contextUsage"]["status"] == "available"
        assert result["contextUsage"]["usedTokens"] == 40

    run(case())


def test_compact_chat_items_removes_old_usage_and_cache_but_keeps_reply():
    chat = {"id": "chat", "sessionId": "old", "contextUsageCache": {"exact": True},
            "items": [{"id": "u", "type": "user", "text": "u"},
                      {"id": "a", "type": "agent", "response": {
                          "plan": {"reply": "reply"}, "contextUsage": {"exact": True}}}]}
    updated = compact_chat_items(chat, {"summary": "s", "summaryDigest": "digest",
                                        "recovery": {"schema": "vrcforge.context_compaction_recovery.v1",
                                                     "authority": "agent", "summarySource": "provider",
                                                     "sourceEntries": [{"role": "user", "text": "u"}],
                                                     "sourceDigest": "x", "summary": "s",
                                                     "summaryDigest": "y"}})
    assert updated is not None
    assert "contextUsageCache" not in updated
    agents = [item for item in updated["items"] if item.get("type") == "agent"]
    assert agents and "contextUsage" not in agents[0]["response"]
    assert agents[0]["response"]["plan"]["reply"] == "reply"


def test_mapper_rejects_cross_project_scope_and_marks_approval_cancelled():
    async def case():
        load = lambda: [{"id": "chat", "sessionId": "session", "projectPath": "P"}]
        runs = lambda **kwargs: {"runs": [{"sessionId": "session", "projectRoot": "P", "turnId": "turn", "clientTurnId": "client", "status": "blocked", "approvalId": "approval"}]}
        unity = lambda project: {"connected": False}
        mapper = TelegramDashboardMonitorMapper(load, runs, unity)
        with pytest.raises(PermissionError):
            await mapper.snapshot("Other", "chat")
        result = await mapper.snapshot("P", "session")
        assert result["taskStatus"] == "waiting_approval"
        assert result["unityStatus"] == "disconnected"

    run(case())


@pytest.mark.parametrize("project", ["", "P"])
def test_monitor_filters_runs_exactly_and_skips_unity_for_general(project):
    async def case():
        unity_calls = []
        def unity(value):
            unity_calls.append(value)
            return {"connected": True}
        mapper = TelegramDashboardMonitorMapper(
            lambda: [{"id": "chat", "sessionId": "session", "projectPath": project}],
            lambda **kwargs: {"runs": [
                {"sessionId": "foreign", "projectRoot": project, "turnId": "wrong-session", "status": "failed"},
                {"sessionId": "session", "projectRoot": "Other", "turnId": "wrong-project", "status": "failed"},
                {"sessionId": "session", "projectRoot": project, "turnId": "correct", "status": "running"}]}, unity)
        result = await mapper.snapshot(project, "chat")
        assert result["runId"] == "correct" and result["taskStatus"] == "running"
        assert result["unityStatus"] == ("connected" if project else "not_applicable")
        assert unity_calls == ([project] if project else [])
    run(case())
