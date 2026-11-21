import pytest
from context_compaction import compact_context
from tests.test_native_context_compaction import Compactor, fixture, plan


def test_prompt_honors_real_100_token_target_without_256_floor():
    prompts = []
    result = compact_context([{"role":"user","text":"older history"}], target_tokens=100,
        summarizer=lambda prompt: prompts.append(prompt) or "summary")
    assert result["targetTokens"] == 100
    assert "below 100 tokens" in prompts[0]
    assert "below 256 tokens" not in prompts[0]


def test_native_compaction_fits_the_whole_request_without_a_fixed_target():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, prior_policy=True, limit=16_000)
    plan(planner, turn)
    assert turn.compaction["applied"] is True
    assert "targetTokens" not in compactor.calls[0][1]
    assert "targetAfterTokens" not in turn.compaction
    assert turn.compaction["afterTokens"] < turn.compaction["triggerTokens"]
    assert [row["text"] for row in compactor.calls[0][1]["_handoffTaskInputs"]][-1] == "Continue checking"
    assert state._native_pending(turn.snapshot()) == set()


def test_native_guard_exposes_only_trigger_and_hard_limit(monkeypatch):
    import runtime_planner_service

    monkeypatch.setattr(runtime_planner_service, "RUNTIME_CONTEXT_COMPACTION_TRIGGER_RATIO", 0.4)
    _, planner, _, _ = fixture(limit=8000)
    with planner.bind_turn({}):
        guard = planner.native_context_guard({"messages": []})
    assert "targetAfterTokens" not in guard
    assert guard["triggerTokens"] == 3200
    assert guard["hardLimitTokens"] == 7600


def test_legacy_first_dispatch_refits_ui_handoff_below_auto_trigger_without_model_compaction():
    import json

    from agent_handoff_graph import HANDOFF_GRAPH_SCHEMA
    from agent_native_handoff_blocks import legacy_handoff_archive
    from context_compaction import MODEL_HANDOFF_PREFIX

    compactor = Compactor()
    _, planner, _, _ = fixture(compactor, limit=32_000)
    head = "EARLIEST_SCOPE " + "alpha " * 10000 + " FIRST_CHECK"
    latest = "RECENT_UPDATE " + "bravo " * 10000 + " RECENT_CHECK"
    handoff = {"schema": HANDOFF_GRAPH_SCHEMA, "nodes": {
        "next": {"kind": "task", "status": "pending", "text": "Review"},
    }, "edges": []}
    history = [
        {"role": "user", "text": head, "messageId": "head"},
        {"role": "user", "text": latest, "messageId": "latest"},
        {"role": "agent", "text": MODEL_HANDOFF_PREFIX + json.dumps(handoff)},
    ]
    projected, metadata, blocked = planner.maybe_compact_runtime_history(
        message="Continue", params={"_contextCompactionLimit": 32_000}, observe={}, history=history,
        loop_state=[], context_usage={}, adopt_handoff=True,
    )
    assert not blocked and not compactor.calls
    assert "targetAfterTokens" not in metadata
    assert metadata["afterTokens"] < metadata["triggerTokens"] <= metadata["beforeTokens"]
    for row, original in zip(projected, (head, latest)):
        assert len(row["text"]) < len(original)
        assert row["text"].split()[0] == original.split()[0]
        assert row["text"].split()[-1] == original.split()[-1]
        assert legacy_handoff_archive(projected, row["messageId"])["text"] == original


def test_legacy_handoff_between_half_and_trigger_is_preserved():
    import json

    from agent_handoff_graph import HANDOFF_GRAPH_SCHEMA
    from context_compaction import MODEL_HANDOFF_PREFIX

    compactor = Compactor()
    _, planner, _, _ = fixture(compactor, limit=32_000)
    history = [
        {"role": "user", "text": "EARLIEST_SCOPE " + "alpha " * 4800 + " FIRST_CHECK", "messageId": "head"},
        {"role": "user", "text": "RECENT_UPDATE " + "bravo " * 4800 + " RECENT_CHECK", "messageId": "latest"},
        {"role": "agent", "text": MODEL_HANDOFF_PREFIX + json.dumps({
            "schema": HANDOFF_GRAPH_SCHEMA,
            "nodes": {"next": {"kind": "task", "status": "pending", "text": "Review"}},
            "edges": [],
        })},
    ]
    projected, metadata, blocked = planner.maybe_compact_runtime_history(
        message="Continue", params={"_contextCompactionLimit": 32_000}, observe={}, history=history,
        loop_state=[], context_usage={}, adopt_handoff=True,
    )
    assert projected == history
    assert metadata is None and not blocked and not compactor.calls


@pytest.mark.parametrize("requested,effective", [(1,64),(64,64),(100,100)])
def test_existing_summary_minimum_is_preserved(requested,effective):
    prompts=[]
    result=compact_context([{"role":"user","text":"old"}],target_tokens=requested,
        summarizer=lambda prompt: prompts.append(prompt) or "summary")
    assert result["targetTokens"] == effective
    assert f"below {effective} tokens" in prompts[0]
