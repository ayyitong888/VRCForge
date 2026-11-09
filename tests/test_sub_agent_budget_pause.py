import json
from pathlib import Path

from agent_subagent_runtime import project_child_budget_pause
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry
from tests.test_sub_agent_runtime_integration import _child_gateway, call


def test_native_child_budget_pause_persists_receipts_and_resumes_same_session(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path / "runtime", [
        call("read", "vrcforge_read_text_file", {"path": "a.txt"}),
        {"role": "assistant", "content": "The saved check returned fixture-body."},
    ])
    roles = build_sub_agent_roles()
    handlers = build_sub_agent_role_handlers(gateway)
    delivered = []
    registry_path = tmp_path / "registry"
    registry = SubAgentTaskRegistry(registry_path, roles, handlers, task_finished=delivered.append)
    source = registry.create_task(
        role="llm_readonly", task="Check which file supports the conclusion.", display_name="Child",
        parent_session_id="parent", project_path=str(tmp_path / "runtime"),
        params={"maxSteps": 1, "_taskSeed": {"sessionId": "parent", "clientTurnId": "parent-turn"}},
    )["task"]
    registry._threads[source["id"]].join(5)
    paused = registry.get_task(source["id"])["task"]
    result = paused["result"]

    assert len(model.requests) == 1
    assert [read["path"] for read in invoked] == ["a.txt"]
    assert paused["status"] == "paused" and paused["error"] == ""
    assert paused["reason"] == "model_turn_budget_exhausted" and paused["canResume"]
    assert result["remainingTask"] == source["task"]
    assert result["ok"] is False
    assert result["partialEvidence"][0]["facts"]["text"] == "fixture-body"
    assert "fixture-body" in result["finalReport"]
    assert delivered[0]["status"] == "paused"
    assert delivered[0]["result"]["partialEvidence"] == result["partialEvidence"]
    assert paused["parentContinuationStatus"] == "delivered"

    reopened = SubAgentTaskRegistry(registry_path, roles, handlers, task_finished=delivered.append)
    restored = reopened.get_task(source["id"])["task"]
    assert restored["status"] == "paused" and restored["canResume"]
    assert restored["result"] == result
    resumed = reopened.resume_task(source["id"], task="Conclude using the saved file check.")["task"]
    reopened._threads[resumed["id"]].join(5)
    finished = reopened.get_task(resumed["id"])["task"]
    assert finished["status"] == "completed"
    assert finished["childSessionId"] == source["childSessionId"]
    assert len(model.requests) == 2
    assert "fixture-body" in json.dumps(model.requests[1]["messages"])
    assert [event["subAgentTaskId"] for event in delivered] == [source["id"]]


def test_existing_budget_failure_is_projected_as_paused_without_redelivery(tmp_path):
    result = {
        "ok": False, "resumable": True, "summaryText": "Old generic pause.",
        "rawResult": {
            "plan": {"nextStep": "paused", "reason": "model_turn_budget_exhausted"},
            "steps": [
                {"actionId": "read-1", "tool": "inspect", "outcome": {"status": "ok"},
                 "result": {"objectName": "Fixture", "componentCount": 1}},
                {"actionId": "read-2", "tool": "inspect", "outcome": {"status": "failed"},
                 "result": {"objectName": "Not confirmed"}},
            ],
        },
    }
    role = SubAgentRole("review", "Review", "Inspect fixtures.", handler="llm_readonly")
    delivered = []
    registry = SubAgentTaskRegistry(tmp_path, [role], {role.id: lambda *_: result},
                                    task_finished=delivered.append)
    task = registry.create_task(
        role=role.id, task="Check the remaining objects.", display_name="Child",
        parent_session_id="parent", params={"_taskSeed": {"sessionId": "parent"}},
    )["task"]
    registry._threads[task["id"]].join(3)
    old = registry.get_task(task["id"])["task"]
    assert old["status"] == "failed"
    old_files = {p: p.read_bytes() for p in Path(tmp_path).rglob("*.jsonl")}

    restored = SubAgentTaskRegistry(tmp_path, [role], {role.id: lambda *_: result},
                                    task_finished=delivered.append).get_task(task["id"])["task"]
    assert restored["status"] == "paused" and restored["canResume"]
    assert restored["error"] == ""
    assert restored["reason"] == "model_turn_budget_exhausted"
    assert restored["result"]["partialEvidence"] == [{
        "actionId": "read-1", "tool": "inspect",
        "facts": {"componentCount": 1, "objectName": "Fixture"}, "excerpt": False,
    }]
    assert "Not confirmed" not in restored["result"]["finalReport"]
    assert restored["parentContinuationStatus"] == "delivered"
    assert len(delivered) == 1
    assert all(p.read_bytes() == previous for p, previous in old_files.items())


def test_budget_pause_bounds_report_to_sampled_receipts():
    receipts = [
        {"actionId": str(index), "tool": "inspect", "outcome": {"status": "ok"},
         "result": {"objectName": f"Fixture {index}", "summary": "x" * 4000}}
        for index in range(40)
    ]
    pause = project_child_budget_pause({
        "plan": {"nextStep": "paused", "reason": "model_turn_budget_exhausted"},
        "steps": receipts,
    }, "Find remaining objects.")
    assert len(pause["partialEvidence"]) == 24
    assert pause["partialEvidence"][0]["actionId"] == "0"
    assert pause["partialEvidence"][-1]["actionId"] == "39"
    assert len(pause["finalReport"]) < 16_000
