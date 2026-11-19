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
        "operationKind": "write", "commitState": "pending", "readbackState": "pending", "executionId": "exec-1",
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


def test_durable_archive_receipt_retains_scope_and_readback_without_recovery_slice():
    call = {"role": "assistant", "tool_calls": [{"id": "call-write", "function": {
        "name": "call_tool", "arguments": json.dumps({"tool": "vrcforge_write", "arguments": {"assetPath": "Assets/Exact"}}),
    }}]}
    result_ref = "result_" + "e" * 32
    response = receipt("call-write", "action-1", tool="vrcforge_write", commitState="complete",
                       readbackState="verified", resultRef=result_ref)
    rows = native_handoff_operations({"handoffArchive": {"old:1": {"source": "tool", "text": json.dumps([call, response])}}})
    row, = rows
    assert row["scopeId"] and row["selectors"] == {"assetPath": "Assets/Exact"}
    assert row["readbackState"] == "verified" and row["resultRef"] == result_ref


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


def test_consumed_correction_anchors_exact_attempts_and_keeps_expired_result_actionable():
    from agent_handoff_graph import apply_handoff_edits, handoff_view
    from agent_tool_result_reader import PAGE_SCHEMA, TOOL_NAME

    ref = "result_" + "e" * 32
    failed_call = {"role": "assistant", "tool_calls": [{"id": "failed", "function": {
        "name": "call_tool", "arguments": json.dumps({
            "tool": "read_tool_result", "arguments": {"resultRef": ref, "jsonPointer": "/bad", "limit": 500},
        })}}]}
    failed = receipt("failed", "failure-action", tool="read_tool_result", status="failed",
                     errorCode="planner_invalid_response")
    corrected_call = {"role": "assistant", "tool_calls": [{"id": "corrected", "function": {
        "name": "vrcforge_runtime_action", "arguments": json.dumps({
            "action": "correct", "tool": "read_tool_result", "correction_for_action_id": "failure-action",
            "arguments": {"resultRef": ref, "jsonPointer": "/index", "limit": 20},
        })}}]}
    page = {"schema": PAGE_SCHEMA, "authority": "untrusted_tool_output", "ok": True,
            "status": "needs_rescan", "expired": True, "reason": "core_scope_unavailable", "resultRef": ref,
            "rescanScope": {"tool": "vrcforge_scan_materials", "assetPaths": [], "dependencyCoverage": "unverified"}}
    corrected = receipt("corrected", "correction-action", tool=TOOL_NAME)
    data = json.loads(corrected["content"])
    data["observations"][0].pop("handoffReceipt")
    data["observations"][0]["observation"] = "current tool data; retainedResultPage=" + json.dumps(page)
    corrected["content"] = json.dumps(data)
    snapshot = {"messages": [failed_call, failed, corrected_call, corrected, {"role": "assistant", "content": "Read it"}]}
    operations = native_handoff_operations(snapshot)
    assert [row["state"] for row in operations] == ["failed", "done"]
    anchors = source_nodes([], operations)
    assert len(anchors) == 1
    task_id, task = next(iter(anchors.items()))
    assert task["status"] == "pending" and not task.get("operationRef")
    assert task["correctionResult"]["rescanScope"] == page["rescanScope"]
    assert task["correctionResult"]["resultRef"] == ref
    edits = source_attempt_edits(anchors, operations)
    failure, success = edits["nodes"].values()
    assert (failure["attemptReceiptId"], failure["attemptActionId"]) == ("failed", "failure-action")
    assert (success["attemptReceiptId"], success["attemptActionId"]) == ("corrected", "correction-action")
    assert success["resultPage"]["status"] == "needs_rescan"
    assert success["reason"] == "The corrected call returned needs_rescan."
    assert not any(text in str(anchors) + str(edits) for text in ("/bad", "500"))
    assert task["requestedSelector"] == success["requestedSelector"] == {"resultRef": ref, "jsonPointer": "/index"}
    assert "jsonPointer" not in task["correctionResult"] and "requestedSelector" not in failure
    graph = apply_handoff_edits(None, {"nodes": anchors}, fold=False)
    graph = apply_handoff_edits(graph, edits)
    assert handoff_view(graph)["readyTaskIds"] == [task_id]
    assert "recoverTaskIds" not in handoff_view(graph)
    assert any(edge[1] == "replaced_by" for edge in graph["edges"])
    archived_operations = native_handoff_operations({"messages": [], "compactionRecovery": [archived(
        failed_call, failed, corrected_call, corrected,
    )]})
    assert list(source_nodes([], archived_operations, graph)) == [task_id]

    usable = {**page, "status": "complete", "expired": False, "reason": ""}
    corrected_ok = receipt("corrected", "correction-action", tool=TOOL_NAME, **native_handoff_receipt_fields(
        {"tool": TOOL_NAME, "result": usable}))
    rows = native_handoff_operations({**snapshot, "messages": [failed_call, failed, corrected_call,
                                                               corrected_ok, {"role": "assistant"}]})
    done = apply_handoff_edits(None, {"nodes": source_nodes([], rows)}, fold=False)
    done = apply_handoff_edits(done, source_attempt_edits(done["nodes"], rows))
    final_task = done["nodes"][task_id]
    assert final_task["status"] == "done"
    assert set((item["type"], item["id"]) for item in final_task["evidence"]) == {
        ("receipt", "failed"), ("receipt", "corrected"), ("scan", ref),
    }


def test_owned_package_receipt_recovers_only_its_exact_selector_after_compaction():
    from agent_tool_result_reader import bind_tool_result_context, read_tool_result
    from runtime_planner_service import sanitize_planner_observation_text

    def call(call_id, tool, **arguments):
        return {"id": call_id, "function": {"name": "call_tool", "arguments": json.dumps({
            "tool": tool, "arguments": arguments,
        })}}

    inspect = "unity_inspect_outfit_package"
    plan = "unity_plan_outfit_import"
    first = r"E:\packages\City Noir.unitypackage"
    second = r"E:\packages\Knit.unitypackage"
    prefab = "Assets/Outfits/City Noir Color18.prefab"
    calls = {"role": "assistant", "tool_calls": [
        call("inspect-a", inspect, packagePath=first, maxEntries=3),
        call("inspect-b", inspect, packagePath=second, maxEntries=9),
        call("plan-a", plan, packagePath=first, selectedPrefab=prefab, dependencyMode="selected_only",
             projectPath="must not expose", arbitrary="must not expose"),
        call("mismatch", "unity_import_outfit_package", packagePath=second, selectedPrefab=prefab),
        call("credential", inspect, packagePath="E:/packages/sk-proj-secretvalue/City.unitypackage"),
        call("failed", plan, packagePath=second),
    ]}
    responses = [
        receipt("inspect-b", "action-b", tool="vrcforge_inspect_outfit_package"),
        receipt("inspect-a", "action-a", tool="vrcforge_inspect_outfit_package"),
        receipt("plan-a", "action-p", tool="vrcforge_plan_outfit_import"),
        receipt("mismatch", "action-m", tool="vrcforge_plan_outfit_import"),
        receipt("credential", "action-c", tool="vrcforge_inspect_outfit_package"),
        receipt("failed", "action-f", tool="vrcforge_plan_outfit_import", status="failed"),
    ]
    plan_response = json.loads(responses[2]["content"])
    plan_response["observations"][0]["observation"] = "Long old receipt " * 2100
    responses[2]["content"] = json.dumps(plan_response)
    for snapshot in (
        {"messages": [], "compactionRecovery": [archived(calls, *responses)]},
        {"messages": [], "handoffArchive": {"group": {"source": "tool", "text": json.dumps([calls, *responses])}}},
    ):
        read = lambda identity: lookup_native_handoff_receipt(snapshot, identity)
        assert read("inspect-a")["sourceSelector"] == {"packagePath": first}
        assert read("action-b")["sourceSelector"] == {"packagePath": second}
        expected = {"packagePath": first, "selectedPrefab": prefab, "dependencyMode": "selected_only"}
        assert read("plan-a")["sourceSelector"] == expected
        assert all("sourceSelector" not in read(identity) for identity in ("mismatch", "credential", "failed"))
        with bind_tool_result_context("owner", "turn", "project", [], handoff_lookup=lambda _kind, identity: read(identity)):
            page = read_tool_result({"executionId": "plan-a"}, sanitize=sanitize_planner_observation_text)
        assert page["hasMore"] is True and page["text"].startswith('{"sourceSelector":')
        assert json.JSONDecoder().raw_decode(page["text"].split(":", 1)[1])[0] == expected


def test_exact_package_selection_survives_redacted_compaction_recovery_and_restart(tmp_path):
    from agent_native_history_store import NativeHistoryStore
    from agent_tool_result_reader import TOOL_NAME, bind_tool_result_context, read_tool_result
    from context_compaction import MODEL_HANDOFF_PREFIX, compact_context
    from runtime_planner_service import sanitize_planner_observation_text
    from tests.test_native_context_compaction import fixture
    from tests.test_native_handoff_blocks import _state

    store = NativeHistoryStore(tmp_path)
    state = _state(store)
    state.begin_native_turn("owner", binding="binding", turn_id="turn", message="Review the chosen outfit")
    exact = {
        "packagePath": r"E:\downloads\City Noir.unitypackage",
        "selectedPrefab": "Assets/Outfits/Color18.prefab",
        "dependencyMode": "selected_only",
    }
    assistant = {"role": "assistant", "tool_calls": [
        {"id": "plan", "type": "function", "function": {"name": "call_tool", "arguments": json.dumps({
            "tool": "unity_plan_outfit_import", "arguments": {**exact, "maxEntries": 900},
        })}},
    ]}
    plan = receipt("plan", "action-plan", tool="vrcforge_plan_outfit_import")
    state.append_native_assistant("owner", binding="binding", message=assistant)
    state.settle_native_call("owner", binding="binding", call_id="plan", content=plan["content"])
    state.append_native_assistant("owner", binding="binding", message={"role": "assistant", "content": "Previewed."})
    before = state.native_conversation("owner", binding="binding")
    _source, blocks = state.native_handoff_projection(before, character_budget=2000)
    recovery = compact_context([{"role": "tool", "text": NATIVE_TRANSACTION_PREFIX + json.dumps([assistant, plan])}],
                               summarizer=lambda _prompt: "Reviewed the chosen outfit.", input_mode="native")["recovery"]
    assert "{{project:" in recovery["sourceEntries"][0]["text"]
    assert "maxEntries" in recovery["sourceEntries"][0]["text"]
    state.replace_native_compacted_history(
        "owner", binding="binding", expected_snapshot=before, handoff_blocks=blocks,
        summary=MODEL_HANDOFF_PREFIX + "Reviewed the chosen outfit.", recovery=recovery,
    )
    reloaded = _state(store).native_conversation("owner", binding="binding")
    assert not any(row["role"] == "tool" for row in reloaded["messages"])
    with bind_tool_result_context("owner", "turn", "project", [],
                                  handoff_lookup=lambda _kind, identity: lookup_native_handoff_receipt(reloaded, identity)):
        page = read_tool_result({"executionId": "plan"}, sanitize=sanitize_planner_observation_text)
    _, planner, _, _ = fixture(prior=False)
    model_observation = planner.native_result_observation({"tool": TOOL_NAME, "status": "completed", "result": page})
    model_page = json.loads(model_observation["observation"].split("; retainedResultPage=", 1)[1])
    assert json.loads(model_page["text"])["sourceSelector"] == exact

def test_archive_cursor_links_plan_source_to_receipts_after_compaction():
    from outfit_package_inspector import INSPECTION_SCHEMA

    cursor = "archive_" + "a" * 32 + "_1"
    path = "D:/examples/MaterialPack.zip"
    first_tool, next_tool = "vrcforge_plan_outfit_import", "vrcforge_inspect_outfit_package"

    def call(identity, tool, arguments):
        return {"role": "assistant", "tool_calls": [{"id": identity, "function": {
            "name": "call_tool", "arguments": json.dumps({"tool": tool, "arguments": arguments}),
        }}]}

    partial = {"status": "partial", "processedCount": 111, "totalCount": 121, "remainingCount": 10,
               "nextRequest": {"tool": next_tool, "arguments": {"archiveCursor": cursor}}}
    first = native_handoff_receipt_fields({
        "tool": first_tool, "result": {"inspection": {"schema": INSPECTION_SCHEMA, "sourceRead": partial}},
    })
    final_coverage = {"status": "complete", "processedCount": 121, "totalCount": 121, "remainingCount": 0}
    final = native_handoff_receipt_fields({
        "tool": next_tool, "result": {"schema": INSPECTION_SCHEMA, "sourceRead": final_coverage},
    })
    old = archived(
        call("start", first_tool, {"packagePath": path, "maxEntries": 110}),
        receipt("start", "plan-invocation", tool=first_tool, **first),
    )
    new = [
        call("resume", next_tool, {"archiveCursor": cursor}),
        receipt("resume", "read-invocation", tool=next_tool, **final),
    ]

    progress, completion = native_handoff_operations({"compactionRecovery": [old], "messages": new})
    assert progress["scopeId"] == completion["scopeId"]
    assert progress["selectors"] == completion["selectors"] == {"packagePath": path}
    assert progress["sourceRead"] == partial
    assert completion["sourceRead"] == final_coverage
    assert cursor not in completion["selectors"].values()
