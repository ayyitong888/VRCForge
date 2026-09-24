from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

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


def test_final_response_recovery_is_exact_bounded_and_hidden_from_session_transcript() -> None:
    state, _lock = make_state()
    response = {
        "ok": True,
        "sessionId": "sess-1",
        "clientTurnId": "turn-0",
        "status": "completed",
        "contextCompaction": {"summary": "full summary"},
        "consumedSteerInputIds": ["steer-1"],
        "deferredSteerFollowups": [{"inputId": "steer-2", "status": "pending"}],
        "result": {"value": "preserved"},
    }
    state.begin_turn(session_id="sess-1", turn_id="server-0", client_turn_id="turn-0")
    assert state.final_response(session_id="sess-1", client_turn_id="turn-0")["status"] == "running"
    assert state.submit_steer(
        session_id="sess-1", target_client_turn_id="turn-0", input_id="steer-2", message="follow up"
    )["accepted"] is True
    late_steers = state.finish_turn(session_id="sess-1", turn_id="server-0", client_turn_id="turn-0")
    assert late_steers[0]["inputId"] == "steer-2"
    state.record_final_response(
        session_id="sess-1", client_turn_id="turn-0", status="completed", response=response
    )

    recovered = state.final_response(session_id="sess-1", client_turn_id="turn-0")
    assert recovered["status"] == "completed"
    assert recovered["response"] == response
    assert state.get_session("sess-1") is None
    assert state.final_response(session_id="sess-1", client_turn_id="other")["status"] == "missing"

    for index in range(1, state.MAX_FINAL_RESPONSES + 3):
        state.record_final_response(
            session_id="sess-1", client_turn_id=f"turn-{index}", status="completed", response={"index": index}
        )
    assert state.final_response(session_id="sess-1", client_turn_id="turn-0")["status"] == "missing"
    assert state.final_response(session_id="sess-1", client_turn_id="turn-10")["status"] == "completed"


def test_final_response_failed_and_finalizing_state_do_not_report_missing() -> None:
    state, _lock = make_state()
    state.begin_turn(session_id="sess-2", turn_id="server-2", client_turn_id="turn-2")
    state.finish_turn(session_id="sess-2", turn_id="server-2", client_turn_id="turn-2")
    assert state.final_response(session_id="sess-2", client_turn_id="turn-2")["status"] == "running"
    state.record_final_response(
        session_id="sess-2",
        client_turn_id="turn-2",
        status="failed",
        response={"ok": False, "status": "failed"},
        error="provider failed",
    )
    failed = state.final_response(session_id="sess-2", client_turn_id="turn-2")
    assert failed["status"] == "failed"
    assert failed["error"] == "provider failed"
    state.clear()
    assert state.final_response(session_id="sess-2", client_turn_id="turn-2")["status"] == "missing"


@pytest.mark.parametrize("next_step", ["pending_approval", "planner_failed"])
@pytest.mark.parametrize("original_status", [None, "failed"])
def test_gateway_final_response_cache_preserves_normal_response_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, next_step: str, original_status: str | None,
) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    expected = {
        "ok": True,
        "sessionId": "sess-normal",
        "clientTurnId": "client-normal",
        "observe": {"value": "kept"},
        "plan": {"nextStep": next_step, "reply": "kept"},
        "steps": [{"tool": "read", "status": "ok"}],
        "contextCompaction": {"summary": "kept"},
    }
    if original_status is not None:
        expected["status"] = original_status

    def body(params: dict[str, object], **_kwargs: object) -> dict[str, object]:
        params["_resolvedRuntimeSessionId"] = "sess-normal"
        params["_resolvedRuntimeTurnId"] = "server-normal"
        params["_resolvedRuntimeClientTurnId"] = "client-normal"
        return expected

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", body)
    result = gateway._runtime_message_impl({})
    assert result == expected
    recovered = gateway.get_runtime_session("sess-normal", client_turn_id="client-normal")
    assert recovered["status"] == "completed"
    assert recovered["response"] == expected


def test_gateway_early_exception_converges_and_duplicate_owner_cannot_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    def early_failure(params: dict[str, object], **_kwargs: object) -> dict[str, object]:
        params["_resolvedRuntimeSessionId"] = "sess-early"
        params["_resolvedRuntimeTurnId"] = "server-early"
        params["_resolvedRuntimeClientTurnId"] = "client-early"
        assert gateway.runtime_sessions.begin_turn(
            session_id="sess-early", turn_id="server-early", client_turn_id="client-early"
        )
        raise RuntimeError("early failure")

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", early_failure)
    with pytest.raises(RuntimeError, match="early failure"):
        gateway._runtime_message_impl({})
    early = gateway.get_runtime_session("sess-early", client_turn_id="client-early")
    assert early["status"] == "failed"
    assert early["response"]["plan"]["nextStep"] == "failed"

    gateway.runtime_sessions.record_final_response(
        session_id="sess-duplicate", client_turn_id="client-duplicate",
        status="completed", response={"sentinel": "original"},
    )
    assert gateway.runtime_sessions.begin_turn(
        session_id="sess-duplicate", turn_id="server-existing", client_turn_id="client-duplicate"
    )

    def duplicate_failure(params: dict[str, object], **_kwargs: object) -> dict[str, object]:
        params["_resolvedRuntimeSessionId"] = "sess-duplicate"
        params["_resolvedRuntimeTurnId"] = "server-new"
        params["_resolvedRuntimeClientTurnId"] = "client-duplicate"
        assert not gateway.runtime_sessions.begin_turn(
            session_id="sess-duplicate", turn_id="server-new", client_turn_id="client-duplicate"
        )
        raise RuntimeError("duplicate owner")

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", duplicate_failure)
    with pytest.raises(RuntimeError, match="duplicate owner"):
        gateway._runtime_message_impl({})
    gateway.runtime_sessions.finish_turn(
        session_id="sess-duplicate", turn_id="server-existing", client_turn_id="client-duplicate"
    )
    gateway.runtime_sessions.record_final_response(
        session_id="sess-duplicate", client_turn_id="client-duplicate",
        status="completed", response={"sentinel": "original"},
    )
    assert gateway.get_runtime_session("sess-duplicate", client_turn_id="client-duplicate")["response"] == {"sentinel": "original"}


def test_internal_tool_blocks_are_session_scoped_and_core_cannot_be_unloaded() -> None:
    state, _lock = make_state()

    assert state.internal_tool_blocks("session-a") == frozenset({"core"})
    assert state.internal_tool_blocks("session-b") == frozenset({"core"})

    assert state.load_internal_tool_block("session-a", "files") == frozenset(
        {"core", "files"}
    )
    assert state.internal_tool_blocks("session-b") == frozenset({"core"})
    assert state.unload_internal_tool_block("session-a", "core") == frozenset(
        {"core", "files"}
    )
    assert state.unload_internal_tool_block("session-a", "files") == frozenset(
        {"core"}
    )


def test_internal_tool_selection_unions_promotes_and_unload_clears() -> None:
    state, _lock = make_state()

    assert state.load_internal_tool_block_selected("session-a", "unity", ["scan_fx"]) == frozenset({"core", "unity"})
    assert state.internal_tool_selections("session-a") == {"unity": ["scan_fx"]}
    state.load_internal_tool_block_selected("session-a", "unity", ["list_avatars"])
    assert state.internal_tool_selections("session-a") == {"unity": ["list_avatars", "scan_fx"]}
    state.load_internal_tool_block_selected("session-a", "unity", None)
    assert state.internal_tool_selections("session-a") == {"unity": None}
    state.unload_internal_tool_block("session-a", "unity")
    assert state.internal_tool_selections("session-a") == {}


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


def test_session_only_cancel_binds_active_turn_and_does_not_leak_to_next_turn() -> None:
    state, _lock = make_state()
    state.begin_turn(session_id="session-race", turn_id="turn-a", client_turn_id="client-a")

    state.mark_cancel_requested(session_id="session-race")
    state.finish_turn(session_id="session-race", turn_id="turn-a", client_turn_id="client-a")

    state.begin_turn(session_id="session-race", turn_id="turn-b", client_turn_id="client-b")
    assert state.consume_cancel_request(
        session_id="session-race", turn_id="turn-b", client_turn_id="client-b"
    ) is False


def test_session_only_cancel_binds_all_active_turns() -> None:
    state, _lock = make_state()
    state.begin_turn(session_id="session-many", turn_id="turn-a", client_turn_id="client-a")
    state.begin_turn(session_id="session-many", turn_id="turn-b", client_turn_id="client-b")

    state.mark_cancel_requested(session_id="session-many")
    assert state.consume_cancel_request(
        session_id="session-many", turn_id="turn-a", client_turn_id="client-a"
    ) is True
    assert state.consume_cancel_request(
        session_id="session-many", turn_id="turn-b", client_turn_id="client-b"
    ) is True


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


def test_native_conversation_is_private_and_tracks_pending_calls() -> None:
    state, _lock = make_state()
    snapshot = state.begin_native_turn(
        "native-1", binding="project|model|protocol|endpoint", turn_id="turn-1", message="hello"
    )
    assert snapshot == {
        "binding": "project|model|protocol|endpoint",
        "turnId": "turn-1",
        "messages": [{"role": "user", "content": "hello"}],
        "activeTurnStart": 0,
    }
    state.append_native_assistant(
        "native-1",
        binding="project|model|protocol|endpoint",
        message={
            "role": "assistant",
            "content": None,
            "reasoning_content": "private reasoning",
            "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "inspect", "arguments": "{not-json"},
            }],
        },
    )
    assert state.native_conversation("native-1", binding="project|model|protocol|endpoint")["messages"][-1]["reasoning_content"] == "private reasoning"
    assert state.session_summary("native-1") == {"turnCount": 0, "restoredFromTranscript": False}
    assert state.get_session("native-1") is None
    with pytest.raises(ValueError, match="pending"):
        state.begin_native_turn("native-1", binding="other", turn_id="turn-2", message="blocked")


def test_native_conversation_settles_multiple_calls_and_rejects_orphans_and_duplicates() -> None:
    state, _lock = make_state()
    state.begin_native_turn("s", binding="b", turn_id="t", message="u")
    state.append_native_assistant(
        "s", binding="b", message={"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "one", "arguments": "raw"}},
            {"id": "b", "type": "function", "function": {"name": "two", "arguments": "{}"}},
        ]}
    )
    with pytest.raises(ValueError, match="unknown"):
        state.settle_native_call("s", binding="b", call_id="missing", content="x")
    state.settle_native_call("s", binding="b", call_id="a", content="one-result")
    with pytest.raises(ValueError, match="already"):
        state.settle_native_call("s", binding="b", call_id="a", content="again")
    with pytest.raises(ValueError, match="pending"):
        state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": "no"})
    state.settle_native_call("s", binding="b", call_id="b", content="two-result")
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": "done"})
    assert [m["role"] for m in state.native_conversation("s", binding="b")["messages"]] == [
        "user", "assistant", "tool", "tool", "assistant"
    ]


def test_native_conversation_same_turn_is_idempotent_and_binding_changes_after_settle() -> None:
    state, _lock = make_state()
    first = state.begin_native_turn("s", binding="b", turn_id="t", message="u")
    assert state.begin_native_turn("s", binding="b", turn_id="t", message="different") == first
    continued = state.begin_native_turn("s", binding="b", turn_id="t2", message="next")
    assert continued["messages"] == [
        {"role": "user", "content": "u"},
        {"role": "user", "content": "next"},
    ]
    with pytest.raises(ValueError, match="non-empty"):
        state.begin_native_turn("s", binding="", turn_id="t2", message="x")
    changed = state.begin_native_turn("s", binding="new", turn_id="t2", message="new-u")
    assert changed == {"binding": "new", "turnId": "t2", "messages": [{"role": "user", "content": "new-u"}], "activeTurnStart": 0}


def test_native_conversation_size_rejection_is_atomic_and_cleanup_releases_private_state() -> None:
    state, _lock = make_state()
    state.begin_native_turn("s", binding="b", turn_id="t", message="u")
    before = state.native_conversation("s", binding="b")
    with pytest.raises(ValueError, match="size"):
        state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": "x" * (2 * 1024 * 1024)})
    assert state.native_conversation("s", binding="b") == before
    state.clear()
    assert state.native_conversation("s", binding="b") is None
    state.begin_native_turn("s", binding="b", turn_id="t", message="u")
    state.discard_session("s")
    assert state.native_conversation("s", binding="b") is None


def test_restore_native_pending_snapshot_settles_then_allows_following_messages() -> None:
    state, _lock = make_state()
    snapshot = {
        "binding": "b", "turnId": "t", "activeTurnStart": 0, "messages": [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call", "type": "function", "function": {"name": "f", "arguments": "raw"}},
            ]},
        ],
    }
    assert state.restore_native_conversation("s", binding="b", snapshot=snapshot) == snapshot
    state.settle_native_call("s", binding="b", call_id="call", content="ok")
    state.append_native_user("s", binding="b", message="after")
    assert state.native_conversation("s", binding="b")["messages"][-1] == {"role": "user", "content": "after"}


def test_restore_native_rejects_corrupt_orphan_duplicate_and_wrong_binding_snapshots() -> None:
    state, _lock = make_state()
    base = {"binding": "b", "turnId": "t", "activeTurnStart": 0, "messages": [{"role": "user", "content": "u"}]}
    with pytest.raises(ValueError):
        state.restore_native_conversation("s", binding="wrong", snapshot=base)
    for bad in (
        {**base, "messages": [{"role": "user", "content": 1}]},
        {**base, "messages": [{"role": "tool", "tool_call_id": "orphan", "content": "x"}]},
        {**base, "messages": [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "dup", "type": "function", "function": {"name": "f", "arguments": ""}},
            {"id": "dup", "type": "function", "function": {"name": "g", "arguments": ""}},
        ]}]},
    ):
        with pytest.raises(ValueError):
            state.restore_native_conversation("s", binding="b", snapshot=bad)
    assert state.native_conversation("s", binding="b") is None


def test_restore_native_prefix_updates_but_never_rolls_back_newer_state() -> None:
    state, _lock = make_state()
    first = {"binding": "b", "turnId": "t", "activeTurnStart": 0, "messages": [{"role": "user", "content": "u"}]}
    newer = {"binding": "b", "turnId": "t2", "activeTurnStart": 0, "messages": [
        {"role": "user", "content": "u"}, {"role": "user", "content": "new"}
    ]}
    state.restore_native_conversation("s", binding="b", snapshot=first)
    assert state.restore_native_conversation("s", binding="b", snapshot=newer) == newer
    assert state.restore_native_conversation("s", binding="b", snapshot=first) == newer
    with pytest.raises(ValueError):
        state.restore_native_conversation("s", binding="b", snapshot={"binding": "b", "turnId": "x", "activeTurnStart": 0, "messages": [{"role": "user", "content": "other"}]})


def test_append_native_user_rejects_pending_and_size_is_atomic() -> None:
    state, _lock = make_state()
    state.begin_native_turn("s", binding="b", turn_id="t", message="u")
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": None, "tool_calls": [
        {"id": "call", "type": "function", "function": {"name": "f", "arguments": ""}},
    ]})
    with pytest.raises(ValueError, match="pending"):
        state.append_native_user("s", binding="b", message="blocked")
    state.settle_native_call("s", binding="b", call_id="call", content="ok")
    before = state.native_conversation("s", binding="b")
    with pytest.raises(ValueError, match="size"):
        state.append_native_user("s", binding="b", message="x" * (2 * 1024 * 1024))
    assert state.native_conversation("s", binding="b") == before


def test_native_first_turn_hydrates_visible_history_once_and_tracks_active_boundary() -> None:
    state, _lock = make_state()
    history = [
        {"role": "user", "text": "earlier question", "createdAt": "old"},
        {"role": "system", "text": "display metadata only"},
        {"role": "agent", "text": "earlier answer", "metadata": {"compact": True}},
        {"role": "agent", "text": "   ", "metadata": {"displayOnly": True}},
    ]
    first = state.begin_native_turn(
        "s", binding="b", turn_id="t", message="current question", initial_history=history
    )
    assert first["activeTurnStart"] == 2
    assert first["messages"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "current question"},
    ]
    assert state.begin_native_turn(
        "s", binding="b", turn_id="t", message="duplicate", initial_history=history
    ) == first


def test_native_completed_prefix_replace_is_atomic_cas_and_preserves_current_suffix() -> None:
    state, _lock = make_state()
    state.begin_native_turn(
        "s", binding="b", turn_id="t", message="current", initial_history=[
            {"role": "user", "text": "old"}, {"role": "agent", "text": "answer"}
        ]
    )
    source = state.native_conversation("s", binding="b")
    replaced = state.replace_native_completed_prefix(
        "s", binding="b", expected_snapshot=source, summary="Older continuity"
    )
    assert replaced["activeTurnStart"] == 1
    assert replaced["messages"] == [
        {"role": "assistant", "content": "Older continuity"},
        {"role": "user", "content": "current"},
    ]
    with pytest.raises(ValueError, match="snapshot"):
        state.replace_native_completed_prefix(
            "s", binding="b", expected_snapshot=source, summary="stale"
        )
    assert state.native_conversation("s", binding="b") == replaced


def test_native_prefix_cas_preserves_pending_current_suffix_and_rejects_invalid_boundary() -> None:
    state, _lock = make_state()
    state.begin_native_turn(
        "s", binding="b", turn_id="t", message="current", initial_history=[
            {"role": "user", "text": "old"}, {"role": "agent", "text": "answer"}
        ]
    )
    state.append_native_assistant(
        "s", binding="b", message={"role": "assistant", "content": None, "tool_calls": [
            {"id": "pending", "type": "function", "function": {"name": "f", "arguments": "raw"}},
        ]}
    )
    source = state.native_conversation("s", binding="b")
    replaced = state.replace_native_completed_prefix(
        "s", binding="b", expected_snapshot=source, summary="Older continuity"
    )
    assert replaced["messages"][1:] == source["messages"][source["activeTurnStart"]:]
    assert state._native_pending(replaced) == {"pending"}
    invalid = dict(replaced)
    invalid["activeTurnStart"] = 2
    with pytest.raises(ValueError, match="active turn"):
        state.restore_native_conversation("other", binding="b", snapshot=invalid)


def test_native_stale_precompaction_snapshot_cannot_expand_owner() -> None:
    state, _lock = make_state()
    state.begin_native_turn(
        "s", binding="b", turn_id="t", message="current", initial_history=[
            {"role": "user", "text": "old"}, {"role": "agent", "text": "answer"}
        ]
    )
    stale = state.native_conversation("s", binding="b")
    compacted = state.replace_native_completed_prefix(
        "s", binding="b", expected_snapshot=stale, summary="Older continuity"
    )
    assert state.restore_native_conversation("s", binding="b", snapshot=stale) == compacted


def test_native_new_binding_hydrates_without_old_private_reasoning_and_pending_old_binding_rejects() -> None:
    state, _lock = make_state()
    state.begin_native_turn("s", binding="old", turn_id="t", message="old")
    state.append_native_assistant(
        "s", binding="old", message={"role": "assistant", "content": None, "reasoning_content": "private", "tool_calls": [
            {"id": "pending", "type": "function", "function": {"name": "f", "arguments": "raw"}},
        ]}
    )
    with pytest.raises(ValueError, match="pending"):
        state.begin_native_turn("s", binding="new", turn_id="new", message="new", initial_history=[])
    state.settle_native_call("s", binding="old", call_id="pending", content="ok")
    fresh = state.begin_native_turn(
        "s", binding="new", turn_id="new", message="new", initial_history=[
            {"role": "agent", "text": "visible old answer", "reasoning_content": "ignored"}
        ]
    )
    assert "private" not in json.dumps(fresh)
    assert fresh["messages"] == [
        {"role": "assistant", "content": "visible old answer"},
        {"role": "user", "content": "new"},
    ]
