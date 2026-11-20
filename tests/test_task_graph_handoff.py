import json

import pytest

from agent_handoff_graph import (
    HANDOFF_GRAPH_SCHEMA,
    HandoffSchemaError,
    apply_handoff_edits,
    handoff_view,
    parse_handoff_graph,
    ready_task_ids,
    serialize_handoff_graph,
)
from context_compaction import _Redactor, _redact_handoff_payload, compact_context


def test_native_resume_reads_use_catalog_alias_and_existing_owner():
    from context_compaction import MODEL_HANDOFF_PREFIX
    from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, project_native_resume_reads

    read = PlannerTool("inspect_source", "Read archive", "project",
                       runtime_name="vrcforge_inspect_outfit_package", block="project/source")
    write = PlannerTool("import_source", "Import archive", "project",
                        runtime_name="vrcforge_import_source", write=True, block="project/source")
    catalog = PlannerCatalogSnapshot(routable_tools=(read, write))
    args = {"archiveCursor": "archive_abcdef0123456789abcdef0123456789_1"}
    source_reads = {
        "partial": {"tool": read.runtime_name, "arguments": args},
        "bad": {"tool": write.runtime_name, "arguments": args},
    }
    content = MODEL_HANDOFF_PREFIX + json.dumps({"resumeReads": source_reads})

    for visible in ([], [read]):
        messages = [{"role": "assistant", "content": content}]
        project_native_resume_reads(messages, lambda: catalog, visible)
        output = json.loads(messages[0]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
        expected = {"tool": read.name, "arguments": args}
        if not visible:
            expected["loadCall"] = {"skill_tool": "load_internal_tool_block",
                                    "skill_params": {"block": read.block, "tools": [read.name]}}
        assert output["resumeReads"] == source_reads
        assert output["resumeReadCalls"] == {"partial": expected}

    next_args = {"archiveCursor": "archive_abcdef0123456789abcdef0123456789_2"}

    def page(call_id, request_args, coverage, status="ok"):
        return [
            {"role": "assistant", "tool_calls": [{"id": call_id, "function": {
                "name": "call_tool", "arguments": json.dumps({"tool": read.name, "arguments": request_args}),
            }}]},
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"observations": [{
                "runtimeTool": read.runtime_name, "outcome": {"status": status},
                "handoffReceipt": {"sourceRead": coverage},
            }]})},
        ]

    partial = page("partial", args, {"status": "partial", "nextRequest": {"tool": read.runtime_name, "arguments": next_args}})
    complete = {"status": "complete", "processedCount": 121, "totalCount": 121, "remainingCount": 0}
    messages = [{"role": "assistant", "content": content}, *partial, *page("failed", next_args, complete, "error")]
    project_native_resume_reads(messages, lambda: catalog, [read])
    output = json.loads(messages[0]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
    assert output["resumeReadCalls"] == {"partial": {"tool": read.name, "arguments": next_args}}
    messages = [{"role": "assistant", "content": content}, *partial, *page("completed", next_args, complete)]
    project_native_resume_reads(messages, lambda: catalog, [read])
    assert messages[0]["content"] == content


def test_handoff_binds_answer_operation_and_todo_to_their_own_sources():
    user = {"id": "user-1", "source": "user", "text": "Use the second option.",
            "references": [{"id": "assistant-1", "text": "Mount now, or import all packages first and mount after?"}]}
    todo = {"progressId": "import-all", "title": "Import all packages", "status": "running"}
    operation = {"id": "exec-781", "kind": "execution", "tool": "unity_import_package", "state": "readback_pending"}
    pointer = {"type": "message", "id": "user-1"}

    def summarize(prompt):
        assert '"status":"running"' in prompt
        return {"nodes": {
            "import": {"kind": "task", "text": todo["title"], "todoId": todo["progressId"],
                       "status": "done", "evidence": [pointer]},
            "mount": {"kind": "task", "text": "Mount the outfits", "status": "pending", "evidence": [pointer]},
        }, "edges": [["mount", "depends_on", "import", {"reason": "Chosen order", "evidence": [pointer]}]]}

    def compact(summarizer, operations, prior=None):
        return compact_context([{"role": "user", "text": user["text"]}], handoff=True, task_inputs=[user],
                               todo_items=[todo], outstanding_operations=operations, prior_handoff=prior,
                               summarizer=summarizer)

    first = compact(summarize, [operation])
    graph = parse_handoff_graph(first["summary"])
    assert graph["nodes"]["import"]["todoId"] == todo["progressId"]
    assert "status" not in graph["nodes"]["import"]
    answer = next(node for node in graph["nodes"].values() if node["kind"] == "decision")
    assert user["references"][0]["text"] in answer["text"] and user["text"] in answer["text"]
    assert answer["evidence"][0] == pointer
    recovery_id, = handoff_view(graph, [todo])["recoverTaskIds"]
    assert graph["nodes"][recovery_id]["operationRef"] == operation
    assert handoff_view(graph, [todo])["readyTaskIds"] == [recovery_id, "import"]

    revised = compact(lambda _: {"nodes": {recovery_id: {"status": "done"}}}, [], first["summary"])
    assert handoff_view(parse_handoff_graph(revised["summary"]), [todo])["recoverTaskIds"] == [recovery_id]
    completed = compact(lambda _: {}, [{**operation, "state": "done"}], revised["summary"])
    done_graph = parse_handoff_graph(completed["summary"])
    unsupported = {**todo, "status": "completed"}
    assert handoff_view(done_graph, [unsupported])["readyTaskIds"] == ["import"]
    verified = {**unsupported, "completionChecks": [{"kind": "write"}], "completionEvidence": {"0": "action-exact"}}
    assert handoff_view(done_graph, [verified])["readyTaskIds"] == ["mount"]


def test_source_order_expansion_and_phase_readiness_survive_round_trip():
    graph = apply_handoff_edits(None, {
        "nodes": {
            "imports": node("task", "Import phase", group=True, todoId="phase-import", status="done"),
            "primary": node("task", "Primary package", todoId="primary"),
            "secondary": node("task", "Secondary package", status="pending"),
            "mounts": node("task", "Mount phase", group=True, status="pending"),
            "mount": node("task", "Mount the primary outfit", status="pending"),
            "verify": node("task", "Verify in scene", status="pending"),
            "inspect": node("task", "Inspect appearance choices", status="pending"),
            "order": node("decision", "Install both packages before mounting the primary outfit.",
                          order={"before": ["imports"], "after": ["mounts", "verify"], "reason": "Both packages first"}),
            "choice": node("decision", "Mount only the primary outfit"),
        },
        "edges": [
            ["primary", "belongs_to", "imports"], ["secondary", "belongs_to", "imports"],
            ["mount", "belongs_to", "mounts"], ["choice", "constrains", "mount"],
        ],
    })
    restored = parse_handoff_graph(serialize_handoff_graph(graph))
    assert restored == graph
    assert all("status" not in restored["nodes"][group] for group in ("imports", "mounts"))
    assert [edge for edge in restored["edges"] if edge[1] == "depends_on"] == [
        ["mounts", "depends_on", "imports", {"decisionId": "order"}],
        ["verify", "depends_on", "imports", {"decisionId": "order"}],
    ]
    completed = [{"progressId": "primary", "status": "completed", "completionChecks": [{"kind": "write"}],
                  "completionEvidence": {"0": "action-primary"}}, {"progressId": "phase-import", "status": "completed"}]
    assert handoff_view(restored, completed)["readyTaskIds"] == ["secondary", "inspect"]
    advanced = apply_handoff_edits(restored, {"nodes": {"secondary": {"status": "done"}}})
    assert handoff_view(advanced, completed)["readyTaskIds"] == ["mount", "verify", "inspect"]


def test_compaction_discards_dependencies_without_an_available_source():
    user = {"id": "user-1", "source": "user", "text": "Import first. Inspect appearance separately."}
    result = compact_context(
        [{"role": "user", "text": user["text"]}], handoff=True, task_inputs=[user],
        summarizer=lambda _: {"nodes": {
            "inspect": node("task", "Inspect appearance", status="pending"),
            "import": node("task", "Import", status="pending"),
            "mount": node("task", "Mount", status="pending"),
        }, "edges": [
            ["import", "depends_on", "inspect"],
            ["mount", "depends_on", "inspect", {"reason": "Unsupported",
                                               "evidence": [{"type": "message", "id": "made-up"}]}],
            ["mount", "depends_on", "import", {"reason": "Import first",
                                              "evidence": [{"type": "message", "id": "user-1"}]}],
        ]},
    )
    graph = parse_handoff_graph(result["summary"])
    assert len(graph["edges"]) == 1
    assert handoff_view(graph)["readyTaskIds"] == ["inspect", "import"]


def test_summary_preserves_a_decision_without_proving_a_new_operational_blocker():
    from context_compaction import _handoff_edit_sources

    old_summary = {"type": "archive", "id": "digest:0"}
    receipt = {"type": "receipt", "id": "call-checked"}
    fields = {"reason": "Needs inspection", "evidence": [old_summary]}
    edits = {"nodes": {"decision": {"kind": "decision", "text": "Import before mounting.",
                                    "evidence": [old_summary]}},
             "edges": [["import", "depends_on", "inspect", fields],
                       ["import", "blocked_by", "uncertain", fields],
                       ["mount", "depends_on", "import", {**fields, "evidence": [old_summary, receipt["id"]]}]]}
    verified = _handoff_edit_sources(edits, None, [], {("archive", "digest:0"), ("receipt", "call-checked")},
                                     set(), summary_refs={"digest:0"})
    assert verified["nodes"]["decision"]["evidence"] == [old_summary]
    assert verified["edges"] == [["mount", "depends_on", "import", {**fields, "evidence": [receipt]}]]


def node(kind, text, **fields):
    return {"kind": kind, "text": text, "evidence": [{"type": "message", "id": "user-1"}], **fields}


def test_typed_graph_preserves_failed_methods_and_separates_parallel_frontier():
    goal = "  Install the outfit.\r\nVerify face settings separately.  "
    graph = apply_handoff_edits(None, {
        "nodes": {
            "goal": node("goal", goal),
            "inspect": node("task", "Inspect outfit", status="done"),
            "import": node("task", "Import outfit", status="pending", language="en"),
            "activate": node("task", "Activate outfit", status="pending"),
            "face": node("task", "Verify face settings", status="in_progress"),
            "first": node("attempt", "Inspect with an unexposed tool", status="failure", reason="Tool unavailable"),
            "second": node("attempt", "Inspect with the available tool", status="success", reason="Package found"),
            "lock": node("issue", "Recovery lock", status="open", facts="Previous import temporarily locks writes"),
            "selected": node("decision", "Import the chosen prefab only", scope="graph"),
        },
        "edges": [
            ["inspect", "belongs_to", "goal"], ["import", "belongs_to", "goal"],
            ["activate", "belongs_to", "goal"], ["face", "belongs_to", "goal"],
            ["first", "attempt_of", "import"], ["second", "attempt_of", "import"],
            ["first", "replaced_by", "second", "Use the available tool to finish preflight"],
            ["import", "blocked_by", "lock"], ["import", "depends_on", "inspect"],
            ["activate", "depends_on", "import"], ["selected", "constrains", "import"],
        ],
    })
    assert graph["nodes"]["goal"]["text"] == goal
    assert handoff_view(graph) == {"readyTaskIds": ["face"], "openIssueIds": ["lock"], "requirementCoverageKnown": False,
                                  "incomplete": True, "anomaly": False}

    continued = apply_handoff_edits(graph, {
        "nodes": {"lock": {"status": "closed"}, "first": None, "import": {"status": "in_progress"}},
        "removeEdges": [["first", "attempt_of", "import"]],
    })
    assert ready_task_ids(continued) == ["import", "face"]
    assert continued["nodes"]["first"] == graph["nodes"]["first"]
    assert continued["nodes"]["import"]["language"] == "en"
    assert continued["edges"] == graph["edges"]

    finished = apply_handoff_edits(continued, {
        "nodes": {"import": {"status": "done", "evidence": [{"type": "receipt", "id": "call-import"}]}},
    })
    assert ready_task_ids(finished) == ["activate", "face"]
    assert finished["nodes"]["import"]["evidence"] == [
        {"type": "message", "id": "user-1"}, {"type": "receipt", "id": "call-import"},
    ]
    assert parse_handoff_graph(serialize_handoff_graph(finished)) == finished


def test_invalid_nodes_and_edges_are_local_and_keep_existing_valid_state():
    prior = apply_handoff_edits(None, {
        "nodes": {"goal": node("goal", "Verify export"), "export": node("task", "Export", status="pending")},
        "edges": [["export", "belongs_to", "goal"]],
    })
    edited = apply_handoff_edits(prior, {
        "nodes": {
            "export": {"status": "misspelled"},
            "review": node("task", "Review", status="pending", metadata={"status": "done"}),
            "missing-proof": {"kind": "task", "text": "No pointer", "status": "pending"},
            "invalid-json": node("decision", "Invalid", confidence=float("nan")),
            "_invalid": node("decision", "Invalid slug"),
        },
        "edges": [
            ["review", "belongs_to", "export"], ["export", "belongs_to", "review"],
            ["review", "depends_on", "export"], ["export", "depends_on", "review"],
            ["export", "depends_on", "missing-proof"], ["review", "unknown", "export"],
        ],
    })
    assert edited["nodes"]["export"] == prior["nodes"]["export"]
    assert set(edited["nodes"]) == {"goal", "export", "review"}
    assert edited["edges"] == prior["edges"] + [
        ["review", "belongs_to", "export"], ["review", "depends_on", "export"],
    ]
    assert ready_task_ids(edited) == ["export"]
    with pytest.raises(HandoffSchemaError):
        apply_handoff_edits(edited, {"properties": {}})
    initial = apply_handoff_edits(None, {
        "nodes": {
            "goal": {"kind": "goal", "text": "Missing source"},
            "review": node("task", "Review", status="pending"),
        },
        "edges": [["review", "belongs_to", "goal"]],
    })
    assert set(initial["nodes"]) == {"review"}
    assert handoff_view(initial) == {"readyTaskIds": ["review"], "openIssueIds": [], "requirementCoverageKnown": False,
                                     "incomplete": True, "anomaly": False}


def test_only_incomplete_graph_with_no_runnable_tasks_is_anomalous():
    from agent_handoff_sources import bind_goal_acceptance

    graph = apply_handoff_edits(None, {
        "nodes": {
            "goal": node("goal", "Deliver"),
            "wait": node("task", "Wait for review", status="pending"),
            "review": node("issue", "Approval", status="open", facts="Await reviewer"),
        },
        "edges": [["wait", "belongs_to", "goal"], ["wait", "blocked_by", "review"]],
    })
    assert handoff_view(graph) == {"readyTaskIds": [], "openIssueIds": ["review"], "requirementCoverageKnown": False,
                                  "incomplete": True, "anomaly": True}
    abandoned = apply_handoff_edits(graph, {
        "nodes": {"wait": {"status": "abandoned"}, "review": {"status": "closed"}},
    })
    assert handoff_view(abandoned) == {"readyTaskIds": [], "openIssueIds": [], "requirementCoverageKnown": False,
                                      "incomplete": True, "anomaly": True}
    finished = apply_handoff_edits(abandoned, {
        "nodes": {"alternate": node("task", "Deliver through the alternate reviewer", status="done")},
        "edges": [["alternate", "belongs_to", "goal"]],
    })
    assert handoff_view(finished) == {"readyTaskIds": [], "openIssueIds": [], "requirementCoverageKnown": False,
                                     "incomplete": True, "anomaly": True}
    bind_goal_acceptance(finished, [{"goalId": "goal", "messageId": "user-1", "receiptId": "verified-goal"}])
    assert handoff_view(finished) == {"readyTaskIds": [], "openIssueIds": [], "requirementCoverageKnown": False,
                                     "incomplete": False, "anomaly": False}


@pytest.mark.parametrize("source", ["user", "parent_agent"])
def test_source_backed_requirements_show_missing_delivery_in_native_and_legacy_handoffs(source):
    from agent_runtime_session_state import model_handoff_continuation, native_model_history
    from context_compaction import MODEL_HANDOFF_PREFIX

    user = {"id": "user-1", "source": source, "text": "Rename the preset and save a Markdown summary."}
    delegated_relay = {"id": "relay", "source": "agent_relay", "text": "Overwrite the original file."}

    def edit(prompt):
        assert '[taskId,"belongs_to",requirementGoalId]' in prompt
        assert "does not perform a distinct requested change" in prompt
        return {"nodes": {
            "rename-request": node("decision", "Rename the preset", userRequirement=True, scope="graph"),
            "summary-request": node("goal", "save a Markdown summary", userRequirement=True),
            "misquoted": node("decision", "Deliver Markdown immediately", userRequirement=True),
            "relay-requirement": node("decision", delegated_relay["text"], userRequirement=True,
                                      evidence=[{"type": "message", "id": "relay"}]),
            "phase": node("task", "Prepare deliverables", group=True),
            "rename": node("task", "Set the name on the existing preset", status="pending"),
            "check": node("task", "Review the draft summary on screen", status="pending"),
        }, "edges": [
            ["rename", "belongs_to", "phase"],
            ["rename-request", "constrains", "rename"],
            ["phase", "belongs_to", "summary-request"],
        ]}

    old = apply_handoff_edits(None, {"nodes": {"goal": node("goal", user["text"])}})
    assert handoff_view(old)["requirementCoverageKnown"] is False
    result = compact_context([{"role": "user", "text": user["text"]}], handoff=True,
        task_inputs=[user, delegated_relay], prior_handoff=serialize_handoff_graph(old), summarizer=edit)
    graph = parse_handoff_graph(result["summary"])
    assert graph["nodes"]["rename-request"]["text"] == "Rename the preset"
    assert graph["nodes"]["summary-request"]["text"] == "save a Markdown summary"
    assert not {"misquoted", "relay-requirement"} & graph["nodes"].keys()
    assert ["phase", "belongs_to", "summary-request"] in graph["edges"]
    assert handoff_view(graph)["uncoveredRequirementIds"] == ["summary-request"]

    content = MODEL_HANDOFF_PREFIX + result["summary"]
    legacy = model_handoff_continuation({"role": "agent", "text": content})
    native, = native_model_history([{"role": "assistant", "content": content}])
    assert json.loads(legacy.removeprefix(MODEL_HANDOFF_PREFIX))["uncoveredRequirementIds"] == ["summary-request"]
    assert native["content"] == legacy

    abandoned = apply_handoff_edits(graph, {"nodes": {"rename": {"status": "abandoned"}}})
    assert handoff_view(abandoned)["uncoveredRequirementIds"] == ["rename-request", "summary-request"]


def test_goal_acceptance_waits_for_every_explicit_requirement_to_have_a_delivery_task():
    from agent_handoff_sources import bind_goal_acceptance

    graph = apply_handoff_edits(None, {"nodes": {
        "goal": node("goal", "Rename the preset and export a summary"),
        "rename-intent": node("goal", "Rename the preset", userRequirement=True),
        "export-intent": node("decision", "export a summary", userRequirement=True),
        "rename": node("task", "Rename the preset item", status="done"),
        "export": node("task", "Save the summary at the agreed destination", status="done"),
    }, "edges": [
        ["rename", "belongs_to", "rename-intent"], ["export-intent", "constrains", "export"],
    ]})
    receipt = [{"goalId": "goal", "messageId": "user-1", "receiptId": "verified-goal"}]
    bind_goal_acceptance(graph, receipt)
    view = handoff_view(graph)
    assert graph["nodes"]["goal"]["goalAcceptance"] == receipt[0]
    assert view["uncoveredRequirementIds"] == [] and view["incomplete"] is False

    missing = apply_handoff_edits(graph, {"nodes": {"export": None}})
    assert handoff_view(missing)["uncoveredRequirementIds"] == ["export-intent"]
    assert handoff_view(missing)["incomplete"] is True
    bind_goal_acceptance(missing, receipt)
    assert "goalAcceptance" not in missing["nodes"]["goal"]


def test_live_todo_requirement_links_reopen_and_cover_without_recompacting():
    from agent_handoff_graph import project_requirement_tasks
    from agent_runtime_session_state import NATIVE_RUNTIME_CONTEXT_PREFIX, native_model_history
    from context_compaction import MODEL_HANDOFF_PREFIX

    graph = apply_handoff_edits(None, {"nodes": {
        "goal": node("goal", "Change the preset size and save the readback"),
        "size": node("goal", "Change the preset size", userRequirement=True),
        "readback": node("decision", "save the readback", userRequirement=True),
        "phase": node("task", "Preset changes", todoId="parent", group=True),
    }})
    assert handoff_view(graph)["uncoveredRequirementIds"] == ["size", "readback"]
    todos = [
        {"progressId": "parent", "title": "Preset changes", "status": "pending", "requirementIds": ["size", "readback"]},
        {"progressId": "resize", "title": "Increase preset size to the requested value", "status": "pending", "requirementIds": ["size", "unknown"]},
        {"progressId": "save", "title": "Save the measured readback", "status": "pending", "requirementIds": ["readback"]},
    ]
    early = project_requirement_tasks(graph, todos[:2])
    assert handoff_view(early, todos[:2])["uncoveredRequirementIds"] == ["readback"]
    request = native_model_history([
        {"role": "assistant", "content": MODEL_HANDOFF_PREFIX + serialize_handoff_graph(graph)},
        {"role": "user", "content": NATIVE_RUNTIME_CONTEXT_PREFIX + json.dumps({"activeTodo": todos})},
    ])
    shown = json.loads(request[0]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
    by_todo = {value["todoId"]: key for key, value in shown["nodes"].items() if value.get("todoId")}
    assert shown["uncoveredRequirementIds"] == []
    assert shown["edges"] == [[by_todo["resize"], "belongs_to", "size"], ["readback", "constrains", by_todo["save"]]]
    dropped = [*todos[:2], {**todos[2], "status": "cancelled"}]
    assert handoff_view(project_requirement_tasks(graph, dropped), dropped)["uncoveredRequirementIds"] == ["readback"]
    saved_with_todos = project_requirement_tasks(graph, todos)
    assert handoff_view(saved_with_todos)["uncoveredRequirementIds"] == []
    assert handoff_view(saved_with_todos, todos[:2])["uncoveredRequirementIds"] == ["readback"]
    assert handoff_view(saved_with_todos, [])["uncoveredRequirementIds"] == ["size", "readback"]


def test_tool_states_and_exact_results_survive_repeated_model_handoff():
    user = {"id": "user-1", "source": "user", "text": "Read source archive and import exact selected package"}
    source_ref = "result_" + "a" * 32
    import_ref = "result_" + "b" * 32
    partial = {"id": "archive-receipt", "kind": "receipt", "callId": "archive-receipt", "tool": "inspect_archive",
               "operationKind": "read", "scopeId": "archive-scope", "state": "done", "outcomeStatus": "ok",
               "resultRef": source_ref, "sourceRead": {"status": "partial", "processedCount": 12,
                            "totalCount": 24, "nextRequest": {"tool": "inspect_archive", "arguments": {"offset": 12}}}}
    preview = {"id": "import-preview", "kind": "preview", "callId": "preview-receipt", "tool": "preview_import",
               "operationKind": "preview", "scopeId": "preview-scope", "state": "done", "outcomeStatus": "ok"}
    interrupted = {"id": "exec-1", "kind": "execution", "callId": "import-receipt", "tool": "import",
                   "operationKind": "write", "scopeId": "import-scope", "state": "interrupted",
                   "executionId": "exec-1", "checkpointId": "ckpt-1", "resultRef": import_ref}

    def compact(operations, prior=None, summarize=lambda _: {}):
        result = compact_context([{"role": "user", "text": user["text"]}], handoff=True, task_inputs=[user],
                                 outstanding_operations=operations, prior_handoff=prior, summarizer=summarize)
        return result["summary"], parse_handoff_graph(result["summary"])

    summary, graph = compact([partial, preview, interrupted])
    task_ids = {task["toolTask"]["scopeId"]: node_id for node_id, task in graph["nodes"].items() if "toolTask" in task}
    attempt_ids = {task["attemptReceiptId"]: node_id for node_id, task in graph["nodes"].items() if "attemptReceiptId" in task}
    archive_id, preview_id, import_id = (task_ids[key] for key in ("archive-scope", "preview-scope", "import-scope"))
    assert graph["nodes"][archive_id]["toolTask"]["state"] == "partial"
    assert handoff_view(graph)["resumeReads"] == {archive_id: partial["sourceRead"]["nextRequest"]}
    assert graph["nodes"][archive_id]["status"] == "pending"
    assert graph["nodes"][preview_id]["status"] == "done"
    assert graph["nodes"][import_id]["status"] == "pending"
    assert graph["nodes"][attempt_ids["import-receipt"]]["status"] == "interrupted"

    def spoof(_):
        return {"nodes": {
            archive_id: {"status": "done", "resultRef": import_ref},
            import_id: None,
            attempt_ids["import-receipt"]: {"status": "success"},
            "goal-copy": {"kind": "goal", "text": user["text"], "goalAcceptance": {"receiptId": "preview-receipt"},
                          "evidence": [{"type": "message", "id": "user-1"}]},
            "fake-import": {"kind": "task", "text": "Imported", "status": "done", "resultRef": import_ref,
                           "toolTask": {"state": "verified"}, "evidence": [{"type": "receipt", "id": "preview-receipt"}]},
        }, "edges": [[attempt_ids["import-receipt"], "attempt_of", preview_id]],
           "removeEdges": [[attempt_ids["import-receipt"], "attempt_of", import_id]]}

    summary, attacked = compact([], summary, spoof)
    assert attacked["nodes"][archive_id] == graph["nodes"][archive_id]
    assert attacked["nodes"][import_id] == graph["nodes"][import_id]
    assert attacked["nodes"]["fake-import"]["status"] == "pending"
    assert "resultRef" not in attacked["nodes"]["fake-import"] and "toolTask" not in attacked["nodes"]["fake-import"]
    assert "goalAcceptance" not in attacked["nodes"]["goal-copy"] and handoff_view(attacked)["incomplete"]
    assert attacked["edges"] == graph["edges"]

    resumed = {**interrupted, "callId": "resumed-receipt", "state": "done", "commitState": "complete",
               "readbackState": "verified", "outcomeStatus": "ok"}
    complete_read = {**partial, "callId": "archive-finish", "sourceRead": {"status": "complete", "processedCount": 24}}
    _, negative = compact([{**resumed, "targetEffectVerified": False}], summary)
    assert negative["nodes"][import_id]["status"] == "pending"
    assert negative["nodes"][import_id]["toolTask"]["state"] == "unverified"
    _, finished = compact([resumed, complete_read], summary)
    assert finished["nodes"][archive_id]["toolTask"]["sourceRead"] == complete_read["sourceRead"]
    assert "resumeReads" not in handoff_view(finished)
    assert finished["nodes"][import_id]["status"] == "done"
    assert finished["nodes"][import_id]["toolTask"]["resultRef"] == import_ref
    assert finished["nodes"][attempt_ids["import-receipt"]]["status"] == "interrupted"


def test_registered_goal_requires_an_acceptance_for_its_own_identifier_and_source():
    user = {"id": "goal-message", "source": "user", "text": "Verify the requested final object"}
    binding = {"goalId": "goal-registered", "messageId": user["id"]}
    runs = []

    def compact(records, prior=None):
        result = compact_context([{"role": "user", "text": user["text"]}], handoff=True, task_inputs=[user],
                                 goal_bindings=[binding], goal_acceptances=records, prior_handoff=prior,
                                 summarizer=lambda _: {"nodes": {"alias": node("goal", user["text"])}})
        runs.append(result["summary"])
        return parse_handoff_graph(runs[-1])

    other_goal = {**binding, "goalId": "goal-other", "receiptId": "passed-elsewhere"}
    other_source = {**binding, "messageId": "another-message", "receiptId": "passed-elsewhere"}
    pending = compact([other_goal, other_source])
    assert set(pending["nodes"]) == {"goal-registered"}
    assert handoff_view(pending)["incomplete"]
    accepted = compact([{**binding, "receiptId": "exact-verified-receipt"}], runs[-1])
    assert not handoff_view(accepted)["incomplete"]


def test_legacy_phases_and_evidence_recover_without_synthetic_attempt_links():
    legacy = {
        "schema": "vrcforge.agent_handoff_graph.v1",
        "goal": "Finish ordered imports",
        "completed": [{"id": "inspect", "action": "Inspect package", "result": "Ready", "evidence": "call-inspect"}],
        "attempted": [{"id": "older-inspect", "action": "Inspect package", "outcome": "failure", "reason": "Timeout"}],
        "decisions": [],
        "blocked": [],
        "nextSteps": [
            {"id": "import", "action": "Import main outfit", "phase": 0, "dependsOn": ["inspect"]},
            {"id": "import-other", "action": "Import second outfit", "phase": 0, "dependsOn": []},
            {"id": "activate", "action": "Activate main outfit", "phase": 1, "dependsOn": []},
        ],
    }
    graph = parse_handoff_graph(json.dumps(legacy))
    assert graph["schema"] == HANDOFF_GRAPH_SCHEMA
    assert ready_task_ids(graph) == ["import", "import-other"]
    assert graph["nodes"]["older-inspect"]["status"] == "failure"
    assert "evidence" not in graph["nodes"]["older-inspect"]
    assert not any("older-inspect" in edge for edge in graph["edges"])
    assert ["activate", "depends_on", "import"] in graph["edges"]
    assert ["activate", "depends_on", "import-other"] in graph["edges"]
    assert graph["nodes"]["inspect"]["evidence"] == [{"type": "legacy", "id": "call-inspect"}]


def test_graph_redacts_properties_pointer_strings_and_relationship_notes():
    secret_key = "ghp_syntheticKeyFixture"
    path = "C:/Users/FakePerson/Documents/fixture.txt"
    token = "literal-fake-credential"
    graph = apply_handoff_edits(None, {
        "nodes": {
            "goal": node("goal", "Verify"),
            "verify": node("task", "Verify receipt", status="pending",
                           metadata=[{secret_key: "label", path: {"api_key": token}},
                                     json.dumps({"client_secret": "synthetic-encoded-credential"})]),
            "first": node("attempt", "Try one", status="failure", reason="Failed"),
            "second": node("attempt", "Try two", status="success", reason="Replied",
                           evidence=[{"type": "receipt", "id": "Bearer fake-receipt"}]),
        },
        "edges": [["verify", "belongs_to", "goal"], ["first", "attempt_of", "verify"],
                  ["second", "attempt_of", "verify"], ["first", "replaced_by", "second", "Bearer fake-reason"]],
    })
    scanner = _Redactor()
    projected = _redact_handoff_payload(serialize_handoff_graph(graph), scanner)
    assert scanner.report.secrets == 5
    assert scanner.report.paths == 1
    for item in (secret_key, path, token, "synthetic-encoded-credential", "fake-receipt", "fake-reason"):
        assert item not in projected
    assert ready_task_ids(parse_handoff_graph(projected)) == ["verify"]
