from __future__ import annotations

import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_capture import DesktopCaptureError, WindowsGraphicsCapture


def fake_png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + bytes(16)


class DesktopCaptureTests(unittest.TestCase):
    def test_helper_output_is_validated_and_atomically_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "VRCForge.exe"
            helper.write_bytes(b"fixture")
            final_path = root / "capture.png"

            def run_helper(command, **_kwargs):
                partial = Path(command[command.index("--output") + 1])
                status = Path(command[command.index("--status") + 1])
                partial.write_bytes(fake_png(4, 3))
                status.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "width": 4,
                            "height": 3,
                            "sampleColorCount": 2,
                            "frameWarning": "",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)

            capture = WindowsGraphicsCapture(helper)
            with patch("desktop_capture.subprocess.run", side_effect=run_helper):
                result = capture.capture_window(42, final_path)

            self.assertTrue(final_path.is_file())
            self.assertEqual(result["captureBackend"], "windows_graphics_capture")
            self.assertTrue(result["occlusionSafe"])
            self.assertEqual((result["width"], result["height"]), (4, 3))
            self.assertFalse(list(root.glob("*.partial.png")))
            self.assertFalse(list(root.glob("*.status.json")))

    def test_timeout_removes_partial_capture_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "VRCForge.exe"
            helper.write_bytes(b"fixture")
            final_path = root / "capture.png"

            def timeout_helper(command, **_kwargs):
                Path(command[command.index("--output") + 1]).write_bytes(b"partial")
                raise subprocess.TimeoutExpired(command, 1)

            capture = WindowsGraphicsCapture(helper)
            with patch("desktop_capture.subprocess.run", side_effect=timeout_helper):
                with self.assertRaisesRegex(DesktopCaptureError, "native deadline"):
                    capture.capture_window(42, final_path)

            self.assertFalse(final_path.exists())
            self.assertFalse(list(root.glob("*.partial.png")))
            self.assertFalse(list(root.glob("*.status.json")))

    def test_helper_environment_does_not_forward_credentials(self) -> None:
        with patch.dict(
            "desktop_capture.os.environ",
            {
                "SystemRoot": r"C:\Windows",
                "TEMP": r"C:\Temp",
                "VRCFORGE_APP_SESSION_TOKEN": "secret",
                "OPENAI_API_KEY": "secret",
            },
            clear=True,
        ):
            environment = WindowsGraphicsCapture._helper_environment()

        self.assertEqual(environment["SystemRoot"], r"C:\Windows")
        self.assertNotIn("VRCFORGE_APP_SESSION_TOKEN", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)


if __name__ == "__main__":
    unittest.main()
