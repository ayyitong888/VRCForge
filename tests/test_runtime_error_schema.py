from __future__ import annotations

import pytest

from runtime_error_schema import (
    RUNTIME_TURN_ACTIVE,
    RUNTIME_TURN_TERMINAL,
    runtime_error,
    runtime_error_from_exception,
    runtime_notice,
    runtime_turn_status,
)


class FakeHttpError(Exception):
    def __init__(self) -> None:
        super().__init__("[WinError 5] Access denied: 'C:\\Users\\secret\\token.json'")
        self.status_code = 503
        self.retryable = True


def test_error_schema_never_carries_exception_text() -> None:
    payload = runtime_error_from_exception("provider_overloaded", FakeHttpError(), phase="stream")
    assert payload["schema"] == "vrcforge.runtime_error.v1"
    assert payload["code"] == "provider_overloaded"
    assert "WinError" not in payload["message"]
    assert "secret" not in str(payload)
    assert payload["detail"]["exceptionType"] == "FakeHttpError"
    assert payload["detail"]["statusCode"] == 503
    assert payload["detail"]["phase"] == "stream"
    assert payload["retryable"] is True


def test_error_schema_bounds_and_rejects_hostile_values() -> None:
    payload = runtime_error(
        "OK!! weird CODE",
        message="x" * 5000,
        detail={"bad key!": "v" * 5000, "n": None, "keep": 7, "flag": False},
    )
    assert payload["code"] == "internal_error", "non-snake-case codes collapse to a safe constant"
    assert len(payload["message"]) <= 500
    assert "bad key!" not in payload["detail"]
    assert payload["detail"]["keep"] == 7
    assert payload["detail"]["flag"] is False


def test_turn_status_groups_and_rejects_unknown() -> None:
    assert RUNTIME_TURN_ACTIVE == {"running", "waiting_user"}
    assert RUNTIME_TURN_TERMINAL == {"completed", "failed", "cancelled", "interrupted"}
    ok = runtime_turn_status("failed", reply="", error=runtime_error("provider_timeout"))
    assert ok["status"] == "failed"
    assert ok["error"]["code"] == "provider_timeout"
    with pytest.raises(ValueError):
        runtime_turn_status("exploded")


def test_turn_status_reply_and_error_never_mix() -> None:
    payload = runtime_turn_status(
        "failed",
        reply="模型已生成的最后一段文本",
        error=runtime_error("context_window_exceeded", message="conversation too long"),
    )
    assert payload["reply"] == "模型已生成的最后一段文本"
    assert payload["error"]["code"] == "context_window_exceeded"
    malformed = runtime_turn_status("failed", reply="r", error={"nope": 1})
    assert malformed["error"]["code"] == "internal_error"


def test_runtime_notice_is_turn_level_not_tool_result() -> None:
    notice = runtime_notice("budget_pause")
    assert notice["status"] == "failed"
    assert notice["error"]["code"] == "turn_stopped"
    assert notice["error"]["detail"]["stopReason"] == "budget_pause"
    assert "tool" not in str(notice)
