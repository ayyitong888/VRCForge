"""Internal result paging is an observation, not an implicit task obligation."""
import json
import re

from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
from agent_task_loop import AgentTaskLoop


def test_failed_optional_result_page_does_not_poison_later_evidence(tmp_path):
    refs = []

    def read_page(pointer):
        def reply(request):
            def visit(value):
                if isinstance(value, dict):
                    if str(value.get("resultRef", "")).startswith("result_"):
                        refs.append(value["resultRef"])
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)
            if not refs:
                for message in request["messages"]:
                    if message["role"] == "tool":
                        visit(json.loads(message["content"]))
                        refs.extend(re.findall(r"result_[0-9a-f]{32}", message["content"]))
            assert refs, "The model must receive an actual current-turn reference"
            return call(pointer, "vrcforge_read_tool_result", {
                "resultRef": refs[0], "jsonPointer": pointer, "limit": 1,
            })
        return reply

    gateway, model, _ = setup_gateway(tmp_path, [
        call("inspect", "vrcforge_get_gameobject", {"gameObjectPath": "Avatar"}),
        read_page("/controls"),
        read_page("/wardrobeCandidates/0/controls"),
        finish,
    ])
    gateway.register_tool(
        "vrcforge_get_gameobject", "When to use: inspect. When NOT to use: write.",
        "plan/preview", lambda args: {
            "ok": True, "wardrobeCandidates": [{"controls": [
                {"name": "Outfit" + str(i), "description": "evidence " * 200}
                for i in range(30)
            ]}],
        },
    )
    result = run(gateway, tmp_path)
    readers = [s for s in result["steps"] if s.get("tool") == "vrcforge_read_tool_result"]
    assert [s["status"] for s in readers] == ["failed", "executed"], json.dumps({
        "steps": [(s.get("tool"), s.get("status")) for s in result["steps"]],
        "plan": {k: result["plan"].get(k) for k in ("nextStep", "reply", "plannerFailure")},
    })
    assert "Requested result field is unavailable" in json.dumps(readers[0])
    assert result["plan"]["nextStep"] == "done"
    assert result["plan"]["task"]["status"] == "completed"
    assert len(model.requests) == 4


def test_explicitly_required_reader_failure_still_blocks_completion():
    loop = AgentTaskLoop("Read the required evidence")
    args = {"resultRef": "result_" + "a" * 32, "jsonPointer": "/controls"}
    loop.require_action(kind="skill", tool="vrcforge_read_tool_result", arguments=args)
    loop.record_action(
        kind="skill", tool="vrcforge_read_tool_result", arguments=args,
        raw_result={"ok": False, "error": "Requested result field is unavailable"},
        outcome={"status": "failed", "summary": "Requested result field is unavailable"},
        native_read_observation=True,
    )
    gated = loop.gate_terminal({
        "planner": "llm", "nextStep": "done", "reply": "Checked",
        "completionClaim": {"satisfied": True},
    })
    assert gated["nextStep"] == "tool_failed"
