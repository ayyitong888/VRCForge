"""Focused rules not already covered by the Gateway alternating-call replay."""
import pytest

from agent_general_no_progress import ToolLoopProgress, tool_result_digest
from agent_task_loop import canonical_action_id


def record(progress, tool, args, result, effect="read"):
    return progress.record(
        action_id=canonical_action_id(effect, tool, args), tool=tool,
        arguments=args, result_digest=tool_result_digest(result), status="ok", summary="", effect=effect,
    )


def test_signature_canonicalizes_nested_arguments_and_results():
    progress = ToolLoopProgress()
    args = {"path": "a.txt", "selection": {"start": 1, "end": 2}}
    reordered = {"selection": {"end": 2, "start": 1}, "path": "a.txt"}
    assert not record(progress, "read", args, {"a": 1, "b": 2})
    assert not record(progress, "read", reordered, {"b": 2, "a": 1})
    assert record(progress, "read", args, {"a": 1, "b": 2}) == "tool_loop_repeated"
    assert not record(progress, "read", {**args, "path": "b.txt"}, {"a": 1, "b": 2})


def test_ten_unchanged_results_with_distinct_arguments_are_no_progress():
    progress = ToolLoopProgress()
    for index in range(10):
        assert not record(progress, "read", {"path": str(index)}, "same")
    assert record(progress, "read", {"path": "10"}, "same") == "tool_loop_no_progress"
    assert not record(progress, "read", {"path": "11"}, "new evidence")


def test_repeats_outside_twenty_call_window_do_not_count():
    progress = ToolLoopProgress()
    for _ in range(2):
        assert not record(progress, "read", {"path": "a"}, "old")
    for index in range(19):
        assert not record(progress, "read", {"path": str(index)}, index)
    assert not record(progress, "read", {"path": "a"}, "old")


def test_different_writes_need_matching_readback_to_infer_no_effect():
    progress = ToolLoopProgress()
    for value in (1, 2):
        assert not record(progress, "write", {"path": "a", "value": value}, "accepted", "write")
        assert not record(progress, "read", {"path": "b"}, 0)
        assert not record(progress, "read", {"path": "a"}, value)
    assert not record(progress, "write", {"path": "a", "value": 3}, "accepted", "write")
    assert record(progress, "read", {"path": "a"}, 2) == "tool_loop_write_unverified"


def test_readback_for_one_material_does_not_verify_a_different_material():
    progress = ToolLoopProgress()
    assert not record(progress, "write", {"materialId": "face", "value": 1}, "accepted", "write")
    assert not record(progress, "read", {"material_id": "face"}, 0)
    assert not record(progress, "write", {"materialId": "body", "value": 2}, "accepted", "write")
    assert not record(progress, "read", {"material_id": "face"}, 0)
    assert not record(progress, "write", {"materialId": "face", "value": 3}, "accepted", "write")
    assert record(progress, "read", {"material_id": "face"}, 0) == "tool_loop_write_unverified"


def test_marked_poll_uses_wait_interval_without_loop_count(tmp_path, monkeypatch):
    from tests.test_native_runtime_gateway import call, finish, run, setup_gateway

    gateway, model, invoked = setup_gateway(tmp_path, [
        *(call(f"poll-{index}", "vrcforge_read_text_file", {"path": "job.txt"}) for index in range(8)),
        finish,
    ])
    gateway._tools["vrcforge_read_text_file"].polling = True
    waits = []
    monkeypatch.setattr(gateway, "_wait_for_provider_retry", lambda seconds, **_: waits.append(seconds) or True)
    result = run(gateway, tmp_path, maxAgenticTurns=10)
    assert result["plan"]["nextStep"] == "done"
    assert len(model.requests) == 9 and len(invoked) == 8
    assert len(waits) == 7 and all(0 < delay <= 1 for delay in waits)
    assert "loopPause" not in result["plan"]


def test_gateway_uses_readback_to_distinguish_changed_and_unchanged_writes(tmp_path):
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
    from tests.test_native_runtime_gateway import call, finish, run

    outcomes = []
    for changed in (False, True):
        root = tmp_path / str(changed)
        project = root / "UnityProject"
        target = "Assets/fixture.txt"
        proposals = []
        for value in range(1, 4):
            proposals.extend([
                call(f"write-{value}", "fixture_write", {"projectRoot": str(project), "path": target, "value": value}),
                call(f"read-{value}", "vrcforge_read_text_file", {"path": target}),
            ])
        gateway, model, writes = _write_gateway_for_mode(root, [*proposals, finish])
        _set_mode(gateway, "auto")
        gateway.approval_transactions.auto_approval_reviewer = lambda _: "allow_auto"
        gateway.register_tool(
            "vrcforge_read_text_file", "When to use: read the fixture. When NOT to use: write.",
            "read/debug", lambda _: {"value": writes[-1]["value"] if changed else 0},
        )
        result = run(gateway, project, maxAgenticTurns=8, message="Update the fixture and verify each change.")
        assert [entry["value"] for entry in writes] == [1, 2, 3], result["plan"]
        outcomes.append((result["plan"]["nextStep"], result["plan"].get("reason")))
        assert len(model.requests) == (7 if changed else 6)
    assert outcomes == [("paused", "tool_loop_write_unverified"), ("done", None)]


def test_marked_poll_stops_at_wait_deadline_without_reexecuting(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    import agent_gateway
    from tests.test_native_runtime_gateway import call, run, setup_gateway

    gateway, model, invoked = setup_gateway(tmp_path, [
        call("poll-1", "vrcforge_read_text_file", {"path": "job.txt"}),
        call("poll-2", "vrcforge_read_text_file", {"path": "job.txt"}),
    ])
    clock = [0.0]
    monkeypatch.setattr(agent_gateway, "time", SimpleNamespace(
        monotonic=lambda: clock[0], perf_counter=time.perf_counter, sleep=time.sleep,
    ))
    tool = gateway._tools["vrcforge_read_text_file"]
    original_handler = tool.handler
    tool.polling = True

    def poll(args):
        clock[0] += 181.0
        return original_handler(args)

    tool.handler = poll
    result = run(gateway, tmp_path, maxAgenticTurns=8)
    assert result["plan"]["nextStep"] == "waiting_for_tool"
    assert result["plan"]["reason"] == "tool_poll_timeout"
    assert len(invoked) == 1 and len(model.requests) == 2


def test_native_batch_delivers_reminder_after_pairing_results_and_settles_pause(tmp_path):
    import json
    from tests.test_native_runtime_gateway import call, run, setup_gateway

    def batch(index):
        first = call(f"a-{index}", "vrcforge_read_text_file", {"path": "a.txt"})
        second = call(f"b-{index}", "vrcforge_read_text_file", {"path": "b.txt"})
        first["tool_calls"].extend(second["tool_calls"])
        return first

    gateway, model, invoked = setup_gateway(tmp_path, [batch(i) for i in range(4)])
    result = run(gateway, tmp_path, maxAgenticTurns=6)
    assert result["plan"]["nextStep"] == "paused"
    assert len(invoked) == 7
    messages = next(iter(gateway.runtime_sessions._native_conversations.values()))["messages"]
    calls = {call["id"] for message in messages for call in message.get("tool_calls", [])}
    outcomes = {message["tool_call_id"]: json.loads(message["content"])
                for message in messages if message["role"] == "tool"}
    assert calls == outcomes.keys()
    assert outcomes["b-3"]["status"] == "not_executed"
    assert any(message.get("hidden") for message in model.requests[3]["messages"])


def test_identical_completed_write_reuses_its_result_digest(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import agent_general_no_progress
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
    from tests.test_native_runtime_gateway import call, run

    project = tmp_path / "UnityProject"
    arguments = {"projectRoot": str(project), "value": 1}
    receipt = {"ok": True, "status": "written", "data": "receipt data " * 80_000}
    serialized_receipts = []

    def count_receipt(value, **options):
        encoded = json.dumps(value, **options)
        if value == receipt:
            serialized_receipts.append(len(encoded))
        return encoded

    monkeypatch.setattr(agent_general_no_progress, "json", SimpleNamespace(dumps=count_receipt))
    gateway, _, writes = _write_gateway_for_mode(tmp_path, [
        call(f"write-{index}", "fixture_write", arguments) for index in range(4)
    ], handler_result=receipt)
    _set_mode(gateway, "auto")
    gateway.approval_transactions.auto_approval_reviewer = lambda _: "allow_auto"
    result = run(gateway, project, maxAgenticTurns=6, message="Update the fixture once.")
    assert result["plan"]["nextStep"] == "paused"
    assert result["plan"]["reason"] == "tool_loop_repeated"
    assert len(writes) == 1
    assert len(serialized_receipts) == 1


@pytest.mark.parametrize("native", [True, False], ids=["native", "legacy"])
def test_gateway_connectivity_compares_business_facts_without_discarding_elapsed_time(tmp_path, monkeypatch, native):
    from types import SimpleNamespace

    import httpx
    import unity_host_diagnostics as diagnostics
    from tests.test_native_runtime_gateway import call, finish, run, setup_gateway

    client = httpx.Client
    for root, statuses in (("unchanged", [200] * 5), ("changing", [200, 201, 202, 203, 204])):
        sent_statuses = []
        times = iter(value for i in range(5) for value in (i, i + (100 + i) / 1000))
        monkeypatch.setattr(diagnostics, "time", SimpleNamespace(monotonic=lambda: next(times)))

        def respond(_request):
            status = statuses[len(sent_statuses)]
            sent_statuses.append(status)
            return httpx.Response(status)

        monkeypatch.setattr(diagnostics.httpx, "Client", lambda **kwargs: client(
            transport=httpx.MockTransport(respond), **kwargs,
        ))
        project = tmp_path / root
        gateway, _, _ = setup_gateway(project, [
            *(call(f"diagnose-{i}", diagnostics.NETWORK_TOOL, {}) for i in range(5)), finish,
        ])
        if not native:
            plans = iter([{
                "planner": "llm", "summary": "Check connectivity.", "skillNeeded": True,
                "skillTool": diagnostics.NETWORK_TOOL, "skillParams": {},
                "continueLoop": True, "nextStep": "call_skill",
            }] * 5 + [{"planner": "llm", "summary": "Connectivity checked.", "reply": "The API responded.", "nextStep": "done"}])
            monkeypatch.setattr(gateway.runtime_planner, "native_conversation_binding", lambda _root: "")
            monkeypatch.setattr(gateway.runtime_planner, "plan_agent_turn", lambda *_args, **_kwargs: next(plans).copy())
        diagnostics.register_host_diagnostic_tools(gateway)
        result = run(gateway, project, maxAgenticTurns=7, message="Check VRChat API connectivity.")
        receipts = [step["result"] for step in result["steps"] if step.get("tool") == diagnostics.NETWORK_TOOL]
        assert [receipt["httpStatus"] for receipt in receipts] == sent_statuses
        assert [receipt["elapsedMs"] for receipt in receipts] == list(range(100, 100 + len(receipts)))
        if root == "unchanged":
            assert result["plan"]["nextStep"] == "paused"
            assert result["plan"]["reason"] == "tool_loop_repeated"
            assert sent_statuses == [200] * 4
        else:
            assert result["plan"]["nextStep"] == "done"
            assert sent_statuses == statuses


def test_progress_digest_keeps_undeclared_and_nested_business_fields():
    receipt = {"elapsedMs": 100, "data": {"elapsedMs": 20}}
    diagnostic = ("elapsedMs",)
    timing_changed = {**receipt, "elapsedMs": 101}
    nested_changed = {**timing_changed, "data": {"elapsedMs": 21}}

    assert tool_result_digest(receipt) != tool_result_digest(timing_changed)
    assert tool_result_digest(receipt, diagnostic_fields=diagnostic) == tool_result_digest(timing_changed, diagnostic_fields=diagnostic)
    assert tool_result_digest(receipt, diagnostic_fields=diagnostic) != tool_result_digest(nested_changed, diagnostic_fields=diagnostic)
    assert receipt == {"elapsedMs": 100, "data": {"elapsedMs": 20}}
