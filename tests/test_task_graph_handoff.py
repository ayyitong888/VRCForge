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
from context_compaction import _Redactor, _redact_handoff_payload


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
    assert handoff_view(graph) == {"readyTaskIds": ["face"], "openIssueIds": ["lock"],
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
    assert handoff_view(initial) == {"readyTaskIds": ["review"], "openIssueIds": [],
                                     "incomplete": True, "anomaly": False}


def test_only_incomplete_graph_with_no_runnable_tasks_is_anomalous():
    graph = apply_handoff_edits(None, {
        "nodes": {
            "goal": node("goal", "Deliver"),
            "wait": node("task", "Wait for review", status="pending"),
            "review": node("issue", "Approval", status="open", facts="Await reviewer"),
        },
        "edges": [["wait", "belongs_to", "goal"], ["wait", "blocked_by", "review"]],
    })
    assert handoff_view(graph) == {"readyTaskIds": [], "openIssueIds": ["review"],
                                  "incomplete": True, "anomaly": True}
    abandoned = apply_handoff_edits(graph, {
        "nodes": {"wait": {"status": "abandoned"}, "review": {"status": "closed"}},
    })
    assert handoff_view(abandoned) == {"readyTaskIds": [], "openIssueIds": [],
                                      "incomplete": True, "anomaly": True}
    finished = apply_handoff_edits(abandoned, {
        "nodes": {"alternate": node("task", "Deliver through the alternate reviewer", status="done")},
        "edges": [["alternate", "belongs_to", "goal"]],
    })
    assert handoff_view(finished) == {"readyTaskIds": [], "openIssueIds": [],
                                     "incomplete": False, "anomaly": False}


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
