from tests.test_native_context_compaction import Compactor, fixture, plan


def test_success_does_not_exhaust_compaction_for_new_history_in_same_turn():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor)
    usage = {}
    assert plan(planner, turn, usage)["nextStep"] == "done"
    state.append_native_assistant("s", binding="b", message={
        "role": "assistant", "content": "New settled observation " * 5000,
    })
    assert plan(planner, turn, usage)["nextStep"] == "done"
    assert len(compactor.calls) == 2
    assert len(model.requests) == 2
    assert usage["compactionCount"] == 2
    assert turn.compaction["applied"] is True


def test_failed_compaction_is_not_retried_against_unchanged_history():
    compactor = Compactor(summary="")
    _state, planner, _model, turn = fixture(compactor)
    assert plan(planner, turn)["nextStep"] == "context_compaction_required"
    assert plan(planner, turn)["nextStep"] == "context_compaction_required"
    assert len(compactor.calls) == 1


def test_failed_compaction_can_retry_after_new_settled_history_in_same_turn():
    compactor = Compactor(summary="")
    state, planner, model, turn = fixture(compactor, limit=20000)
    assert plan(planner, turn)["nextStep"] == "done"
    assert len(compactor.calls) == 1

    compactor.summary = "Earlier task: inspected fixture; no writes approved."
    state.append_native_assistant("s", binding="b", message={
        "role": "assistant", "content": "NEW_SETTLED_RESULT " * 1500,
    })
    assert plan(planner, turn)["nextStep"] == "done"
    assert len(compactor.calls) == 2
    assert len(model.requests) == 2
    assert turn.compaction["applied"] is True
