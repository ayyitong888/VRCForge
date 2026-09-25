from copy import deepcopy

import pytest

from agent_runtime_native_turn import NativeRuntimeTurn
from agent_task_loop import AgentTaskLoop, approval_task_context
from tests.test_agent_runtime_session_state import make_state


def definitions(names):
    return [{"type": "function", "function": {"name": name, "description": f"Full {name}", "parameters": {"type": "object"}}} for name in names]


def turn(state, session="s", binding="b", turn_id="t"):
    return NativeRuntimeTurn(state, None, session, turn_id, binding, "task", [], None, None, None)


def test_global_append_subset_whole_reload_permission_and_exit():
    state, _ = make_state()
    owner = turn(state)
    stages = [
        (["core", "control"], ["core", "control"]),
        (["core", "control", "a1"], ["core", "control", "a1"]),
        (["core", "control", "a1", "b1"], ["core", "control", "a1", "b1"]),
        (["core", "control", "a1", "a2", "b1"], ["core", "control", "a1", "b1", "a2"]),
        # Whole-block exposure adds remaining members at the global tail.
        (["core", "control", "a0", "a1", "a2", "b1"], ["core", "control", "a1", "b1", "a2", "a0"]),
        (["core", "control", "b1"], ["core", "control", "b1"]),
        (["core", "control", "a1", "b1"], ["core", "control", "b1", "a1"]),
        # Newly allowed writes and newly active exit append, even if core.
        (["exit_skill", "core", "control", "write", "a1", "b1"], ["core", "control", "b1", "a1", "exit_skill", "write"]),
        (["core", "control", "a1", "b1"], ["core", "control", "b1", "a1"]),
    ]
    for visible, expected in stages:
        current = definitions(visible)
        frozen = deepcopy(current)
        result = owner.order_tools(current)
        assert [item["function"]["name"] for item in result] == expected
        assert {x["function"]["name"]: x for x in result} == {x["function"]["name"]: x for x in current}
        assert current == frozen
    assert "toolOrder" not in str(owner.messages())


def test_order_is_session_binding_scoped_and_private_resume_restores_it():
    state, _ = make_state()
    owner = turn(state)
    owner.order_tools(definitions(["a1", "b1"]))
    assert [x["function"]["name"] for x in owner.order_tools(definitions(["a1", "a2", "b1"]))] == ["a1", "b1", "a2"]
    seed = owner.approval_seed(AgentTaskLoop("task", session_id="s").approval_seed())
    context = approval_task_context(seed, tool="fixture", arguments={})
    assert context["_nativeConversation"]["toolOrder"] == ["a1", "b1", "a2"]
    restored, _ = make_state()
    restored.restore_native_conversation("s", binding="b", snapshot=context["_nativeConversation"])
    continued = turn(restored, turn_id="next")
    assert [x["function"]["name"] for x in continued.order_tools(definitions(["a0", "a1", "a2", "b1"]))] == ["a1", "b1", "a2", "a0"]
    unrelated = turn(restored, session="other")
    assert unrelated.order_tools(definitions(["b1", "a1"])) == definitions(["b1", "a1"])
    changed = turn(restored, binding="new", turn_id="new-turn")
    assert changed.order_tools(definitions(["b1", "a1"])) == definitions(["b1", "a1"])


def test_compaction_preserves_order_and_old_snapshot_can_start_order():
    state, _ = make_state()
    owner = turn(state)
    owner.order_tools(definitions(["b1", "a1"]))
    continued = turn(state, turn_id="next")
    snapshot = continued.snapshot()
    continued.replace_completed_prefix(snapshot, "previous task")
    assert continued.snapshot()["toolOrder"] == ["b1", "a1"]
    legacy = deepcopy(continued.snapshot())
    legacy.pop("toolOrder")
    fresh, _ = make_state()
    fresh.restore_native_conversation("s", binding="b", snapshot=legacy)
    assert turn(fresh, turn_id="third").order_tools(definitions(["a1", "b1"])) == definitions(["a1", "b1"])


@pytest.mark.parametrize("bad", [["duplicate", "duplicate"], [1], "a", [""]])
def test_malformed_private_order_rejected(bad):
    state, _ = make_state()
    snapshot = turn(state).snapshot()
    snapshot["toolOrder"] = bad
    fresh, _ = make_state()
    with pytest.raises(ValueError):
        fresh.restore_native_conversation("s", binding="b", snapshot=snapshot)
