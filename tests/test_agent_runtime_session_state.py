from __future__ import annotations

import threading
from pathlib import Path

from agent_gateway import AgentGateway
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts


def make_state() -> tuple[AgentRuntimeSessionState, threading.RLock]:
    lock = threading.RLock()
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=lock)), lock


def test_gateway_owns_one_runtime_session_state_with_the_gateway_lock(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    assert gateway.runtime_sessions is gateway.runtime_sessions
    assert gateway.runtime_sessions.shared_state_lock is gateway._lock
    assert gateway.desktop._ports.runtime_cancel_requested.__self__ is gateway.runtime_sessions
    assert not hasattr(gateway, "_runtime_sessions")
    assert not hasattr(gateway, "_cancelled_runtime_turns")
    assert not hasattr(gateway, "_runtime_stream_context")

    gateway.runtime_sessions.append_turn(
        "before-reconfigure",
        now="now",
        updated_at="now",
        turn={"id": "turn"},
    )
    gateway.runtime_sessions.mark_cancel_requested(turn_id="cancelled")
    gateway.configure_paths(tmp_path / "next-config.json", tmp_path / "next-audit")
    assert gateway.runtime_sessions.session_count() == 0
    assert gateway.runtime_sessions.cancel_requested(turn_id="cancelled") is False


def test_restore_append_bootstrap_and_clear_preserve_session_contract() -> None:
    state, _lock = make_state()
    history = [
        {"role": "user", "text": " first ", "createdAt": "before"},
        {"role": "unknown", "message": "second"},
        {"role": "agent", "text": "   "},
    ]

    assert state.restore_session("sess-1", history, "now") == 2
    assert state.restore_session("sess-1", [{"text": "ignored"}], "later") == 0
    restored = state.get_session("sess-1")
    assert restored == {
        "id": "sess-1",
        "createdAt": "now",
        "updatedAt": "now",
        "restoredFromTranscript": True,
        "turns": [
            {
                "id": "restored_0000",
                "createdAt": "before",
                "restored": True,
                "role": "user",
                "message": "first",
            },
            {
                "id": "restored_0001",
                "createdAt": "now",
                "restored": True,
                "role": "user",
                "message": "second",
            },
        ],
    }

    state.append_turn("sess-1", now="now", updated_at="after", turn={"id": "live"})
    state.record_desktop_bootstrap(
        "sess-1",
        now="after",
        status_summary="completed",
        result_summary={"apps": 2},
    )
    session = state.get_session("sess-1")
    assert session is not None
    assert session["updatedAt"] == "after"
    assert session["turns"][-1] == {"id": "live"}
    assert session["desktopBootstrapCompleted"] is True
    assert session["desktopBootstrapToolCalls"] == 1
    assert session["desktopBootstrapStatus"] == "completed"
    assert session["desktopBootstrapSummary"] == {"apps": 2}
    assert state.desktop_bootstrap_completed("sess-1") is True
    assert state.session_summary("sess-1") == {"turnCount": 3, "restoredFromTranscript": True}

    state.clear()
    assert state.session_count() == 0
    assert state.get_session("sess-1") is None


def test_cancel_markers_preserve_turn_precedence_and_single_consumption() -> None:
    state, _lock = make_state()

    state.mark_cancel_requested(session_id="session-only")
    state.mark_cancel_requested(session_id="ignored-session", turn_id="turn-1", client_turn_id="client-1")

    assert state.cancel_requested(session_id="session-only") is True
    assert state.cancel_requested(session_id="ignored-session") is False
    assert state.cancel_requested(turn_id="turn-1") is True
    assert state.consume_cancel_request(
        session_id="ignored-session",
        turn_id="turn-1",
        client_turn_id="client-1",
    ) is True
    assert state.cancel_requested(turn_id="turn-1", client_turn_id="client-1") is False
    assert state.consume_cancel_request(session_id="session-only") is True
    assert state.consume_cancel_request(session_id="session-only") is False


def test_stream_context_is_turn_local_between_threads() -> None:
    state, _lock = make_state()
    barrier = threading.Barrier(3)
    observed: dict[str, dict[str, str]] = {}

    def worker(name: str) -> None:
        state.set_stream_context({"turnId": name})
        barrier.wait()
        observed[name] = state.stream_context()
        barrier.wait()

    first = threading.Thread(target=worker, args=("first",))
    second = threading.Thread(target=worker, args=("second",))
    first.start()
    second.start()
    barrier.wait()
    assert state.stream_context() == {}
    barrier.wait()
    first.join(timeout=2)
    second.join(timeout=2)

    assert observed == {
        "first": {"turnId": "first"},
        "second": {"turnId": "second"},
    }


def test_runtime_steer_mailbox_is_scoped_bounded_fifo_and_single_drain() -> None:
    state, _lock = make_state()
    state.begin_turn(
        session_id="session-1",
        turn_id="turn-1",
        client_turn_id="client-1",
    )

    accepted = state.submit_steer(
        session_id="session-1",
        target_client_turn_id="client-1",
        input_id="input-1",
        message="inspect the package first",
    )
    assert accepted["accepted"] is True
    assert accepted["mode"] == "steer"
    assert state.submit_steer(
        session_id="session-1",
        target_client_turn_id="wrong-turn",
        input_id="input-wrong",
        message="must not cross turns",
    )["accepted"] is False

    assert state.drain_steer(
        session_id="session-1",
        client_turn_id="client-1",
    ) == [
        {"inputId": "input-1", "message": "inspect the package first"}
    ]
    assert state.drain_steer(session_id="session-1", client_turn_id="client-1") == []
    assert state.submit_steer(
        session_id="session-1", target_client_turn_id="client-1", input_id="input-1", message="replay"
    )["reason"] == "duplicate_input"

    for index in range(20):
        assert state.submit_steer(
            session_id="session-1",
            target_client_turn_id="client-1",
            input_id=f"burst-{index}",
            message=f"message {index}",
        )["accepted"] is True
    assert state.submit_steer(
        session_id="session-1",
        target_client_turn_id="client-1",
        input_id="overflow",
        message="overflow",
    )["reason"] == "mailbox_full"

    state.finish_turn(session_id="session-1", client_turn_id="client-1")
    assert state.submit_steer(
        session_id="session-1",
        target_client_turn_id="client-1",
        input_id="late",
        message="late follow-up",
    )["reason"] == "turn_not_active"


def test_runtime_finish_clears_client_cancel_marker() -> None:
    state, _lock = make_state()
    state.begin_turn(session_id="session-cancel", turn_id="turn-cancel", client_turn_id="client-cancel")
    state.mark_cancel_requested(session_id="session-cancel", client_turn_id="client-cancel")
    assert state.cancel_requested(client_turn_id="client-cancel") is True
    state.finish_turn(session_id="session-cancel", client_turn_id="client-cancel")
    assert state.cancel_requested(client_turn_id="client-cancel") is False


def test_runtime_active_turns_are_exact_pairs_for_concurrent_session_turns() -> None:
    state, _lock = make_state()
    state.begin_turn(session_id="shared", turn_id="a", client_turn_id="client-a")
    state.begin_turn(session_id="shared", turn_id="b", client_turn_id="client-b")
    assert state.submit_steer(session_id="shared", target_client_turn_id="client-a", input_id="a1", message="a")['accepted']
    assert state.submit_steer(session_id="shared", target_client_turn_id="client-b", input_id="b1", message="b")['accepted']
    state.finish_turn(session_id="shared", client_turn_id="client-a")
    assert state.submit_steer(session_id="shared", target_client_turn_id="client-b", input_id="b2", message="b2")['accepted']
    state.mark_cancel_requested(session_id="shared", client_turn_id="client-a")
    assert state.drain_steer(session_id="shared", client_turn_id="client-b")


def test_discard_session_clears_every_active_turn_and_mailbox() -> None:
    state, _lock = make_state()
    state.begin_turn(session_id="discarded", turn_id="turn-a", client_turn_id="client-a")
    state.begin_turn(session_id="discarded", turn_id="turn-b", client_turn_id="client-b")
    assert state.submit_steer(
        session_id="discarded",
        target_client_turn_id="client-a",
        input_id="input-a",
        message="a",
    )["accepted"]
    state.discard_session("discarded")
    assert state.submit_steer(
        session_id="discarded",
        target_client_turn_id="client-a",
        input_id="late-a",
        message="late",
    )["reason"] == "turn_not_active"


def test_concurrent_reuse_of_active_client_turn_is_rejected_without_replacing_owner() -> None:
    state, _lock = make_state()
    assert state.begin_turn(session_id="shared", turn_id="old-turn", client_turn_id="reused-client") is True
    assert state.begin_turn(session_id="shared", turn_id="new-turn", client_turn_id="reused-client") is False
    assert state.submit_steer(
        session_id="shared",
        target_client_turn_id="reused-client",
        input_id="old-owner-input",
        message="belongs to original owner",
    )["accepted"]
    state.finish_turn(session_id="shared", turn_id="old-turn", client_turn_id="reused-client")
    assert state.submit_steer(
        session_id="shared",
        target_client_turn_id="reused-client",
        input_id="late-input",
        message="late",
    )["reason"] == "turn_not_active"
    assert state.submit_steer(
        session_id="discarded",
        target_client_turn_id="client-b",
        input_id="late-b",
        message="late",
    )["reason"] == "turn_not_active"
