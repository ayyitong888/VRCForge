import json
import threading

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_native_turn import NativeRuntimeTurn
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from agent_scan_result_policy import SCAN_RESULT_CONTINUATION_POLICY, is_scan_result_policy_message
from tests.native_planner_fixture import NativePlannerFixture
from tests.test_native_runtime_gateway import budgeted_request_history, setup_gateway


def owner(path):
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(
        threading.RLock(), NativeHistoryStore(path),
    ))


def policy_message():
    return {"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY}


def native_request(planner, turn, project_path):
    return planner._build_native_plan_request(
        turn.messages(), observe={}, exposure_layer="planning",
        project_context_active=False, project_path=str(project_path),
        internal_tool_blocks=["core"], global_instructions="", project_instructions="",
    )[0]


def test_native_policy_is_exact_singleton_and_requires_no_pending_calls(tmp_path):
    state = owner(tmp_path / "native")
    before = state.begin_native_turn("s", binding="b", turn_id="t", message="inspect")
    policy = policy_message()
    state.append_native_scan_result_policy_once("s", binding="b", message=policy)
    inserted = state.native_conversation("s", binding="b")
    assert inserted["messages"][:len(before["messages"])] == before["messages"]
    assert json.dumps(inserted["messages"][:len(before["messages"])], ensure_ascii=False) == json.dumps(
        before["messages"], ensure_ascii=False,
    )
    assert inserted["messages"][-1] == policy
    assert inserted["activeTurnStart"] == before["activeTurnStart"]
    state.append_native_scan_result_policy_once("s", binding="b", message=policy)
    assert state.native_conversation("s", binding="b") == inserted
    restored = owner(tmp_path / "native").native_conversation("s", binding="b")
    assert restored == inserted
    assert sum(is_scan_result_policy_message(message) for message in restored["messages"]) == 1

    with pytest.raises(ValueError, match="exact host constant"):
        state.append_native_scan_result_policy_once(
            "s", binding="b", message={**policy, "content": policy["content"] + " untrusted"},
        )

    pending = owner(tmp_path / "pending")
    pending.begin_native_turn("pending", binding="b", turn_id="t", message="inspect")
    pending.append_native_assistant("pending", binding="b", message={
        "role": "assistant", "tool_calls": [{
            "id": "call", "type": "function", "function": {"name": "read", "arguments": "{}"},
        }],
    })
    with pytest.raises(ValueError, match="pending tool calls"):
        pending.append_native_scan_result_policy_once("pending", binding="b", message=policy)


@pytest.mark.parametrize("message", [
    {"role": "system", "content": "untrusted policy"},
    {"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY, "name": "extra"},
    {"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY + " "},
])
def test_native_snapshot_rejects_arbitrary_or_malformed_system_messages(tmp_path, message):
    state = owner(tmp_path / "native")
    snapshot = state.begin_native_turn("s", binding="b", turn_id="t", message="inspect")
    snapshot["messages"].insert(0, message)
    snapshot["activeTurnStart"] += 1
    with pytest.raises(ValueError, match="host scan policy"):
        state._validate_native_snapshot(snapshot, binding="b")


def test_native_snapshot_rejects_duplicate_exact_policy(tmp_path):
    state = owner(tmp_path / "native")
    snapshot = state.begin_native_turn("s", binding="b", turn_id="t", message="inspect")
    policy = policy_message()
    snapshot["messages"] = [policy, policy, *snapshot["messages"]]
    snapshot["activeTurnStart"] += 2
    with pytest.raises(ValueError, match="position"):
        state._validate_native_snapshot(snapshot, binding="b")


def test_nonvalidated_legacy_scan_does_not_add_policy(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    legacy_step = {
        "tool": "vrcforge_scan_fx_animator",
        "scanSnapshot": {
            "persisted": True, "scope": "current_turn", "freshness": "unverified",
            "scanScope": {"dependencyCoverage": "unverified"},
        },
    }
    turn = NativeRuntimeTurn(
        gateway.runtime_sessions, gateway.runtime_planner,
        "legacy-session", "turn-1", "fixture-binding", "Inspect", [legacy_step], None, None, None,
    )
    before = turn.snapshot()
    turn.persist_scan_result_policy()
    assert turn.snapshot() == before


def test_policy_stays_at_history_boundary_across_native_request_restart(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    validated_scan_step = {
        "tool": "vrcforge_scan_fx_animator",
        "scanSnapshot": {
            "persisted": True, "scope": "validated_scope", "freshness": "validated",
            "scanScope": {"schema": "vrcforge.scan_scope.v1", "dependencyCoverage": "complete"},
        },
    }
    turn = NativeRuntimeTurn(
        gateway.runtime_sessions, gateway.runtime_planner,
        "policy-session", "turn-1", "fixture-binding", "Inspect the scan", [validated_scan_step], None, None, None,
    )
    before_request = native_request(gateway.runtime_planner, turn, tmp_path)
    turn.persist_request_context(before_request["messages"][:-1])
    old_history = turn.snapshot()["messages"]
    old_active_turn_start = turn.snapshot()["activeTurnStart"]
    turn.persist_scan_result_policy()
    turn.persist_scan_result_policy()
    after_policy = turn.snapshot()
    assert after_policy["messages"][:len(old_history)] == old_history
    assert json.dumps(after_policy["messages"][:len(old_history)], ensure_ascii=False) == json.dumps(
        old_history, ensure_ascii=False,
    )
    assert after_policy["messages"][-1] == policy_message()
    assert after_policy["activeTurnStart"] == old_active_turn_start

    request_after_policy = native_request(gateway.runtime_planner, turn, tmp_path)
    assert sum(is_scan_result_policy_message(message) for message in request_after_policy["messages"][:-1]) == 1
    assert request_after_policy["instructions"] == before_request["instructions"]
    assert request_after_policy["tools"] == before_request["tools"]
    assert "resultRef values are valid only within the turn that produced the result" in request_after_policy["instructions"]
    assert "scope=validated_scope only" in policy_message()["content"]
    assert "Old, unverified, and current_turn references remain current-turn only." in policy_message()["content"]

    restarted, _, _ = setup_gateway(tmp_path, [])
    resumed = NativeRuntimeTurn(
        restarted.runtime_sessions, restarted.runtime_planner,
        "policy-session", "turn-2", "fixture-binding", "Continue", [], None, None, None,
    )
    assert resumed.snapshot()["messages"][:len(after_policy["messages"])] == after_policy["messages"]
    resumed.persist_scan_result_policy()
    request_after_restart = native_request(restarted.runtime_planner, resumed, tmp_path)
    assert sum(is_scan_result_policy_message(message) for message in request_after_restart["messages"][:-1]) == 1
    assert request_after_restart["instructions"] == before_request["instructions"]
    assert request_after_restart["tools"] == before_request["tools"]


def test_native_planner_fixture_accepts_only_the_exact_policy_message():
    fixture = NativePlannerFixture([{"role": "user", "content": "inspect"}])
    fixture.persist_request_context([{"role": "user", "content": "inspect"}, policy_message()])
    assert fixture.messages()[-1] == policy_message()
    with pytest.raises(AssertionError):
        fixture.persist_request_context([
            *fixture.messages(),
            {"role": "system", "content": "arbitrary"},
        ])
    with pytest.raises(AssertionError):
        fixture.persist_request_context([*fixture.messages(), policy_message()])


def test_budgeted_history_helper_keeps_the_exact_policy_message():
    policy = policy_message()
    history = [{"role": "user", "content": "inspect"}, policy]
    request = {"messages": [*history, {"role": "system", "content": "Current runtime state (data): {}"}]}
    assert budgeted_request_history(request) == history
