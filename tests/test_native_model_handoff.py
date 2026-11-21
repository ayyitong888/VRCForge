import json
from pathlib import PureWindowsPath

import pytest

from agent_handoff_graph import HANDOFF_GRAPH_SCHEMA, parse_handoff_graph
from context_compaction import LEGACY_HANDOFF_PREFIX, MODEL_HANDOFF_PREFIX, NATIVE_TRANSACTION_PREFIX, compact_context
from tests.test_native_context_compaction import fixture


def _prior_graph(prompt):
    return json.loads(prompt.split("\nPRIOR_HANDOFF=", 1)[1].split("\nRETAINED_STATUS=", 1)[0])


def _node(kind, text, status=None, **fields):
    return {"kind": kind, "text": text, **({"status": status} if status else {}), **fields}


def _with_source(prompt, payload):
    source = json.loads(prompt.split("\nREDACTED_ENTRIES=", 1)[1])[0]["sourceRef"]
    for node in payload["nodes"].values():
        node.setdefault("evidence", [source])
    payload["edges"] = [edge + [{"reason": "Prerequisite recorded in this fixture", "evidence": [source]}]
                        if len(edge) == 3 and edge[1] in {"depends_on", "blocked_by"} else edge
                        for edge in payload.get("edges", ())]
    return payload


class ModelHandoff:
    def __init__(self):
        self.prompts = []

    def compact(self, entries, metadata):
        def summarize(prompt):
            self.prompts.append(prompt)
            if isinstance(_prior_graph(prompt), dict):
                return {}
            return _with_source(prompt, {"nodes": {
                "goal": _node("goal", "Retain the inspection objective."),
                "readback": _node("issue", "Readback is required", "open", facts="Evidence was partial"),
                "inspect": _node("task", "Review the next authorized evidence.", "pending"),
            }, "edges": [["inspect", "blocked_by", "readback"], ["inspect", "belongs_to", "goal"]]})
        return compact_context(entries, handoff=metadata["handoff"], has_todo=metadata["hasTodo"],
                               real_context_limit=metadata["realContextLimit"],
                               recovery_history=metadata.get("_recoveryHistory"),
                               handoff_archive_indices=metadata.get("_handoffArchiveIndices"),
                               prior_handoff=metadata.get("_priorHandoff"),
                               retained_evidence=metadata.get("_retainedEvidence") or (),
                               task_inputs=metadata.get("_handoffTaskInputs") or (), summarizer=summarize)


def _pair(state, index, *, wait=False):
    ids = [f"group-{index}-a", f"group-{index}-b"]
    calls = [{"id": call_id, "type": "function", "function": {"name": "read", "arguments": "{}"}} for call_id in ids]
    if index == 0:
        calls.append({"id": "old-todo", "type": "function", "function": {"name": "call_tool", "arguments": json.dumps({
            "tool": "progress_list", "arguments": {}})}})
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": None, "tool_calls": calls})
    for call_id in ids[:1] if wait else ids:
        state.settle_native_call("s", binding="b", call_id=call_id, content=json.dumps({"status": "complete", "raw": call_id}))
    if index == 0:
        state.settle_native_call("s", binding="b", call_id="old-todo", content='{"activeTodo":["OLD_TODO_CONTENT"]}')
    if wait:
        state.mark_native_dispatch("s", binding="b", call_id=ids[-1])
    return ids[-1]


def _request(planner, snapshot, observe=None):
    return planner._build_native_plan_request(snapshot["messages"], observe=observe or {}, exposure_layer="execution",
        project_context_active=False, project_path="", internal_tool_blocks=(),
        global_instructions="ORIGINAL_AGENT_RULES", project_instructions="EXACT_PROJECT_AGENTS")[0]


def test_manual_native_handoff_orders_user_graph_and_exact_pending_approval_after_restart():
    compactor = ModelHandoff()
    state, planner, model, turn = fixture(compactor, prior=False, current="ORIGINAL_USER_TASK", limit=32_000)
    for index in range(8):
        _pair(state, index)
        if index == 0:
            state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": None, "tool_calls": [
                {"id": "old-progress-only", "type": "function", "function": {"name": "progress_update", "arguments": "{}"}}]})
            state.settle_native_call("s", binding="b", call_id="old-progress-only", content='{"content":"OLD_PROGRESS_ONLY_LIST"}')
    approval_call_id = _pair(state, 8, wait=True)
    before = turn.snapshot()
    pending_start = next(i for i, item in enumerate(before["messages"]) if any(call["id"] == approval_call_id for call in item.get("tool_calls", [])))
    with planner.bind_turn({}):
        request = _request(planner, before, {"activeTodo": [{"id": "task1", "content": "TODO_EXACT_CONTENT", "status": "in_progress"}]})
        candidate, _ = planner.maybe_compact_native_context(request, turn, force=True, preserve_pending=True, has_todo=True)
    assert turn.compaction["applied"] is True
    assert not model.requests
    after = turn.snapshot()
    graph_index = next(i for i, row in enumerate(after["messages"]) if str(row.get("content")).startswith(MODEL_HANDOFF_PREFIX))
    assert 0 < after["activeTurnStart"] < graph_index
    graph = parse_handoff_graph(after["messages"][graph_index]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
    assert graph["nodes"]["readback"]["status"] == "open"
    assert after["messages"][after["activeTurnStart"]]["content"] == before["messages"][before["activeTurnStart"]]["content"]
    assert after["messages"][graph_index + 1:] == before["messages"][pending_start:]
    assert "TODO_EXACT_CONTENT" in json.dumps(after["messages"])
    assert "EXACT_PROJECT_AGENTS" in candidate["instructions"]
    rewritten_prompt, = compactor.prompts
    for pinned in ("TODO_EXACT_CONTENT", "OLD_TODO_CONTENT", "OLD_PROGRESS_ONLY_LIST", "EXACT_PROJECT_AGENTS"):
        assert pinned not in rewritten_prompt
    assert "ORIGINAL_USER_TASK" in rewritten_prompt.split("\nTASK_INPUTS=", 1)[1].split("\nPRIOR_HANDOFF=", 1)[0]
    assert "group-4-a" in rewritten_prompt
    assert "group-0-a" in rewritten_prompt and "group-0-b" in rewritten_prompt
    assert any("OLD_TODO_CONTENT" in entry["text"] for entry in after["compactionRecovery"][-1]["sourceEntries"])
    records = json.loads(rewritten_prompt.split("REDACTED_ENTRIES=", 1)[1])
    assert "group-5-a" in json.dumps(records) and approval_call_id not in json.dumps(records)
    next_record = next(record for record in records if "group-1-a" in json.dumps(record.get("transaction")))
    archive = after["compactionRecovery"][-1]
    assert archive["sourceDigest"] in rewritten_prompt
    assert any("OLD_PROGRESS_ONLY_LIST" in row["text"] for row in archive["sourceEntries"])
    assert "group-1-a" in archive["sourceEntries"][next_record["recordIndex"]]["text"]
    assert after["activeDispatchCallId"] == approval_call_id
    assert state._native_pending(after) == {approval_call_id}
    restored = state.restore_native_conversation("s", binding="b", snapshot=before)
    assert restored == after
    state.settle_native_call("s", binding="b", call_id=approval_call_id, content='{"status":"approved"}')
    assert not state._native_pending(turn.snapshot())


def test_long_active_turn_retains_all_user_texts_and_last_public_reply():
    compactor = ModelHandoff()
    state, planner, _, turn = fixture(compactor, prior=False, current="ORIGINAL_USER_TASK", limit=32_000)
    _pair(state, 90)
    for ordinal in (1, 2):
        state.begin_native_turn("s", binding="b", turn_id=f"previous-{ordinal}", message=f"PREVIOUS_EXACT_INPUT_{ordinal}")
        _pair(state, 90 + ordinal)
        state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": f"PREVIOUS_EXACT_REPLY_{ordinal}"})
    state.begin_native_turn("s", binding="b", turn_id="current", message="CURRENT_LONG_TURN")
    for index in range(8):
        _pair(state, index)
    before = turn.snapshot()
    with planner.bind_turn({}):
        planner.maybe_compact_native_context(_request(planner, before), turn, force=True)
    assert turn.compaction["applied"] is True
    after = turn.snapshot()
    assert [row["content"] for row in after["messages"] if row.get("handoffUser")] == [
        "ORIGINAL_USER_TASK", "PREVIOUS_EXACT_INPUT_1", "PREVIOUS_EXACT_INPUT_2", "CURRENT_LONG_TURN"]
    assert [row["content"] for row in after["messages"] if str(row.get("content")).startswith("PREVIOUS_EXACT_REPLY")] == ["PREVIOUS_EXACT_REPLY_2"]
    assert "group-91-a" in compactor.prompts[0].split("REDACTED_ENTRIES=", 1)[1]
    assert "group-90-a" in compactor.prompts[0] and "group-4-a" in compactor.prompts[0]


def test_verified_handoff_larger_than_the_old_graph_limit_is_kept_in_one_call():
    from context_compaction import estimate_compaction_prompt_tokens

    attempts = []
    evidence = "evidence " * 6000

    def summarize(prompt):
        attempts.append(prompt)
        return _with_source(prompt, {"nodes": {
            "goal": _node("goal", "Intent"),
            "readback": _node("task", evidence, "pending"),
        }})
    result = compact_context([{"role": "tool", "text": "OLD_TOOL_ROW"}], handoff=True, real_context_limit=240_000, summarizer=summarize)
    assert result["ok"] is True and len(attempts) == 1
    assert "targetTokens" not in result
    assert estimate_compaction_prompt_tokens(result["summary"]) > 12_000
    assert "OLD_TOOL_ROW" in attempts[0]
    assert parse_handoff_graph(result["summary"])["nodes"]["readback"]["text"] == evidence
    assert result["recovery"]["summary"] == result["summary"]


def test_long_handoff_merges_ordered_pages_and_keeps_earliest_decision_after_provider_size_retry():
    source = [{"role": "agent", "text": "EARLIEST_HANDOFF ordered batch: inspect, apply, validate."}]
    source.extend({"role": "tool", "text": f"BEGIN_COMPLETE_GROUP_{i} " + "result " * 700 + f" END_COMPLETE_GROUP_{i};"}
                  for i in range(12))
    seen_groups = []
    attempts = 0
    def summarize(prompt):
        nonlocal attempts
        attempts += 1
        rows = json.loads(prompt.split("REDACTED_ENTRIES=", 1)[1])
        text = "\n".join(row["text"] for row in rows)
        for i in range(12):
            assert (f"BEGIN_COMPLETE_GROUP_{i} " in text) == (f"END_COMPLETE_GROUP_{i};" in text)
        if attempts == 1:
            assert "EARLIEST_HANDOFF" in text
            raise RuntimeError("HTTP 413 payload too large")
        prior = _prior_graph(prompt)
        if prior is not None:
            assert prior["nodes"]["order"]["text"] == "inspect, apply, validate"
        seen_groups.extend(i for i in range(12) if f"BEGIN_COMPLETE_GROUP_{i} " in text)
        return {} if prior else _with_source(prompt, {"nodes": {
            "goal": _node("goal", "Follow the agreed order."),
            "order": _node("decision", "inspect, apply, validate", scope="graph",
                           order={"before": ["inspect"], "after": ["apply", "validate"],
                                  "reason": "The original batch calls for inspection first."}),
            "inspect": _node("task", "Inspect", "pending"),
            "apply": _node("task", "Apply", "pending"),
            "validate": _node("task", "Validate", "pending"),
        }, "edges": [["validate", "depends_on", "apply"]]})

    result = compact_context(source, handoff=True, real_context_limit=9_000, target_tokens=1200, summarizer=summarize)
    assert result["ok"] is True
    assert result["fidelity"] == "paged" and result["coverage"]["omittedEntries"] == 0
    assert result["coverage"]["complete"] is True
    assert seen_groups == list(range(12))
    from agent_runtime_session_state import model_handoff_continuation
    projected = model_handoff_continuation({"role": "assistant", "content": MODEL_HANDOFF_PREFIX + result["summary"]})
    assert json.loads(projected.removeprefix(MODEL_HANDOFF_PREFIX))["readyTaskIds"] == ["inspect"]


def test_failed_later_page_cannot_install_partial_handoff():
    source = [{"role": "agent", "text": "Example " * 1300 + str(i)} for i in range(5)]
    calls = 0
    def summarize(_prompt):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("HTTP 401 unauthorized")
        return _with_source(_prompt, {"nodes": {"goal": _node("goal", "Continue the work."),
                                               "validate": _node("task", "Validate.", "pending")}})
    result = compact_context(source, handoff=True, real_context_limit=8_000, target_tokens=1200, summarizer=summarize)
    assert calls == 2 and result["coverage"]["completedPages"] == 1
    assert result["ok"] is False and not result["summary"]
    assert len(result["recovery"]["sourceEntries"]) == 5


def test_retained_success_is_evidence_for_handoff_reconciliation_without_quoting_tail():
    from runtime_planner_service import native_handoff_retained_evidence
    private_path = str(PureWindowsPath("C:/", "Users", "Private Name", "Material.zip"))
    tail = [
        {"role": "assistant", "tool_calls": [{"id": "recent-callback", "function": {"name": "call_tool", "arguments": json.dumps({
            "tool": "unity_import_outfit_package", "arguments": {"packagePath": private_path,
            "selectedPrefab": "Assets/Material/Prefab.prefab", "dependencyMode": "selected_only"}})}}]},
        {"role": "tool", "tool_call_id": "recent-callback", "content": json.dumps({"observations": [{"runtimeTool": "vrcforge_import_outfit_package",
          "actionId": "later-exact-action", "outcome": {"status": "ok", "readbackState": "verified"},
          "observation": "VERBATIM_TAIL_BODY"}]})},
    ]
    evidence = native_handoff_retained_evidence(tail)
    prompts = []
    result = compact_context([{"role": "tool", "text": "Material.zip returned an earlier approval failure before the outfit import."}],
        handoff=True, retained_evidence=evidence, summarizer=lambda prompt: prompts.append(prompt) or _with_source(prompt, {
            "nodes": {
                "goal": _node("goal", "Follow ordered imports."),
                "first-import": _node("attempt", "Import Material.zip", "failure", reason="Approval was denied"),
                "import": _node("task", "Import Material.zip", "done",
                                 evidence=[{"type": "receipt", "id": "later-exact-action"}]),
                "outfit": _node("task", "Continue outfit import", "pending"),
            }, "edges": [["first-import", "attempt_of", "import"], ["outfit", "depends_on", "import"]],
        }))
    assert result["ok"] and "Continue outfit import" in result["summary"]
    projection = prompts[0].split("RETAINED_STATUS=", 1)[1].split("\nREDACTED_ENTRIES=", 1)[0]
    facts, = json.loads(projection)
    assert facts["callId"] == "recent-callback"
    assert facts["selectors"] == {"packagePath": "Material.zip", "selectedPrefab": "Assets/Material/Prefab.prefab",
                                  "dependencyMode": "selected_only"}
    assert facts["outcomes"] == [{"tool": "vrcforge_import_outfit_package", "actionId": "later-exact-action", "status": "ok",
                                  "readbackState": "verified"}]
    assert "Private Name" not in prompts[0] and "VERBATIM_TAIL_BODY" not in prompts[0]


def test_repeated_compaction_resolves_linked_work_and_keeps_failure_history():
    order_source = {"type": "message", "id": "recorded-order"}
    recovery_source = {"type": "receipt", "id": "older-call"}
    shader_source = {"type": "receipt", "id": "shader-read"}
    prior = {"schema": HANDOFF_GRAPH_SCHEMA, "nodes": {
                 "goal": _node("goal", "Finish imports and validate the avatar."),
                 "package-a": _node("task", "Import package A", "pending"),
                 "package-b": _node("task", "Import package B", "pending"),
                 "check": _node("task", "Check the shader", "pending"),
                 "recovery": _node("issue", "Package A recovery pending", "open", facts="Recovery paused writes"),
                 "shader": _node("issue", "Shader cannot be accessed", "open", facts="Readback did not return a shader"),
                 "retry": _node("attempt", "Original import attempt", "failure", reason="Unity reloaded",
                                evidence=[{"type": "receipt", "id": "older-call"}]),
                 "order": _node("decision", "Import A before B", scope="graph", evidence=[order_source],
                                order={"before": ["package-a"], "after": ["package-b"], "reason": "Original import order."}),
             },
             "edges": [["package-b", "depends_on", "package-a", {"decisionId": "order"}],
                       ["package-a", "blocked_by", "recovery", {"reason": "Recovery paused writes", "evidence": [recovery_source]}],
                       ["check", "blocked_by", "shader", {"reason": "Shader lookup failed", "evidence": [shader_source]}],
                       ["retry", "attempt_of", "package-a"]]}
    rows = [{"role": "tool", "text": "receipt-2: Package A import confirmed."}]

    def summarize(prompt):
        assert _prior_graph(prompt) == prior
        return _with_source(prompt, {"nodes": {
            "package-a": {"status": "done"},
            "recovery": {"status": "closed"},
        }})

    result = compact_context(rows, handoff=True, prior_handoff=json.dumps(prior), summarizer=summarize)
    graph = parse_handoff_graph(result["summary"])
    for node_id in ("goal", "package-b", "check", "shader", "order"):
        assert graph["nodes"][node_id] == prior["nodes"][node_id]
    assert graph["nodes"]["package-a"]["status"] == "done"
    assert graph["nodes"]["retry"] == prior["nodes"]["retry"]
    assert graph["nodes"]["recovery"]["status"] == "closed"
    assert {"type": "receipt", "id": "older-call"} in graph["nodes"]["retry"]["evidence"]
    assert graph["edges"] == prior["edges"]
    assert result["recovery"]["sourceEntries"] == rows

    from agent_runtime_session_state import native_model_history
    message = {"role": "assistant", "content": MODEL_HANDOFF_PREFIX + result["summary"]}
    projected, = native_model_history([message])
    assert json.loads(projected["content"].removeprefix(MODEL_HANDOFF_PREFIX))["readyTaskIds"] == ["package-b"]
    assert parse_handoff_graph(message["content"].removeprefix(MODEL_HANDOFF_PREFIX)) == graph


def test_older_phase_graph_is_supplied_as_linked_prerequisites():
    prior = {"schema": "vrcforge.agent_handoff_graph.v1", "goal": "Verify the document",
             "completed": [{"id": "find", "action": "Find document", "result": "Located", "evidence": "call-1"}],
             "attempted": [{"id": "early-read", "action": "Read document", "outcome": "failure", "reason": "Network stalled"}],
             "decisions": [], "blocked": [], "nextSteps": [
                 {"id": "read", "action": "Read again", "phase": 1, "dependsOn": ["find"]},
                 {"id": "verify", "action": "Verify document", "phase": 2, "dependsOn": []},
             ]}
    prompts = []
    source = [{"role": "assistant", "text": MODEL_HANDOFF_PREFIX + json.dumps(prior)},
              {"role": "tool", "text": "A partial document required a new read before verification."}]

    def summarize(prompt):
        prompts.append(prompt)
        old = _prior_graph(prompt)
        assert ["read", "depends_on", "find"] in old["edges"]
        assert ["verify", "depends_on", "read"] in old["edges"]
        pointer = json.loads(prompt.split("\nPRIOR_HANDOFF_SOURCE=", 1)[1].split("\n", 1)[0])
        return {"nodes": {
            "read-first": _node("decision", "Locate the document before reading it", evidence=[pointer],
                                order={"before": ["find"], "after": ["read"], "reason": "Recorded read prerequisite"}),
            "verify-last": _node("decision", "Verify after reading", evidence=[pointer],
                                 order={"before": ["read"], "after": ["verify"], "reason": "Recorded verification phase"}),
        }}

    result = compact_context(source[1:], handoff=True, recovery_history=source, handoff_archive_indices=[1],
                             prior_handoff=json.dumps(prior), summarizer=summarize)
    graph = parse_handoff_graph(result["summary"])
    assert ["read", "depends_on", "find", {"decisionId": "read-first"}] in graph["edges"]
    assert ["verify", "depends_on", "read", {"decisionId": "verify-last"}] in graph["edges"]
    assert graph["nodes"]["early-read"] == _node("attempt", "Read document", "failure", reason="Network stalled")


def test_retries_one_wrong_handoff_envelope_with_same_graph_and_same_records():
    prior = {"schema": HANDOFF_GRAPH_SCHEMA, "nodes": {
        "goal": _node("goal", "Finish verification"),
        "inspect": _node("task", "Read artifact", "pending"),
    }, "edges": []}
    rows = [{"role": "assistant", "text": "Artifact review completed."}]
    prompts = []

    def summarize(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"nodes": []}
        return _with_source(prompt, {"nodes": {"inspect": {"status": "done"}}})

    result = compact_context(rows, handoff=True, prior_handoff=json.dumps(prior), summarizer=summarize)
    assert result["ok"] and result["providerAttempts"] == 2
    assert len(prompts) == 2 and _prior_graph(prompts[0]) == _prior_graph(prompts[1]) == prior
    assert prompts[0].split("REDACTED_ENTRIES=", 1)[1] == prompts[1].split("REDACTED_ENTRIES=", 1)[1]
    assert "Schema feedback" in prompts[1]
    assert parse_handoff_graph(result["summary"])["nodes"]["inspect"]["status"] == "done"

    def partial(prompt):
        return _with_source(prompt, {"nodes": {"valid": _node("task", "Continue with evidence", "pending",
                             metadata={"api_key": "credential fixture"}),
                         "invented": _node("task", "Use invented evidence", "pending",
                                           evidence=[{"type": "scan", "id": "made-up-reference"}])},
                         "edges": [["valid", "depends_on", "INVALID_PROVIDER_REFERENCE"]]})

    result = compact_context(rows, handoff=True, prior_handoff=json.dumps(prior), summarizer=partial)
    assert result["ok"] and result["providerAttempts"] == 1
    graph = parse_handoff_graph(result["summary"])
    assert graph["nodes"]["valid"]["metadata"] == {"api_key": "[REDACTED_SECRET]"}
    assert "invented" not in graph["nodes"] and graph["edges"] == []


def test_verbatim_assistant_reply_is_never_cut_to_force_fit():
    compactor = ModelHandoff()
    state, planner, model, turn = fixture(compactor, prior=False, current="ORIGINAL_USER_TASK", limit=8_000)
    for index in range(8):
        _pair(state, index)
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": "EXACT_LARGE_REPLY " * 5000})
    before = turn.snapshot()
    with planner.bind_turn({}):
        _, guard = planner.maybe_compact_native_context(_request(planner, before), turn, force=True)
    assert guard["blocked"] is True
    assert turn.compaction["applied"] is True and turn.compaction["blocked"] is True
    assert turn.compaction["failureClass"] == "still_over_threshold"
    assert turn.compaction["afterTokens"] > turn.compaction["hardLimitTokens"]
    saved = turn.snapshot()
    reply_id = saved["handoffLastReplyId"]
    assert state.native_handoff_archive("s", binding="b", message_id=reply_id)["text"] == before["messages"][-1]["content"]
    graph = next(row["content"] for row in saved["messages"] if row.get("content", "").startswith(MODEL_HANDOFF_PREFIX))
    assert parse_handoff_graph(graph.removeprefix(MODEL_HANDOFF_PREFIX))["nodes"]["inspect"]["status"] == "pending"
    assert saved["compactionRecovery"][-1]["summary"] == graph.removeprefix(MODEL_HANDOFF_PREFIX)
    assert len(compactor.prompts) == 1
    from tests.test_native_context_compaction import plan

    assert plan(planner, turn)["nextStep"] == "context_compaction_required"
    assert not model.requests


@pytest.mark.parametrize("exact_trigger", [False, True])
def test_successful_native_compaction_at_or_above_trigger_retires_session(tmp_path, monkeypatch, exact_trigger):
    import threading

    import runtime_planner_service
    from agent_native_history_store import NativeHistoryStore
    from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
    from tests.test_native_context_compaction import Compactor, plan

    compactor = Compactor()
    store = NativeHistoryStore(tmp_path)
    state, planner, model, turn = fixture(compactor, prior=False, current="Original request", limit=16_000, store=store)
    if exact_trigger:
        def measured_at_trigger(summary, blocks, fit_limit, _measure):
            return summary, blocks, fit_limit + 1

        monkeypatch.setattr(runtime_planner_service, "fit_handoff_post_context", measured_at_trigger)
    for index in range(3):
        _pair(state, index)
    with planner.bind_turn({}):
        request = _request(planner, turn.snapshot())
        fixed = planner.native_context_guard({**request, "messages": [request["messages"][-1]]})["beforeTokens"]
        compactor.summary = json.dumps({"schema": HANDOFF_GRAPH_SCHEMA, "nodes": {
            "active": _node("task", "Irreducible task details " + "x" * ((14_400 - fixed - 240) * 4), "pending"),
        }, "edges": []})
        planner.maybe_compact_native_context(request, turn, force=True)
        pressure = turn.compaction
        assert pressure["applied"] and not pressure["blocked"]
        assert pressure["triggerTokens"] <= pressure["afterTokens"] < pressure["hardLimitTokens"]
        if exact_trigger:
            assert pressure["afterTokens"] == pressure["triggerTokens"] == 13_600
    assert pressure["sessionReadOnly"] is True
    assert "failureClass" not in pressure
    assert plan(planner, turn)["nextStep"] == "context_compaction_required"
    assert not model.requests and len(compactor.calls) == 1
    restored = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), store))
    assert restored.session_is_read_only("s")
    assert not restored.session_is_read_only("another-session")
    assert restored.native_conversation("s", binding="b") == turn.snapshot()


@pytest.mark.parametrize(("detail_repetitions", "folded"), [(5_000, False), (12_000, True)])
def test_native_budget_folds_completed_branch_only_when_needed(tmp_path, detail_repetitions, folded):
    from agent_native_history_store import NativeHistoryStore
    from tests.test_native_context_compaction import Compactor
    from tests.test_native_handoff_blocks import _state

    pointer = {"type": "receipt", "id": "verified-detail"}
    summary = json.dumps({"schema": HANDOFF_GRAPH_SCHEMA, "nodes": {
        "complete": _node("task", "Completed export", "done", evidence=[pointer]),
        "detail": _node("task", "Verified rows " * detail_repetitions, "done", evidence=[pointer]),
        "pending": _node("task", "Review the exported result", "pending"),
    }, "edges": [["detail", "belongs_to", "complete"], ["pending", "depends_on", "detail"]]})
    store = NativeHistoryStore(tmp_path)
    state, planner, _, turn = fixture(Compactor(summary), prior=False, current="Original scope", limit=32_000, store=store)
    for index in range(4):
        _pair(state, index)
    with planner.bind_turn({}):
        planner.maybe_compact_native_context(_request(planner, turn.snapshot()), turn, force=True)
    assert turn.compaction["applied"] is True
    assert turn.compaction["afterTokens"] < turn.compaction["triggerTokens"]
    restarted = _state(store)
    saved = restarted.native_conversation("s", binding="b")
    node_graph = next(row["content"] for row in saved["messages"] if row.get("content", "").startswith(MODEL_HANDOFF_PREFIX))
    graph = parse_handoff_graph(node_graph.removeprefix(MODEL_HANDOFF_PREFIX))
    if not folded:
        assert turn.compaction["afterTokens"] > turn.compaction["contextLimit"] // 2
        assert graph == parse_handoff_graph(summary)
        return
    assert set(graph["nodes"]) == {"complete", "pending"}
    assert graph["edges"] == [["pending", "depends_on", "complete"]]
    graph_id = next(item["id"] for item in graph["nodes"]["complete"]["evidence"] if item["type"] == "message")
    assert restarted.native_handoff_archive("s", binding="b", message_id=graph_id)["text"] == summary


def test_native_pressure_preserves_both_long_endpoints_and_retrieves_middle_after_restart(tmp_path):
    from agent_native_history_store import NativeHistoryStore
    from tests.test_native_handoff_blocks import _state

    originals = {
        "head": "OPENING_REQUEST " + "alpha " * 3600 + " ORIGINAL_END",
        "middle": "MIDDLE_UPDATE " + "bravo " * 2800,
        "tail": "LATEST_UPDATE " + "charlie " * 3600 + " LATEST_CONSTRAINT",
    }
    store = NativeHistoryStore(tmp_path)
    compactor = ModelHandoff()
    state, planner, _, turn = fixture(compactor, prior=False, current=originals["head"], limit=24_000, store=store)
    for index, (identity, text) in enumerate(list(originals.items())[1:]):
        _pair(state, index)
        state.append_native_user("s", binding="b", input_id=identity, message=text)
    with planner.bind_turn({}):
        planner.maybe_compact_native_context(_request(planner, turn.snapshot()), turn, force=True)
    assert turn.compaction["applied"] is True
    assert turn.compaction["afterTokens"] < turn.compaction["triggerTokens"]
    restarted = _state(store)
    saved = restarted.native_conversation("s", binding="b")
    first, middle, latest = [row for row in saved["messages"] if row.get("handoffUser")]
    assert first["content"].startswith("OPENING_REQUEST ") and first["content"].endswith(" ORIGINAL_END")
    assert "MIDDLE_UPDATE" not in middle["content"] and 'messageId="middle"' in middle["content"]
    assert latest["content"].startswith("LATEST_UPDATE ") and latest["content"].endswith(" LATEST_CONSTRAINT")
    for row, original in zip((first, middle, latest), originals.values()):
        assert restarted.native_handoff_archive("s", binding="b", message_id=row["messageId"])["text"] == original


def test_native_handoff_projects_lossless_transactions_and_drops_registry_definitions_only():
    definition = {"name": "inspect", "description": "A recreatable registry description", "parameters": {"type": "object"}}
    observations = [{"actionId": "result-a", "loadedToolDefinitions": [definition],
                     "observation": "argumentValidationIssues=limit:maximum->20", "outcome": {"status": "failed"}}]
    calls = [{"id": key, "function": {"name": "call_tool", "arguments": json.dumps({"tool": tool, "arguments": {"target": key}})}}
             for key, tool in (("load", "load_internal_tool_block"), ("read", "read_business_data"))]
    group = [{"role": "assistant", "content": "Inspect both selected targets.", "tool_calls": calls},
             *({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"observations": observations})} for call in calls)]
    row = {"role": "tool", "text": NATIVE_TRANSACTION_PREFIX + json.dumps(group)}
    seen = []

    def summarize(prompt):
        records = json.loads(prompt.split("\nREDACTED_ENTRIES=", 1)[1])
        seen.extend(records)
        return _with_source(prompt, {"nodes": {"goal": _node("goal", "Inspect the selected targets")}})

    result = compact_context([row], summarizer=summarize, handoff=True)
    assert result["ok"] and result["recovery"]["sourceEntries"] == [row]
    projected_request, projected_catalog, projected_work = seen[0]["transaction"]
    assert projected_request["content"] == group[0]["content"]
    assert [item["function"]["arguments"]["arguments"]["target"] for item in projected_request["tool_calls"]] == ["load", "read"]
    receipt, = projected_catalog["content"]["observations"]
    assert receipt == {key: value for key, value in observations[0].items() if key != "loadedToolDefinitions"} | {"loadedToolNames": ["inspect"]}
    assert projected_work["content"]["observations"] == observations


def test_native_handoff_seeds_verbatim_legacy_decision_and_keeps_archive_pointer():
    legacy = "Current goal:\n- Finish the batch.\n\nDecisions:\n- Inspect, then apply and verify.\n\nTodo:\n- Inspect."
    old = {"role": "assistant", "text": LEGACY_HANDOFF_PREFIX + legacy}
    record = {"role": "user", "text": "Keep the established order."}
    prompts = []
    result = compact_context([record], handoff=True, prior_handoff=legacy, recovery_history=[old, record],
                             handoff_archive_indices=[1], summarizer=lambda prompt: prompts.append(prompt) or {})
    assert result["ok"] and "LEGACY_HANDOFF_TEXT=" in prompts[0]
    decision = parse_handoff_graph(result["summary"])["nodes"]["legacy-decision-1"]
    assert decision == {"kind": "decision", "text": "Inspect, then apply and verify.",
                        "evidence": [{"type": "archive", "id": result["recovery"]["sourceDigest"] + ":0"}]}
    assert result["recovery"]["sourceEntries"][0] == old
