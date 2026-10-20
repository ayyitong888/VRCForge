"""Execute the actual startup function up to its first worker, offline.

AST extraction avoids constructing the dashboard's global services or touching
user storage. A sentinel stops at the first worker; no product process starts.
"""
import ast
import builtins
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dashboard_server.py"


class FirstWorkerReached(BaseException):
    pass


def execute_startup_prefix(import_hook=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_startup")
    function.decorator_list = []
    events = []
    def worker():
        events.append("worker")
        raise FirstWorkerReached()
    namespace = {
        "asyncio": SimpleNamespace(get_running_loop=lambda: "offline-loop"),
        "EVENT_BUS": SimpleNamespace(set_loop=lambda loop: events.append("event_loop")),
        "_RUNTIME_PLANNER_MODEL": SimpleNamespace(start=worker),
        "emit_log": lambda *args: events.append(("log", args)),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    original_import = builtins.__import__
    def tracked_import(name, *args, **kwargs):
        if name == "openai.resources":
            events.append("sdk_resources")
            if import_hook:
                return import_hook(name, *args, **kwargs)
            return SimpleNamespace()
        return original_import(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=tracked_import):
        try:
            namespace["on_startup"]().send(None)
        except FirstWorkerReached:
            pass
    return events


class SdkStartupTests(unittest.TestCase):
    def test_sdk_resources_are_ready_before_any_worker_starts(self):
        self.assertEqual(execute_startup_prefix(), ["event_loop", "sdk_resources", "worker"])

    def test_optional_sdk_absence_is_logged_without_blocking_app(self):
        def missing(*args, **kwargs):
            raise ModuleNotFoundError("No module named 'openai'", name="openai")
        events = execute_startup_prefix(missing)
        self.assertEqual(events[-1], "worker")
        logs = [value[1] for value in events if isinstance(value, tuple)]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0], "warn")
        self.assertIn("No module named 'openai'", logs[0][3]["error"])

    def test_sdk_import_failure_remains_visible(self):
        def broken(*args, **kwargs):
            raise KeyError("openai.resources")
        events = execute_startup_prefix(broken)
        self.assertEqual(events[-1], "worker")
        logs = [value[1] for value in events if isinstance(value, tuple)]
        self.assertEqual(len(logs), 1)
        self.assertIn("openai.resources", logs[0][3]["error"])


if __name__ == "__main__":
    unittest.main()
