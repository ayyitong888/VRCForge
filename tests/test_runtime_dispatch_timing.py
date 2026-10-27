"""Existing ledger events carry timing; the live/model timeline stays unchanged."""
import json

from tests.test_native_runtime_gateway import call, run, setup_gateway


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
