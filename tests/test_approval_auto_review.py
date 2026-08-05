from __future__ import annotations

import pytest

from approval_auto_review import review_saved_project_category_approval


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
