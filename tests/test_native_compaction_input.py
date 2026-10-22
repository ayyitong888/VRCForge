from __future__ import annotations

import json
from concurrent.futures import CancelledError

import pytest

from context_compaction import compact_context


def _entries(prompt: str) -> list[dict[str, str]]:
    return json.loads(prompt.split("REDACTED_ENTRIES=", 1)[1])


def test_native_context_window_drops_oldest_entry_and_marks_subset() -> None:
    history = [
        {"role": "user", "text": "old goal"},
        {"role": "tool", "text": "tool transaction"},
        {"role": "assistant", "text": "latest answer"},
    ]
    prompts: list[list[dict[str, str]]] = []

    def summarize(prompt: str) -> dict[str, str]:
        prompts.append(_entries(prompt))
        if len(prompts) == 1:
            raise RuntimeError("HTTP 400 context length exceeded")
        return {"summary": "native summary"}

    result = compact_context(history, input_mode="native", summarizer=summarize)

    assert len(prompts) == 2
    assert [entry["text"] for entry in prompts[0]] == [
        "old goal", "tool transaction", "latest answer"
    ]
    assert [entry["text"] for entry in prompts[1]] == [
        "tool transaction", "latest answer"
    ]
    assert result["summary"] == "native summary"
    assert result["coverage"]["selectedInputComplete"] is True
    assert result["coverage"]["omittedEntries"] == 1
    assert result["completeness"]["sourceRecoveryComplete"] is True
    assert result["completeness"]["summarizerInputComplete"] is False


def test_native_transient_retries_reset_after_context_trim() -> None:
    history = [
        {"role": "user", "text": "old"},
        {"role": "assistant", "text": "latest"},
    ]
    prompts: list[list[dict[str, str]]] = []
    calls_for_selected_input = 0

    def summarize(prompt: str) -> dict[str, str]:
        nonlocal calls_for_selected_input
        current = _entries(prompt)
        prompts.append(current)
        if len(prompts) == 1:
            raise RuntimeError("context window exceeded")
        calls_for_selected_input += 1
        if calls_for_selected_input < 3:
            raise TimeoutError("temporary transport failure")
        return {"summary": "recovered"}

    result = compact_context(history, input_mode="native", summarizer=summarize)

    assert len(prompts) == 4
    assert all(len(prompt) == 1 for prompt in prompts[1:])
    assert result["summary"] == "recovered"
    assert result["providerAttempts"] == 4


def test_native_cancelled_error_is_propagated_without_fallback() -> None:
    calls = 0
    cancellation = CancelledError("stop")

    def summarize(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise cancellation

    with pytest.raises(CancelledError) as raised:
        compact_context(
            [{"role": "user", "text": "keep this"}],
            input_mode="native",
            summarizer=summarize,
        )

    assert raised.value is cancellation
    assert calls == 1


def test_native_recovery_keeps_all_normalized_entries_and_does_not_mutate_source() -> None:
    history = [
        {"role": "user", "text": "first"},
        {"role": "tool", "text": "tool result"},
        {"role": "assistant", "text": "last"},
    ]
    original = [dict(entry) for entry in history]

    result = compact_context(
        history,
        input_mode="native",
        summarizer=lambda _prompt: {"summary": "complete"},
    )

    assert history == original
    assert result["recovery"]["sourceEntries"] == [
        {"role": "user", "text": "first"},
        {"role": "tool", "text": "tool result"},
        {"role": "assistant", "text": "last"},
    ]
    assert result["completeness"]["sourceRecoveryComplete"] is True
    assert result["coverage"]["omittedEntries"] == 0
    assert result["completeness"]["summarizerInputComplete"] is True


def test_native_single_huge_entry_fails_closed_without_retry_loop() -> None:
    calls = 0

    def summarize(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return "should not be sent"

    result = compact_context(
        [{"role": "user", "text": "x" * 200_000}],
        input_mode="native",
        real_context_limit=512,
        summarizer=summarize,
    )

    assert calls == 0
    assert result["fidelity"] == "fallback"
    assert result["completeness"]["summarizerInputComplete"] is False
    assert result["coverage"]["selectedInputComplete"] is False


def test_native_provider_failure_is_not_reported_as_success() -> None:
    def summarize(_prompt: str) -> str:
        raise RuntimeError("API key is invalid")

    result = compact_context(
        [{"role": "user", "text": "retain this goal"}],
        input_mode="native",
        summarizer=summarize,
    )

    assert result["fidelity"] == "fallback"
    assert result["recovery"]["summarySource"] == "fallback"
    assert result["completeness"]["summarizerInputComplete"] is False
    assert result["coverage"]["selectedInputComplete"] is False


def test_native_runtime_accepts_bounded_provider_summary_and_keeps_current_instruction():
    from tests.test_native_context_compaction import fixture, plan

    class RealCompactor:
        result = None
        def compact(self, history, metadata):
            assert metadata["inputMode"] == "native"
            self.result = compact_context(history, input_mode=metadata["inputMode"],
                real_context_limit=metadata["realContextLimit"],
                target_tokens=metadata["targetTokens"],
                summarizer=lambda prompt: {"summary": "Earlier read only work; awaiting user review."})
            return self.result

    compactor = RealCompactor()
    state, planner, model, turn = fixture(compactor, current="Do not edit any model; wait for my review.")
    assert plan(planner, turn)["nextStep"] == "done"
    assert turn.compaction["applied"] is True
    assert compactor.result["coverage"]["omittedEntries"] > 0
    assert compactor.result["completeness"]["sourceRecoveryComplete"] is True
    assert any(message.get("content") == "Do not edit any model; wait for my review."
               for message in model.requests[0]["messages"])
    assert any("old context" in item["text"] for item in compactor.result["recovery"]["sourceEntries"])


@pytest.mark.parametrize("status", [400, 422])
def test_native_request_format_error_does_not_trim_history_or_retry(status):
    calls = []
    def summarize(prompt):
        calls.append(prompt)
        error = RuntimeError("extra_forbidden: unsupported parameter reasoning_effort")
        error.status_code = status
        raise error
    result = compact_context([{"role": "user", "text": str(i)} for i in range(20)],
        input_mode="native", summarizer=summarize)
    assert len(calls) == 1
    assert result["fallbackReason"] == "provider_request"
    assert result["coverage"]["omittedEntries"] == 0
    assert len(result["recovery"]["sourceEntries"]) == 20
