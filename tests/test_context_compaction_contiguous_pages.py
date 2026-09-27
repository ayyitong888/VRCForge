import json
from concurrent.futures import CancelledError

import pytest

from context_compaction import compact_context, redact_context_text, normalize_compaction_recovery, _estimate_entry_tokens


def entries(prompt):
    return json.loads(prompt.split("REDACTED_ENTRIES=", 1)[1])


def test_every_middle_entry_reaches_summarizer_in_contiguous_order():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"ENTRY{i}:" + "中" * 1000} for i in range(5)]
    prompts = []
    result = compact_context(history, target_tokens=2200,
                             summarizer=lambda p: prompts.append(p) or "Safe page summary")
    assert len(prompts) == 3
    assert [row for p in prompts for row in entries(p)] == history
    assert result["completeness"]["summarizerInputComplete"] is True
    assert result["retainedEntryCount"] == 5
    assert result["coverage"]["completedPages"] == result["coverage"]["pageCount"] == 3
    assert normalize_compaction_recovery(result["recovery"]) == result["recovery"]


def test_long_entry_splits_without_losing_characters_after_complete_redaction():
    text = "START" + "中" * 2600 + " password=PRIVATE_SOURCE " + "尾" * 2300 + "END"
    history = [{"role": "user", "text": text}]
    prompts = []
    result = compact_context(history, target_tokens=2200,
                             summarizer=lambda p: prompts.append(p) or "Safe page summary")
    assert 1 < len(prompts) <= 3
    assert "".join(row["text"] for p in prompts for row in entries(p)) == redact_context_text(text)[0]
    assert all(row["role"] == "user" for p in prompts for row in entries(p))
    assert "PRIVATE_SOURCE" not in json.dumps(prompts)
    assert result["coverage"]["complete"] is True
    assert result["recovery"]["sourceEntries"][0]["text"].endswith("END")


def test_failed_second_page_never_claims_complete_or_retains_rejected_candidate():
    history = [{"role": "user", "text": "中" * 4900}]
    prompts = []
    def summarize(prompt):
        prompts.append(prompt)
        return "SAFE_FIRST_PAGE" if len(prompts) == 1 else "REJECTED_PAGE password=PRIVATE_OUTPUT"
    result = compact_context(history, target_tokens=2200, summarizer=summarize)
    assert len(prompts) == 2
    assert result["completeness"]["summarizerInputComplete"] is False
    assert result["coverage"]["completedPages"] == 1
    assert result["coverage"]["failedPageIndex"] == 1
    assert result["recovery"]["sourceEntries"] == history
    assert "SAFE_FIRST_PAGE" in result["recovery"]["summary"]
    assert "REJECTED_PAGE" not in json.dumps(result) and "PRIVATE_OUTPUT" not in json.dumps(result)
    assert result["fallbackReason"] == "sensitive_provider_output"


def test_call_budget_is_global_and_preflight_never_samples_first_and_last():
    prompts = []
    result = compact_context([{"role": "user", "text": "中" * 12000}], target_tokens=2200,
                             summarizer=lambda p: prompts.append(p) or "wrong")
    assert prompts == []
    assert result["providerAttempts"] == 0
    assert result["fallbackReason"] == "provider_call_budget"
    assert result["coverage"]["complete"] is False
    assert len(result["recovery"]["sourceEntries"][0]["text"]) == 12000


def test_long_validated_summary_uses_navigation_not_a_cut_candidate():
    candidate = "FULL_CANDIDATE_PREFIX " + "summary " * 1500 + " COMPLETE_TAIL"
    result = compact_context([{"role": "user", "text": "small"}], summarizer=lambda _: candidate)
    assert result["recovery"]["summary"] == candidate
    assert "FULL_CANDIDATE_PREFIX" not in result["summary"]
    assert "summary bounded" not in result["summary"]
    assert result["recovery"]["summaryDigest"] in result["summary"]


def test_retry_consumes_global_call_budget_without_skipping_an_unread_page():
    prompts = []
    def summarize(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise TimeoutError("temporary timeout")
        return "Safe summary"
    result = compact_context([{"role": "user", "text": "中" * 4900}], target_tokens=2200,
                             summarizer=summarize, sleep=lambda _: None)
    assert len(prompts) == result["providerAttempts"] == 3
    assert prompts[0] == prompts[1]
    assert result["coverage"]["completedPages"] == 2
    assert result["coverage"]["pageCount"] == 3
    assert result["coverage"]["complete"] is False
    assert result["fallbackReason"] == "provider_call_budget"
    assert result["coverage"]["nextSourcePosition"]["textOffset"] > 0


def test_cancelled_later_page_escapes_without_retry_or_fallback():
    calls = []
    def summarize(prompt):
        calls.append(prompt)
        if len(calls) == 2:
            raise CancelledError("stop")
        return "Safe summary"
    with pytest.raises(CancelledError):
        compact_context([{"role": "user", "text": "中" * 4900}], target_tokens=2200, summarizer=summarize)
    assert len(calls) == 2


def test_each_framed_request_stays_under_reserved_context_budget():
    prompts = []
    result = compact_context([{"role": "user", "text": '字"\\\n' * 1000}], target_tokens=2000,
                             real_context_limit=6000, summarizer=lambda p: prompts.append(p) or "Safe summary")
    assert prompts and result["coverage"]["complete"] is True
    assert max(_estimate_entry_tokens({"role": "user", "text": p}) for p in prompts) == result["maxRequestEstimatedTokens"]
    assert result["maxRequestEstimatedTokens"] <= result["requestBudgetTokens"] == 3000
