"""Exercise the production startup prefix without launching background services."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("owned", [True, False])
def test_only_backend_owner_recovers_before_shell_start(owned):
    source = Path(__file__).resolve().parents[1] / "dashboard_server.py"
    module = ast.parse(source.read_text(encoding="utf-8-sig"))
    startup = next(node for node in module.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_startup")
    # Stop at the first actual worker launch; retain the preceding production
    # branch/order rather than duplicating its implementation in the fixture.
    for index, statement in enumerate(startup.body):
        if isinstance(statement, ast.Expr) and ast.unparse(statement.value) == "AGENT_GATEWAY.shell.start()":
            startup.body = startup.body[:index + 1]
            break
    else:
        pytest.fail("startup shell boundary not found")
    startup.decorator_list = []
    calls = []
    namespace = {
        "asyncio": asyncio,
        "EVENT_BUS": SimpleNamespace(set_loop=lambda loop: None),
        "_RUNTIME_PLANNER_MODEL": SimpleNamespace(start=lambda: calls.append("planner")),
        "BACKEND_OWNER_LEASE": SimpleNamespace(owned=owned),
        "AGENT_GATEWAY": SimpleNamespace(
            start_runtime_continuations=lambda: calls.append("admission"),
            runtime_runs=SimpleNamespace(recover_interrupted_runs=lambda: calls.append("recover")),
            shell=SimpleNamespace(start=lambda: calls.append("shell")),
        ),
        "emit_log": lambda *args: None,
    }
    exec(compile(ast.Module(body=[startup], type_ignores=[]), str(source), "exec"), namespace)
    asyncio.run(namespace["on_startup"]())
    assert calls == (["planner", "admission", "recover", "shell"] if owned else ["planner", "admission", "shell"])
