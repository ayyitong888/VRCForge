"""Offline behavior contracts for multiple reads and single supervised writes.

No new batch API is assumed: a standard native assistant receipt carries calls.
The first test is intentionally red until the host can admit independent reads.
"""
import json

import pytest

from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def _receipt(*items):
    receipt = dict(items[0])
    receipt["tool_calls"] = [entry for item in items for entry in item["tool_calls"]]
    return receipt


def test_two_independent_reads_share_one_model_response_and_keep_results_separate(tmp_path):
    receipt = _receipt(
        call("read-alpha", "vrcforge_read_text_file", {"path": "alpha.txt"}),
        call("read-beta", "vrcforge_read_text_file", {"path": "beta.txt"}),
    )
    gateway, model, invoked = setup_gateway(tmp_path, [receipt, finish])

    def read(arguments):
        invoked.append(arguments["path"])
        return {"text": "result-for-" + arguments["path"]}

    gateway._tools["vrcforge_read_text_file"].handler = read
    result = run(gateway, tmp_path, maxAgenticTurns=2)

    assert sorted(invoked) == ["alpha.txt", "beta.txt"], (
        "Both independent reads must execute from the same assistant receipt; "
        "the blanket parallel_calls_unsupported rejection is the regression."
    )
    # One response requests both reads; the next response is the final answer.
    # There must be no intermediate model call to admit the second read.
    assert len(model.requests) == 2
    history = model.requests[1]["messages"]
    tool_results = {item["tool_call_id"]: item for item in history if item["role"] == "tool"}
    assert set(tool_results) == {"read-alpha", "read-beta"}
    for call_id, own, sibling in (
        ("read-alpha", "alpha.txt", "beta.txt"),
        ("read-beta", "beta.txt", "alpha.txt"),
    ):
        content = tool_results[call_id]["content"]
        assert "result-for-" + own in content
        assert "result-for-" + sibling not in content, "Do not copy the entire batch result to every call ID"
        assert "parallel_calls_unsupported" not in content
        observations = json.loads(content)["observations"]
        assert len(observations) == 1
        assert observations[0]["outcome"]["status"] == "ok"
    assert result["plan"]["nextStep"] == "done"


@pytest.mark.parametrize("mode", ["approval", "auto"])
def test_two_writes_in_one_receipt_never_dispatch_as_a_batch(tmp_path, mode):
    project = str(tmp_path / "UnityProject")
    receipt = _receipt(
        call("write-alpha", "fixture_write", {"projectRoot": project, "value": "alpha"}),
        call("write-beta", "fixture_write", {"projectRoot": project, "value": "beta"}),
    )
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [receipt, {
        "role": "assistant", "content": "The proposed write batch was not executed."
    }])
    _set_mode(gateway, mode)

    run(gateway, tmp_path, message="Write the fixture", maxAgenticTurns=2)

    assert not invoked, "A multi-write receipt must not bypass single-write admission, including auto mode"
    assert len(model.requests) == 2
    tool_results = [m for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_results} == {"write-alpha", "write-beta"}
    assert all("failed" in m["content"] or "invalid" in m["content"] for m in tool_results)


def test_single_write_still_requires_the_existing_real_approval_owner(tmp_path):
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        call("single-write", "fixture_write", {
            "projectRoot": str(tmp_path / "UnityProject"), "value": "one"
        })
    ])
    _set_mode(gateway, "approval")

    result = run(gateway, tmp_path, message="Write the fixture", maxAgenticTurns=2)

    assert len(model.requests) == 1
    assert result["write"]["status"] == "approval_pending"
    assert result["plan"]["nextStep"] == "needs_user_action"
    assert not invoked


@pytest.mark.parametrize("stateful_name,arguments", [
    ("vrcforge_load_internal_tool_block", {"block": "diagnostics_build/compile_logs"}),
    ("vrcforge_ask_user", {"question": "Continue?"}),
])
def test_write_false_stateful_calls_remain_serial_not_parallel(tmp_path, stateful_name, arguments):
    receipt = _receipt(
        call("read-alpha", "vrcforge_read_text_file", {"path": "alpha.txt"}),
        call("stateful", stateful_name, arguments),
    )
    gateway, model, invoked = setup_gateway(tmp_path, [receipt, {
        "role": "assistant", "content": "The mixed receipt was not executed."
    }])
    state_changes = []
    def stateful(params):
        assert len(invoked) == 1, "The prior read must finish before state changes"
        state_changes.append(params)
        return {"ok": True}
    gateway.register_tool(
        stateful_name, "When to use: update runtime state. When NOT to use: as a pure read.",
        "plan/preview", stateful,
        write=False,
    )

    run(gateway, tmp_path, maxAgenticTurns=2)

    assert gateway._tools[stateful_name].write is False
    assert len(state_changes) == 1
    assert len(invoked) == 1
    assert len(model.requests) == 2


def test_mixed_read_write_read_resumes_only_pending_write_then_remaining_read(tmp_path):
    project = str(tmp_path / "UnityProject")
    receipt = _receipt(
        call("before", "vrcforge_read_text_file", {"path": "before.txt"}),
        call("write", "fixture_write", {"projectRoot": project, "value": "approved"}),
        call("after", "vrcforge_read_text_file", {"path": "after.txt"}),
    )
    gateway, model, writes = _write_gateway_for_mode(tmp_path, [receipt, finish])
    reads = []
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", lambda args: reads.append(args["path"]) or {"text": args["path"]})
    _set_mode(gateway, "approval")
    initial = run(gateway, tmp_path, message="Read, write once, then read", maxAgenticTurns=2)
    assert reads == ["before.txt"] and writes == []
    assert len(model.requests) == 1
    assert initial["write"]["status"] == "approval_pending"
    approval_id = initial["approval_id"]
    approval = gateway._approvals[approval_id]
    gateway.approval_transactions.approve(approval_id)
    applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    assert applied["status"] == "applied"
    resumed = gateway.resume_runtime_task_after_approval(approval, applied)
    assert reads == ["before.txt", "after.txt"]
    assert len(writes) == 1 and len(model.requests) == 2
    assert resumed["plan"]["nextStep"] == "done"
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in model.requests[1]["messages"] if m["role"] == "tool"}
    assert set(results) == {"before", "write", "after"}
    assert "before.txt" in json.dumps(results["before"])
    assert "after.txt" not in json.dumps(results["before"])
    assert "after.txt" in json.dumps(results["after"])
    assert "approved" not in json.dumps(results["after"])


def test_stop_after_first_read_settles_remaining_call_without_dispatch(tmp_path):
    from tests.test_native_runtime_continuations import _binding
    receipt = _receipt(call("first", "vrcforge_read_text_file", {"path": "one"}),
                       call("later", "vrcforge_read_text_file", {"path": "two"}))
    gateway, model, reads = setup_gateway(tmp_path, [receipt])
    def read(args):
        reads.append(args["path"])
        gateway.request_runtime_cancel({"sessionId": "native-session"})
        return {"text": "first finished"}
    gateway._tools["vrcforge_read_text_file"].handler = read
    result = run(gateway, tmp_path)
    assert result["plan"]["nextStep"] == "cancelled"
    assert reads == ["one"] and len(model.requests) == 1
    snapshot = gateway.runtime_sessions.native_conversation("native-session", binding=_binding(tmp_path))
    assert not gateway.runtime_sessions._native_pending(snapshot)
    later = next(m for m in snapshot["messages"] if m.get("tool_call_id") == "later")
    assert json.loads(later["content"])["status"] == "not_executed"


def test_loader_cannot_authorize_a_previously_unadvertised_same_receipt_call(tmp_path):
    receipt = _receipt(call("load", "vrcforge_load_internal_tool_block", {}),
                       call("guess", "fixture_late_read", {}))
    gateway, model, _ = setup_gateway(tmp_path, [receipt, {"role": "assistant", "content": "Not completed"}])
    late = []
    def load(_args):
        gateway.register_tool("fixture_late_read", "When to use: read. When NOT to use: write.",
                              "plan/preview", lambda args: late.append(args) or {"ok": True})
        return {"ok": True}
    gateway.register_tool("vrcforge_load_internal_tool_block", "When to use: load. When NOT to use: write.", "plan/preview", load)
    run(gateway, tmp_path, maxAgenticTurns=2)
    assert not late
    result = next(m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "guess")
    assert "not advertised" in result["content"] or "not visible" in result["content"]


def test_real_question_pauses_queue_until_answer(tmp_path):
    import threading
    receipt = _receipt(call("question", "vrcforge_ask_user", {"question": "Continue?"}),
                       call("after-question", "vrcforge_read_text_file", {"path": "after.txt"}))
    gateway, model, reads = setup_gateway(tmp_path, [receipt, finish])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.",
                          "plan/preview", gateway.create_runtime_question)
    completed = threading.Event()
    gateway._runtime_turn_completed = lambda payload: completed.set()
    first = run(gateway, tmp_path, maxAgenticTurns=2)
    assert first["plan"]["nextStep"] == "needs_user_action"
    assert not reads and len(model.requests) == 1
    question = gateway.questions.list(session_id="native-session", project_root=str(tmp_path), include_answered=True)["questions"][0]
    try:
        gateway.questions.answer(question["questionId"], {"sessionId": "native-session", "projectRoot": str(tmp_path), "answer": "yes"})
        assert completed.wait(5)
    finally:
        assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert len(reads) == 1 and len(model.requests) == 2
    messages = [m for m in model.requests[-1]["messages"] if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in messages} == {"question", "after-question"}
    question_result = next(m for m in messages if m["tool_call_id"] == "question")
    assert "fixture-body" not in question_result["content"]


def test_scope_denial_terminates_and_settles_unexecuted_queue(tmp_path):
    from tests.test_native_runtime_continuations import _binding
    receipt = _receipt(call("deny", "vrcforge_read_text_file", {"path": "one"}),
                       call("later", "vrcforge_read_text_file", {"path": "two"}))
    gateway, model, reads = setup_gateway(tmp_path, [receipt])
    gateway._tools["vrcforge_read_text_file"].handler = lambda args: reads.append(args["path"]) or {
        "ok": False, "status": "needs_user_action", "error": "Scope denied"}
    result = run(gateway, tmp_path)
    assert result["plan"]["nextStep"] == "needs_user_action"
    assert reads == ["one"] and len(model.requests) == 1
    snapshot = gateway.runtime_sessions.native_conversation("native-session", binding=_binding(tmp_path))
    assert not gateway.runtime_sessions._native_pending(snapshot)


def test_steer_discards_remaining_proposals_before_replanning(tmp_path):
    receipt = _receipt(call("first", "vrcforge_read_text_file", {"path": "one"}),
                       call("second", "vrcforge_read_text_file", {"path": "two"}))
    gateway, model, reads = setup_gateway(tmp_path, [receipt, finish])
    def read(args):
        reads.append(args["path"])
        if args["path"] == "one":
            gateway.runtime_sessions.submit_steer(session_id="native-session", target_client_turn_id="native-turn",
                input_id="steer-test", message="Keep the answer short")
        return {"text": args["path"]}
    gateway._tools["vrcforge_read_text_file"].handler = read
    result = run(gateway, tmp_path, maxAgenticTurns=2)
    assert result["plan"]["nextStep"] == "done"
    assert reads == ["one"] and len(model.requests) == 2
    messages = model.requests[1]["messages"]
    assert any(m.get("role") == "user" and "Keep the answer short" in m.get("content", "") for m in messages)


@pytest.mark.parametrize("budget", [1, 2])
def test_steer_after_read_prevents_queued_write_and_respects_model_budget(tmp_path, budget):
    receipt = _receipt(call("first", "vrcforge_read_text_file", {"path": "one"}),
                       call("write", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "no"}))
    gateway, model, writes = _write_gateway_for_mode(tmp_path, [receipt, finish])
    _set_mode(gateway, "approval")
    reads = []
    def read(args):
        reads.append(args["path"])
        accepted = gateway.runtime_sessions.submit_steer(session_id="native-session", target_client_turn_id="native-turn",
            input_id="no-write", message="Do not modify anything")
        assert accepted["accepted"]
        return {"text": "read complete"}
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.", "plan/preview", read)
    result = run(gateway, tmp_path, maxAgenticTurns=budget)
    assert reads == ["one"] and not writes
    assert not gateway.approval_transactions.list_approvals()
    assert len(model.requests) == budget
    assert result["plan"]["nextStep"] == ("paused" if budget == 1 else "done")


def test_stop_question_settles_remaining_proposals(tmp_path):
    from agent_runtime_native_turn import NativeRuntimeTurn
    from tests.test_native_runtime_continuations import _binding
    receipt = _receipt(call("question", "vrcforge_ask_user", {"question": "Continue?"}),
                       call("later", "vrcforge_read_text_file", {"path": "two"}))
    gateway, model, reads = setup_gateway(tmp_path, [receipt])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.",
                          "plan/preview", gateway.create_runtime_question)
    run(gateway, tmp_path)
    snapshot = gateway.runtime_sessions.native_conversation("native-session", binding=_binding(tmp_path))
    cancelled = NativeRuntimeTurn.cancel_question(gateway.runtime_sessions, {"sessionId": "native-session", "_nativeConversation": snapshot})
    assert set(cancelled) == {"question", "later"}
    assert not reads and len(model.requests) == 1
    snapshot = gateway.runtime_sessions.native_conversation("native-session", binding=_binding(tmp_path))
    assert not gateway.runtime_sessions._native_pending(snapshot)
