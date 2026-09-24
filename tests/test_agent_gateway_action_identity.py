"""Execute the production registration block against real task-loop identities."""
import ast
import inspect
import textwrap

import pytest

from agent_gateway import AgentGateway
from agent_task_loop import AgentTaskLoop, canonical_action_id


@pytest.mark.parametrize("entrypoint,explicit", [(False, False), (True, False), (False, True)])
def test_skill_registration_retains_planned_identity(entrypoint, explicit):
    tree = ast.parse(textwrap.dedent(inspect.getsource(AgentGateway._runtime_message_impl_body)))
    registration = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == (
            "action_kind == 'skill' and (not completion_requirement) "
            "and (not native_read_observation)"
        )
    )
    require_call = next(node for node in ast.walk(registration)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "require_action")
    assert ast.unparse(require_call) == (
        "task_loop.require_action(kind=action_kind, tool=task_record_tool, "
        "arguments=requirement_arguments)"
    )
    tool = "vrcforge_get_compile_errors"
    planned = {}
    executed = {"projectPath": "C:/bound/project"}
    loop = AgentTaskLoop("compile", session_id="identity-test")
    planned_id = canonical_action_id("skill", tool, planned)
    task_record_tool = "entrypoint" if entrypoint else tool
    completion_if = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and ast.unparse(node.test) == "completion_requirement"
    )
    record = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "task_action" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "record_action"
    )
    code = compile(ast.fix_missing_locations(ast.Module(
        body=[completion_if, registration, record], type_ignores=[]
    )), "gateway_registration", "exec")
    scope = {
        "action_kind": "skill",
        "completion_requirement": {"tool": tool, "arguments": {}} if explicit else {},
        "native_read_observation": False,
        "planned_tool": tool,
        "planned_arguments": planned,
        "task_record_tool": task_record_tool,
        "action_arguments": executed,
        "step_tool": tool,
        "step_payload": {"status": "executed", "result": {"ok": True}, "outcome": {"status": "ok"}},
        "action_outcome": {"status": "ok", "success": True},
        "task_loop": loop,
        "planned_action_id": planned_id,
        "plan": {},
        "ensure_dict": lambda value: value if isinstance(value, dict) else {},
        "str": str,
        "isinstance": isinstance,
        "dict": dict,
    }
    exec(code, scope)
    task_action = scope["task_action"]
    requirements = loop.snapshot()["requirements"]
    assert len(requirements) == 1
    assert requirements[0]["actionId"] == task_action["actionId"]
    if not entrypoint:
        assert task_action["actionId"] == planned_id
