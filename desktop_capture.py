from __future__ import annotations

import json
import os
import struct
import subprocess
import threading
from pathlib import Path
from typing import Any


class DesktopCaptureError(RuntimeError):
    pass


_CAPTURE_LAUNCH_LOCK = threading.Lock()
_MAX_CAPTURE_BYTES = 128 * 1024 * 1024
_MAX_STATUS_BYTES = 64 * 1024


class WindowsGraphicsCapture:
    def __init__(self, helper_path: Path | None = None, *, timeout_seconds: float = 8.0) -> None:
        self.helper_path = helper_path
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 20.0))

    def available_helper(self) -> Path | None:
        candidates: list[Path] = []
        if self.helper_path is not None:
            candidates.append(self.helper_path)
        configured = str(os.environ.get("VRCFORGE_CAPTURE_HELPER") or "").strip()
        if configured:
            candidates.append(Path(configured))
        app_dir = str(os.environ.get("VRCFORGE_APP_DIR") or "").strip()
        if app_dir:
            candidates.append(Path(app_dir) / "VRCForge.exe")
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file() and resolved.suffix.casefold() == ".exe":
                return resolved
        return None

    def capture_window(self, window_handle: int, output_path: Path) -> dict[str, Any]:
        helper = self.available_helper()
        if helper is None:
            raise DesktopCaptureError("The packaged Windows.Graphics.Capture helper is unavailable.")
        if int(window_handle) <= 0:
            raise DesktopCaptureError("A positive window handle is required for Windows.Graphics.Capture.")
        final_path = output_path.resolve()
        if final_path.suffix.casefold() != ".png":
            raise DesktopCaptureError("Windows.Graphics.Capture output must use the .png extension.")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = final_path.with_name(f"{final_path.stem}.partial.png")
        status_path = final_path.with_name(f"{final_path.stem}.status.json")
        for path in (final_path, partial_path, status_path):
            path.unlink(missing_ok=True)
        command = [
            str(helper),
            "--vrcforge-capture-window",
            str(int(window_handle)),
            "--output",
            str(partial_path),
            "--status",
            str(status_path),
        ]
        try:
            with _CAPTURE_LAUNCH_LOCK:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=self._helper_environment(),
                    timeout=self.timeout_seconds,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
            status = self._read_status(status_path)
            if completed.returncode != 0 or status.get("ok") is not True:
                detail = str(status.get("error") or f"helper exited with code {completed.returncode}")[-1000:]
                raise DesktopCaptureError(f"Windows.Graphics.Capture failed: {detail}")
            width, height = self._validate_png(partial_path)
            if int(status.get("width") or 0) != width or int(status.get("height") or 0) != height:
                raise DesktopCaptureError("Windows.Graphics.Capture metadata does not match the PNG dimensions.")
            os.replace(partial_path, final_path)
            return {
                "format": "png",
                "width": width,
                "height": height,
                "captureBackend": "windows_graphics_capture",
                "occlusionSafe": True,
                "sampleColorCount": max(0, int(status.get("sampleColorCount") or 0)),
                "frameWarning": str(status.get("frameWarning") or ""),
            }
        except subprocess.TimeoutExpired as exc:
            raise DesktopCaptureError("Windows.Graphics.Capture exceeded its native deadline.") from exc
        finally:
            partial_path.unlink(missing_ok=True)
            status_path.unlink(missing_ok=True)

    @staticmethod
    def _helper_environment() -> dict[str, str]:
        environment: dict[str, str] = {}
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA"):
            value = str(os.environ.get(key) or "").strip()
            if value:
                environment[key] = value
        system_root = environment.get("SystemRoot") or environment.get("WINDIR") or r"C:\Windows"
        environment["PATH"] = str(Path(system_root) / "System32")
        return environment

    @staticmethod
    def _read_status(path: Path) -> dict[str, Any]:
        try:
            size = path.stat().st_size
            if size <= 0 or size > _MAX_STATUS_BYTES:
                raise DesktopCaptureError("Windows.Graphics.Capture returned invalid status metadata size.")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DesktopCaptureError("Windows.Graphics.Capture did not return valid status metadata.") from exc
        if not isinstance(payload, dict):
            raise DesktopCaptureError("Windows.Graphics.Capture status metadata must be an object.")
        return payload

    @staticmethod
    def _validate_png(path: Path) -> tuple[int, int]:
        try:
            size = path.stat().st_size
            if size < 33 or size > _MAX_CAPTURE_BYTES:
                raise DesktopCaptureError("Windows.Graphics.Capture PNG size is outside the bounded limit.")
            with path.open("rb") as handle:
                header = handle.read(24)
        except OSError as exc:
            raise DesktopCaptureError("Windows.Graphics.Capture PNG could not be read.") from exc
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise DesktopCaptureError("Windows.Graphics.Capture output is not a PNG image.")
        width, height = struct.unpack(">II", header[16:24])
        if width <= 0 or height <= 0 or width > 16384 or height > 16384 or width * height > 80_000_000:
            raise DesktopCaptureError("Windows.Graphics.Capture PNG dimensions exceed the bounded limit.")
        return width, height
