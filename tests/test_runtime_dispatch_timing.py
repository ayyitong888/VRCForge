"""Existing ledger events carry timing; the live/model timeline stays unchanged."""
import json

import pytest

from agent_runtime_turn_identity import continuation_client_turn_id
from tests.test_native_runtime_gateway import call, run, setup_gateway


@pytest.mark.parametrize("question_count", [3, 4])
def test_live_runtime_events_use_the_same_identity_as_the_run_ledger(tmp_path, question_count):
    gateway, _, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Finished"}])
    session_id = "s" * 180
    client_turn_id = "turn-1700000000000-0123456789abcd"
    for index in range(question_count):
        question_id = f"question_20261117_1420{index:02d}_000001_fixture"
        client_turn_id = continuation_client_turn_id(client_turn_id, "question", question_id)
    status_events, timeline_events = [], []
    gateway._runtime_status_changed = status_events.append
    gateway._runtime_timeline_changed = timeline_events.append

    response = run(gateway, tmp_path, sessionId=session_id, clientTurnId=client_turn_id)
    records = gateway.runtime_runs.read_events(limit=0)
    start = next(row for row in records if row["event"] == "runtime_turn_started")
    terminal = next(row for row in records if row["event"] == "runtime_turn_completed")

    assert len(client_turn_id) > 160
    assert status_events and timeline_events
    for event in [start, terminal, response, *status_events, *timeline_events]:
        assert (event["sessionId"], event["turnId"], event["clientTurnId"]) == (
            session_id, start["turnId"], client_turn_id,
        )


def test_queued_dispatch_timing_attributes_prior_persistence_without_extra_events(tmp_path, monkeypatch):
    batch = call("first", "vrcforge_read_text_file", {"path": "one"})
    batch["tool_calls"] += call("second", "vrcforge_read_text_file", {"path": "two"})["tool_calls"]
    gateway, model, invoked = setup_gateway(tmp_path, [batch])
    clock = [100.0]
    monkeypatch.setattr("agent_gateway.time.perf_counter", lambda: clock[0])
    original_append = gateway._runtime_run_ledger.append_timeline_event
    emitted = []

    def append(_self, **kwargs):
        emitted.append(kwargs)
        original_append(**kwargs)
        clock[0] += 0.025

    def publish(payload):
        assert "timing" not in payload["timelineEvent"]
        clock[0] += 0.015

    monkeypatch.setattr(type(gateway._runtime_run_ledger), "append_timeline_event", append)
    gateway._runtime_timeline_changed = publish
    result = run(gateway, tmp_path, maxAgenticTurns=1)
    assert len(invoked) == 2 and len(model.requests) == 1
    assert result["plan"]["nextStep"] == "paused"
    calls = [row for row in emitted if row["event"]["kind"] == "tool_call"]
    assert [row["timing"]["queuedNativeCall"] for row in calls] == [False, True]
    assert [row["event"]["kind"] for row in emitted] == [
        "tool_call", "tool_result", "tool_call", "tool_result", "assistant",
    ]
    for previous, current in zip(emitted, emitted[1:]):
        timing = current["timing"]
        assert timing["clock"] == "monotonic_turn_ms"
        assert timing["previousEvent"] == {
            "eventId": previous["event"]["id"], "persistMs": 25.0, "publishMs": 15.0,
        }
        assert timing["eventReadyMs"] >= previous["timing"]["eventReadyMs"]
    rows = [json.loads(line) for line in gateway._runtime_run_ledger.log_path.read_text(encoding="utf-8").splitlines()]
    persisted = [row for row in rows if row["event"] == "runtime_turn_timeline_event"]
    assert len(persisted) == len(emitted)
    assert [row["timing"] for row in persisted] == [row["timing"] for row in emitted]
    assert all("timing" not in row["timelineEvent"] for row in persisted)
