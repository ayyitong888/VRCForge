"""Execute the real async route bodies with slow synchronous service ports."""
import ast
import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def load_route(name, namespace):
    tree = ast.parse((Path(__file__).resolve().parents[1] / "dashboard_server.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "dashboard_server.py", "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("operation", ["cancel", "steer", "enqueue"])
def test_runtime_mutation_does_not_block_event_loop(operation):
    def slow_result(*args, **kwargs):
        time.sleep(0.15)
        return {"ok": True, "accepted": True}

    async def exercise():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.005)

        gateway = SimpleNamespace(request_runtime_cancel=slow_result, runtime_runs=SimpleNamespace(list_runs=lambda **kwargs: {}))
        queue = SimpleNamespace(submit_runtime_steer=slow_result, enqueue_runtime_followup=slow_result)
        namespace = {"asyncio": asyncio, "AGENT_GATEWAY": gateway, "RUNTIME_QUEUE_PORT": queue,
                     "EVENT_BUS": SimpleNamespace(broadcast=AsyncMock()), "AgentGatewayError": RuntimeError}
        route = load_route("app_agent_runtime_cancel" if operation == "cancel" else "app_agent_runtime_queue", namespace)
        request = SimpleNamespace(session_id="session", turn_id="turn", client_turn_id="cid", reason="user stop",
                                  lane_id="session", target_client_turn_id="owner" if operation == "steer" else "",
                                  message="continue", attachments=[], project_type="general", project_path="", project_root="",
                                  provider="fixture", provider_label="fixture", plan_mode=False, model="fixture")
        task = asyncio.create_task(heartbeat())
        try:
            await asyncio.sleep(0)
            before = ticks
            result = await route(request)
            assert result["ok"] is True
            assert ticks - before >= 3, f"{operation} blocked the event loop"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(exercise())
