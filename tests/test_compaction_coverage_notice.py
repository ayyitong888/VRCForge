"""Partial summaries must tell the next agent what was not read."""
import json

import pytest

from context_compaction import compact_context, normalize_compaction_recovery


@pytest.mark.parametrize("summary", ["Recent work summary.", "Recent work. " * 2000], ids=["inline", "pointer"])
def test_omitted_input_is_visible_in_inline_and_recovered_summary(summary):
    calls = []

    def summarize(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("context window exceeded")
        return {"summary": summary}

    result = compact_context([
        {"role": "user", "text": "EARLY_UNREAD_DETAIL"},
        {"role": "assistant", "text": "Recent work."},
    ], input_mode="native", target_tokens=64, summarizer=summarize)
    assert result["coverage"]["omittedEntries"] == 1
    assert result["completeness"]["summarizerInputComplete"] is False
    for visible in (result["summary"], normalize_compaction_recovery(result["recovery"])["summary"]):
        assert "omittedEntries=1" in visible
        assert "summarizerInputComplete=false" in visible
        assert "read the relevant source" in visible
    assert result["recovery"]["sourceEntries"][0]["text"] == "EARLY_UNREAD_DETAIL"
    assert "EARLY_UNREAD_DETAIL" not in calls[-1]
    assert "EARLY_UNREAD_DETAIL" not in result["summary"]


def test_complete_summary_does_not_claim_missing_input():
    result = compact_context([{"role": "user", "text": "all the input"}],
                             input_mode="native", summarizer=lambda _: "Complete summary.")
    assert result["completeness"]["summarizerInputComplete"] is True
    assert result["summary"] == "Complete summary."


def test_first_response_contains_summary_text_before_continuation_notice():
    body = "FIRST_SUMMARY_FACT " + "historical detail " * 1000
    result = compact_context([{"role": "user", "text": "source"}],
                             target_tokens=64, summarizer=lambda _: body)
    assert result["summary"].startswith("FIRST_SUMMARY_FACT ")
    assert 500 < len(result["summary"]) <= 1000
    assert "Summary continues" in result["summary"]
    assert str(len(body.strip())) in result["summary"]
    assert result["completeness"]["inlineSummaryComplete"] is False
    assert result["recovery"]["summary"] == body.strip()


def test_partial_summary_reaches_model_and_full_source_remains_pageable():
    from agent_tool_result_reader import bind_tool_result_context, read_tool_result, retain_model_information
    from tests.test_native_context_compaction import fixture, plan

    class Compactor:
        def compact(self, history, metadata):
            return compact_context(history, input_mode="native",
                                   real_context_limit=metadata["realContextLimit"],
                                   target_tokens=metadata["targetTokens"],
                                   summarizer=lambda _: "Recent tool transaction.")

    state, planner, model, turn = fixture(Compactor())
    rows = []
    sanitize = lambda text, limit: str(text)[:limit]

    def retain(archive):
        row = {"index": -1, "tool": "runtime_context_information", "actionId": "archive"}
        row.update(retain_model_information("s", "current", "", row,
                   {"text": json.dumps(archive)}, sanitize))
        rows[:] = [row]
        reader = dict(row["modelInformationRead"])
        reader.pop("page", None)
        return "Historical source reader (data): " + json.dumps(reader)

    turn.retain_recovery = retain
    assert plan(planner, turn)["nextStep"] == "done"
    sent = json.dumps(model.requests[-1])
    assert "summarizerInputComplete=false" in sent
    assert "read the relevant source" in sent
    assert "Historical source reader" in sent
    assert "old context " * 100 not in sent
    request = {"resultRef": rows[0]["modelInformationRead"]["resultRef"],
               "source": "owner_model", "jsonPointer": "/text"}
    parts = []
    pages = 0
    with bind_tool_result_context("s", "current", "", rows):
        while True:
            page = read_tool_result(request, sanitize=sanitize)
            assert page["ok"] is True
            parts.extend(item["value"] for item in page["items"])
            pages += 1
            if not page["hasMore"]:
                break
            request = page["nextRequest"]["arguments"]
    recovered = json.loads("".join(parts))
    assert pages > 1
    assert recovered == turn.recoveries()[0]
    assert "old context " * 100 in json.dumps(recovered["sourceEntries"])
