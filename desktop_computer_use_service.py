from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from desktop_executor import DesktopController
from desktop_operations import (
    DESKTOP_INTERACTIVE_OPERATIONS,
    DESKTOP_REPLAY_SAFE_OPERATIONS,
    canonical_desktop_operation,
)
from desktop_worker import DesktopActionBrokerError, EmbeddedDesktopWorker, desktop_executor_enabled


AGENT_DESKTOP_ACTION_MAX_ITEMS = 120
DESKTOP_BRIDGE_HEARTBEAT_TTL_SECONDS = 45
DESKTOP_BRIDGE_ACTION_TYPES = {"desktop_rescue", "computer_use"}
DESKTOP_ACTION_PARAMS_MAX_BYTES = 64 * 1024
DESKTOP_ACTION_RESULT_MAX_BYTES = 128 * 1024
DESKTOP_ACTION_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
DESKTOP_ACTION_CLEARABLE_FIELDS = {
    "bridgeId",
    "bridgeName",
    "provider",
    "claimRequestId",
    "claimedAt",
    "error",
    "result",
    "resultSummary",
}


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class DesktopComputerUsePorts:
    """Frozen host capabilities used by the app-lifetime Desktop owner."""

    shared_state_lock: threading.RLock
    ensure_config: Callable[[], Any]
    runtime_cancel_requested: Callable[..., bool]
    signal_background_activity: Callable[[str], None]
    has_tool: Callable[[str], bool]
    call_tool: Callable[[str, dict[str, Any], str], dict[str, Any]]
    run_vision_analysis: Callable[[str, list[dict[str, Any]]], dict[str, Any]]
    build_screenshot_attachment: Callable[[Path, Path], dict[str, Any]]
    append_jsonl: Callable[[Path, str, dict[str, Any]], dict[str, Any]]
    read_jsonl: Callable[[Path, int], list[dict[str, Any]]]
    summarize_text: Callable[[str, int], str]
    summarize_params: Callable[[Any], dict[str, Any]]
    redact_sensitive: Callable[[Any], Any]
    normalize_filesystem_path: Callable[[str], str]
    normalize_execution_mode: Callable[[Any], str]
    on_actions_changed: Callable[[], None] | None = None
    controller_factory: Callable[[Path], DesktopController] | None = None


class DesktopComputerUseService:
    """Own Desktop action state, bridge authority, turn scope and worker life."""

    def __init__(
        self,
        audit_dir: Path,
        capture_dir: Path,
        ports: DesktopComputerUsePorts,
    ) -> None:
        self.audit_dir = audit_dir
        self.capture_dir = capture_dir
        self._ports = ports
        self._lock = ports.shared_state_lock
        self._desktop_bridges: dict[str, dict[str, Any]] = {}
        self._desktop_action_payloads: dict[str, dict[str, Any]] = {}
        self._desktop_action_results: dict[str, dict[str, Any]] = {}
        self._runtime_computer_use_context = threading.local()
        self._desktop_action_condition = threading.Condition(self._lock)
        self._computer_use_turn_grants: dict[str, dict[str, str]] = {}
        self._worker = EmbeddedDesktopWorker(
            self,
            capture_dir,
            on_actions_changed=ports.on_actions_changed,
            controller_factory=ports.controller_factory,
        )

    @property
    def desktop_action_log_path(self) -> Path:
        return self.audit_dir / "desktop-actions.jsonl"

    @property
    def desktop_bridge_log_path(self) -> Path:
        return self.audit_dir / "desktop-bridges.jsonl"

    def configure_paths(self, audit_dir: Path) -> None:
        with self._lock:
            self.audit_dir = audit_dir
            self.capture_dir = audit_dir / "desktop-captures"
            self._worker.capture_dir = self.capture_dir
            self._desktop_bridges.clear()
            self._desktop_action_payloads.clear()
            self._desktop_action_results.clear()
            self._computer_use_turn_grants.clear()

    @staticmethod
    def embedded_worker_enabled() -> bool:
        return desktop_executor_enabled()

    def start_embedded_worker(self) -> dict[str, Any]:
        return self._worker.start()

    def stop_embedded_worker(self) -> dict[str, Any]:
        return self._worker.stop()

    def embedded_worker_status(self) -> dict[str, Any]:
        return self._worker.status()

    @staticmethod
    def _error(message: str, status_code: int = 400) -> DesktopActionBrokerError:
        return DesktopActionBrokerError(message, status_code)

    def _text(self, value: Any, limit: int = 240) -> str:
        return self._ports.summarize_text(str(value if value is not None else ""), limit)

    @staticmethod
    def normalize_visual_accent(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if not text.startswith("#"):
            text = "#" + text
        digits = text[1:]
        if len(digits) == 3 and all(char in "0123456789abcdef" for char in digits):
            digits = "".join(char * 2 for char in digits)
        if len(digits) == 6 and all(char in "0123456789abcdef" for char in digits):
            return "#" + digits
        return ""

    @contextmanager
    def runtime_turn_context(self, params: dict[str, Any]) -> Iterator[None]:
        context = self._runtime_computer_use_context
        previous = {
            "enabled": bool(getattr(context, "enabled", False)),
            "visual_theme": str(getattr(context, "visual_theme", "light")),
            "visual_accent": str(getattr(context, "visual_accent", "")),
            "session_id": str(getattr(context, "session_id", "")),
            "turn_id": str(getattr(context, "turn_id", "")),
            "client_turn_id": str(getattr(context, "client_turn_id", "")),
        }
        computer_use_requested = bool(params.get("_computerUseRequested"))
        if computer_use_requested:
            self.consume_computer_use_turn_grant(
                str(params.get("_computerUseGrantId") or ""),
                session_id=str(params.get("session_id") or params.get("sessionId") or ""),
                client_turn_id=str(params.get("client_turn_id") or params.get("clientTurnId") or ""),
                project_root=str(
                    params.get("projectRoot")
                    or params.get("project_root")
                    or params.get("projectPath")
                    or ""
                ),
            )
        context.enabled = computer_use_requested
        visual_theme = str(params.get("_computerUseVisualTheme") or "light").strip().lower()
        context.visual_theme = visual_theme if visual_theme in {"light", "dark"} else "light"
        context.visual_accent = self.normalize_visual_accent(params.get("_computerUseVisualAccent"))
        context.session_id = str(params.get("session_id") or params.get("sessionId") or "")
        context.turn_id = ""
        context.client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "")
        try:
            yield
        finally:
            for key, value in previous.items():
                setattr(context, key, value)

    def bind_runtime_identity(self, *, session_id: str, turn_id: str, client_turn_id: str) -> None:
        context = self._runtime_computer_use_context
        context.session_id = session_id
        context.turn_id = turn_id
        context.client_turn_id = client_turn_id

    @staticmethod
    def desktop_action_operations(params: dict[str, Any]) -> list[str]:
        operation = canonical_desktop_operation(params.get("operation"))
        operations = [operation] if operation else []
        if operation == "sequence":
            operations.extend(
                canonical_desktop_operation(step.get("operation"))
                for step in _ensure_list(params.get("steps"))
                if isinstance(step, dict)
            )
        return [item for item in operations if item]

    @classmethod
    def desktop_action_is_replay_safe(cls, params: dict[str, Any]) -> bool:
        operations = cls.desktop_action_operations(params)
        return bool(operations) and all(item in DESKTOP_REPLAY_SAFE_OPERATIONS for item in operations)

    @classmethod
    def desktop_action_is_interactive(cls, params: dict[str, Any]) -> bool:
        return any(item in DESKTOP_INTERACTIVE_OPERATIONS for item in cls.desktop_action_operations(params))

    @classmethod
    def desktop_action_params_audit(cls, params: dict[str, Any]) -> dict[str, Any]:
        operations = cls.desktop_action_operations(params)
        text_length = 0
        if isinstance(params.get("text"), str):
            text_length += len(str(params.get("text") or ""))
        if isinstance(params.get("value"), str):
            text_length += len(str(params.get("value") or ""))
        for step in _ensure_list(params.get("steps")):
            if isinstance(step, dict) and isinstance(step.get("text"), str):
                text_length += len(str(step.get("text") or ""))
            if isinstance(step, dict) and isinstance(step.get("value"), str):
                text_length += len(str(step.get("value") or ""))
        return {
            "operation": operations[0] if operations else "",
            "operations": operations[:32],
            "stepCount": len(_ensure_list(params.get("steps"))) if operations[:1] == ["sequence"] else 0,
            "textLength": text_length,
            "parameterKeys": sorted(str(key) for key in params if str(key) not in {"text", "steps"})[:32],
        }

    @classmethod
    def desktop_action_result_audit(cls, result: dict[str, Any]) -> dict[str, Any]:
        scalar_keys = {
            "operation", "count", "stepCount", "characterCount", "width", "height",
            "format", "durationMs", "clicks", "button", "repeat", "sampleColorCount",
            "frameWarning", "artifactRelativePath",
        }
        summary = {
            key: value
            for key, value in result.items()
            if key in scalar_keys and isinstance(value, (str, int, float, bool))
        }
        steps = _ensure_list(result.get("steps"))
        if steps:
            summary["steps"] = [
                {
                    "index": int(step.get("index") or index + 1),
                    "operation": str(step.get("operation") or ""),
                    "result": cls.desktop_action_result_audit(_ensure_dict(step.get("result"))),
                }
                for index, step in enumerate(steps[:32])
                if isinstance(step, dict)
            ]
        summary["resultKeys"] = sorted(str(key) for key in result)[:32]
        return summary

    @staticmethod
    def desktop_action_result_payload(value: Any) -> dict[str, Any]:
        current = _ensure_dict(value)
        for _ in range(3):
            nested = _ensure_dict(current.get("result"))
            if not nested:
                break
            current = nested
        return current

    def desktop_action_vision_analysis(self, message: str, value: Any) -> dict[str, Any] | None:
        result = self.desktop_action_result_payload(value)
        screenshot_paths: list[str] = []

        def collect(candidate: dict[str, Any]) -> None:
            if str(candidate.get("operation") or "") == "screenshot" and candidate.get("artifactPath"):
                screenshot_paths.append(str(candidate["artifactPath"]))
            if isinstance(candidate.get("screenshot"), dict):
                collect(_ensure_dict(candidate.get("screenshot")))
            for step in _ensure_list(candidate.get("steps"))[:32]:
                if isinstance(step, dict):
                    collect(_ensure_dict(step.get("result")))

        collect(result)
        if not screenshot_paths:
            return None
        try:
            attachment = self._ports.build_screenshot_attachment(
                Path(screenshot_paths[-1]),
                self.capture_dir,
            )
        except (OSError, ValueError) as exc:
            return {
                "schema": "vrcforge.vision_analysis.v1",
                "status": "error",
                "reason": "desktop_screenshot_unreadable",
                "error": self._text(exc, 300),
                "imageCount": 1,
            }
        return self._ports.run_vision_analysis(
            "Analyze this current desktop screenshot for the next action in the user's explicit Computer Use task. "
            + self._text(message, 1200),
            [attachment],
        )

    def desktop_action_observation(self, value: Any) -> str:
        result = self.desktop_action_result_payload(value)
        if not result:
            return ""
        parts: list[str] = []
        for key in (
            "operation", "summary", "count", "stepCount", "width", "height",
            "windowHandle", "x", "y", "durationMs",
        ):
            item = result.get(key)
            if item not in (None, ""):
                parts.append(f"{key}={self._text(item, 240)}")
        apps = [item for item in _ensure_list(result.get("apps")) if isinstance(item, dict)][:30]
        if apps:
            parts.append(
                "apps="
                + json.dumps(
                    [
                        {
                            "displayName": self._text(item.get("displayName") or item.get("name"), 120),
                            "id": self._text(item.get("id") or item.get("appId"), 300),
                            "isRunning": bool(item.get("isRunning")),
                            "windows": [
                                {
                                    "windowHandle": window.get("windowHandle"),
                                    "title": self._text(window.get("title"), 140),
                                    "processId": window.get("processId"),
                                }
                                for window in _ensure_list(item.get("windows"))[:6]
                                if isinstance(window, dict)
                            ],
                        }
                        for item in apps
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        windows = [item for item in _ensure_list(result.get("windows")) if isinstance(item, dict)][:12]
        if windows:
            parts.append(
                "windows="
                + json.dumps(
                    [
                        {
                            "windowHandle": item.get("windowHandle"),
                            "title": self._text(item.get("title"), 160),
                            "className": self._text(item.get("className"), 80),
                            "processId": item.get("processId"),
                            "rect": item.get("rect"),
                        }
                        for item in windows
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        controls = [item for item in _ensure_list(result.get("controls")) if isinstance(item, dict)][:80]
        if controls:
            parts.append(
                "elements="
                + json.dumps(
                    [
                        {
                            "index": item.get("index"),
                            "name": self._text(item.get("name") or item.get("title"), 120),
                            "automationId": self._text(item.get("automationId"), 100),
                            "controlType": self._text(item.get("controlType") or item.get("className"), 80),
                            "enabled": item.get("enabled"),
                            "offscreen": item.get("offscreen"),
                            "focused": item.get("focused"),
                            "rect": item.get("rect"),
                        }
                        for item in controls
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        steps = [item for item in _ensure_list(result.get("steps")) if isinstance(item, dict)][:32]
        if steps:
            step_summaries = []
            for index, step in enumerate(steps, start=1):
                nested = self.desktop_action_observation(step.get("result"))
                step_summaries.append(
                    f"{int(step.get('index') or index)}:{step.get('operation') or ''}({nested})"
                )
            parts.append("steps=" + " | ".join(step_summaries))
        for nested_key in ("accessibility", "screenshot"):
            nested = _ensure_dict(result.get(nested_key))
            if nested:
                parts.append(f"{nested_key}=({self.desktop_action_observation(nested)})")
        for key in ("selectedText", "documentText"):
            if result.get(key):
                parts.append(f"{key}={self._text(result.get(key), 1200)}")
        return self._text("; ".join(parts), 6000)

    def _append_desktop_action_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._desktop_action_condition:
            row = self._ports.append_jsonl(
                self.desktop_action_log_path,
                "vrcforge.desktop_action.v1",
                event,
            )
            self._desktop_action_condition.notify_all()
            return row

    def _desktop_action_with_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        action_id = str(row.get("actionId") or "")
        payload = self._desktop_action_payloads.get(action_id)
        return {**row, **({"params": payload} if payload is not None else {})}

    def computer_use_turn_active(self) -> bool:
        return bool(getattr(self._runtime_computer_use_context, "enabled", False))

    def computer_use_model_invocable(self, config: Any = None) -> bool:
        config = config or self._ports.ensure_config()
        return bool(
            config.developer_options_enabled
            and config.computer_use_enabled
            and self.computer_use_turn_active()
        )

    def require_computer_use_enabled(self) -> Any:
        config = self._ports.ensure_config()
        if not config.developer_options_enabled or not config.computer_use_enabled:
            raise self._error(
                "Computer Use is disabled. Enable it under Settings > Developer Options first.",
                403,
            )
        return config

    def issue_computer_use_turn_grant(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.require_computer_use_enabled()
        params = params or {}
        client_turn_id = str(params.get("clientTurnId") or params.get("client_turn_id") or "").strip()
        if not client_turn_id:
            raise self._error("clientTurnId is required for a Computer Use turn grant.", 400)
        grant_id = f"cug_{secrets.token_urlsafe(24)}"
        grant = {
            "sessionId": str(params.get("sessionId") or params.get("session_id") or "").strip(),
            "clientTurnId": client_turn_id,
            "projectRoot": str(
                params.get("projectRoot")
                or params.get("project_root")
                or params.get("projectPath")
                or ""
            ).strip(),
        }
        with self._lock:
            while len(self._computer_use_turn_grants) >= 64:
                self._computer_use_turn_grants.pop(next(iter(self._computer_use_turn_grants)))
            self._computer_use_turn_grants[grant_id] = grant
        return {"ok": True, "schema": "vrcforge.computer_use_turn_grant.v1", "grantId": grant_id}

    def consume_computer_use_turn_grant(
        self,
        grant_id: str,
        *,
        session_id: str = "",
        client_turn_id: str = "",
        project_root: str = "",
    ) -> None:
        grant_id = str(grant_id or "").strip()
        if not grant_id:
            raise self._error(
                "Computer Use requires a user-issued turn grant from + > Desktop Rescue or /desktop.",
                403,
            )
        with self._lock:
            grant = self._computer_use_turn_grants.pop(grant_id, None)
        if grant is None:
            raise self._error("Computer Use turn grant is missing, invalid, or already consumed.", 403)
        if str(grant.get("clientTurnId") or "") != str(client_turn_id or "").strip():
            raise self._error("Computer Use turn grant does not match this client turn.", 403)
        granted_session = str(grant.get("sessionId") or "").strip()
        if granted_session and granted_session != str(session_id or "").strip():
            raise self._error("Computer Use turn grant does not match this session.", 403)
        granted_project = str(grant.get("projectRoot") or "").strip()
        if granted_project and self._ports.normalize_filesystem_path(
            granted_project
        ) != self._ports.normalize_filesystem_path(project_root):
            raise self._error("Computer Use turn grant does not match this project.", 403)

    def request_turn_authorized_desktop_action_and_wait(
        self,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.require_computer_use_enabled()
        if not self.computer_use_turn_active():
            raise self._error(
                "Computer Use can only run inside a user-started + > Desktop Rescue or /desktop task.",
                403,
            )
        request_params = dict(params or {})
        context = self._runtime_computer_use_context
        session_id = str(getattr(context, "session_id", ""))
        turn_id = str(getattr(context, "turn_id", ""))
        client_turn_id = str(getattr(context, "client_turn_id", ""))
        if self._ports.runtime_cancel_requested(
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
        ):
            raise self._error(
                "Computer Use turn was cancelled before the desktop action started.",
                409,
            )
        if session_id:
            request_params.setdefault("sessionId", session_id)
        if client_turn_id:
            request_params.setdefault("clientTurnId", client_turn_id)
        action_params = dict(_ensure_dict(request_params.get("params")))
        action_params["_visualTheme"] = str(getattr(context, "visual_theme", "light"))
        action_params["_visualAccent"] = str(getattr(context, "visual_accent", ""))
        request_params["params"] = action_params
        payload = self.request_desktop_action(request_params)
        action_id = str(payload.get("actionId") or "")
        if action_id and self._ports.runtime_cancel_requested(
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
        ):
            self.request_desktop_action_cancel(
                action_id,
                {"reason": "User stopped the Computer Use turn."},
            )
        return self._wait_for_desktop_action_payload(payload, request_params)

    def request_desktop_action(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        action = re.sub(
            r"[^a-z0-9_.-]+",
            "_",
            str(params.get("action") or "").strip().lower(),
        ).strip("_")
        if action not in {"screenshot", "annotation", "browser", "desktop_rescue", "computer_use"}:
            raise self._error("Unsupported desktop action.", 400)
        self._ports.signal_background_activity("desktop_action")
        project_root = str(
            params.get("projectRoot")
            or params.get("project_root")
            or params.get("projectPath")
            or ""
        ).strip()
        session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
        client_turn_id = str(params.get("clientTurnId") or params.get("client_turn_id") or "").strip()
        prompt = self._text(params.get("prompt") or params.get("message"), 800)
        status = "requested"
        result: dict[str, Any] = {}
        error = ""
        action_id = ""
        bridge_candidates: list[dict[str, Any]] = []
        action_params = self._ports.redact_sensitive(_ensure_dict(params.get("params")))
        params_size = len(json.dumps(action_params, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if params_size > DESKTOP_ACTION_PARAMS_MAX_BYTES:
            raise self._error("Desktop action params exceed the 64 KiB limit.", 413)
        if action in DESKTOP_BRIDGE_ACTION_TYPES and self.desktop_action_is_interactive(action_params):
            config = self._ports.ensure_config()
            if self._ports.normalize_execution_mode(config.execution_mode) not in {
                "auto",
                "roslyn_full_auto",
            }:
                raise self._error(
                    "Interactive Computer Use requires Auto Approval or Full Permission. Read-only list_windows, cursor_position, screenshot, and wait remain available.",
                    403,
                )
        if action == "screenshot" and self._ports.has_tool("vrcforge_capture_screenshot"):
            try:
                result = self._ports.call_tool(
                    "vrcforge_capture_screenshot",
                    action_params,
                    "desktop-agent",
                )
                status = "executed" if result.get("ok") else "failed"
                error = str(result.get("error") or "")
            except Exception as exc:  # noqa: BLE001 - explicit actions return actionable errors.
                status = "failed"
                error = str(exc)
        elif action in DESKTOP_BRIDGE_ACTION_TYPES:
            capable = [
                bridge
                for bridge in self._live_desktop_bridges()
                if action in set(bridge.get("capabilities") or [])
            ]
            if capable:
                action_id = (
                    f"dact_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_"
                    f"{secrets.token_hex(3)}"
                )
                bridge_candidates = [
                    {
                        "bridgeId": str(bridge.get("bridgeId") or ""),
                        "name": str(bridge.get("name") or ""),
                        "provider": str(bridge.get("provider") or ""),
                    }
                    for bridge in capable[:5]
                ]
            else:
                status = "unavailable"
                error = (
                    "Desktop control bridge is not connected. Launch this action from a configured "
                    "desktop skill/provider."
                )
        else:
            status = "recorded"
        event = {
            "event": "desktop_action",
            "status": status,
            "action": action,
            "sessionId": session_id,
            "clientTurnId": client_turn_id,
            "projectRoot": project_root,
            "promptSummary": prompt,
            "paramsSummary": self.desktop_action_params_audit(action_params),
            "replaySafe": self.desktop_action_is_replay_safe(action_params),
            "controlRisk": "interactive" if self.desktop_action_is_interactive(action_params) else "read_only",
            "resultSummary": self._ports.summarize_params(result) if result else {},
            "error": error,
        }
        if action_id:
            event["actionId"] = action_id
            with self._lock:
                self._desktop_action_payloads[action_id] = action_params
        if bridge_candidates:
            event["bridgeCandidates"] = bridge_candidates
        self._append_desktop_action_event(event)
        return {
            "ok": status != "failed",
            "schema": "vrcforge.desktop_action.v1",
            "status": status,
            "action": action,
            "actionId": action_id,
            "event": self._ports.redact_sensitive(event),
            "result": self._ports.redact_sensitive(result),
            "error": error,
        }

    def request_desktop_action_and_wait(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        payload = self.request_desktop_action(params)
        return self._wait_for_desktop_action_payload(payload, params)

    def _wait_for_desktop_action_payload(
        self,
        payload: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = str(payload.get("actionId") or "")
        if payload.get("status") != "requested" or not action_id or params.get("waitForCompletion") is False:
            return payload
        timeout_ms = max(100, min(int(params.get("waitTimeoutMs") or 60_000), 120_000))
        deadline = time.monotonic() + timeout_ms / 1000
        with self._desktop_action_condition:
            while time.monotonic() < deadline:
                row = self._desktop_action_rows_by_id().get(action_id)
                status = str((row or {}).get("status") or "")
                if status in DESKTOP_ACTION_TERMINAL_STATUSES:
                    return {
                        "ok": status in {"completed", "cancelled"},
                        "schema": "vrcforge.desktop_action.v1",
                        "status": status,
                        "action": str((row or {}).get("action") or payload.get("action") or ""),
                        "actionId": action_id,
                        "event": self._ports.redact_sensitive(row or {}),
                        "result": self._ports.redact_sensitive(
                            self._desktop_action_results.get(action_id, {})
                        ),
                        "error": str((row or {}).get("error") or ""),
                    }
                self._desktop_action_condition.wait(
                    timeout=max(0.0, deadline - time.monotonic())
                )
        row = self._desktop_action_rows_by_id().get(action_id) or {}
        try:
            cancelled = self.request_desktop_action_cancel(
                action_id,
                {"reason": "Computer Use action exceeded its turn wait timeout."},
            )
            row = _ensure_dict(cancelled.get("action")) or row
        except DesktopActionBrokerError:
            pass
        return {
            **payload,
            "status": str(row.get("status") or payload.get("status") or "requested"),
            "event": self._ports.redact_sensitive(row or _ensure_dict(payload.get("event"))),
            "timedOut": True,
            "error": "Desktop action exceeded the turn wait timeout and cancellation was requested.",
        }

    def list_desktop_actions(
        self,
        *,
        limit: int = 50,
        session_id: str = "",
        project_root: str = "",
    ) -> dict[str, Any]:
        rows = self._project_desktop_action_rows(limit_events=0)
        normalized_project_root = (
            self._ports.normalize_filesystem_path(project_root) if project_root else ""
        )
        filtered = []
        for row in rows:
            if session_id and str(row.get("sessionId") or "") != session_id:
                continue
            row_project = str(row.get("projectRoot") or "").strip()
            if (
                normalized_project_root
                and row_project
                and self._ports.normalize_filesystem_path(row_project) != normalized_project_root
            ):
                continue
            filtered.append(self._ports.redact_sensitive(row))
        filtered.sort(
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            reverse=True,
        )
        filtered = filtered[: max(1, min(limit, AGENT_DESKTOP_ACTION_MAX_ITEMS))]
        active = self.list_active_desktop_actions(limit=8)
        return {
            "ok": True,
            "schema": "vrcforge.desktop_actions.v1",
            "actions": filtered,
            "count": len(filtered),
            "activeActions": active["actions"],
            "activeCount": active["count"],
        }

    def list_active_desktop_actions(self, *, limit: int = 8) -> dict[str, Any]:
        rows = [
            self._ports.redact_sensitive(row)
            for row in self._project_desktop_action_rows(limit_events=0)
            if str(row.get("action") or "") in DESKTOP_BRIDGE_ACTION_TYPES
            and str(row.get("status") or "") in {"requested", "claimed", "cancel_requested"}
        ]
        running = [
            row for row in rows if str(row.get("status") or "") in {"claimed", "cancel_requested"}
        ]
        waiting = [row for row in rows if str(row.get("status") or "") == "requested"]
        running.sort(
            key=lambda item: (
                0 if str(item.get("status") or "") == "cancel_requested" else 1,
                str(item.get("updatedAt") or item.get("createdAt") or ""),
            )
        )
        waiting.sort(key=lambda item: str(item.get("createdAt") or item.get("updatedAt") or ""))
        active = (running + waiting)[: max(1, min(limit, 32))]
        return {
            "ok": True,
            "schema": "vrcforge.desktop_active_actions.v1",
            "actions": active,
            "count": len(active),
        }

    def get_desktop_action_result(self, action_id: str) -> dict[str, Any]:
        action_id = str(action_id or "").strip()
        if not action_id:
            raise self._error("Desktop action id is required.", 400)
        row = self._desktop_action_rows_by_id().get(action_id)
        if row is None:
            raise self._error("Unknown desktop action id.", 404)
        result = self._desktop_action_results.get(action_id)
        return {
            "ok": True,
            "schema": "vrcforge.desktop_action_result.v1",
            "action": self._ports.redact_sensitive(row),
            "resultAvailable": result is not None,
            "result": self._ports.redact_sensitive(result or {}),
        }

    def _project_desktop_action_rows(self, *, limit_events: int = 1000) -> list[dict[str, Any]]:
        """Merge lifecycle events sharing an actionId into one row; legacy rows pass through."""
        events = self._ports.read_jsonl(self.desktop_action_log_path, limit_events)
        rows: list[dict[str, Any]] = []
        by_action: dict[str, dict[str, Any]] = {}
        for event in events:
            action_id = str(event.get("actionId") or "").strip()
            if not action_id:
                rows.append(dict(event))
                continue
            row = by_action.get(action_id)
            if row is None:
                row = dict(event)
                row["id"] = action_id
                by_action[action_id] = row
                rows.append(row)
                continue
            created_at = row.get("createdAt") or event.get("createdAt")
            for key, value in event.items():
                if key in DESKTOP_ACTION_CLEARABLE_FIELDS:
                    row[key] = value
                    continue
                if value is None or value == "":
                    continue
                if isinstance(value, (dict, list)) and not value:
                    continue
                row[key] = value
            row["id"] = action_id
            row["createdAt"] = created_at
            row["updatedAt"] = event.get("updatedAt") or event.get("createdAt") or row.get("updatedAt")
        return rows

    def _pending_desktop_actions(self) -> list[dict[str, Any]]:
        pending = [
            row
            for row in self._project_desktop_action_rows(limit_events=0)
            if str(row.get("actionId") or "").strip()
            and str(row.get("status") or "") == "requested"
        ]
        pending.sort(key=lambda item: str(item.get("createdAt") or item.get("updatedAt") or ""))
        return pending

    def _live_desktop_bridges(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        live: list[dict[str, Any]] = []
        with self._lock:
            for record in self._desktop_bridges.values():
                heartbeat = _parse_utc_timestamp(str(record.get("lastHeartbeatAt") or ""))
                if heartbeat is not None and (
                    now - heartbeat
                ).total_seconds() <= DESKTOP_BRIDGE_HEARTBEAT_TTL_SECONDS:
                    record["status"] = "connected"
                    live.append(self._public_desktop_bridge(record))
                else:
                    record["status"] = "stale"
        return live

    @staticmethod
    def _public_desktop_bridge(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "credentialDigest"}

    @staticmethod
    def _desktop_bridge_credential_digest(credential: str) -> str:
        return hashlib.sha256(credential.encode("utf-8")).hexdigest()

    def _require_desktop_bridge(
        self,
        bridge_id: str,
        credential: str,
        *,
        require_live: bool = True,
    ) -> dict[str, Any]:
        bridge_id = str(bridge_id or "").strip()
        credential = str(credential or "").strip()
        with self._lock:
            record = self._desktop_bridges.get(bridge_id)
            if record is None:
                raise self._error(
                    "Unknown desktop bridge. Register the bridge before continuing.",
                    404,
                )
            expected = str(record.get("credentialDigest") or "")
            supplied = self._desktop_bridge_credential_digest(credential) if credential else ""
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                raise self._error("Desktop bridge credential is missing or invalid.", 401)
            heartbeat = _parse_utc_timestamp(str(record.get("lastHeartbeatAt") or ""))
            is_live = bool(
                heartbeat is not None
                and (
                    datetime.now(timezone.utc) - heartbeat
                ).total_seconds() <= DESKTOP_BRIDGE_HEARTBEAT_TTL_SECONDS
            )
            record["status"] = "connected" if is_live else "stale"
            if require_live and not is_live:
                raise self._error(
                    "Desktop bridge heartbeat is stale. Register or heartbeat before continuing.",
                    409,
                )
            return self._public_desktop_bridge(dict(record))

    def register_desktop_bridge(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        name = self._text(str(params.get("name") or "").strip(), 120) or "desktop-bridge"
        provider = self._text(str(params.get("provider") or "").strip(), 120) or "unknown"
        capabilities: list[str] = []
        for item in _ensure_list(params.get("capabilities")):
            capability = re.sub(r"[^a-z0-9_.-]+", "_", str(item).strip().lower()).strip("_")
            if capability in DESKTOP_BRIDGE_ACTION_TYPES and capability not in capabilities:
                capabilities.append(capability)
        if not capabilities:
            raise self._error(
                "Desktop bridge must declare at least one supported capability.",
                400,
            )
        operations: list[str] = []
        for item in _ensure_list(params.get("operations")):
            operation = re.sub(r"[^a-z0-9_.-]+", "_", str(item).strip().lower()).strip("_")
            if operation and operation not in operations:
                operations.append(operation)
        operations = operations[:64]
        bridge_id = (
            f"bridge_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_"
            f"{secrets.token_hex(3)}"
        )
        bridge_credential = secrets.token_urlsafe(32)
        now = _utc_now_iso()
        record = {
            "id": bridge_id,
            "bridgeId": bridge_id,
            "name": name,
            "provider": provider,
            "capabilities": capabilities,
            "operations": operations,
            "status": "connected",
            "registeredAt": now,
            "lastHeartbeatAt": now,
            "credentialDigest": self._desktop_bridge_credential_digest(bridge_credential),
        }
        with self._lock:
            self._desktop_bridges[bridge_id] = record
        public_record = self._public_desktop_bridge(record)
        self._ports.append_jsonl(
            self.desktop_bridge_log_path,
            "vrcforge.desktop_bridge.v1",
            {"event": "desktop_bridge_registered", **public_record},
        )
        return {
            "ok": True,
            "schema": "vrcforge.desktop_bridge.v1",
            "bridge": self._ports.redact_sensitive(public_record),
            "bridgeCredential": bridge_credential,
            "heartbeatTtlSeconds": DESKTOP_BRIDGE_HEARTBEAT_TTL_SECONDS,
        }

    def heartbeat_desktop_bridge(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        bridge_id = str(params.get("bridgeId") or params.get("bridge_id") or "").strip()
        credential = str(
            params.get("bridgeCredential") or params.get("bridge_credential") or ""
        ).strip()
        self._require_desktop_bridge(bridge_id, credential, require_live=False)
        with self._lock:
            record = self._desktop_bridges[bridge_id]
            record["lastHeartbeatAt"] = _utc_now_iso()
            record["status"] = "connected"
            snapshot = self._public_desktop_bridge(dict(record))
        return {
            "ok": True,
            "schema": "vrcforge.desktop_bridge.v1",
            "bridge": self._ports.redact_sensitive(snapshot),
            "pendingActionCount": len(self._pending_desktop_actions()),
            "heartbeatTtlSeconds": DESKTOP_BRIDGE_HEARTBEAT_TTL_SECONDS,
        }

    def unregister_desktop_bridge(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        bridge_id = str(params.get("bridgeId") or params.get("bridge_id") or "").strip()
        credential = str(
            params.get("bridgeCredential") or params.get("bridge_credential") or ""
        ).strip()
        bridge = self._require_desktop_bridge(bridge_id, credential, require_live=False)
        with self._lock:
            self._desktop_bridges.pop(bridge_id, None)
            self._recover_stale_desktop_action_claims()
        self._ports.append_jsonl(
            self.desktop_bridge_log_path,
            "vrcforge.desktop_bridge.v1",
            {"event": "desktop_bridge_unregistered", **bridge, "status": "disconnected"},
        )
        return {
            "ok": True,
            "schema": "vrcforge.desktop_bridge.v1",
            "bridgeId": bridge_id,
            "status": "disconnected",
        }

    def desktop_bridge_status(self) -> dict[str, Any]:
        live = self._live_desktop_bridges()
        supported_operations = sorted(
            {
                str(operation)
                for bridge in live
                for operation in _ensure_list(bridge.get("operations"))
                if str(operation).strip()
            }
        )
        return {
            "ok": True,
            "schema": "vrcforge.desktop_bridge_status.v1",
            "connected": bool(live),
            "bridges": [self._ports.redact_sensitive(bridge) for bridge in live],
            "count": len(live),
            "pendingActionCount": len(self._pending_desktop_actions()),
            "heartbeatTtlSeconds": DESKTOP_BRIDGE_HEARTBEAT_TTL_SECONDS,
            "supportedActions": sorted(DESKTOP_BRIDGE_ACTION_TYPES),
            "supportedOperations": supported_operations,
        }

    def _desktop_action_rows_by_id(self) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("actionId") or ""): row
            for row in self._project_desktop_action_rows(limit_events=0)
            if str(row.get("actionId") or "").strip()
        }

    def _recover_stale_desktop_action_claims(self) -> int:
        live_bridge_ids = {
            str(bridge.get("bridgeId") or "") for bridge in self._live_desktop_bridges()
        }
        recovered = 0
        for row in self._desktop_action_rows_by_id().values():
            status = str(row.get("status") or "")
            bridge_id = str(row.get("bridgeId") or "")
            if (
                status not in {"claimed", "cancel_requested"}
                or not bridge_id
                or bridge_id in live_bridge_ids
            ):
                continue
            terminal_cancel = status == "cancel_requested"
            replay_safe = bool(row.get("replaySafe"))
            failed_closed = not terminal_cancel and not replay_safe
            event = {
                "event": (
                    "desktop_action_cancelled"
                    if terminal_cancel
                    else ("desktop_action_failed_closed" if failed_closed else "desktop_action_requeued")
                ),
                "actionId": str(row.get("actionId") or ""),
                "status": "cancelled" if terminal_cancel else ("failed" if failed_closed else "requested"),
                "action": row.get("action"),
                "bridgeId": "",
                "bridgeName": "",
                "provider": "",
                "claimRequestId": "",
                "claimedAt": "",
                "sessionId": row.get("sessionId"),
                "projectRoot": row.get("projectRoot"),
                "error": (
                    "Cancelled after the desktop bridge disconnected."
                    if terminal_cancel
                    else (
                        "Desktop bridge disconnected after a non-idempotent action started; the action was not replayed."
                        if failed_closed
                        else ""
                    )
                ),
                "releasedBridgeId": bridge_id,
            }
            self._append_desktop_action_event(event)
            if terminal_cancel or failed_closed:
                self._desktop_action_payloads.pop(str(row.get("actionId") or ""), None)
            recovered += 1
        return recovered

    def claim_desktop_action(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        bridge_id = str(params.get("bridgeId") or params.get("bridge_id") or "").strip()
        credential = str(
            params.get("bridgeCredential") or params.get("bridge_credential") or ""
        ).strip()
        claim_request_id = self._text(
            str(params.get("claimRequestId") or params.get("claim_request_id") or "").strip(),
            160,
        )
        bridge = self._require_desktop_bridge(bridge_id, credential, require_live=True)
        requested_types: list[str] = []
        for item in _ensure_list(params.get("actions")):
            action_type = re.sub(r"[^a-z0-9_.-]+", "_", str(item).strip().lower()).strip("_")
            if action_type in DESKTOP_BRIDGE_ACTION_TYPES and action_type not in requested_types:
                requested_types.append(action_type)
        capabilities = set(bridge.get("capabilities") or [])
        with self._lock:
            self._recover_stale_desktop_action_claims()
            rows = self._desktop_action_rows_by_id()
            owned = [
                row
                for row in rows.values()
                if str(row.get("status") or "") in {"claimed", "cancel_requested"}
                and str(row.get("bridgeId") or "") == bridge_id
            ]
            if claim_request_id:
                matching = [
                    row
                    for row in owned
                    if str(row.get("claimRequestId") or "") == claim_request_id
                ]
                if matching:
                    target = max(
                        matching,
                        key=lambda item: str(
                            item.get("updatedAt") or item.get("createdAt") or ""
                        ),
                    )
                    return {
                        "ok": True,
                        "schema": "vrcforge.desktop_action.v1",
                        "action": self._ports.redact_sensitive(
                            self._desktop_action_with_payload(target)
                        ),
                        "pendingCount": len(self._pending_desktop_actions()),
                        "idempotent": True,
                    }
            if owned:
                target = max(
                    owned,
                    key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
                )
                return {
                    "ok": True,
                    "schema": "vrcforge.desktop_action.v1",
                    "action": self._ports.redact_sensitive(
                        self._desktop_action_with_payload(target)
                    ),
                    "pendingCount": len(self._pending_desktop_actions()),
                    "idempotent": True,
                }
            pending = self._pending_desktop_actions()
            target = None
            for row in pending:
                action_type = str(row.get("action") or "")
                if requested_types and action_type not in requested_types:
                    continue
                if action_type not in capabilities:
                    continue
                action_id = str(row.get("actionId") or "")
                if action_id not in self._desktop_action_payloads:
                    self._append_desktop_action_event(
                        {
                            "event": "desktop_action_failed_closed",
                            "actionId": action_id,
                            "status": "failed",
                            "action": action_type,
                            "sessionId": row.get("sessionId"),
                            "projectRoot": row.get("projectRoot"),
                            "error": (
                                "Desktop action payload is unavailable after backend restart; "
                                "the action was not replayed."
                            ),
                        }
                    )
                    continue
                target = row
                break
            if target is None:
                return {
                    "ok": True,
                    "schema": "vrcforge.desktop_action.v1",
                    "action": None,
                    "pendingCount": len(self._pending_desktop_actions()),
                }
            event = {
                "event": "desktop_action_claimed",
                "actionId": str(target.get("actionId") or ""),
                "status": "claimed",
                "action": target.get("action"),
                "bridgeId": bridge_id,
                "bridgeName": bridge.get("name"),
                "provider": bridge.get("provider"),
                "claimRequestId": claim_request_id or f"claim_{secrets.token_hex(8)}",
                "claimedAt": _utc_now_iso(),
                "sessionId": target.get("sessionId"),
                "projectRoot": target.get("projectRoot"),
            }
            self._append_desktop_action_event(event)
        merged = {
            **self._desktop_action_with_payload(target),
            **{key: value for key, value in event.items() if value not in (None, "")},
            "id": str(target.get("actionId") or ""),
        }
        return {
            "ok": True,
            "schema": "vrcforge.desktop_action.v1",
            "action": self._ports.redact_sensitive(merged),
            "pendingCount": max(0, len(pending) - 1),
            "idempotent": False,
        }

    def request_desktop_action_cancel(
        self,
        action_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        action_id = str(
            action_id or params.get("actionId") or params.get("action_id") or ""
        ).strip()
        reason = self._text(params.get("reason") or "User requested cancellation.", 500)
        if not action_id:
            raise self._error("Desktop action id is required.", 400)
        with self._lock:
            row = self._desktop_action_rows_by_id().get(action_id)
            if row is None:
                raise self._error("Unknown desktop action id.", 404)
            current = str(row.get("status") or "")
            if current in DESKTOP_ACTION_TERMINAL_STATUSES or current == "cancel_requested":
                return {
                    "ok": True,
                    "schema": "vrcforge.desktop_action.v1",
                    "status": current,
                    "action": self._ports.redact_sensitive(row),
                    "idempotent": True,
                }
            if current not in {"requested", "claimed"}:
                raise self._error(
                    f"Desktop action cannot be cancelled from {current or 'unknown'}.",
                    409,
                )
            next_status = "cancel_requested" if current == "claimed" else "cancelled"
            event = {
                "event": (
                    "desktop_action_cancel_requested"
                    if next_status == "cancel_requested"
                    else "desktop_action_cancelled"
                ),
                "actionId": action_id,
                "status": next_status,
                "action": row.get("action"),
                "bridgeId": row.get("bridgeId"),
                "sessionId": row.get("sessionId"),
                "projectRoot": row.get("projectRoot"),
                "cancelReason": reason,
            }
            self._append_desktop_action_event(event)
            merged = {**row, **event, "id": action_id}
            if next_status == "cancelled":
                self._desktop_action_payloads.pop(action_id, None)
        return {
            "ok": True,
            "schema": "vrcforge.desktop_action.v1",
            "status": next_status,
            "action": self._ports.redact_sensitive(merged),
            "idempotent": False,
        }

    def desktop_action_cancel_requested(self, action_id: str) -> bool:
        row = self._desktop_action_rows_by_id().get(str(action_id or "").strip())
        return str((row or {}).get("status") or "") in {"cancel_requested", "cancelled"}

    def complete_desktop_action(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        bridge_id = str(params.get("bridgeId") or params.get("bridge_id") or "").strip()
        credential = str(
            params.get("bridgeCredential") or params.get("bridge_credential") or ""
        ).strip()
        action_id = str(params.get("actionId") or params.get("action_id") or "").strip()
        status = str(params.get("status") or "completed").strip().lower()
        if status not in DESKTOP_ACTION_TERMINAL_STATUSES:
            raise self._error(
                "Desktop action completion status must be completed, failed, or cancelled.",
                400,
            )
        self._require_desktop_bridge(bridge_id, credential, require_live=False)
        with self._lock:
            row = self._desktop_action_rows_by_id().get(action_id)
            if row is None:
                raise self._error("Unknown desktop action id.", 404)
            row_status = str(row.get("status") or "")
            claimed_bridge = str(row.get("bridgeId") or "")
            if row_status in DESKTOP_ACTION_TERMINAL_STATUSES:
                if claimed_bridge == bridge_id and row_status == status:
                    return {
                        "ok": status in {"completed", "cancelled"},
                        "schema": "vrcforge.desktop_action.v1",
                        "status": status,
                        "action": self._ports.redact_sensitive(row),
                        "error": str(row.get("error") or ""),
                        "idempotent": True,
                    }
                raise self._error(
                    f"Desktop action is already {row_status or 'closed'}.",
                    409,
                )
            self._require_desktop_bridge(bridge_id, credential, require_live=True)
            if row_status not in {"claimed", "cancel_requested"}:
                raise self._error("Desktop action must be claimed before completion.", 409)
            if claimed_bridge != bridge_id:
                raise self._error("Desktop action is claimed by another bridge.", 409)
            if row_status == "cancel_requested":
                status = "cancelled"
            error = self._text(params.get("error"), 500)
            result_payload = (
                self._ports.redact_sensitive(_ensure_dict(params.get("result")))
                if params.get("result")
                else {}
            )
            result_size = len(
                json.dumps(result_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )
            if result_size > DESKTOP_ACTION_RESULT_MAX_BYTES:
                raise self._error("Desktop action result exceeds the 128 KiB limit.", 413)
            result_summary = (
                self.desktop_action_result_audit(result_payload) if result_payload else {}
            )
            event = {
                "event": "desktop_action_cancelled" if status == "cancelled" else "desktop_action_completed",
                "actionId": action_id,
                "status": status,
                "action": row.get("action"),
                "bridgeId": bridge_id,
                "resultSummary": result_summary,
                "error": error,
                "sessionId": row.get("sessionId"),
                "projectRoot": row.get("projectRoot"),
            }
            self._desktop_action_results[action_id] = result_payload
            self._desktop_action_payloads.pop(action_id, None)
            while len(self._desktop_action_results) > AGENT_DESKTOP_ACTION_MAX_ITEMS:
                self._desktop_action_results.pop(next(iter(self._desktop_action_results)))
            self._append_desktop_action_event(event)
        merged = {**row, **event, "status": status, "id": action_id}
        return {
            "ok": status in {"completed", "cancelled"},
            "schema": "vrcforge.desktop_action.v1",
            "status": status,
            "action": self._ports.redact_sensitive(merged),
            "error": error,
            "idempotent": False,
        }
