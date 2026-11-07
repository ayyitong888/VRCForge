"""Exercise the app-owned continuation replay without opening a user profile."""
import ast
import asyncio
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace


def test_interrupted_sub_agent_notices_are_written_off_the_event_loop_in_order():
    source = Path(__file__).resolve().parents[1] / "dashboard_server.py"
    module = ast.parse(source.read_text(encoding="utf-8-sig"))
    replay = next(node for node in module.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "replay_sub_agent_parent_continuations")
    event_loop_thread = get_ident()
    calls = []
    notices = []

    def record_notice(**notice):
        notices.append((get_ident(), notice))
        calls.append(notice["owned_id"])

    def replay_pending():
        calls.append("pending")
        return 1

    namespace = {
        "asyncio": asyncio,
        "SUB_AGENT_COLLABORATION": SimpleNamespace(
            interrupted_parent_continuations=lambda: [
                {"subAgentTaskId": "first", "taskSeed": {"sessionId": "chat"}},
                {"subAgentTaskId": "second"},
            ],
            replay_parent_continuations=replay_pending,
        ),
        "AGENT_GATEWAY": SimpleNamespace(
            record_interrupted_runtime_task=record_notice,
            reconcile_runtime_shell_continuations=lambda: {"delivered": 0},
            questions=SimpleNamespace(reconcile_runtime_continuations=lambda: None),
        ),
        "emit_log": lambda *args: None,
    }
    exec(compile(ast.Module(body=[replay], type_ignores=[]), str(source), "exec"), namespace)
    assert asyncio.run(namespace["replay_sub_agent_parent_continuations"]()) == 1
    assert calls == ["first", "second", "pending"]
    assert all(thread != event_loop_thread for thread, _ in notices)
    assert [notice["task_seed"] for _, notice in notices] == [{"sessionId": "chat"}, None]
