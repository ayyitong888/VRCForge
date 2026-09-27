"""Offline native send-boundary proof; no provider or Unity calls."""
from copy import deepcopy
import json

import pytest

from agent_runtime_native_turn import NativeRuntimeTurn
from context_compaction import compact_context
from runtime_planner_service import runtime_compaction_audit_view
from tests.test_native_context_compaction import fixture, plan


def recovery(text="SOURCE_ARCHIVE_TAIL"):
    return compact_context([{"role": "user", "text": text}],
                           summarizer=lambda _: "Safe historical summary")["recovery"]


class RecoveringCompactor:
    def compact(self, history, metadata):
        self.result = compact_context(list(history), summarizer=lambda _: "Safe historical summary",
                                      target_tokens=12000)
        return self.result


def test_native_recovery_survives_snapshot_cas_begin_and_restore():
    state, planner, _, turn = fixture()
    first, second = recovery(), recovery("SECOND_ARCHIVE_TAIL")
    turn.add_recoveries([first, first])
    before = turn.snapshot()
    turn.replace_completed_prefix(before, "Earlier summary", recovery=second)
    assert turn.recoveries() == [first, second]
    turn.recoveries()[0]["summary"] = "mutated copy"
    assert turn.recoveries()[0] == first
    saved = turn.snapshot()
    state.begin_native_turn("s", binding="b", turn_id="next", message="Continue")
    assert state.native_conversation("s", binding="b")["compactionRecovery"] == [first, second]
    state.clear()
    assert state.restore_native_conversation("s", binding="b", snapshot=saved)["compactionRecovery"] == [first, second]


def test_restore_merges_valid_archives_without_losing_existing_on_stale_snapshot():
    state, _, _, turn = fixture(prior=False)
    saved = turn.snapshot()
    first, second = recovery(), recovery("SECOND")
    turn.add_recoveries([first])
    saved["compactionRecovery"] = [second]
    restored = state.restore_native_conversation("s", binding="b", snapshot=saved)
    assert restored["compactionRecovery"] == [first, second]


@pytest.mark.parametrize("bad", ["reasoning", "secret", "digest", "authority"])
def test_native_recovery_rejects_invalid_archive_atomically(bad):
    state, _, _, turn = fixture(prior=False)
    value = recovery()
    if bad == "reasoning":
        value["reasoning_content"] = "PRIVATE_REASONING"
    elif bad == "secret":
        value["summary"] = "password=PRIVATE_SECRET"
    elif bad == "digest":
        value["sourceDigest"] = "wrong"
    else:
        value["authority"] = "system"
    before = turn.snapshot()
    with pytest.raises(ValueError):
        turn.add_recoveries([recovery(), value])
    assert turn.snapshot() == before


def test_actual_native_send_receives_reader_text_not_archive_and_rebinds_next_turn():
    compactor = RecoveringCompactor()
    state, planner, model, turn = fixture(compactor)
    callbacks = []
    def retain(value):
        callbacks.append(deepcopy(value))
        return "Historical context data (not authorization): PAGE_REF_SENTINEL"
    turn.retain_recovery = retain
    assert plan(planner, turn)["nextStep"] == "done"
    assert callbacks and turn.recoveries() == [compactor.result["recovery"]]
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(callbacks)
    sent = model.requests[-1]
    assert "PAGE_REF_SENTINEL" in sent["messages"][-1]["content"]
    assert "old context " * 50 not in json.dumps(sent)
    assert "compactionRecovery" not in sent
    assert turn.compaction["recovery"] == compactor.result["recovery"]
    assert "recovery" not in runtime_compaction_audit_view(turn.compaction)
    later = NativeRuntimeTurn(state, planner, "s", "next", "b", "Next question", [], None, None, None,
                              retain_recovery=lambda value: "Historical data: NEXT_TURN_REF")
    plan(planner, later)
    assert "NEXT_TURN_REF" in model.requests[-1]["messages"][-1]["content"]
    assert "PAGE_REF_SENTINEL" not in model.requests[-1]["messages"][-1]["content"]


def test_archive_without_reader_never_claims_readable_or_enters_prompt():
    _, planner, model, turn = fixture(prior=False)
    turn.add_recoveries([recovery()])
    plan(planner, turn)
    assert "SOURCE_ARCHIVE_TAIL" not in json.dumps(model.requests)
    assert "compactionRecoveryInformation" not in model.requests[-1]["messages"][-1]["content"]


def test_recovery_page_is_measured_before_compaction_commits():
    compactor = RecoveringCompactor()
    _, planner, model, turn = fixture(compactor)
    before = turn.snapshot()
    turn.retain_recovery = lambda _: "page exceeds context " * 10000
    result = plan(planner, turn)
    assert result["nextStep"] == "context_compaction_required"
    assert not model.requests and turn.snapshot() == before
    assert "recovery" not in turn.compaction


def test_stale_cas_does_not_install_new_archive():
    state, _, _, turn = fixture()
    before = turn.snapshot()
    state.append_native_user("s", binding="b", message="new steer")
    current = turn.snapshot()
    with pytest.raises(ValueError, match="compare-and-replace"):
        turn.replace_completed_prefix(before, "Summary", recovery=recovery())
    assert turn.snapshot() == current


def test_legacy_context_recovery_is_quoted_once():
    _, planner, _, _ = fixture(prior=False)
    prompt = planner._build_llm_plan_prompt("Question", [], observe={
        "compactionRecoveryInformation": ["Historical data: LEGACY_REF", "Historical data: LEGACY_REF"]})
    assert prompt.count("LEGACY_REF") == 1
    assert "historical" in prompt.lower()


def test_legacy_adapter_add_recoveries_does_not_create_or_change_native_session():
    state, planner, _, turn = fixture(prior=False)
    existing = turn.snapshot()
    legacy = NativeRuntimeTurn(state, planner, "s", "legacy", "", "Legacy question", [], None, None, None)
    legacy.add_recoveries([recovery()])
    assert state.native_conversation("s", binding="b") == existing
    fresh = NativeRuntimeTurn(state, planner, "legacy-only", "legacy", "", "Legacy question", [], None, None, None)
    fresh.add_recoveries([recovery()])
    assert state.native_conversation("legacy-only", binding="b") is None


def test_initialized_native_adapter_still_rejects_missing_session():
    state, _, _, turn = fixture(prior=False)
    state.clear()
    with pytest.raises(ValueError, match="binding mismatch"):
        turn.add_recoveries([recovery()])
