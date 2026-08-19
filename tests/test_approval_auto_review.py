from __future__ import annotations

import pytest

from approval_auto_review import (
    review_general_auto_approval,
    review_saved_project_category_approval,
    select_independent_reviewer_model,
)


def _approval() -> dict:
    return {
        "targetTool": "vrcforge_create_gameobject",
        "riskLevel": "medium",
        "arguments": {"name": {"type": "str", "length": 6}},
        "preview": {"operation": "create"},
    }


def test_manual_approval_path_does_not_call_non_lightweight_reviewer() -> None:
    called = False

    def request_text(_prompt: str) -> str:
        nonlocal called
        called = True
        return '{"decision":"allow_auto"}'

    assert review_saved_project_category_approval(
        _approval(), model="gpt-5.6-sol", request_text=request_text
    ) == "manual"
    assert called is False


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ('{"decision":"allow_auto"}', "allow_auto"),
        ('{"decision":"manual"}', "manual"),
        ('{"decision":"allow_auto","extra":true}', "manual"),
        ("not-json", "manual"),
    ],
)
def test_lightweight_reviewer_is_strictly_binary(response: str, expected: str) -> None:
    assert review_saved_project_category_approval(
        _approval(), model="gemini-2.5-flash", request_text=lambda _prompt: response
    ) == expected


def test_lightweight_reviewer_failure_falls_back_to_manual() -> None:
    def fail(_prompt: str) -> str:
        raise TimeoutError("review timed out")

    assert review_saved_project_category_approval(
        _approval(), model="gpt-5.6-terra", request_text=fail
    ) == "manual"


def test_independent_reviewer_selection_excludes_active_and_non_text_models() -> None:
    assert select_independent_reviewer_model(
        "deepseek/deepseek-v4-pro",
        [
            {"id": "deepseek/deepseek-v4-pro"},
            {"id": "openai/whisper-mini-transcribe"},
            {"id": "google/gemini-2.5-flash"},
        ],
    ) == "google/gemini-2.5-flash"


def test_general_auto_review_uses_distinct_model_and_never_sends_content() -> None:
    prompts: list[str] = []
    approval = {
        "targetTool": "vrcforge_write_file",
        "riskLevel": "medium",
        "arguments": {
            "path": "C:/General/notes.txt",
            "content": "SENSITIVE_FILE_CONTENT",
            "overwrite": False,
        },
    }
    decision = review_general_auto_approval(
        approval,
        active_model="deepseek/deepseek-v4-pro",
        reviewer_model="google/gemini-2.5-flash",
        request_text=lambda prompt: prompts.append(prompt) or '{"decision":"allow_auto"}',
    )
    assert decision == "allow_auto"
    assert "SENSITIVE_FILE_CONTENT" not in prompts[0]
    assert '"contentBytes":22' in prompts[0]


def test_general_auto_review_fails_closed_when_reviewer_is_same_model() -> None:
    called = False

    def request_text(_prompt: str) -> str:
        nonlocal called
        called = True
        return '{"decision":"allow_auto"}'

    assert review_general_auto_approval(
        {"targetTool": "vrcforge_write_file", "arguments": {}},
        active_model="gemini-2.5-flash",
        reviewer_model="gemini-2.5-flash",
        request_text=request_text,
    ) == "manual"
    assert called is False
