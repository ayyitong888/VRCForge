from __future__ import annotations

import re
from typing import Any


WINDOWS_DESKTOP_OPERATIONS = {
    "list_apps",
    "launch_app",
    "list_windows",
    "get_window",
    "window_state",
    "inspect_window",
    "cursor_position",
    "screenshot",
    "focus_window",
    "move_pointer",
    "click",
    "drag",
    "scroll",
    "type_text",
    "key_press",
    "focus_element",
    "invoke_element",
    "set_value",
    "secondary_action",
    "wait",
    "sequence",
}

DESKTOP_OPERATION_ALIASES = {
    "get_window_state": "window_state",
    "activate_window": "focus_window",
    "capture": "screenshot",
    "move": "move_pointer",
    "drag_pointer": "drag",
    "type": "type_text",
    "press": "key_press",
    "press_key": "key_press",
    "invoke": "invoke_element",
    "set_element_value": "set_value",
    "perform_secondary_action": "secondary_action",
    "sleep": "wait",
}

DESKTOP_REPLAY_SAFE_OPERATIONS = {
    "list_apps",
    "list_windows",
    "get_window",
    "window_state",
    "inspect_window",
    "cursor_position",
    "screenshot",
    "wait",
}

DESKTOP_INTERACTIVE_OPERATIONS = {
    "launch_app",
    "focus_window",
    "move_pointer",
    "click",
    "drag",
    "scroll",
    "type_text",
    "key_press",
    "focus_element",
    "invoke_element",
    "set_value",
    "secondary_action",
}


def canonical_desktop_operation(value: Any) -> str:
    operation = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().lower()).strip("_")
    return DESKTOP_OPERATION_ALIASES.get(operation, operation)


def canonical_desktop_params(value: Any) -> dict[str, Any]:
    params = dict(value) if isinstance(value, dict) else {}
    operation = canonical_desktop_operation(params.get("operation"))
    if params.get("id") not in (None, "") and operation in {
        "get_window",
        "window_state",
        "inspect_window",
        "screenshot",
        "focus_window",
        "click",
        "drag",
        "scroll",
        "type_text",
        "key_press",
        "focus_element",
        "invoke_element",
        "set_value",
        "secondary_action",
    }:
        params.setdefault("windowHandle", params.get("id"))
    window = params.get("window")
    if isinstance(window, dict):
        if window.get("id") not in (None, ""):
            params.setdefault("windowHandle", window.get("id"))
        for key in ("app", "processId", "processPath", "title"):
            if window.get(key) not in (None, ""):
                target_key = "titleContains" if key == "title" else key
                params.setdefault(target_key, window.get(key))
    aliases = {
        "window_handle": "windowHandle",
        "process_id": "processId",
        "process_path": "processPath",
        "title_contains": "titleContains",
        "include_screenshot": "includeScreenshot",
        "include_text": "includeText",
        "element_index": "elementIndex",
        "automation_id": "automationId",
        "control_type": "controlType",
        "click_count": "clicks",
        "mouse_button": "button",
        "screenshot_id": "screenshotId",
        "from_x": "fromX",
        "from_y": "fromY",
        "to_x": "toX",
        "to_y": "toY",
        "from_x_ratio": "fromXRatio",
        "from_y_ratio": "fromYRatio",
        "to_x_ratio": "toXRatio",
        "to_y_ratio": "toYRatio",
        "scroll_x": "scrollX",
        "scroll_y": "scrollY",
        "relative_to_window": "relativeToWindow",
        "duration_ms": "durationMs",
        "interval_ms": "intervalMs",
        "delay_ms": "delayMs",
        "timeout_ms": "timeoutMs",
        "allow_legacy_capture": "allowLegacyCapture",
    }
    for source, target in aliases.items():
        if source in params and target not in params:
            params[target] = params[source]
    return params
