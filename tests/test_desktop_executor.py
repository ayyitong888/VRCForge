from __future__ import annotations

import io
import struct
import subprocess
import tempfile
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from PIL import Image

from agent_gateway import AgentGateway
from desktop_apps import WindowsAppCatalog
from desktop_executor import DesktopActionCancelled, DesktopExecutorError, WindowsDesktopController
from desktop_operations import canonical_desktop_params
from desktop_worker import EmbeddedDesktopWorker
from runtime_planner_service import RuntimePlannerService


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
            png_buffer = io.BytesIO()
            Image.new("RGB", (2, 2), (20, 40, 60)).save(png_buffer, format="PNG")
            png_path = capture_dir / "screen.png"
            png_path.write_bytes(png_buffer.getvalue())
            gateway = AgentGateway(root / "config.json", root / "audit")
            seen: list[dict[str, Any]] = []

            def analyze(_message: str, images: list[dict[str, Any]]) -> dict[str, Any]:
                seen.extend(images)
                return {"status": "analyzed", "text": "A settings window is visible."}

            gateway.vision_analyze_fn = analyze
            for screenshot_path in (path, png_path):
                result = {"status": "completed", "result": {"operation": "screenshot", "artifactPath": str(screenshot_path), "width": 2, "height": 2}}
                vision = gateway._desktop_action_vision_analysis("inspect settings", result)  # noqa: SLF001
                self.assertEqual(vision["status"], "analyzed")
                self.assertTrue(seen[-1]["dataUrl"].startswith("data:image/png;base64,"))
            planner = object.__new__(RuntimePlannerService)
            planner._desktop = SimpleNamespace(  # type: ignore[attr-defined]  # noqa: SLF001
                summarize_action_result=gateway.desktop.desktop_action_observation
            )
            observation = planner._llm_loop_step_observation(  # noqa: SLF001
                {"tool": "vrcforge_agent_desktop_action", "result": result, "desktopVision": vision}
            )

        self.assertEqual(len(seen), 2)
        self.assertIn("operation=screenshot", observation)
        self.assertIn("A settings window is visible", observation)

    def test_embedded_worker_executes_and_returns_full_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            controller = FakeDesktopController(root / "captures")
            worker = EmbeddedDesktopWorker(
                gateway.desktop,
                root / "captures",
                controller_factory=lambda _path: controller,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            try:
                result = gateway.desktop.request_desktop_action_and_wait(
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
                gateway.desktop,
                root / "captures",
                controller_factory=FakeDesktopController,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            try:
                requested = gateway.desktop.request_desktop_action(
                    {
                        "action": "computer_use",
                        "prompt": "wait until cancelled",
                        "params": {"operation": "wait_for_cancel"},
                    }
                )
                action_id = requested["actionId"]
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    row = gateway.desktop._desktop_action_rows_by_id().get(action_id)  # noqa: SLF001
                    if row and row.get("status") == "claimed":
                        break
                    time.sleep(0.01)
                gateway.desktop.request_desktop_action_cancel(action_id, {"reason": "test cancellation"})
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    row = gateway.desktop._desktop_action_rows_by_id().get(action_id)  # noqa: SLF001
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
                gateway.desktop,
                root / "captures",
                controller_factory=FakeDesktopController,
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.05,
            )
            worker.start()
            self.assertTrue(gateway.desktop.desktop_bridge_status()["connected"])
            worker.stop()
            self.assertFalse(gateway.desktop.desktop_bridge_status()["connected"])

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
                gateway.desktop.request_desktop_action_and_wait,
                write=True,
            )
            worker = EmbeddedDesktopWorker(
                gateway.desktop,
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

    def test_inspect_observation_keeps_document_text_first_and_announces_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = AgentGateway(Path(temp_dir) / "config.json", Path(temp_dir) / "audit")
            document = "テクスチャ PNGファイルのみです。" + "本文" * 5000 + "レイヤーをコピーペーストして使ってください。"
            controls = [
                {"index": i, "name": f"element-{i}", "controlType": "Text", "enabled": True}
                for i in range(120)
            ]
            result = {
                "operation": "inspect_window",
                "count": 120,
                "controls": controls,
                "documentText": document,
                "truncated": True,
                "snapshotId": "snap-1114496-123456",
                "summary": "Inspected 120 UI Automation elements.",
            }
            observation = gateway.desktop.desktop_action_observation(result)

        self.assertLess(observation.index("documentText="), observation.index("elements="))
        self.assertIn("テクスチャ PNGファイルのみです。", observation)
        self.assertIn("snapshotId=snap-1114496-123456", observation)
        self.assertIn("truncationNotice=", observation)
        self.assertIn("documentText truncated to", observation)
        self.assertIn("read_snapshot snapshotId=snap-1114496-123456 textOffset=", observation)
        self.assertIn("elements truncated: shown 80 of 120 enumerated", observation)
        self.assertIn("UIA walk hit its limit", observation)
        self.assertIn("reads the same stable snapshot", observation)
        self.assertLessEqual(len(observation), gateway.desktop.OBSERVATION_TOTAL_BUDGET + 2000)

    def test_inspect_observation_returns_full_text_without_notice_when_it_fits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = AgentGateway(Path(temp_dir) / "config.json", Path(temp_dir) / "audit")
            result = {
                "operation": "inspect_window",
                "count": 2,
                "controls": [
                    {"index": 0, "name": "OK", "controlType": "Button"},
                    {"index": 1, "name": "Cancel", "controlType": "Button"},
                ],
                "documentText": "短い本文",
                "truncated": False,
                "summary": "Inspected 2 UI Automation elements.",
            }
            observation = gateway.desktop.desktop_action_observation(result)

        self.assertIn("documentText=短い本文", observation)
        self.assertIn('"name":"OK"', observation)
        self.assertNotIn("truncationNotice=", observation)

    def test_read_snapshot_pages_elements_and_text_from_cached_snapshot(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._uia_snapshots = {}
        elements = [{"index": i, "name": f"element-{i}"} for i in range(200)]
        snapshot_id = controller._cache_uia_snapshot(  # noqa: SLF001
            {"windowHandle": 123, "processId": 42, "title": "BOOTH"},
            elements,
            document_text="ご説明" * 4000,
            selected_text="",
            document_text_end_reached=True,
        )

        page_one = controller._read_snapshot({"snapshotId": snapshot_id}, {}, lambda: False)  # noqa: SLF001
        self.assertEqual(page_one["elementCount"], 200)
        self.assertEqual(len(page_one["elements"]), 80)
        self.assertEqual(page_one["nextElementOffset"], 80)
        self.assertEqual(page_one["documentTextLength"], 12000)
        self.assertEqual(page_one["nextTextOffset"], 9000)

        page_two = controller._read_snapshot(  # noqa: SLF001
            {"snapshotId": snapshot_id, "offset": 80, "textOffset": 9000}, {}, lambda: False
        )
        self.assertEqual(page_two["elements"][0]["index"], 80)
        self.assertEqual(page_two["documentText"], "ご説明" * 1000)
        self.assertIsNone(page_two["nextTextOffset"])

        # Snapshot paging is read-only against the cached copy: the same slice is
        # returned even though nothing live was touched in between.
        repeat = controller._read_snapshot({"snapshotId": snapshot_id, "offset": 80}, {}, lambda: False)  # noqa: SLF001
        self.assertEqual(repeat["elements"], page_two["elements"])

        with self.assertRaisesRegex(DesktopExecutorError, r"^snapshot_expired:"):
            controller._read_snapshot({"snapshotId": "snap-123-1"}, {}, lambda: False)  # noqa: SLF001

        # LRU eviction must surface the same explicit snapshot_expired code instead
        # of an empty completed page.
        controller._uia_snapshots.clear()
        with self.assertRaisesRegex(DesktopExecutorError, r"^snapshot_expired:"):
            controller._read_snapshot({"snapshotId": snapshot_id}, {}, lambda: False)  # noqa: SLF001

    def test_read_snapshot_continues_live_text_into_the_same_snapshot(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._uia_snapshots = {}
        full_document = "先頭" * 4000 + "続報" * 1000 + "[END]"  # 8000 + 2005 chars
        with tempfile.TemporaryDirectory() as temp_dir:
            controller.capture_dir = Path(temp_dir)
            snapshot_id = controller._cache_uia_snapshot(  # noqa: SLF001
                {"windowHandle": 7, "processId": 9, "title": "large page"},
                [],
                document_text=full_document[:8000],  # captured prefix, more exists
                document_text_end_reached=False,
            )
            read_calls: list[dict[str, Any]] = []

            class ContinuingUia:
                def read_text(self, window_handle, *, text_offset, text_limit, cancel_check=None):
                    read_calls.append({"window_handle": window_handle, "text_offset": text_offset})
                    return {
                        "text": full_document[text_offset : text_offset + text_limit],
                        "endReached": True,
                    }

            controller.uia = ContinuingUia()

            page = controller._read_snapshot(  # noqa: SLF001
                {"snapshotId": snapshot_id, "textOffset": 9000, "textLimit": 1200}, {}, lambda: False
            )
            with Path(page["documentArtifactPath"]).open(encoding="utf-8", newline="") as handle:
                artifact = handle.read()

        self.assertEqual(artifact, controller._uia_snapshots[7]["documentText"], "the artifact is extended byte-faithfully")  # noqa: SLF001
        self.assertEqual(read_calls, [{"window_handle": 7, "text_offset": 8000 - 64}],
                         "continuation re-reads the captured tail seam before appending")
        self.assertEqual(page["documentTextLength"], len(full_document))
        self.assertTrue(page["documentText"].endswith("[END]"))
        self.assertIs(page["documentTextEndReached"], True, "live continuation reaches and reports the true end")
        snapshot = controller._uia_snapshots[7]  # noqa: SLF001
        self.assertEqual(snapshot["documentText"], full_document, "the seam match appends without duplication")
        self.assertIs(snapshot["documentTextEndReached"], True)

    def test_read_snapshot_refuses_append_when_the_live_seam_changed(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._uia_snapshots = {}
        captured = "固定" * 100  # 200 chars captured
        live_changed = "別物" * 100 + "追加" * 50

        class DriftingUia:
            def read_text(self, _window_handle, *, text_offset, text_limit, cancel_check=None):
                return {"text": live_changed[text_offset : text_offset + text_limit], "endReached": True}

        controller.uia = DriftingUia()
        snapshot_id = controller._cache_uia_snapshot(  # noqa: SLF001
            {"windowHandle": 7, "processId": 9, "title": "drifting"},
            [],
            document_text=captured,
            document_text_end_reached=False,
        )
        page = controller._read_snapshot(  # noqa: SLF001
            {"snapshotId": snapshot_id, "textOffset": 0, "textLimit": 500}, {}, lambda: False
        )
        self.assertEqual(page["documentText"], captured, "a changed seam never appends into the snapshot")
        self.assertEqual(controller._uia_snapshots[7]["documentText"], captured)  # noqa: SLF001
        self.assertEqual(page["liveTextContinuation"], "stale")
        self.assertIn("snapshot is no longer valid", page["liveTextContinuationDetail"])
        self.assertIn("re-run inspect_window", page["liveTextContinuationDetail"])
        self.assertIs(page["documentTextEndReached"], False)

    def test_read_snapshot_live_continuation_failure_keeps_capture_and_says_so(self) -> None:
        from desktop_uia import DesktopUiaError

        controller = object.__new__(WindowsDesktopController)
        controller._uia_snapshots = {}

        class FailingUia:
            def read_text(self, *_args, **_kwargs):
                raise DesktopUiaError("window is gone")

        controller.uia = FailingUia()
        snapshot_id = controller._cache_uia_snapshot(  # noqa: SLF001
            {"windowHandle": 7, "processId": 9, "title": "closed"},
            [],
            document_text="既存" * 10,
            document_text_end_reached=False,
        )
        page = controller._read_snapshot(  # noqa: SLF001
            {"snapshotId": snapshot_id, "textOffset": 0, "textLimit": 500}, {}, lambda: False
        )
        self.assertEqual(page["documentText"], "既存" * 10, "captured text is still served")
        self.assertIs(page["documentTextEndReached"], False, "a failed continuation is never reported as finished")
        self.assertEqual(page["liveTextContinuation"], "unavailable")
        self.assertIn("could not be read", page["liveTextContinuationDetail"])

    def test_inspect_window_passes_text_limit_and_carries_end_of_document_flag(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller.user32 = SimpleNamespace(EnumChildWindows=lambda *_args: True)
        controller._uia_snapshots = {}
        captured: dict[str, Any] = {}

        class FakeUia:
            def inspect(self, window_handle: int, *, limit: int, text_limit: int, cancel_check=None) -> dict[str, Any]:
                captured["limit"] = limit
                captured["text_limit"] = text_limit
                return {
                    "elements": [{"index": 0, "name": "body"}],
                    "documentText": "ご説明" * 3000,
                    "documentTextEndReached": False,
                    "truncated": False,
                }

        controller.uia = FakeUia()
        controller._resolve_input_window = lambda _params: {"windowHandle": 12, "processId": 3, "title": "BOOTH"}  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as temp_dir:
            controller.capture_dir = Path(temp_dir)
            result = controller._inspect_window({"textLimit": 5000}, {}, lambda: False)  # noqa: SLF001
            self.assertEqual(captured["text_limit"], 5000, "caller textLimit must reach the UIA helper")
            self.assertIs(result["documentTextEndReached"], False, "end-of-document flag must reach the model result")
            self.assertEqual(result["documentArtifactChars"], 9000, "the full capture lands in the durable artifact")
            with Path(result["documentArtifactPath"]).open(encoding="utf-8", newline="") as handle:
                self.assertEqual(handle.read(), "ご説明" * 3000)
        snapshot = controller._uia_snapshots[12]  # noqa: SLF001
        self.assertIs(snapshot["documentTextEndReached"], False)

    def test_inspect_maps_unknown_end_flag_for_name_fallback_sources(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller.user32 = SimpleNamespace(EnumChildWindows=lambda *_args: True)
        controller._uia_snapshots = {}

        class NameFallbackUia:
            def inspect(self, _window_handle, *, limit, text_limit, cancel_check=None):
                return {
                    "elements": [{"index": 0, "name": "edit content"}],
                    "documentText": "edit content",
                    "documentTextEndReached": "unknown",
                    "truncated": False,
                }

        controller.uia = NameFallbackUia()
        controller._resolve_input_window = lambda _params: {"windowHandle": 12, "processId": 3, "title": "Edit"}  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as temp_dir:
            controller.capture_dir = Path(temp_dir)
            result = controller._inspect_window({}, {}, lambda: False)  # noqa: SLF001

        self.assertIsNone(result["documentTextEndReached"], "a name-fallback source reports unknown, never finished")
        self.assertIsNone(controller._uia_snapshots[12]["documentTextEndReached"])  # noqa: SLF001

    def test_read_snapshot_propagates_unknown_end_flag(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._uia_snapshots = {}

        class UnreadableUia:
            def read_text(self, *_args, **_kwargs):
                return {"text": "", "endReached": "unknown"}

        controller.uia = UnreadableUia()
        snapshot_id = controller._cache_uia_snapshot(  # noqa: SLF001
            {"windowHandle": 11, "processId": 1, "title": "edit window"},
            [],
            document_text="既知" * 50,
            document_text_end_reached=None,
        )
        page = controller._read_snapshot(  # noqa: SLF001
            {"snapshotId": snapshot_id, "textOffset": 0, "textLimit": 5000}, {}, lambda: False
        )
        self.assertIsNone(page["documentTextEndReached"], "unknown propagates; the model must not see finished")
        self.assertEqual(page["liveTextContinuation"], "unavailable")

    def test_read_snapshot_reports_whether_document_reading_is_finished(self) -> None:
        controller = object.__new__(WindowsDesktopController)
        controller._uia_snapshots = {}

        class EmptyUia:
            def read_text(self, *_args, **_kwargs):
                return {"text": "", "endReached": False}

        controller.uia = EmptyUia()
        incomplete_id = controller._cache_uia_snapshot(  # noqa: SLF001
            {"windowHandle": 11, "processId": 1, "title": "large"},
            [],
            document_text="ご説明" * 100,
            document_text_end_reached=False,
        )
        complete_id = controller._cache_uia_snapshot(  # noqa: SLF001
            {"windowHandle": 12, "processId": 1, "title": "small"},
            [],
            document_text="ご説明" * 100,
            document_text_end_reached=True,
        )

        # Nothing more is readable: the incomplete capture still must not
        # pretend the document ended.
        tail = controller._read_snapshot(  # noqa: SLF001
            {"snapshotId": incomplete_id, "textOffset": 250}, {}, lambda: False
        )
        self.assertIsNone(tail["nextTextOffset"])
        self.assertIs(tail["documentTextEndReached"], False, "an incomplete capture is never reported as finished")

        finished = controller._read_snapshot(  # noqa: SLF001
            {"snapshotId": complete_id, "textOffset": 250}, {}, lambda: False
        )
        self.assertIs(finished["documentTextEndReached"], True, "a proved-complete document at its tail reports finished")

    def test_observation_distinguishes_snapshot_paging_from_unfinished_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = AgentGateway(Path(temp_dir) / "config.json", Path(temp_dir) / "audit")
            unfinished = gateway.desktop.desktop_action_observation({
                "operation": "inspect_window",
                "count": 1,
                "controls": [{"index": 0, "name": "body", "controlType": "Text"}],
                "documentText": "短いが続きがある本文",
                "documentTextEndReached": False,
                "snapshotId": "snap-1-1",
                "documentArtifactPath": r"E:\audit\desktop-captures\documents\snap-1-1.txt",
                "documentArtifactChars": 9000,
                "summary": "Inspected 1 UI Automation elements.",
            })
            finished = gateway.desktop.desktop_action_observation({
                "operation": "inspect_window",
                "count": 1,
                "controls": [{"index": 0, "name": "body", "controlType": "Text"}],
                "documentText": "短くて完結な本文",
                "documentTextEndReached": True,
                "snapshotId": "snap-1-1",
                "documentArtifactPath": r"E:\audit\desktop-captures\documents\snap-1-1.txt",
                "documentArtifactChars": 8,
                "summary": "Inspected 1 UI Automation elements.",
            })
            paged = gateway.desktop.desktop_action_observation({
                "operation": "read_snapshot",
                "snapshotId": "snap-1-1",
                "documentText": "続きの本文",
                "documentTextLength": 20000,
                "nextTextOffset": 9000,
                "documentTextEndReached": False,
                "summary": "Snapshot page.",
            })
            unknown_source = gateway.desktop.desktop_action_observation({
                "operation": "inspect_window",
                "count": 1,
                "controls": [{"index": 0, "name": "edit content", "controlType": "Edit"}],
                "documentText": "要素名から読んだ本文",
                "documentTextEndReached": None,
                "snapshotId": "snap-1-1",
                "summary": "Inspected 1 UI Automation elements.",
            })
            unreadable = gateway.desktop.desktop_action_observation({
                "operation": "inspect_window",
                "count": 1,
                "controls": [{"index": 0, "name": "picture", "controlType": "Image"}],
                "documentText": "",
                "documentTextEndReached": None,
                "summary": "Inspected 1 UI Automation elements.",
            })

        self.assertIn("documentTextEndReached=unknown: the body text came from an element-name fallback", unknown_source)
        self.assertIn("cannot be proven", unknown_source)
        self.assertIn("documentTextEndReached=unknown: no readable body text was found in this window", unreadable)
        self.assertIn("unreadable rather than finished", unreadable)
        self.assertIn("documentTextEndReached=false: more text exists beyond this snapshot", unfinished)
        self.assertIn("continue with read_snapshot", unfinished)
        self.assertIn("full captured text saved at", unfinished)
        self.assertIn("vrcforge_read_text_file", unfinished)
        self.assertNotIn("more text exists beyond this snapshot", finished)
        self.assertNotIn("full captured text saved at", finished, "a finished read carries no artifact detour")
        self.assertIn("more text remains in this snapshot", paged)
        self.assertIn("textOffset=9000", paged)
        self.assertNotIn("beyond this snapshot", paged)

    def test_failed_desktop_action_envelope_surfaces_error_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = AgentGateway(Path(temp_dir) / "config.json", Path(temp_dir) / "audit")
            envelope = {
                "ok": False,
                "schema": "vrcforge.desktop_action.v1",
                "status": "failed",
                "actionId": "act-1",
                "result": {},
                "error": (
                    "snapshot_expired: Snapshot snap-1-1 is no longer cached (evicted or replaced); "
                    "call inspect_window again to capture a fresh snapshot and restart paging from offset 0."
                ),
            }
            observation = gateway.desktop.desktop_action_observation(envelope)

        self.assertIn("status=failed", observation)
        self.assertIn("snapshot_expired", observation)
        self.assertIn("call inspect_window again", observation)


if __name__ == "__main__":
    unittest.main()
