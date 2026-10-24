"""Authenticated presentation reads for image Resources referenced in chat replies."""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from scene_capture_preview import read_scene_capture_preview


_CAPTURE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_STORE_ID = re.compile(r"^[0-9a-f]{32}$", re.I)


def read_chat_scene_image_resource(
    registry,
    *,
    uri: str,
    session_id: str,
    client_turn_id: str,
    project_path: str,
    chat: Mapping[str, object],
    runtime_timeline_reader: Callable[..., dict] | None = None,
) -> dict[str, str]:
    """Read a captured scene image only when its exact URI belongs to this chat turn."""
    session_id = _required(session_id, "sessionId")
    client_turn_id = _required(client_turn_id, "clientTurnId")
    project_path = _required(project_path, "projectPath")
    if not isinstance(uri, str) or len(uri) > 2048:
        raise ValueError("Invalid chat image Resource URI.")
    parts = urlsplit(uri)
    query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
    expected_context = f"runtime:{session_id}"
    if (
        parts.scheme != "vrcforge"
        or parts.netloc != "scene-capture-preview"
        or not _CAPTURE_ID.fullmatch(parts.path.removeprefix("/"))
        or parts.path.count("/") != 1
        or parts.fragment
        or set(query) != {"context", "revision", "store"}
        or any(len(values) != 1 for values in query.values())
        or query["context"][0] != expected_context
        or not query["revision"][0].isdigit()
        or int(query["revision"][0]) < 1
        or not _STORE_ID.fullmatch(query["store"][0])
    ):
        raise ValueError("Chat image Resource URI is not a supported scene capture.")

    if str(chat.get("sessionId") or "") != session_id:
        raise ValueError("Chat image Resource belongs to a different chat session.")
    chat_project = str(chat.get("projectPath") or "")
    if not chat_project or _normalized_project(chat_project) != _normalized_project(project_path):
        raise ValueError("Chat image Resource belongs to a different project.")
    response = _matching_chat_response(chat, session_id, client_turn_id, uri)
    if response is None and not _runtime_capture_has_uri(runtime_timeline_reader, session_id, client_turn_id, project_path, uri):
        raise ValueError("Chat image Resource is not present in this assistant reply.")

    capture_id = parts.path.removeprefix("/")
    envelope = registry.validate_reference(
        uri,
        expected_type="scene_capture_preview",
        expected_identity={"captureId": capture_id, "sessionId": session_id},
        context_id=expected_context,
    )
    identity = envelope.get("identity")
    if not isinstance(identity, Mapping) or _normalized_project(str(identity.get("projectRoot") or "")) != _normalized_project(project_path):
        raise ValueError("Chat image Resource belongs to a different project.")
    result = read_scene_capture_preview(envelope)
    content = result["contents"][0]
    return {
        "uri": uri,
        "mimeType": "image/png",
        "sha256": str(content["_meta"]["sha256"]),
        "pngBase64": str(content["blob"]),
    }


def _runtime_capture_has_uri(reader, session_id, client_turn_id, project_path, uri):
    """A committed tool event can precede the client's debounced chat save."""
    if reader is None:
        return False
    cursor = 0
    while True:
        page = reader(session_id=session_id, client_turn_id=client_turn_id, after_sequence=cursor, limit=200)
        run = page.get("run")
        if (not isinstance(run, Mapping) or run.get("sessionId") != session_id
                or run.get("clientTurnId") != client_turn_id
                or _normalized_project(str(run.get("projectRoot") or "")) != _normalized_project(project_path)):
            return False
        events = page.get("events")
        if isinstance(events, list) and any(isinstance(event, Mapping) and event.get("kind") == "tool_result"
                and _capture_result_has_uri(event.get("payload"), uri) for event in events):
            return True
        next_cursor = page.get("nextSequence")
        if not page.get("hasMore") or type(next_cursor) is not int or next_cursor <= cursor:
            return False
        cursor = next_cursor


def _matching_chat_response(chat: Mapping[str, object], session_id: str, client_turn_id: str, uri: str) -> Mapping[str, object] | None:
    items = chat.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "streaming" and item.get("clientTurnId") == client_turn_id:
            timeline = item.get("timeline")
            if isinstance(timeline, list) and any(
                isinstance(event, Mapping) and event.get("kind") == "tool_result"
                and _capture_result_has_uri(event.get("payload"), uri)
                for event in timeline
            ):
                return item
        if item.get("type") != "agent":
            continue
        response = item.get("response")
        if not isinstance(response, Mapping):
            continue
        if str(response.get("sessionId") or response.get("session_id") or "") != session_id:
            continue
        if str(response.get("clientTurnId") or "") != client_turn_id:
            continue
        plan = response.get("plan")
        if isinstance(plan, Mapping) and uri in str(plan.get("reply") or ""):
            return response
        timeline = response.get("timeline")
        if isinstance(timeline, list) and any(
            isinstance(event, Mapping) and event.get("kind") == "tool_result"
            and _capture_result_has_uri(event.get("payload"), uri)
            for event in timeline
        ):
            return response
        steps = response.get("steps") or (plan.get("steps") if isinstance(plan, Mapping) else None)
        if isinstance(steps, list) and any(
            isinstance(step, Mapping) and _capture_result_has_uri(step.get("result"), uri)
            for step in steps
        ):
            return response
    return None


def public_capture_previews(result: object) -> list[dict[str, object]]:
    """Only immutable screenshot references, never file paths or raw tool data."""
    if not isinstance(result, Mapping):
        return []
    captures = result.get("captures")
    candidates = captures if isinstance(captures, list) else [result]
    projected = []
    for capture in candidates[:32]:
        if not isinstance(capture, Mapping):
            continue
        preview = capture.get("preview")
        uri = preview.get("resourceUri") if isinstance(preview, Mapping) else None
        if not isinstance(uri, str) or len(uri) > 2048 or not _capture_result_has_uri(capture, uri):
            continue
        projected.append({"angle": str(capture.get("angle") or "")[:80], "preview": {
            key: preview[key] for key in ("schema", "mimeType", "captureId", "resourceUri")
        }})
    return projected


def _capture_result_has_uri(result: object, uri: str) -> bool:
    if not isinstance(result, Mapping):
        return False
    captures = result.get("captures")
    candidates = captures if isinstance(captures, list) else [result]
    for capture in candidates:
        if not isinstance(capture, Mapping):
            continue
        preview = capture.get("preview")
        if (isinstance(preview, Mapping)
                and preview.get("schema") == "vrcforge.scene_capture_preview.v1"
                and preview.get("mimeType") == "image/png"
                and preview.get("resourceUri") == uri
                and urlsplit(uri).scheme == "vrcforge"
                and urlsplit(uri).netloc == "scene-capture-preview"
                and _CAPTURE_ID.fullmatch(str(preview.get("captureId") or ""))
                and preview.get("captureId") == urlsplit(uri).path.removeprefix("/")):
            return True
    return False


def _normalized_project(value: str) -> str:
    from pathlib import Path
    import os

    if not value:
        return ""
    return os.path.normcase(os.path.abspath(str(Path(value))))


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{label} is required.")
    return value.strip()
