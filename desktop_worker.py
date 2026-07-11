from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agent_gateway import AgentGateway, AgentGatewayError
from desktop_executor import (
    WINDOWS_DESKTOP_OPERATIONS,
    DesktopActionCancelled,
    DesktopController,
    DesktopExecutorError,
    WindowsDesktopController,
)
from desktop_overlay import WindowsDesktopActivityOverlay


class EmbeddedDesktopWorker:
    def __init__(
        self,
        gateway: AgentGateway,
        capture_dir: Path,
        *,
        on_actions_changed: Callable[[], None] | None = None,
        controller_factory: Callable[[Path], DesktopController] | None = None,
        poll_interval_seconds: float = 0.2,
        heartbeat_interval_seconds: float = 10.0,
    ) -> None:
        self.gateway = gateway
        self.capture_dir = capture_dir
        self.on_actions_changed = on_actions_changed
        self._uses_default_controller = controller_factory is None
        self.controller_factory = controller_factory or (lambda path: WindowsDesktopController(path))
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)
        self.heartbeat_interval_seconds = max(1.0, heartbeat_interval_seconds)
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._worker_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._controller: DesktopController | None = None
        self._activity_overlay: WindowsDesktopActivityOverlay | None = None
        self._bridge_id = ""
        self._bridge_credential = ""
        self._current_action_id = ""
        self._last_error = ""

    @property
    def available(self) -> bool:
        return sys.platform == "win32" and hasattr(ctypes, "WinDLL")

    def start(self) -> dict[str, Any]:
        with self._state_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return self.status()
            if not self.available and self._uses_default_controller:
                self._last_error = "The native desktop executor is available only on Windows."
                return self.status()
            self._stop_event.clear()
            self._controller = self.controller_factory(self.capture_dir)
            self._activity_overlay = WindowsDesktopActivityOverlay() if self._uses_default_controller and self.available else None
            self._register_bridge()
            self._worker_thread = threading.Thread(target=self._worker_loop, name="vrcforge-desktop-worker", daemon=True)
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="vrcforge-desktop-heartbeat", daemon=True)
            self._worker_thread.start()
            self._heartbeat_thread.start()
            return self.status()

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        self._stop_event.set()
        if self._activity_overlay is not None:
            self._activity_overlay.hide()
        for thread in (self._worker_thread, self._heartbeat_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=max(0.1, timeout))
        with self._state_lock:
            alive = [
                thread.name
                for thread in (self._worker_thread, self._heartbeat_thread)
                if thread is not None and thread.is_alive()
            ]
            if alive:
                self._last_error = "Desktop executor threads did not stop: " + ", ".join(alive)
                return {**self.status(), "stopBlocked": True, "aliveThreads": alive}
            bridge_id = self._bridge_id
            credential = self._bridge_credential
            self._worker_thread = None
            self._heartbeat_thread = None
            self._current_action_id = ""
            self._activity_overlay = None
            if bridge_id and credential:
                try:
                    self.gateway.unregister_desktop_bridge(
                        {"bridgeId": bridge_id, "bridgeCredential": credential}
                    )
                except AgentGatewayError:
                    pass
            self._bridge_id = ""
            self._bridge_credential = ""
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            overlay_info = self._activity_overlay.diagnostics() if self._activity_overlay is not None else {}
            return {
                "available": self.available,
                "running": bool(self._worker_thread and self._worker_thread.is_alive()),
                "bridgeId": self._bridge_id,
                "currentActionId": self._current_action_id,
                "nativeOverlay": bool(self._activity_overlay),
                "nativeOverlayInfo": overlay_info,
                "lastError": self._last_error,
                "operations": sorted(WINDOWS_DESKTOP_OPERATIONS),
            }

    def _register_bridge(self) -> None:
        registration = self.gateway.register_desktop_bridge(
            {
                "name": "VRCForge Windows Desktop",
                "provider": "embedded-ctypes-win32",
                "capabilities": ["computer_use", "desktop_rescue"],
                "operations": sorted(WINDOWS_DESKTOP_OPERATIONS),
            }
        )
        self._bridge_id = str(registration["bridge"]["bridgeId"])
        self._bridge_credential = str(registration["bridgeCredential"])
        self._last_error = ""

    def _credentials(self) -> tuple[str, str]:
        with self._state_lock:
            return self._bridge_id, self._bridge_credential

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.heartbeat_interval_seconds):
            bridge_id, credential = self._credentials()
            if not bridge_id or not credential:
                continue
            try:
                self.gateway.heartbeat_desktop_bridge(
                    {"bridgeId": bridge_id, "bridgeCredential": credential}
                )
            except AgentGatewayError as exc:
                with self._state_lock:
                    self._last_error = str(exc)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            bridge_id, credential = self._credentials()
            if not bridge_id or not credential:
                self._stop_event.wait(self.poll_interval_seconds)
                continue
            try:
                claim = self.gateway.claim_desktop_action(
                    {
                        "bridgeId": bridge_id,
                        "bridgeCredential": credential,
                        "actions": ["computer_use", "desktop_rescue"],
                        "claimRequestId": f"embedded-{time.monotonic_ns()}",
                    }
                )
            except AgentGatewayError as exc:
                with self._state_lock:
                    self._last_error = str(exc)
                self._stop_event.wait(self.poll_interval_seconds)
                continue
            action = claim.get("action")
            if not isinstance(action, dict):
                self._stop_event.wait(self.poll_interval_seconds)
                continue
            self._execute_claimed_action(bridge_id, credential, action)

    def _execute_claimed_action(
        self,
        bridge_id: str,
        credential: str,
        action: dict[str, Any],
    ) -> None:
        action_id = str(action.get("actionId") or "")
        with self._state_lock:
            self._current_action_id = action_id
        self._notify_changed()
        status = "completed"
        result: dict[str, Any] = {}
        error = ""
        try:
            if self._controller is None:
                raise DesktopExecutorError("Desktop controller is not initialized.")
            if self._activity_overlay is not None:
                visual_theme = str((action.get("params") or {}).get("_visualTheme") or "light")
                self._activity_overlay.show(
                    lambda: self._cancel_from_overlay(action_id),
                    theme=visual_theme,
                )
            result = self._controller.execute(
                action,
                lambda: self._stop_event.is_set() or self.gateway.desktop_action_cancel_requested(action_id),
            )
            if self.gateway.desktop_action_cancel_requested(action_id):
                status = "cancelled"
                result = {}
        except DesktopActionCancelled as exc:
            status = "cancelled"
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - claimed actions must always settle.
            status = "failed"
            error = str(exc)
        try:
            self.gateway.complete_desktop_action(
                {
                    "bridgeId": bridge_id,
                    "bridgeCredential": credential,
                    "actionId": action_id,
                    "status": status,
                    "result": result,
                    "error": error,
                }
            )
            with self._state_lock:
                self._last_error = error if status == "failed" else ""
        except AgentGatewayError as exc:
            with self._state_lock:
                self._last_error = str(exc)
        finally:
            if self._activity_overlay is not None:
                self._activity_overlay.hide()
            with self._state_lock:
                self._current_action_id = ""
            self._notify_changed()

    def _cancel_from_overlay(self, action_id: str) -> None:
        try:
            self.gateway.request_desktop_action_cancel(
                action_id,
                {"reason": "User pressed Ctrl+Shift+F12."},
            )
        except AgentGatewayError:
            pass
        self._notify_changed()

    def _notify_changed(self) -> None:
        if self.on_actions_changed is None:
            return
        try:
            self.on_actions_changed()
        except Exception:
            pass


def desktop_executor_enabled() -> bool:
    value = os.environ.get("VRCFORGE_DESKTOP_EXECUTOR", "").strip().lower()
    if value in {"0", "false", "off", "disabled"}:
        return False
    if value in {"1", "true", "on", "enabled"}:
        return True
    return sys.platform == "win32" and "pytest" not in sys.modules
