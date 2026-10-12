"""Scope-bound, small public facade over the existing AgentQuestionService."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any


class TelegramQuestionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TelegramBotQuestions:
    def __init__(
        self,
        list_pending: Callable[..., Awaitable[Mapping[str, Any]]],
        answer: Callable[..., Awaitable[Mapping[str, Any]]],
        *,
        max_items: int = 8,
        max_text: int = 1000,
    ) -> None:
        if max_items <= 0 or max_text <= 0:
            raise ValueError("question bounds must be positive")
        self._list_pending = list_pending
        self._answer = answer
        self._max_items = max_items
        self._max_text = max_text

    async def list(self, project_root: str, session_id: str) -> list[dict[str, Any]]:
        project, session = self._scope(project_root, session_id)
        payload = await self._list_pending(project=project, session=session)
        rows = payload.get("questions") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise TelegramQuestionError("question_scope_unavailable")
        scoped = [
            row for row in rows
            if isinstance(row, Mapping)
            and str(row.get("projectRoot") or "") == project
            and str(row.get("sessionId") or "") == session
            and str(row.get("status") or "") == "pending"
        ]
        return [_public_question(row, self._max_text) for row in scoped[: self._max_items]]

    async def answer(self, question_id: str, answer: str, project_root: str, session_id: str) -> dict[str, Any]:
        project, session = self._scope(project_root, session_id)
        resolved = str(question_id or "").strip()
        if not resolved:
            raise TelegramQuestionError("question_id_required")
        answer_text = str(answer or "")
        if not answer_text.strip() or len(answer_text) > 2000:
            raise TelegramQuestionError("question_answer_invalid")
        current = await self.list(project, session)
        if not any(str(item.get("questionId") or "") == resolved for item in current):
            raise TelegramQuestionError("question_not_in_scope")
        payload = await self._answer(
            resolved,
            {"answer": answer_text, "projectRoot": project, "sessionId": session},
        )
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise TelegramQuestionError("question_answer_unavailable")
        question = payload.get("question")
        if not isinstance(question, Mapping) or str(question.get("status") or "") != "answered":
            raise TelegramQuestionError("question_answer_unavailable")
        continuation = str(question.get("runtimeContinuationStatus") or "")
        result: dict[str, Any] = {"status": continuation if continuation in {"queued", "interrupted", "delivered"} else "answered"}
        result["question"] = _public_question(question, self._max_text)
        if payload.get("idempotent") is True:
            result["idempotent"] = True
        return result

    @staticmethod
    def _scope(project_root: str, session_id: str) -> tuple[str, str]:
        project, session = str(project_root or "").strip(), str(session_id or "").strip()
        if project_root is None or not session:
            raise TelegramQuestionError("question_scope_required")
        return project, session


def _public_question(row: Mapping[str, Any], max_text: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "questionId": str(row.get("questionId") or row.get("id") or "")[:160],
        "header": str(row.get("header") or "")[:160],
        "question": str(row.get("question") or "")[:max_text],
        "status": str(row.get("status") or "pending")[:40],
    }
    options = row.get("options")
    if isinstance(options, list):
        result["options"] = [
            {
                "id": str(item.get("id") or "")[:100],
                "label": str(item.get("label") or "")[:200],
                "description": str(item.get("description") or "")[:300],
            }
            for item in options[:8]
            if isinstance(item, Mapping)
        ]
    return result
