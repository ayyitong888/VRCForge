import json
import threading
import subprocess
import sys

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts


def owner(path):
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), NativeHistoryStore(path)))


def populate(state):
    state.begin_native_turn("s", binding="project|provider", turn_id="t1", message="inspect")
    state.load_internal_tool_block_selected("s", "avatar", ["read_menu"])
    state.order_native_tools("s", binding="project|provider", visible_names=["core", "read_menu"])
    state.append_native_assistant("s", binding="project|provider", message={
        "role": "assistant", "content": None, "reasoning_content": "original reasoning",
        "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "read_menu", "arguments": '{"page":1}'}}]})
    state.settle_native_call("s", binding="project|provider", call_id="call1", content='{"page":1,"hasMore":true}')
    state.append_native_assistant("s", binding="project|provider", message={"role": "assistant", "content": "found"})
    return state.native_conversation("s", binding="project|provider")


def test_restart_and_append_preserve_exact_native_messages_and_tool_order(tmp_path):
    before = populate(owner(tmp_path))
    restarted = owner(tmp_path)
    restarted.restore_session("s", [{"role": "agent", "text": "lossy UI summary"}], "now")
    assert restarted.internal_tool_selections("s") == {"avatar": ["read_menu"]}
    assert restarted.internal_tool_blocks("s") == frozenset({"core", "avatar"})
    after = restarted.begin_native_turn("s", binding="project|provider", turn_id="t2", message="continue")
    assert json.dumps(after["messages"][:-1], ensure_ascii=False) == json.dumps(before["messages"], ensure_ascii=False)
    assert after["messages"][-1] == {"role": "user", "content": "continue"}
    assert after["toolOrder"] == before["toolOrder"]
    assert restarted.order_native_tools("s", binding="project|provider", visible_names=["read_menu", "core"]) == before["toolOrder"]


def test_corrupt_history_fails_without_flattening_or_overwriting(tmp_path):
    populate(owner(tmp_path))
    path = next(tmp_path.glob("*.json"))
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        owner(tmp_path).begin_native_turn("s", binding="project|provider", turn_id="t2", message="continue",
            initial_history=[{"role": "agent", "text": "flattened"}])
    assert path.read_text() == "broken"


def test_pending_call_is_preserved_not_reexecuted(tmp_path):
    state = owner(tmp_path)
    state.begin_native_turn("s", binding="b", turn_id="t", message="u")
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "write", "arguments": "{}"}}]})
    with pytest.raises(ValueError, match="pending tool calls"):
        owner(tmp_path).begin_native_turn("s", binding="b", turn_id="next", message="continue")


def test_scoping_delete_and_memory_clear(tmp_path):
    state = owner(tmp_path)
    original = populate(state)
    assert owner(tmp_path).native_conversation("s", binding="other-project") is None
    assert owner(tmp_path / "another-profile").native_conversation("s", binding="project|provider") is None
    state.clear()
    assert state.native_conversation("s", binding="project|provider") == original
    state.discard_session("s")
    assert owner(tmp_path).native_conversation("s", binding="project|provider") is None


def test_atomic_write_failure_keeps_previous_disk_and_memory(tmp_path, monkeypatch):
    state = owner(tmp_path)
    before = populate(state)
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr("agent_native_history_store.os.replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        state.append_native_user("s", binding="project|provider", message="new")
    assert state.native_conversation("s", binding="project|provider") == before
    assert owner(tmp_path).native_conversation("s", binding="project|provider") == before
    assert not list(tmp_path.glob(".native-*"))


def test_real_gateway_restart_request_keeps_original_native_prefix(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, run, call, finish
    first, first_model, invoked = setup_gateway(tmp_path, [
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish])
    run(first, tmp_path)
    assert len(invoked) == 1
    # Bind exactly as the gateway does; provider metadata is scoped to the turn.
    with first.runtime_planner.bind_turn({}):
        binding = first.runtime_planner.native_conversation_binding(str(tmp_path))
    saved = first.runtime_sessions.native_conversation("native-session", binding=binding)
    restarted, resumed_model, resumed_invoked = setup_gateway(tmp_path, [
        {"role": "assistant", "content": "continued"}])
    run(restarted, tmp_path, message="Continue", clientTurnId="second-turn",
        history=[{"role": "agent", "text": "UI-only summary must not replace tool history"}])
    sent = resumed_model.requests[0]
    assert sent["messages"][:len(saved["messages"])] == saved["messages"]
    assert sent["messages"][len(saved["messages"])] == {"role": "user", "content": "Continue"}
    assert sent["tools"] == first_model.requests[-1]["tools"]
    assert {k: v for k, v in sent.items() if k != "messages"} == {
        k: v for k, v in first_model.requests[-1].items() if k != "messages"}
    assert not resumed_invoked


def test_unload_after_restart_is_persisted(tmp_path):
    populate(owner(tmp_path))
    state = owner(tmp_path)
    assert state.unload_internal_tool_block("s", "avatar") == frozenset({"core"})
    assert owner(tmp_path).internal_tool_selections("s") == {}


def test_fresh_python_process_restores_after_writer_exits(tmp_path):
    script = "from pathlib import Path; from tests.test_native_history_storage import owner,populate; import sys; populate(owner(Path(sys.argv[1])))"
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True, timeout=30)
    restored = owner(tmp_path).begin_native_turn("s", binding="project|provider", turn_id="t2", message="continue")
    assert [m["role"] for m in restored["messages"]] == ["user", "assistant", "tool", "assistant", "user"]
    assert restored["messages"][1]["reasoning_content"] == "original reasoning"
    assert restored["messages"][2]["content"] == '{"page":1,"hasMore":true}'


def test_switch_binding_then_switch_back_preserves_both_histories(tmp_path):
    state = owner(tmp_path)
    first = populate(state)
    state.begin_native_turn("s", binding="other-route", turn_id="t2", message="another model")
    state.append_native_assistant("s", binding="other-route", message={"role": "assistant", "content": "second"})
    restarted = owner(tmp_path)
    recovered = restarted.begin_native_turn("s", binding="project|provider", turn_id="t3", message="back")
    assert recovered["messages"][:-1] == first["messages"]
    other = owner(tmp_path).native_conversation("s", binding="other-route")
    assert other["messages"][-1]["content"] == "second"
    restarted.discard_session("s")
    assert not list(tmp_path.glob("*.json"))
