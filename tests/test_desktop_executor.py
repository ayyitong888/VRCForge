from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from agent_gateway import AgentGateway
from desktop_executor import DesktopActionCancelled
from desktop_worker import EmbeddedDesktopWorker


class FakeDesktopController:
    def __init__(self, _capture_dir: Path) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, action: dict[str, Any], cancel_check) -> dict[str, Any]:
        self.calls.append(action)
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
        if params.get("operation") == "wait_for_cancel":
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if cancel_check():
                    raise DesktopActionCancelled("cancelled by test")
                time.sleep(0.01)
            raise AssertionError("cancel signal was not observed")
        return {
            "operation": params.get("operation"),
            "echo": params.get("value"),
            "summary": "fake desktop action completed",
        }


class DesktopExecutorTests(unittest.TestCase):
    def test_embedded_worker_executes_and_returns_full_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            controller = FakeDesktopController(root / "captures")
            worker = EmbeddedDesktopWorker(
                gateway,
                root / "captures",
                controller_factory=lambda _path: controller,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            try:
                result = gateway.request_desktop_action_and_wait(
                    {
                        "action": "computer_use",
                        "prompt": "execute fake operation",
                        "params": {"operation": "echo", "value": "round-trip"},
                        "waitTimeoutMs": 3000,
                    }
                )
            finally:
                worker.stop()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["operation"], "echo")
        self.assertEqual(result["result"]["echo"], "round-trip")
        self.assertEqual(controller.calls[0]["params"]["value"], "round-trip")

    def test_embedded_worker_observes_broker_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            worker = EmbeddedDesktopWorker(
                gateway,
                root / "captures",
                controller_factory=FakeDesktopController,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            try:
                requested = gateway.request_desktop_action(
                    {
                        "action": "computer_use",
                        "prompt": "wait until cancelled",
                        "params": {"operation": "wait_for_cancel"},
                    }
                )
                action_id = requested["actionId"]
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    row = gateway._desktop_action_rows_by_id().get(action_id)  # noqa: SLF001
                    if row and row.get("status") == "claimed":
                        break
                    time.sleep(0.01)
                gateway.request_desktop_action_cancel(action_id, {"reason": "test cancellation"})
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    row = gateway._desktop_action_rows_by_id().get(action_id)  # noqa: SLF001
                    if row and row.get("status") == "cancelled":
                        break
                    time.sleep(0.01)
            finally:
                worker.stop()

        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "cancelled")
        self.assertIn("cancelled", str(row.get("error") or ""))

    def test_worker_stop_unregisters_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            worker = EmbeddedDesktopWorker(
                gateway,
                root / "captures",
                controller_factory=FakeDesktopController,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            self.assertTrue(gateway.desktop_bridge_status()["connected"])
            worker.stop()
            self.assertFalse(gateway.desktop_bridge_status()["connected"])

    def test_interactive_text_and_bridge_credential_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            config = gateway.ensure_config()
            config.enabled = True
            config.execution_mode = "auto"
            gateway.save_config(config)
            gateway.register_tool(
                "vrcforge_agent_desktop_action",
                "test desktop action",
                "supervised-write",
                gateway.request_desktop_action_and_wait,
                write=True,
            )
            worker = EmbeddedDesktopWorker(
                gateway,
                root / "captures",
                controller_factory=FakeDesktopController,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            credential = worker._bridge_credential  # noqa: SLF001
            secret_text = "DESKTOP_SECRET_TEXT_78421"
            try:
                outcome = gateway.call_tool(
                    "vrcforge_agent_desktop_action",
                    {
                        "action": "computer_use",
                        "prompt": "type the supplied value",
                        "params": {"operation": "type_text", "text": secret_text},
                        "waitTimeoutMs": 3000,
                    },
                    agent_name="test-agent",
                )
                result = outcome["result"]
            finally:
                worker.stop()
            action_log = gateway.desktop_action_log_path.read_text(encoding="utf-8")
            bridge_log = gateway.desktop_bridge_log_path.read_text(encoding="utf-8")
            tool_audit_log = gateway.audit_log_path.read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed")
        self.assertNotIn(secret_text, action_log)
        self.assertNotIn(credential, bridge_log)
        self.assertNotIn(secret_text, tool_audit_log)
        self.assertIn(f'"textLength": {len(secret_text)}', tool_audit_log)


if __name__ == "__main__":
    unittest.main()
