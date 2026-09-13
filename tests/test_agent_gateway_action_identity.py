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
        and ast.unparse(node.test) == "action_kind == 'skill' and (not completion_requirement)"
    )
    siblings = next(value for node in ast.walk(tree) for _, value in ast.iter_fields(node) if isinstance(value, list) and registration in value)
    record = siblings[siblings.index(registration) + 1]
    code = compile(ast.fix_missing_locations(ast.Module(body=[registration, record], type_ignores=[])), "gateway_registration", "exec")
    tool = "vrcforge_get_compile_errors"
    planned = {}
    executed = {"projectPath": "C:/bound/project"}
    loop = AgentTaskLoop("compile", session_id="identity-test")
    planned_id = canonical_action_id("skill", tool, planned)
    if explicit:
        loop.require_action(kind="skill", tool=tool, arguments=planned)
    scope = {
        "action_kind": "skill", "completion_requirement": {"arguments": {}} if explicit else {},
        "planned_arguments": planned, "action_arguments": executed,
        "step_tool": tool, "task_record_tool": "entrypoint" if entrypoint else tool,
        "task_loop": loop, "planned_action_id": planned_id, "plan": {},
        "step_payload": {"outcome": {"status": "ok", "success": True}},
        "ensure_dict": lambda value: value if isinstance(value, dict) else {},
    }
    exec(code, scope)
    requirements = loop.snapshot()["requirements"]
    assert len(requirements) == 1
    assert requirements[0]["actionId"] == scope["task_action"]["actionId"]
    if not entrypoint:
        assert scope["task_action"]["actionId"] == planned_id
