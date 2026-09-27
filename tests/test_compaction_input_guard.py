from copy import deepcopy
from tests.test_native_context_compaction import Compactor, fixture, plan


class IncompleteCompactor(Compactor):
    def compact(self, history, metadata):
        result = super().compact(history, metadata)
        return {**result, "providerAttempts": 2,
            "completeness": {"summarizerInputComplete": False}}


def test_native_keeps_history_when_summarizer_did_not_read_all_input():
    state, planner, model, turn = fixture(IncompleteCompactor())
    before = deepcopy(state.native_conversation("s", binding="b"))
    plan(planner, turn)
    after = state.native_conversation("s", binding="b")
    assert after["messages"] == before["messages"]
    assert turn.compaction["applied"] is False
    assert turn.compaction["attempts"] == 2
    assert turn.compaction["failureClass"] == "incomplete_input"
    assert model.requests == []


def test_legacy_keeps_history_when_summarizer_did_not_read_all_input():
    _, planner, _, _ = fixture(IncompleteCompactor())
    history = [{"role": "user", "text": "retained history " * 5000}]
    with planner.bind_turn({}):
        changed, metadata, blocked = planner.maybe_compact_runtime_history(
            message="continue", params={"_contextCompactionLimit": 8000}, observe={},
            history=history, loop_state=[], context_usage={})
    assert changed == history
    assert metadata["applied"] is False
    assert metadata["attempts"] == 2
    assert metadata["failureClass"] == "incomplete_input"
    assert blocked is True
