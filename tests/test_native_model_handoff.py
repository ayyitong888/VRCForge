import json
from pathlib import PureWindowsPath

from agent_handoff_graph import HANDOFF_GRAPH_SCHEMA, parse_handoff_graph
from context_compaction import MODEL_HANDOFF_PREFIX, compact_context
from tests.test_native_context_compaction import fixture


def _prior_graph(prompt):
    return json.loads(prompt.split("\nPRIOR_HANDOFF=", 1)[1].split("\nRETAINED_STATUS=", 1)[0])


def _edit(op, section, node_id, **fields):
    return {"op": op, "section": section, "id": node_id, **({"fields": fields} if fields else {})}


class ModelHandoff:
    def __init__(self):
        self.prompts = []

    def compact(self, entries, metadata):
        def summarize(prompt):
            self.prompts.append(prompt)
            if isinstance(_prior_graph(prompt), dict):
                return {"goal": None, "operations": []}
            return {"goal": "Retain the inspection objective.", "operations": [
                _edit("add", "blocked", "readback", facts="Evidence was partial", blocker="Readback is required",
                      evidence="Earlier result recorded a limit"),
                _edit("add", "nextSteps", "inspect", action="Review the next authorized evidence.", phase=1, dependsOn=[]),
            ]}
        return compact_context(entries, handoff=metadata["handoff"], has_todo=metadata["hasTodo"],
                               real_context_limit=metadata["realContextLimit"], target_tokens=metadata["targetTokens"],
                               recovery_history=metadata.get("_recoveryHistory"),
                               handoff_archive_indices=metadata.get("_handoffArchiveIndices"),
                               prior_handoff=metadata.get("_priorHandoff"),
                               retained_evidence=metadata.get("_retainedEvidence") or (), summarizer=summarize)


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


def test_manual_native_handoff_keeps_exact_tail_and_same_pending_approval_after_restart():
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
    oldest_tail = next(i for i, item in enumerate(before["messages"]) if any(call["id"] == "group-5-a" for call in item.get("tool_calls", [])))
    with planner.bind_turn({}):
        request = _request(planner, before, {"activeTodo": [{"id": "task1", "content": "TODO_EXACT_CONTENT", "status": "in_progress"}]})
        candidate, _ = planner.maybe_compact_native_context(request, turn, force=True, preserve_pending=True, has_todo=True)
    assert turn.compaction["applied"] is True
    assert not model.requests
    after = turn.snapshot()
    assert after["messages"][0]["content"].startswith(MODEL_HANDOFF_PREFIX)
    graph = parse_handoff_graph(after["messages"][0]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
    assert graph["blocked"][0]["id"] == "readback"
    assert after["messages"][after["activeTurnStart"]] == before["messages"][before["activeTurnStart"]]
    assert [item for item in after["messages"] if item.get("role") in {"tool", "assistant"}][1:] == before["messages"][oldest_tail:]
    assert "TODO_EXACT_CONTENT" in json.dumps(after["messages"])
    assert "EXACT_PROJECT_AGENTS" in candidate["instructions"]
    rewritten_prompt, = compactor.prompts
    for pinned in ("TODO_EXACT_CONTENT", "OLD_TODO_CONTENT", "OLD_PROGRESS_ONLY_LIST", "EXACT_PROJECT_AGENTS", "ORIGINAL_USER_TASK"):
        assert pinned not in rewritten_prompt
    assert "group-4-a" in rewritten_prompt
    assert "group-0-a" in rewritten_prompt and "group-0-b" in rewritten_prompt
    assert any("OLD_TODO_CONTENT" in entry["text"] for entry in after["compactionRecovery"][-1]["sourceEntries"])
    records = json.loads(rewritten_prompt.split("REDACTED_ENTRIES=", 1)[1])
    assert all("group-5-a" not in item["text"] and approval_call_id not in item["text"] for item in records)
    next_record = next(record for record in records if "group-1-a" in record["text"])
    archive = after["compactionRecovery"][-1]
    assert next_record["recordIndex"] == 2 and archive["sourceDigest"] in rewritten_prompt
    assert "OLD_PROGRESS_ONLY_LIST" in archive["sourceEntries"][1]["text"]
    assert "group-1-a" in archive["sourceEntries"][next_record["recordIndex"]]["text"]
    assert after["activeDispatchCallId"] == approval_call_id
    assert state._native_pending(after) == {approval_call_id}
    restored = state.restore_native_conversation("s", binding="b", snapshot=before)
    assert restored == after
    state.settle_native_call("s", binding="b", call_id=approval_call_id, content='{"status":"approved"}')
    assert not state._native_pending(turn.snapshot())


def test_long_active_turn_preserves_previous_completed_dialogue_and_parallel_receipts():
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
    prior_start = next(i for i, item in enumerate(before["messages"]) if item.get("content") == "PREVIOUS_EXACT_INPUT_1")
    intact_previous = before["messages"][prior_start:before["activeTurnStart"]]
    with planner.bind_turn({}):
        planner.maybe_compact_native_context(_request(planner, before), turn, force=True)
    assert turn.compaction["applied"] is True
    after = turn.snapshot()
    assert after["messages"][after["activeTurnStart"] - len(intact_previous):after["activeTurnStart"]] == intact_previous
    assert "PREVIOUS_EXACT_INPUT_1" not in compactor.prompts[0]
    assert "group-91-a" not in compactor.prompts[0].split("REDACTED_ENTRIES=", 1)[1]
    assert "group-90-a" in compactor.prompts[0] and "group-4-a" in compactor.prompts[0]


def test_no_todo_handoff_has_unresolved_evidence_and_only_handoff_rewrites_for_budget():
    attempts = []
    def summarize(prompt):
        attempts.append(prompt)
        return {"goal": "Intent", "operations": [
            _edit("add", "blocked", "partial", blocker="Unverified", facts="Partial",
                  evidence="evidence " * 250 if len(attempts) == 1 else "One result"),
            _edit("add", "nextSteps", "readback", action="Inspect readback.", phase=1, dependsOn=[]),
        ]}
    result = compact_context([{"role": "tool", "text": "OLD_TOOL_ROW"}], handoff=True, target_tokens=150, summarizer=summarize)
    assert result["ok"] is True and len(attempts) == 2
    assert all("OLD_TOOL_ROW" in prompt for prompt in attempts)
    assert "resulting graph too large" in attempts[1]
    assert parse_handoff_graph(result["summary"])["blocked"][0]["evidence"] == "One result"


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
            assert prior["decisions"][0]["decision"] == "inspect, apply, validate"
        seen_groups.extend(i for i in range(12) if f"BEGIN_COMPLETE_GROUP_{i} " in text)
        return {"goal": None, "operations": []} if prior else {
            "goal": "Follow the agreed order.", "operations": [
                _edit("add", "decisions", "order", decision="inspect, apply, validate"),
            ]}

    result = compact_context(source, handoff=True, real_context_limit=9_000, target_tokens=1200, summarizer=summarize)
    assert result["ok"] is True
    assert result["fidelity"] == "paged" and result["coverage"]["omittedEntries"] == 0
    assert result["coverage"]["complete"] is True
    assert seen_groups == list(range(12))
    assert parse_handoff_graph(result["summary"])["decisions"] == [{"id": "order", "decision": "inspect, apply, validate"}]


def test_failed_later_page_cannot_install_partial_handoff():
    source = [{"role": "agent", "text": "Example " * 1300 + str(i)} for i in range(5)]
    calls = 0
    def summarize(_prompt):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("HTTP 401 unauthorized")
        return {"goal": "Continue the work.", "operations": [
            _edit("add", "nextSteps", "validate", action="Validate.", phase=1, dependsOn=[]),
        ]}
    result = compact_context(source, handoff=True, real_context_limit=8_000, target_tokens=1200, summarizer=summarize)
    assert calls == 2 and result["coverage"]["completedPages"] == 1
    assert result["ok"] is False and not result["summary"]
    assert len(result["recovery"]["sourceEntries"]) == 5


def test_retained_success_is_evidence_for_handoff_reconciliation_without_quoting_tail():
    from runtime_planner_service import native_handoff_retained_evidence
    private_path = str(PureWindowsPath("C:/", "Users", "Private Name", "Material.zip"))
    tail = [
        {"role": "assistant", "tool_calls": [{"id": "recent-callback", "function": {"name": "call_tool", "arguments": json.dumps({
            "tool": "unity_import_outfit_package", "arguments": {"packagePath": private_path}})}}]},
        {"role": "tool", "tool_call_id": "recent-callback", "content": json.dumps({"observations": [{"runtimeTool": "vrcforge_import_outfit_package",
          "actionId": "later-exact-action", "outcome": {"status": "ok"}, "observation": "VERBATIM_TAIL_BODY"}]})},
    ]
    evidence = native_handoff_retained_evidence(tail)
    prompts = []
    result = compact_context([{"role": "tool", "text": "Material.zip returned an earlier approval failure before the outfit import."}],
        handoff=True, retained_evidence=evidence, summarizer=lambda prompt: prompts.append(prompt) or {
            "goal": "Follow ordered imports.", "operations": [
                _edit("add", "attempted", "first-import", action="Import Material.zip", outcome="failure", reason="Approval failure"),
                _edit("add", "completed", "import", action="Import Material.zip", result="Later attempt succeeded", evidence="later-exact-action"),
                _edit("add", "nextSteps", "outfit", action="Continue outfit import", phase=1, dependsOn=["import"]),
            ],
        })
    assert result["ok"] and "Continue outfit import" in result["summary"]
    projection = prompts[0].split("RETAINED_STATUS=", 1)[1].split("\nREDACTED_ENTRIES=", 1)[0]
    facts, = json.loads(projection)
    assert facts["callId"] == "recent-callback" and facts["selectors"]["packagePath"] == "Material.zip"
    assert facts["outcomes"] == [{"tool": "vrcforge_import_outfit_package", "actionId": "later-exact-action", "status": "ok"}]
    assert "Private Name" not in prompts[0] and "VERBATIM_TAIL_BODY" not in prompts[0]


def test_repeated_compaction_moves_resolved_work_without_rewriting_unmentioned_graph_nodes():
    prior = {"schema": HANDOFF_GRAPH_SCHEMA, "goal": "Finish ordered imports and validate the avatar.",
             "decisions": [{"id": "variants", "decision": "Use the selected outfit variant."}],
             "attempted": [{"id": "retry-history", "action": "Import material support", "outcome": "unknown",
                            "reason": "Timed out when Unity reloaded"}],
             "completed": [{"id": "face", "action": "Mount face prefab", "result": "Scene readback confirmed",
                            "evidence": "receipt-1"}],
             "blocked": [{"id": "outfit-recovery", "blocker": "Write paused", "facts": "Package A recovery pending",
                          "evidence": "Earlier unknown outcome"},
                         {"id": "shader-check", "blocker": "Material compatibility", "facts": "Shader unchecked",
                          "evidence": "Preflight warning"}],
             "nextSteps": [{"id": "package-a", "action": "Import package A", "phase": 1, "dependsOn": []},
                           {"id": "readback", "action": "Verify face readback", "phase": 1, "dependsOn": ["face"]},
                           {"id": "package-b", "action": "Import package B", "phase": 2, "dependsOn": ["package-a"]}]}
    rows = [{"role": "tool", "text": "receipt-2: Package A import handler reported success."}]

    def summarize(prompt):
        assert _prior_graph(prompt) == prior
        assert _prior_graph(prompt)["nextSteps"][0]["id"] == "package-a"
        return {"goal": None, "operations": [
            _edit("remove", "blocked", "outfit-recovery"),
            _edit("remove", "nextSteps", "package-a"),
            _edit("add", "completed", "package-a", action="Import package A", result="Handler reported success",
                  evidence="receipt-2"),
        ]}

    result = compact_context(rows, handoff=True, prior_handoff=json.dumps(prior), summarizer=summarize)
    graph = parse_handoff_graph(result["summary"])
    assert graph["goal"] == prior["goal"]
    assert graph["decisions"] == prior["decisions"]
    assert graph["attempted"] == prior["attempted"]
    assert graph["completed"][0] == prior["completed"][0]
    assert graph["completed"][1]["id"] == "package-a"
    assert graph["blocked"] == prior["blocked"][1:]
    assert graph["nextSteps"] == prior["nextSteps"][1:]
    assert result["recovery"]["sourceEntries"] == rows

    partial = compact_context(rows, handoff=True, prior_handoff=result["summary"],
        summarizer=lambda _: {"goal": None, "operations": [_edit("update", "completed", "package-a", evidence="Later readback")]})
    final = parse_handoff_graph(partial["summary"])
    assert final["completed"][1] == {**graph["completed"][1], "evidence": "Later readback"}
    assert final["nextSteps"] == graph["nextSteps"]

    invalid = compact_context(rows, handoff=True, prior_handoff=partial["summary"],
        summarizer=lambda _: {"goal": None, "operations": [_edit("remove", "completed", "package-a")]})
    assert not invalid["ok"] and not invalid["summary"] and invalid["fallbackReason"] == "schema_error"


def test_provider_quoted_phase_and_dependencies_become_typed_graph_fields():
    result = compact_context([{"role": "tool", "text": "A partially received document required a new read."}], handoff=True,
        summarizer=lambda _: {"goal": "Verify document", "operations": [
            _edit("add", "completed", "find", action="Find document", result="Located", evidence="call-1"),
            _edit("add", "nextSteps", "read", action="Read again", phase="1", dependsOn="find"),
            _edit("add", "nextSteps", "verify", action="Verify document", phase="2", dependsOn="read, find"),
            _edit("add", "attempted", "early-read", action="Read once", outcome="Missing page",
                  reason="Network stalled"),
        ]})
    graph = parse_handoff_graph(result["summary"])
    assert [node["phase"] for node in graph["nextSteps"]] == [1, 2]
    assert graph["nextSteps"][1]["dependsOn"] == ["read", "find"]
    assert graph["attempted"] == [{
        "id": "early-read", "action": "Read once", "outcome": "unknown", "reason": "Missing page Network stalled",
    }]


def test_retries_one_wrong_handoff_envelope_with_same_graph_and_same_records():
    prior = {"schema": HANDOFF_GRAPH_SCHEMA, "goal": "Finish verification", "completed": [], "attempted": [],
             "decisions": [], "blocked": [], "nextSteps": [{"id": "inspect", "action": "Read artifact",
                                                             "phase": 0, "dependsOn": []}]}
    rows = [{"role": "assistant", "text": "Artifact review completed."}]
    prompts = []

    def summarize(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"goal": "INVALID_PROVIDER_BODY", "operations": {"add": []}}
        return {"goal": None, "operations": [
            _edit("remove", "nextSteps", "inspect"),
            _edit("add", "completed", "inspect", action="Read artifact", result="Verified", evidence="call-1"),
        ]}

    result = compact_context(rows, handoff=True, prior_handoff=json.dumps(prior), summarizer=summarize)
    assert result["ok"] and result["providerAttempts"] == 2
    assert len(prompts) == 2 and _prior_graph(prompts[0]) == _prior_graph(prompts[1]) == prior
    assert prompts[0].split("REDACTED_ENTRIES=", 1)[1] == prompts[1].split("REDACTED_ENTRIES=", 1)[1]
    assert "operations as a JSON array" in prompts[1] and "INVALID_PROVIDER_BODY" not in prompts[1]
    assert parse_handoff_graph(result["summary"])["completed"][0]["id"] == "inspect"

    invalid_prompts = []
    invalid_edits = {"goal": None, "operations": [
        _edit("update", "nextSteps", "inspect", dependsOn=["INVALID_PROVIDER_REFERENCE"]),
    ]}
    rejected = compact_context(rows, handoff=True, prior_handoff=json.dumps(prior),
        summarizer=lambda prompt: invalid_prompts.append(prompt) or invalid_edits)
    assert rejected["providerAttempts"] == 2 and rejected["fallbackReason"] == "schema_error"
    assert not rejected["ok"] and not rejected["summary"]
    assert "nextSteps[0].dependsOn" in invalid_prompts[1]
    assert "INVALID_PROVIDER_REFERENCE" not in invalid_prompts[1]


def test_verbatim_tail_is_never_cut_to_force_fit():
    compactor = ModelHandoff()
    state, planner, _, turn = fixture(compactor, prior=False, current="ORIGINAL_USER_TASK", limit=8_000)
    for index in range(8):
        _pair(state, index)
    state.append_native_user("s", binding="b", message="EXACT_LARGE_CORRECTION " * 5000)
    before = turn.snapshot()
    with planner.bind_turn({}):
        _, guard = planner.maybe_compact_native_context(_request(planner, before), turn, force=True)
    assert guard["blocked"] is True
    assert turn.compaction["failureClass"] == "insufficient_summary_budget"
    assert turn.snapshot() == before and not compactor.prompts
