from agent_handoff_folding import fold_completed_subtrees


def task(status):
    return {"kind": "task", "text": status, "status": status}


def test_completed_branch_retains_original_receipt_and_preserves_parallel_work():
    graph = {
        "nodes": {
            "goal": {"kind": "goal", "text": "Finish both exports"},
            "bundle": task("done"), "compile": task("done"), "verify": task("pending"), "fix": task("in_progress"),
            "old": {"kind": "attempt", "status": "failure", "reason": "timeout"},
            "new": {"kind": "attempt", "status": "success", "reason": "retry returned ok",
                    "evidence": [{"type": "receipt", "id": "run-17"}]},
            "still-relevant": {"kind": "attempt", "status": "failure", "reason": "bad input"},
            "policy": {"kind": "decision", "scope": "graph", "text": "Use the approved destination"},
            "sign-off": {"kind": "issue", "status": "open", "facts": "Awaiting a signature"},
        },
        "edges": [["bundle", "belongs_to", "goal"], ["compile", "belongs_to", "bundle"],
                  ["verify", "depends_on", "compile"], ["old", "attempt_of", "compile"],
                  ["new", "attempt_of", "compile"], ["old", "replaced_by", "new", "the response timed out"],
                  ["still-relevant", "attempt_of", "fix"], ["policy", "constrains", "compile"],
                  ["compile", "blocked_by", "sign-off"]],
    }
    folded = fold_completed_subtrees(graph)
    assert set(folded["nodes"]) == {"goal", "bundle", "verify", "fix", "still-relevant", "policy", "sign-off"}
    assert folded["nodes"]["bundle"]["evidence"] == [{"type": "receipt", "id": "run-17"}]
    assert ["verify", "depends_on", "bundle"] in folded["edges"]
    assert ["policy", "constrains", "bundle"] in folded["edges"]
    assert ["bundle", "blocked_by", "sign-off"] in folded["edges"]
    assert ["still-relevant", "attempt_of", "fix"] in folded["edges"]
    assert fold_completed_subtrees(folded) == folded
    assert "compile" in graph["nodes"]


def test_contraction_does_not_create_a_new_dependency_cycle():
    graph = {
        "nodes": {"parent": task("done"), "outer": task("done"),
                  "child": {**task("done"), "evidence": [{"type": "receipt", "id": "r"}]}},
        "edges": [["child", "belongs_to", "parent"], ["outer", "depends_on", "child"],
                  ["parent", "depends_on", "outer"]],
    }
    assert fold_completed_subtrees(graph) == graph


def test_abandoned_or_shared_branch_keeps_its_prior_attempt():
    graph = {
        "nodes": {"parent": task("done"), "elsewhere": task("pending"), "abandoned": task("abandoned"),
                  "child": task("done"), "attempt": {"kind": "attempt", "status": "failure",
                      "reason": "unavailable", "evidence": [{"type": "message", "id": "old"}]}},
        "edges": [["abandoned", "belongs_to", "parent"], ["child", "belongs_to", "parent"],
                  ["child", "belongs_to", "elsewhere"], ["attempt", "attempt_of", "abandoned"]],
    }
    assert fold_completed_subtrees(graph) == graph
