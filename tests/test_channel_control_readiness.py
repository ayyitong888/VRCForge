import asyncio
from types import SimpleNamespace

import pytest

from channel_dashboard_session_controls import has_unresolved_state
from channel_session_controls import SessionControlError


def test_general_scope_is_exact_and_answers_in_flight_stay_blocking():
    async def run():
        approvals, questions = [], []
        async def list_approvals(project, session):
            assert project == "" and session == "s"
            return approvals
        def list_questions(**params):
            assert params["include_answered"] and params["session_id"] == "s"
            return {"questions": questions}
        async def check(items=None):
            return await has_unresolved_state(chat={"id": "c", "sessionId": "s", "items": items or []},
                project_root="", approval_port=SimpleNamespace(list_pending=list_approvals), list_questions=list_questions)
        assert not await check()
        questions.append({"projectRoot": "/other", "sessionId": "s", "status": "pending"})
        assert not await check()
        questions.append({"projectRoot": "", "sessionId": "s", "status": "answered", "runtimeContinuationStatus": "queued"})
        assert await check()
        questions[-1]["runtimeContinuationStatus"] = "completed"
        assert not await check()
        approvals.append({"status": "pending"})
        assert await check()
        approvals.clear()
        assert await check([{"type": "user", "queueStatus": "queued"}])
        assert await check([{"type": "subagent", "task": {"status": "running"}}])
        assert not await check([{"type": "subagent", "task": {"status": "completed"}}])
    asyncio.run(run())


def test_unreadable_authority_never_allows_context_mutation():
    async def run():
        async def unavailable(*args):
            raise RuntimeError("SECRET")
        with pytest.raises(SessionControlError) as exc:
            await has_unresolved_state(chat={"sessionId": "s", "items": []}, project_root="",
                approval_port=SimpleNamespace(list_pending=unavailable), list_questions=lambda **params: {"questions": []})
        assert str(exc.value) == "recovery_unavailable"
    asyncio.run(run())
