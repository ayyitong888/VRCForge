import asyncio

import pytest

from telegram_bot_questions import TelegramBotQuestions, TelegramQuestionError


def run(coro):
    return asyncio.run(coro)


def test_list_is_scope_bound_and_public_fields_are_allowlisted():
    async def case():
        async def listing(**kwargs):
            assert kwargs == {"project": "P", "session": "S"}
            return {"questions": [{"questionId": "q1", "projectRoot": "P", "sessionId": "S", "status": "pending", "question": "Choose", "secret": "x", "options": [{"id": "a", "label": "A", "value": "hidden"}]}]}

        facade = TelegramBotQuestions(listing, lambda *_args, **_kwargs: {})
        rows = await facade.list("P", "S")
        assert rows == [{"questionId": "q1", "header": "", "question": "Choose", "status": "pending", "options": [{"id": "a", "label": "A", "description": ""}]}]

    run(case())


def test_answer_checks_current_scope_before_calling_service_and_reports_queued():
    async def case():
        calls = []

        async def listing(**kwargs):
            return {"questions": [{"questionId": "q1", "projectRoot": "P", "sessionId": "S", "question": "Choose", "status": "pending"}]}

        async def answer(question_id, params):
            calls.append((question_id, params))
            return {"ok": True, "question": {"questionId": question_id, "projectRoot": "P", "sessionId": "S", "question": "Choose", "status": "answered", "runtimeContinuationStatus": "interrupted"}}

        facade = TelegramBotQuestions(listing, answer)
        result = await facade.answer("q1", "A", "P", "S")
        assert result["status"] == "interrupted"
        assert calls == [("q1", {"answer": "A", "projectRoot": "P", "sessionId": "S"})]

    run(case())


def test_answer_out_of_scope_does_not_call_service():
    async def case():
        called = False

        async def answer(*_args):
            nonlocal called
            called = True

        async def listing(**kwargs):
            return {"questions": []}

        facade = TelegramBotQuestions(listing, answer)
        with pytest.raises(TelegramQuestionError) as caught:
            await facade.answer("q1", "A", "P", "S")
        assert caught.value.code == "question_not_in_scope"
        assert not called

    run(case())


def test_invalid_answer_and_unverified_service_result_are_rejected():
    async def case():
        async def listing(**kwargs):
            return {"questions": [{"questionId": "q1", "projectRoot": "P", "sessionId": "S", "status": "pending"}]}

        async def answer(question_id, params):
            return {"ok": False, "question": {"status": "answered"}}

        facade = TelegramBotQuestions(listing, answer)
        with pytest.raises(TelegramQuestionError):
            await facade.answer("q1", "", "P", "S")
        with pytest.raises(TelegramQuestionError):
            await facade.answer("q1", "x" * 2001, "P", "S")
        with pytest.raises(TelegramQuestionError) as caught:
            await facade.answer("q1", "valid", "P", "S")
        assert caught.value.code == "question_answer_unavailable"

    run(case())
