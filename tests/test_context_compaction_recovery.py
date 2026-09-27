"""Compaction recovery retains complete sanitized data, without promising a reader."""
import hashlib
import json

import pytest

from context_compaction import COMPACTION_SCHEMA, MAX_SUMMARY_CHARS, compact_context


TAIL = "COMPLETE_COMPACTION_RECOVERY_TAIL"


def test_complete_validated_summary_survives_inline_budget():
    candidate = "summary fact " * 600 + TAIL
    result = compact_context([{"role": "user", "text": "Original goal"}],
                             summarizer=lambda _: candidate, target_tokens=12000)
    assert len(result["summary"]) <= MAX_SUMMARY_CHARS
    assert TAIL not in result["summary"]
    assert result["recovery"]["summary"] == candidate
    assert result["recovery"]["summaryDigest"] == hashlib.sha256(candidate.encode()).hexdigest()
    assert result["recovery"]["summarySource"] == "provider"
    assert result["completeness"] == {"inlineSummaryComplete": False,
        "sourceRecoveryComplete": True, "summarizerInputComplete": True}
    assert result["schema"] == COMPACTION_SCHEMA
    assert result["summaryDigest"] == hashlib.sha256(result["summary"].encode()).hexdigest()
    assert result["fidelity"] == "full" and result["providerAttempts"] == 1
    assert result["targetTokens"] == 12000
    assert "nextRequest" not in result["recovery"]


def test_over_call_budget_source_is_complete_in_recovery_without_sampling():
    history = [{"role": "user", "text": "Original goal"},
        {"role": "assistant", "text": "middle detail " * 1500 + TAIL},
        {"role": "user", "text": "Latest question"}, {"role": "assistant", "text": "Latest answer"}]
    prompts = []
    result = compact_context(history, summarizer=lambda prompt: prompts.append(prompt) or "Fitted summary", target_tokens=100)
    assert prompts == []
    assert result["recovery"]["sourceEntries"] == history
    assert result["recovery"]["sourceDigest"] == result["sourceDigest"]
    assert result["retainedEntryCount"] == 0 and result["entryCount"] == 4
    assert result["completeness"] == {"inlineSummaryComplete": True,
        "sourceRecoveryComplete": True, "summarizerInputComplete": False}
    assert result["fidelity"] == "fallback"
    assert result["fallbackReason"] == "provider_call_budget"


def test_fallback_keeps_all_sanitized_source_without_provider_or_store():
    source = "ordinary source " * 1500 + TAIL + " password=PRIVATE_INPUT_SECRET"
    result = compact_context([{"role": "user", "text": source}], target_tokens=64)
    recovery = result["recovery"]
    assert TAIL in recovery["sourceEntries"][0]["text"]
    assert "PRIVATE_INPUT_SECRET" not in json.dumps(result)
    assert recovery["summary"] == result["summary"]
    assert recovery["summarySource"] == "fallback"
    assert result["fallbackReason"] == "provider_call_budget"
    assert result["providerAttempts"] == 0 and result["targetTokens"] == 64
    assert len(result["summary"]) <= 1000
    assert result["completeness"]["sourceRecoveryComplete"] is True
    assert result["completeness"]["summarizerInputComplete"] is False


@pytest.mark.parametrize("candidate,reason", [
    ("REJECTED_PROVIDER_ONLY password=PRIVATE_OUTPUT_SECRET", "sensitive_provider_output"),
    ({"currentGoal": "REJECTED_PROVIDER_ONLY"}, "schema_error"),
    ("", "empty_response"),
])
def test_rejected_candidate_is_never_retained(candidate, reason):
    result = compact_context([{"role": "user", "text": "Safe source " + TAIL}], summarizer=lambda _: candidate)
    assert result["fallbackReason"] == reason
    assert result["recovery"]["summarySource"] == "fallback"
    assert TAIL in result["recovery"]["sourceEntries"][0]["text"]
    assert "REJECTED_PROVIDER_ONLY" not in json.dumps(result)
    assert "PRIVATE_OUTPUT_SECRET" not in json.dumps(result)


def test_structured_summary_preserves_complete_validated_rendering():
    candidate = {"currentGoal": "Goal", "completed": [], "decisions": [], "constraints": [],
        "todo": [], "references": [], "recentContext": ["recent fact " * 600 + TAIL]}
    result = compact_context([{"role": "user", "text": "Goal"}], summarizer=lambda _: candidate)
    assert result["recovery"]["summary"].endswith(TAIL)
    assert "Recent context:" in result["recovery"]["summary"]
    assert result["completeness"]["inlineSummaryComplete"] is False
