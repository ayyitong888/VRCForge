import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("action_kind", ["write", "shell", "skill"])
def test_gateway_emits_pairable_request_with_honest_stage(action_kind):
    tree = ast.parse(Path("agent_gateway.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for index, statement in enumerate(body):
            if not isinstance(statement, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "timeline_kind"
                for target in statement.targets
            ):
                continue
            call = body[index + 1]
            if isinstance(call, ast.Expr) and isinstance(call.value, ast.Call) and isinstance(call.value.func, ast.Name) and call.value.func.id == "append_timeline_event":
                emitted = []
                namespace = {
                    "action_kind": action_kind,
                    "planned_tool": "vrcforge_write_file",
                    "planned_action_id": "actual-action",
                    "plan": {"summary": "Prepared supervised execution"},
                    "append_timeline_event": lambda *args, **kwargs: emitted.append((args, kwargs)),
                }
                code = compile(ast.Module(body=[statement, call], type_ignores=[]), "<production-timeline-request>", "exec")
                exec(code, namespace)
                assert emitted[0][0][0] == "tool_call"
                assert emitted[0][1]["status"] == ("prepared" if action_kind in {"write", "shell"} else "started")
                return
    pytest.fail("production request timeline emission not found")
