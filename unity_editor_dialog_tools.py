"""Project-bound native Unity dialog inspection and approved-operation continuation.

Uses the existing Win32 enumerator and button primitive. It never identifies an
operation's write footprint from button captions: the hosting gateway must supply
the original approved operation and its ready checkpoint through a trusted lookup.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import unity_editor_window_probe as native


class UnityEditorDialogError(ValueError):
    """An exact dialog, button, or approved source operation is unavailable."""


SourceApprovalLookup = Callable[[str], dict[str, Any]]


def _project_path(value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        raise UnityEditorDialogError("An exact existing Unity projectPath is required.")
    project = Path(text).expanduser().resolve()
    if not project.is_dir():
        raise UnityEditorDialogError("The selected Unity projectPath does not exist.")
    return project


def _positive(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise UnityEditorDialogError(f"An exact {label} is required.")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def inspect_unity_editor_dialogs(params: dict[str, Any] | str | Path) -> dict[str, Any]:
    """Read native dialog text/buttons for exactly the selected project's PID."""
    project = _project_path(params.get("projectPath") if isinstance(params, dict) else params)
    result: dict[str, Any] = {
        "schema": "vrcforge.unity_editor_dialogs.v1", "projectPath": str(project),
        "available": False, "unityProcessId": None, "dialogs": [], "probeError": None,
    }
    try:
        descriptor = json.loads((project / "Library/VRCForge/mcp-core.json").read_text(encoding="utf-8"))
        pid = _positive(descriptor.get("processId"), "Unity process identity")
        result["unityProcessId"] = pid
        if os.name != "nt":
            return result
        windows = native._enumerate_process_windows(pid)
    except Exception as exc:  # This bounded read-only boundary reports failures explicitly.
        result["probeError"] = {"code": "unity_editor_dialog_probe_failed", "message": str(exc)[:512]}
        return result

    result["available"] = True
    for window in windows:
        if not isinstance(window, dict) or window.get("visible") is not True:
            continue
        # Unity utility windows may also be owned. Report candidates honestly;
        # only exact readable native Button controls are actionable below.
        if str(window.get("className") or "").casefold() != "#32770" and not window.get("ownerWindow"):
            continue
        controls = [dict(c) for c in window.get("controls", []) if isinstance(c, dict)]
        identity = {
            "projectPath": str(project), "unityProcessId": pid,
            "windowHandle": window.get("windowHandle"), "ownerWindow": window.get("ownerWindow"),
            "title": str(window.get("title") or ""), "className": str(window.get("className") or ""),
            "visible": window.get("visible"), "enabled": window.get("enabled"), "controls": controls,
        }
        truncated = bool(window.get("titleTruncated") or window.get("textTruncated")) or any(c.get("textTruncated") for c in controls)
        buttons = []
        for control in controls:
            if str(control.get("className") or "").casefold() != "button":
                continue
            direct_parent = control.get("parentWindow") == window.get("windowHandle")
            buttons.append({
                "buttonHandle": control.get("windowHandle"), "text": str(control.get("text") or ""),
                "buttonStyle": control.get("buttonStyle"),
                "visible": control.get("visible") is True, "enabled": control.get("enabled") is True,
                "nativeClickable": bool(direct_parent and control.get("buttonStyle") in {0, 1}
                                        and not truncated and window.get("enabled") is True
                                        and control.get("visible") is True
                                        and control.get("enabled") is True and control.get("text")),
            })
        result["dialogs"].append({
            "dialogId": _digest(identity), **identity,
            "text": "\n".join([identity["title"], *[str(c.get("text") or "") for c in controls
                                                   if str(c.get("className") or "").casefold() != "button"]]).strip(),
            "buttons": buttons, "textTruncated": truncated,
            "supported": any(b["nativeClickable"] for b in buttons),
            "limitation": "" if any(b["nativeClickable"] for b in buttons) else
                "No complete readable, enabled native button is available; custom-drawn controls are unsupported.",
        })
    return result


def _selected(snapshot: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if snapshot.get("available") is not True or snapshot.get("probeError"):
        raise UnityEditorDialogError("The selected Unity dialogs could not be inspected.")
    dialog_id = str(params.get("dialogId") or "")
    if not dialog_id:
        raise UnityEditorDialogError("Inspect first and select an exact dialogId.")
    dialog = next((d for d in snapshot["dialogs"] if d["dialogId"] == dialog_id), None)
    if dialog is None:
        raise UnityEditorDialogError("The inspected dialog content or identity changed; inspect again.")
    handle = _positive(params.get("buttonHandle"), "observed buttonHandle")
    text = params.get("buttonText")
    if not isinstance(text, str) or not text:
        raise UnityEditorDialogError("The exact observed buttonText is required.")
    button = next((b for b in dialog["buttons"] if b["buttonHandle"] == handle and b["text"] == text), None)
    if not button or not button["nativeClickable"]:
        raise UnityEditorDialogError("The exact observed button is unavailable or unsupported.")
    return dialog, button


def _source(params: dict[str, Any], project: str,
            lookup: SourceApprovalLookup | None) -> dict[str, Any]:
    approval_id = str(params.get("sourceApprovalId") or "").strip()
    if not approval_id or lookup is None:
        raise UnityEditorDialogError(
            "unsupported-write-scope: a native confirmation must continue an existing approved operation with a ready checkpoint.")
    source = lookup(approval_id)
    if not isinstance(source, dict) or source.get("approved") is not True:
        raise UnityEditorDialogError("The source operation is not approved.")
    if _project_path(source.get("projectPath")) != Path(project):
        raise UnityEditorDialogError("The source operation belongs to a different Unity project.")
    if source.get("checkpointReady") is not True or not str(source.get("checkpointId") or ""):
        raise UnityEditorDialogError("unsupported-write-scope: the source operation has no ready asset checkpoint.")
    return {"sourceApprovalId": approval_id, "checkpointId": str(source["checkpointId"]),
            "sourceTool": str(source.get("sourceTool") or ""),
            "operationId": str(source.get("operationId") or "")}


def prepare_unity_editor_dialog_action(
    params: dict[str, Any], _preview: dict[str, Any] | None,
    *, source_approval_lookup: SourceApprovalLookup | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze observed text/button identities under the original approved write."""
    snapshot = inspect_unity_editor_dialogs(params)
    dialog, button = _selected(snapshot, params)
    source = _source(params, snapshot["projectPath"], source_approval_lookup)
    prepared = {
        "projectPath": snapshot["projectPath"], "dialogId": dialog["dialogId"],
        "buttonHandle": button["buttonHandle"], "buttonText": button["text"],
        "sourceApprovalId": source["sourceApprovalId"], "expectedSource": source,
        "expectedUnityProcessId": snapshot["unityProcessId"],
        "expectedWindowHandle": dialog["windowHandle"],
    }
    return prepared, {
        "schema": "vrcforge.unity_editor_dialog_action.v1", **prepared,
        "dialogText": dialog["text"], "dialogTitle": dialog["title"],
        "checkpointAvailable": True, "checkpointScope": "original-approved-operation-only",
        "dialogClickRollbackAvailable": False,
        "note": "The button click itself has no inverse. Asset recovery uses only the original operation's checkpoint; closure does not prove asset success.",
    }


def handle_unity_editor_dialog(
    params: dict[str, Any], *, source_approval_lookup: SourceApprovalLookup | None = None,
) -> dict[str, Any]:
    """Reinspect, revalidate the source approval, click once, and observe closure."""
    snapshot = inspect_unity_editor_dialogs(params)
    dialog, button = _selected(snapshot, params)
    if snapshot["unityProcessId"] != params.get("expectedUnityProcessId"):
        raise UnityEditorDialogError("The selected Unity process changed after approval.")
    if dialog["windowHandle"] != params.get("expectedWindowHandle"):
        raise UnityEditorDialogError("The selected dialog window changed after approval.")
    source = _source(params, snapshot["projectPath"], source_approval_lookup)
    if source != params.get("expectedSource"):
        raise UnityEditorDialogError("The source approval or checkpoint changed after preparation.")
    native.post_native_dialog_button_click(snapshot["unityProcessId"], dialog["windowHandle"],
                                          button["buttonHandle"], button["text"])
    result: dict[str, Any] = {
        "schema": "vrcforge.unity_editor_dialog_action.v1", "projectPath": snapshot["projectPath"],
        "unityProcessId": snapshot["unityProcessId"], "windowHandle": dialog["windowHandle"],
        "buttonHandle": button["buttonHandle"], "buttonText": button["text"], **source,
        "buttonClicked": True, "dialogClosed": None, "retryable": False,
        "assetOperationVerified": False, "dialogClickRollbackAvailable": False,
    }
    deadline = time.monotonic() + 2.0
    while True:
        after = inspect_unity_editor_dialogs(params)
        if after.get("available") is not True or after.get("probeError") or after.get("unityProcessId") != snapshot["unityProcessId"]:
            message = "The button was dispatched, but the same Unity process could not be inspected afterwards."
            break
        current = next((d for d in after["dialogs"] if d["windowHandle"] == dialog["windowHandle"]), None)
        if current is None or current["dialogId"] != dialog["dialogId"]:
            return {**result, "ok": True, "dialogClosed": current is None, "dialogChanged": current is not None}
        if time.monotonic() >= deadline:
            message = "The button was dispatched, but the dialog did not close or change within two seconds."
            break
        time.sleep(0.05)
    return {**result, "ok": False, "mutationStarted": True, "commitState": "unknown", "error": message}
