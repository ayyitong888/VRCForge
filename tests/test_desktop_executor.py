from __future__ import annotations

import struct
import tempfile
import sys
import time
import unittest
from pathlib import Path
from typing import Any

from agent_gateway import AgentGateway
from desktop_executor import DesktopActionCancelled, DesktopExecutorError, WindowsDesktopController
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
    @unittest.skipUnless(sys.platform == "win32", "Windows desktop controller requires Win32")
    def test_windows_controller_initializes_uia_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = WindowsDesktopController(Path(temp_dir))

        self.assertTrue(hasattr(controller, "uia"))

    def test_uia_element_action_returns_bounded_metadata_without_value(self) -> None:
        class FakeUia:
            def execute(self, request: dict[str, Any]) -> dict[str, Any]:
                self.request = request
                return {
                    "ok": True,
                    "performed": "set_value",
                    "element": {"index": 7, "name": "Composer", "controlType": "ControlType.Edit"},
                }

        controller = object.__new__(WindowsDesktopController)
        controller.uia = FakeUia()
        controller._resolve_window = lambda _params: {"windowHandle": 42}
        result = controller._uia_action(
            {"operation": "set_value", "elementIndex": 7, "value": "private text"},
            {},
            lambda: False,
        )

        self.assertEqual(result["performed"], "set_value")
        self.assertEqual(result["characterCount"], len("private text"))
        self.assertNotIn("value", result)

    def test_sequence_validates_every_step_before_side_effects(self) -> None:
        class SequenceProbe(WindowsDesktopController):
            def __init__(self) -> None:
                self.effects: list[str] = []

            def _validate_operation_params(self, operation: str, params: dict[str, Any]) -> None:
                if params.get("invalid"):
                    raise DesktopExecutorError("invalid later step")

            def _execute_operation(self, operation, params, action, cancel_check):
                if operation == "sequence":
                    return super()._execute_operation(operation, params, action, cancel_check)
                self.effects.append(operation)
                return {"operation": operation}

        controller = SequenceProbe()
        with self.assertRaisesRegex(DesktopExecutorError, "invalid later step"):
            controller.execute(
                {
                    "action": "computer_use",
                    "params": {
                        "operation": "sequence",
                        "steps": [
                            {"operation": "click", "x": 1, "y": 1},
                            {"operation": "type_text", "text": "x", "invalid": True},
                        ],
                    },
                },
                lambda: False,
            )
        self.assertEqual(controller.effects, [])

    def test_ambiguous_window_title_requires_a_precise_target(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._enumerate_windows = lambda **_kwargs: [
            {"windowHandle": 1, "title": "Editor - A", "processId": 10},
            {"windowHandle": 2, "title": "Editor - B", "processId": 20},
        ]
        with self.assertRaisesRegex(DesktopExecutorError, "ambiguous"):
            controller._resolve_window({"titleContains": "Editor"})
        selected = controller._resolve_window({"titleContains": "Editor", "processId": 20})
        self.assertEqual(selected["windowHandle"], 2)

    def test_desktop_screenshot_is_analyzed_and_observed_by_planner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_dir = root / "audit" / "desktop-captures"
            capture_dir.mkdir(parents=True)
            pixels = bytes((0, 0, 255, 0, 0, 255, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0))
            dib = struct.pack("<IiiHHIIiiII", 40, 2, -2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
            bmp = struct.pack("<2sIHHI", b"BM", 14 + len(dib) + len(pixels), 0, 0, 14 + len(dib)) + dib + pixels
            path = capture_dir / "screen.bmp"
            path.write_bytes(bmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            seen: list[dict[str, Any]] = []

            def analyze(_message: str, images: list[dict[str, Any]]) -> dict[str, Any]:
                seen.extend(images)
                return {"status": "analyzed", "text": "A settings window is visible."}

            gateway.vision_analyze_fn = analyze
            result = {"status": "completed", "result": {"operation": "screenshot", "artifactPath": str(path), "width": 2, "height": 2}}
            vision = gateway._desktop_action_vision_analysis("inspect settings", result)  # noqa: SLF001
            observation = gateway._llm_loop_step_observation(  # noqa: SLF001
                {"tool": "vrcforge_agent_desktop_action", "result": result, "desktopVision": vision}
            )

        self.assertTrue(seen[0]["dataUrl"].startswith("data:image/png;base64,"))
        self.assertIn("operation=screenshot", observation)
        self.assertIn("A settings window is visible", observation)

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
