from copy import deepcopy
import json
import uuid

import pytest

import agent_scan_result_policy as policy
from agent_runtime_native_turn import NativeRuntimeTurn
from tests.test_native_scan_result_policy import native_request, owner
from tests.test_native_runtime_gateway import setup_gateway
from tests.test_native_context_compaction import Compactor, fixture, plan


def message(version):
    content = (policy.SCAN_RESULT_CONTINUATION_POLICY if version == 1
               else policy.SCAN_RESULT_CONTINUATION_POLICY_V2)
    return {"role": "system", "content": content}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def test_legacy_policy_upgrades_once_in_place_and_survives_disk_restart(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    session, binding = uuid.uuid4().hex, uuid.uuid4().hex
    state = gateway.runtime_sessions
    state.begin_native_turn(session, binding=binding, turn_id=uuid.uuid4().hex, message="inspect")
    state.append_native_scan_result_policy_once(session, binding=binding, message=message(1))
    turn = NativeRuntimeTurn(state, gateway.runtime_planner, session, uuid.uuid4().hex,
                             binding, "continue after Play", [], None, None, None)
    before = turn.snapshot()
    request = native_request(gateway.runtime_planner, turn, tmp_path)
    turn.persist_scan_result_policy()
    after = turn.snapshot()
    assert encoded(after["messages"][:len(before["messages"])]) == encoded(before["messages"])
    assert after["messages"].count(message(1)) == after["messages"].count(message(2)) == 1
    assert after["messages"][-1] == message(2)
    turn.persist_scan_result_policy()
    assert turn.snapshot() == after
    updated = native_request(gateway.runtime_planner, turn, tmp_path)
    assert encoded(updated["instructions"]) == encoded(request["instructions"])
    assert encoded(updated["tools"]) == encoded(request["tools"])
    assert encoded(updated["messages"][:len(before["messages"])]) == encoded(before["messages"])
    restarted, _, _ = setup_gateway(tmp_path, [])
    resumed = NativeRuntimeTurn(restarted.runtime_sessions, restarted.runtime_planner,
                                session, uuid.uuid4().hex, binding, "again", [], None, None, None)
    resumed.persist_scan_result_policy()
    assert encoded(resumed.snapshot()["messages"][:len(after["messages"])]) == encoded(after["messages"])
    assert resumed.snapshot()["messages"].count(message(2)) == 1


@pytest.mark.parametrize("versions", [(1,), (2,), (1, 2)])
def test_compaction_restores_only_latest_trusted_version_at_tail(tmp_path, versions):
    state = owner(tmp_path / "native")
    state.begin_native_turn("s", binding="b", turn_id="old", message="old")
    for version in versions:
        state.append_native_scan_result_policy_once("s", binding="b", message=message(version))
    state.begin_native_turn("s", binding="b", turn_id="new", message="new")
    before = state.native_conversation("s", binding="b")
    suffix = deepcopy(before["messages"][before["activeTurnStart"]:])
    state.replace_native_completed_prefix("s", binding="b", expected_snapshot=before, summary="summary")
    after = state.native_conversation("s", binding="b")
    assert after["messages"] == [{"role": "assistant", "content": "summary"}, *suffix, message(versions[-1])]
    assert owner(tmp_path / "native").native_conversation("s", binding="b") == after


def test_planner_compaction_measures_and_persists_the_same_version():
    state, planner, model, turn = fixture(Compactor(), prior_policy=False)
    # Both policies belong to the completed prefix, not the current turn.
    snapshot = turn.snapshot()
    snapshot["messages"][snapshot["activeTurnStart"]:snapshot["activeTurnStart"]] = [message(1), message(2)]
    snapshot["activeTurnStart"] += 2
    state._native_conversations.clear()
    state.restore_native_conversation("s", binding="b", snapshot=snapshot)
    measured = []
    guard = planner.native_context_guard
    def capture(request, **kwargs):
        measured.append(deepcopy(request["messages"]))
        return guard(request, **kwargs)
    planner.native_context_guard = capture
    assert plan(planner, turn)["nextStep"] == "done"
    candidate = next(rows for rows in measured if rows[0].get("role") == "assistant")
    assert candidate.count(message(2)) == model.requests[0]["messages"].count(message(2)) == 1
    assert message(1) not in candidate
    assert turn.snapshot()["messages"].count(message(2)) == 1


@pytest.mark.parametrize("versions", [(1, 1), (2, 2), (2, 1)])
def test_versions_cannot_duplicate_or_downgrade(tmp_path, versions):
    state = owner(tmp_path / "native")
    snapshot = state.begin_native_turn("s", binding="b", turn_id="t", message="inspect")
    snapshot["messages"].extend(message(version) for version in versions)
    with pytest.raises(ValueError, match="position"):
        state._validate_native_snapshot(snapshot, binding="b")


@pytest.mark.parametrize("mutation", ["space", "extra", "user"])
def test_v2_is_still_an_exact_host_constant(mutation):
    candidate = message(2)
    if mutation == "space":
        candidate["content"] += " "
    elif mutation == "extra":
        candidate["name"] = "untrusted"
    else:
        candidate["role"] = "user"
    assert not policy.is_scan_result_policy_message(candidate)


def test_policy_states_read_first_dispatch_and_replacement_without_claiming_validity():
    text = message(2)["content"]
    for required in ("before invoking a scan tool", "Play", "domain reload", "observable business result",
                     "replacementRef", "function.name=call_tool", "not validity", "current_turn"):
        assert required in text
