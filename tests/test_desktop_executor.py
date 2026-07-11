from __future__ import annotations

import struct
import subprocess
import tempfile
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent_gateway import AgentGateway
from desktop_apps import WindowsAppCatalog
from desktop_executor import DesktopActionCancelled, DesktopExecutorError, WindowsDesktopController
from desktop_operations import canonical_desktop_params
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
    def test_launch_app_breaks_away_from_backend_job(self) -> None:
        catalog = WindowsAppCatalog()
        catalog.resolve_app = lambda _selector: {
            "name": "Fixture",
            "appId": r"C:\Fixture.exe",
            "launchKind": "executable",
        }

        with patch("desktop_apps.subprocess.Popen") as popen:
            catalog.launch_app("Fixture")

        flags = int(popen.call_args.kwargs["creationflags"])
        breakaway_flag = int(getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
        if not breakaway_flag:
            self.skipTest("Windows process breakaway flags are unavailable on this platform")
        self.assertTrue(flags & breakaway_flag)

    @unittest.skipUnless(sys.platform == "win32", "Windows desktop controller requires Win32")
    def test_windows_controller_initializes_uia_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = WindowsDesktopController(Path(temp_dir))

        self.assertTrue(hasattr(controller, "uia"))

    def test_uia_element_action_returns_bounded_metadata_without_value(self) -> None:
        class FakeUia:
            def execute(self, request: dict[str, Any], _cancel_check=None) -> dict[str, Any]:
                self.request = request
                return {
                    "ok": True,
                    "performed": "set_value",
                    "element": {"index": 7, "name": "Composer", "controlType": "ControlType.Edit"},
                }

        controller = object.__new__(WindowsDesktopController)
        controller.uia = FakeUia()
        window = {"windowHandle": 42, "processId": 9, "processPath": "C:/Fixture.exe"}
        controller._focus_window = lambda *_args: {"window": window}
        controller._resolve_input_window = lambda _params: window
        controller._uia_snapshots = {
            42: {
                "observedAt": time.monotonic(),
                "processId": 9,
                "processPath": "C:/Fixture.exe",
                "elements": [
                    {
                        "index": 7,
                        "name": "Composer",
                        "automationId": "MarkerInput",
                        "controlType": "ControlType.Edit",
                        "className": "TextBox",
                    }
                ],
            }
        }
        result = controller._uia_action(
            {"operation": "set_value", "elementIndex": 7, "value": "private text"},
            {},
            lambda: False,
        )

        self.assertEqual(result["performed"], "set_value")
        self.assertEqual(result["characterCount"], len("private text"))
        self.assertNotIn("value", result)

    def test_gateway_canonicalizes_desktop_aliases_before_permission_classification(self) -> None:
        for operation in ("press", "press_key", "type", "invoke", "set_element_value", "perform_secondary_action"):
            self.assertTrue(AgentGateway._desktop_action_is_interactive({"operation": operation}))  # noqa: SLF001
        self.assertTrue(AgentGateway._desktop_action_is_replay_safe({"operation": "get_window_state"}))  # noqa: SLF001
        self.assertFalse(AgentGateway._desktop_action_is_replay_safe({"operation": "drag_pointer"}))  # noqa: SLF001

    def test_window2_parameter_shape_is_normalized_once(self) -> None:
        normalized = canonical_desktop_params(
            {
                "operation": "click",
                "window": {"id": 42, "app": "C:/Fixture.exe", "processId": 9},
                "element_index": 7,
                "click_count": 2,
                "mouse_button": "right",
                "screenshot_id": "frame.png",
            }
        )

        self.assertEqual(normalized["windowHandle"], 42)
        self.assertEqual(normalized["elementIndex"], 7)
        self.assertEqual(normalized["clicks"], 2)
        self.assertEqual(normalized["button"], "right")
        self.assertEqual(normalized["screenshotId"], "frame.png")

    def test_protected_windows_key_is_rejected_before_input(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        with self.assertRaisesRegex(DesktopExecutorError, "does not allow Windows"):
            controller._key_press(
                {"operation": "key_press", "keys": "Win+r", "windowHandle": 42},
                {},
                lambda: False,
            )

    def test_official_key_chord_is_split_from_key_field(self) -> None:
        assert WindowsDesktopController._key_names_from_params({"key": "Control_L + Shift_L + period"}) == [
            "Control_L",
            "Shift_L",
            "period",
        ]
        assert WindowsDesktopController._key_names_from_params({"keys": ["Control_L+a", "Return"]}) == [
            "Control_L",
            "a",
            "Return",
        ]

    def test_protected_process_is_rejected_as_an_automation_target(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._resolve_window = lambda _params: {
            "windowHandle": 42,
            "processPath": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "className": "HwndWrapper[fixture]",
        }
        with self.assertRaisesRegex(DesktopExecutorError, "protected application"):
            controller._resolve_input_window({"windowHandle": 42})

    def test_vrcforge_self_target_is_read_only(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._resolve_window = lambda _params: {
            "windowHandle": 42,
            "processPath": r"D:\VRCForge\VRCForge.exe",
            "className": "Tauri Window",
        }

        self.assertEqual(controller._resolve_read_window({"windowHandle": 42})["windowHandle"], 42)
        with self.assertRaisesRegex(DesktopExecutorError, "cannot control or inspect VRCForge"):
            controller._resolve_input_window({"windowHandle": 42})

    def test_text_fallback_click_uses_resolved_bounds_without_reusing_element_selector(self) -> None:
        class FakeUia:
            @staticmethod
            def execute(_request, _cancel_check):
                return {
                    "performed": "keyboard_replace_required",
                    "element": {"rect": {"left": 130, "top": 240, "width": 200, "height": 40}},
                }

        controller = object.__new__(WindowsDesktopController)
        controller.uia = FakeUia()
        controller._focus_window = lambda *_args: None
        controller._resolve_input_window = lambda _params: {
            "windowHandle": 42,
            "rect": {"left": 100, "top": 200, "width": 500, "height": 400},
        }
        controller._observed_element_expectations = lambda _window, _params: {}
        calls: list[tuple[str, dict[str, Any]]] = []
        controller._click = lambda params, _action, _cancel: calls.append(("click", params)) or {}
        controller._key_press = lambda params, _action, _cancel: calls.append(("key", params)) or {}
        controller._type_text = lambda params, _action, _cancel: calls.append(("type", params)) or {}

        result = controller._uia_action(
            {"operation": "set_value", "windowHandle": 42, "elementIndex": 7, "value": "replacement"},
            {},
            lambda: False,
        )

        self.assertEqual(result["performed"], "keyboard_replace")
        self.assertNotIn("elementIndex", calls[0][1])
        self.assertEqual((calls[0][1]["x"], calls[0][1]["y"]), (130, 60))
        self.assertEqual(calls[1][1]["key"], "Control_L+a")
        self.assertEqual(calls[2][1]["text"], "replacement")

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
