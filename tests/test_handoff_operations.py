import json

from agent_handoff_operations import (
    lookup_native_handoff_receipt,
    native_handoff_operations,
    native_handoff_receipt_fields,
)
from context_compaction import NATIVE_TRANSACTION_PREFIX
from agent_handoff_sources import source_attempt_edits, source_nodes


def receipt(call_id, action_id, *, tool="unity_write", status="ok", **fields):
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"observations": [{
        "actionId": action_id, "kind": "write", "runtimeTool": tool, "outcome": {"status": status},
        "handoffReceipt": fields,
    }]})}


def archived(*messages):
    return {"sourceEntries": [{"role": "tool", "text": NATIVE_TRANSACTION_PREFIX + json.dumps(messages)}]}


def test_dispatch_receipt_is_recoverable_after_compaction_with_exact_execution_id():
    original = {"role": "assistant", "tool_calls": [{"id": "call-write", "function": {
        "name": "call_tool", "arguments": json.dumps({"tool": "unity_write"})}}]}
    active = {"messages": [original], "activeDispatchCallId": "call-write"}
    assert native_handoff_operations(active)[0]["state"] == "executing"
    assert lookup_native_handoff_receipt(active, "call-write")["status"] == "executing"

    execution = receipt("call-write", "action-1", executionId="exec-1", commitState="pending", readbackState="pending")
    snapshot = {"messages": [{"role": "assistant", "content": "Earlier model handoff"}],
                "compactionRecovery": [archived(original, execution)]}
    assert native_handoff_operations(snapshot) == [{
        "kind": "execution", "id": "exec-1", "callId": "call-write", "tool": "unity_write",
        "state": "readback_pending", "actionId": "action-1", "outcomeStatus": "ok",
    }]
    assert lookup_native_handoff_receipt(snapshot, "exec-1")["handoffReceipt"]["readbackState"] == "pending"
    assert lookup_native_handoff_receipt(snapshot, "not-owned") is None
    assert lookup_native_handoff_receipt({"messages": [{"role": "user", "content": execution["content"]}]}, "exec-1") is None


def test_unconsumed_legacy_receipt_uses_exact_call_not_reused_action_id():
    messages = [
        receipt("call-1", "same-action", status="failed"),
        {"role": "assistant"},
        receipt("call-2", "same-action"),
    ]
    snapshot = {"messages": messages}
    assert [row["id"] for row in native_handoff_operations(snapshot) if row["state"] == "receipt_pending"] == ["call-2"]
    assert lookup_native_handoff_receipt(snapshot, "same-action") is None
    assert lookup_native_handoff_receipt(snapshot, "call-2")["outcome"]["status"] == "ok"


def test_execution_metadata_comes_from_owner_receipt():
    assert native_handoff_receipt_fields({
        "result": {"requestTrace": {"executionId": "exec-real"}, "result": {"previewId": "preview-real"}},
        "outcome": {"readbackState": "pending", "summary": "executionId=exec-untrusted"},
    }) == {"executionId": "exec-real", "previewId": "preview-real", "readbackState": "pending"}


def test_recorded_correction_binds_the_failed_call_and_exact_result_reference():
    result_ref = "result_" + "c" * 32
    first_call = {"role": "assistant", "tool_calls": [{"id": "first", "function": {
        "name": "call_tool", "arguments": json.dumps({"tool": "read_tool_result", "arguments": {"resultRef": result_ref}}),
    }}]}
    failure = receipt("first", "invalid-action", tool="read_tool_result", status="failed", errorCode="planner_invalid_response")
    correction_call = {"role": "assistant", "tool_calls": [{"id": "corrected", "function": {
        "name": "runtime_action",
        "arguments": json.dumps({"action": "correct", "correction_for_action_id": "invalid-action",
                                 "tool": "read_tool_result", "arguments": {"resultRef": result_ref}}),
    }}]}
    correction = receipt("corrected", "corrected-action", tool="read_tool_result", resultStatus="needs_rescan")
    operations = native_handoff_operations({"messages": [first_call, failure]})
    edits = source_attempt_edits(source_nodes([], operations), operations)
    assert len(edits["nodes"]) == 1
    assert next(iter(edits["nodes"].values()))["status"] == "failure"
    operations = native_handoff_operations({"messages": [first_call, failure, correction_call, correction]})
    edits = source_attempt_edits(source_nodes([], operations), operations)
    assert [attempt["status"] for attempt in edits["nodes"].values()] == ["failure", "success"]
    assert edits["edges"][-1][1] == "replaced_by"
    assert edits["edges"][-1][3].endswith("planner_invalid_response")
    assert all({"type": "scan", "id": result_ref} in attempt["evidence"] for attempt in edits["nodes"].values())

    from agent_tool_result_reader import PAGE_SCHEMA, TOOL_NAME
    replacement = "result_" + "d" * 32
    fields = native_handoff_receipt_fields({"tool": TOOL_NAME, "result": {
        "schema": PAGE_SCHEMA, "resultRef": result_ref, "replacementRef": replacement, "status": "replaced"}})
    fresh = receipt("corrected", "corrected-action", tool=TOOL_NAME, **fields)
    operations = native_handoff_operations({"messages": [first_call, failure, correction_call, fresh]})
    edits = source_attempt_edits(source_nodes([], operations), operations)
    corrected = next(node for node in edits["nodes"].values() if node["status"] == "success")
    assert {"type": "scan", "id": replacement} in corrected["evidence"]
    assert {"type": "scan", "id": result_ref} not in corrected["evidence"]


def test_readback_settles_only_the_same_job_with_commit_and_verification():
    pending = receipt("pending", "action", executionId="exec-1", readbackState="pending", jobId="job-1")
    other = receipt("other", "other-action", tool="status", commitState="complete", readbackState="verified", jobId="job-2")
    incomplete = receipt("incomplete", "other-action", tool="status", commitState="complete", jobId="job-1")
    messages = [pending, {"role": "assistant"}, other, incomplete]
    assert native_handoff_operations({"messages": messages})[0]["state"] == "readback_pending"
    verified = receipt("verified", "verified-action", tool="status", commitState="complete", readbackState="verified", jobId="job-1")
    restored = native_handoff_operations({"messages": [*messages, verified]})[0]
    assert restored["state"] == "done" and restored["settlementCallId"] == "verified"
