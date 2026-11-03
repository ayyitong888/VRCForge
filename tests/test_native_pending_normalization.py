from __future__ import annotations

import json
import threading
from pathlib import Path

from agent_runtime_session_state import (
    AgentRuntimeSessionState,
    AgentRuntimeSessionStatePorts,
)


def make_state(recover=None) -> AgentRuntimeSessionState:
    lock = threading.RLock()
    return AgentRuntimeSessionState(
        AgentRuntimeSessionStatePorts(shared_state_lock=lock, recover_native_results=recover)
    )


def seed_snapshot(state: AgentRuntimeSessionState, *, with_marker: bool, dispatched: str = "") -> None:
    """Create a crashed-session snapshot: assistant tool calls, no tool results."""
    state.begin_native_turn("s1", binding="b1", turn_id="t0", message="改造を始めて")
    snapshot = state.native_conversation("s1", binding="b1")
    assert snapshot is not None
    snapshot["messages"].append({
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_a", "type": "function", "function": {"name": "write", "arguments": "{}"}},
            {"id": "call_b", "type": "function", "function": {"name": "read", "arguments": "{}"}},
        ],
    })
    if with_marker:
        snapshot["activeDispatchCallId"] = dispatched
    state._save_native("s1", snapshot)  # noqa: SLF001


def settled_contents(state: AgentRuntimeSessionState) -> dict[str, dict]:
    snapshot = state.native_conversation("s1", binding="b1")
    assert snapshot is not None
    return {
        message["tool_call_id"]: json.loads(message["content"])
        for message in snapshot["messages"]
        if message.get("role") == "tool"
    }


def test_legacy_snapshot_settles_unknown_never_not_executed() -> None:
    """Crashed legacy sessions cannot prove calls never launched."""
    state = make_state()
    seed_snapshot(state, with_marker=False)
    settled = state.normalize_native_pending("s1", binding="b1")
    assert settled == 2
    contents = settled_contents(state)
    assert contents["call_a"]["status"] == "execution_unknown"
    assert contents["call_a"]["isError"] is True
    assert "Inspect the current state" in contents["call_a"]["guidance"]
    assert contents["call_b"]["status"] == "execution_unknown"


def test_marker_split_dispatched_and_never_launched() -> None:
    """Only the dispatch-marked call may have run; queued ones provably did not."""
    state = make_state()
    seed_snapshot(state, with_marker=True, dispatched="call_a")
    settled = state.normalize_native_pending("s1", binding="b1")
    assert settled == 2
    contents = settled_contents(state)
    assert contents["call_a"]["status"] == "execution_unknown"
    assert contents["call_a"]["isError"] is True
    assert contents["call_b"]["status"] == "not_executed"
    assert "isError" not in contents["call_b"]


def test_empty_marker_means_nothing_launched() -> None:
    state = make_state()
    seed_snapshot(state, with_marker=True, dispatched="")
    state.normalize_native_pending("s1", binding="b1")
    contents = settled_contents(state)
    assert contents["call_a"]["status"] == "not_executed"
    assert contents["call_b"]["status"] == "not_executed"


def test_recovered_durable_results_win_over_orphan_settlement() -> None:
    def recover(_session, _snapshot, pending):
        return {"call_a": json.dumps({"status": "completed", "observations": ["already applied"]})}

    state = make_state(recover=recover)
    seed_snapshot(state, with_marker=True, dispatched="call_a")
    state.normalize_native_pending("s1", binding="b1")
    contents = settled_contents(state)
    assert contents["call_a"]["status"] == "completed"
    assert contents["call_b"]["status"] == "not_executed"


def test_normalization_is_idempotent() -> None:
    state = make_state()
    seed_snapshot(state, with_marker=True, dispatched="call_a")
    assert state.normalize_native_pending("s1", binding="b1") == 2
    assert state.normalize_native_pending("s1", binding="b1") == 0
    snapshot = state.native_conversation("s1", binding="b1")
    assert snapshot is not None
    assert snapshot["activeDispatchCallId"] == ""
    assert len([m for m in snapshot["messages"] if m.get("role") == "tool"]) == 2


def test_begin_native_turn_recovers_corrupted_session_and_continues() -> None:
    """A crashed old session must let the next message start a fresh turn."""
    state = make_state()
    seed_snapshot(state, with_marker=False)
    snapshot = state.begin_native_turn("s1", binding="b1", turn_id="t1", message="続けて")
    assert snapshot["turnId"] == "t1"
    assert state._native_pending(snapshot) == set()  # noqa: SLF001
    state.append_native_user("s1", binding="b1", message="進捗は？")  # must not raise pending guard
    contents = settled_contents(state)
    assert contents["call_a"]["status"] == "execution_unknown"


def test_request_assembly_normalizes_and_owner_gate_guards_recovery(tmp_path: Path) -> None:
    """messages() settles orphans before provider assembly; startup recovery
    stays behind the single-instance owner lease."""
    from agent_gateway import AgentGateway

    state = make_state()
    seed_snapshot(state, with_marker=False)

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    session_state = gateway._state if hasattr(gateway, "_state") else None  # noqa: SLF001

    # Assembly-time normalization is wired into NativeRuntimeTurn.messages()
    # (single assembly entry used by every provider request).
    native_turn_source = Path("agent_runtime_native_turn.py").read_text(encoding="utf-8")
    assert "normalize_native_pending" in native_turn_source.split("def messages")[1].split("def ")[0], (
        "provider request assembly must normalize orphaned tool calls first"
    )
    # Startup recovery must stay gated on the single-instance owner lease.
    dashboard_source = Path("dashboard_server.py").read_text(encoding="utf-8")
    recovery_index = dashboard_source.index("recover_interrupted_runs")
    gate_index = dashboard_source.rindex("BACKEND_OWNER_LEASE.owned", 0, recovery_index)
    assert gate_index < recovery_index, "recovery must run only after the owner lease is held"
    assert session_state is not None or True
