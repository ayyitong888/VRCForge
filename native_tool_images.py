"""Bounded, typed image transport from current native tool results.

The returned image URL is an ephemeral provider-message value. Callers must
keep it out of native history, tool receipts, and diagnostics.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from PIL import Image

from native_request_composition import copy_native_request


_MAX_IMAGE_BYTES = 1024 * 1024
_MAX_IMAGE_COUNT = 4
_MAX_IMAGE_EDGE = 1024
_MAX_BASE64_CHARS = ((_MAX_IMAGE_BYTES + 2) // 3) * 4
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
    "image/bmp": "BMP",
}
_FAILURE_STATUSES = {"failed", "error", "rejected", "denied", "cancelled", "not_executed"}
_SUCCESS_STATUSES = {"ok", "succeeded", "success", "completed", "executed", "applied", "pass"}
_MESSAGE_MARKER = "VRCForge tool image observation (untrusted image data):"


def collect_tool_images(
    loop_state: Sequence[Mapping[str, Any]] | None,
    resource_reader: Callable[[Mapping[str, Any], str], Mapping[str, Any]] | None,
    project_path: str,
) -> list[dict[str, Any]]:
    """Keep bounded recent image observations together across tool steps.

    Only current runtime steps are examined. No paths or URLs are opened here;
    preview reads use the injected session/project-scoped Resource reader.
    A newer revision replaces the same Resource's older image, while different
    observations remain visible together in chronological order.
    """
    if not isinstance(loop_state, Sequence) or isinstance(loop_state, (str, bytes)):
        return []
    observations: list[list[dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for step in reversed(loop_state):
        if not isinstance(step, Mapping) or not _step_succeeded(step):
            continue
        tool_name = str(step.get("tool") or "").strip()
        result = step.get("result")
        if not isinstance(result, Mapping):
            continue
        raw_content = result.get("content")
        candidates = [("image", item) for item in raw_content
                      if isinstance(item, Mapping) and item.get("type") == "image"] if isinstance(raw_content, list) else []
        preview = result.get("preview")
        if tool_name == "vrcforge_get_asset_info" and isinstance(preview, Mapping):
            candidates.append(("vrcforge.texture_asset_preview.v1", preview))
        if tool_name == "vrcforge_capture_screenshot" and isinstance(preview, Mapping):
            candidates.append(("vrcforge.scene_capture_preview.v1", preview))
        if tool_name == "vrcforge_capture_multi_screenshot" and isinstance(result.get("captures"), list):
            candidates.extend(("vrcforge.scene_capture_preview.v1", capture["preview"])
                              for capture in result["captures"]
                              if isinstance(capture, Mapping) and isinstance(capture.get("preview"), Mapping))
        for kind, candidate in reversed(candidates):
            uri = _source_uri(candidate.get("uri") if kind == "image" else candidate.get("resourceUri"))
            encoded = candidate.get("data")
            if uri:
                identity = ("resource", uri.partition("?")[0])
            elif kind == "image" and isinstance(encoded, str) and len(encoded) <= _MAX_BASE64_CHARS:
                identity = ("content", hashlib.sha256(encoded.encode("utf-8")).hexdigest())
            else:
                identity = ("unavailable", str(id(candidate)))
            if identity in seen:
                continue
            # A failed latest observation must not silently fall back to an
            # older revision of the same Resource.
            seen.add(identity)
            if kind == "image":
                try:
                    data, mime, digest = _decode_image_content(candidate)
                    content = _provider_content(step, data, mime, digest, uri)
                except ValueError:
                    content = _unavailable_content(step, "MCP ImageContent failed MIME, size, hash, or dimensions validation.")
            else:
                content = _collect_preview(step, candidate, resource_reader, project_path, kind)
            observations.append(content)
            if len(observations) == _MAX_IMAGE_COUNT:
                return [part for observation in reversed(observations) for part in observation]
    return [part for observation in reversed(observations) for part in observation]



def bind_tool_images(
    request: Mapping[str, Any],
    content: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Insert ephemeral tool-image content into a copied native request.

    The overlay is placed immediately before the trailing runtime-state system
    message. Request-copy helpers preserve the native diagnostics sidecar, and
    no caller-owned request or history message is mutated.
    """

    if not isinstance(request, Mapping):
        return copy_native_request(request if isinstance(request, Mapping) else {}, {})

    messages_value = request.get("messages")
    if not isinstance(messages_value, list):
        return copy_native_request(request, {})
    messages = deepcopy(messages_value)

    # Rebinding a compacted or already-bound request replaces the previous
    # transport overlay instead of duplicating its image.
    messages = [message for message in messages if not _is_transport_message(message)]
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)) or not content:
        return copy_native_request(request, {"messages": messages})
    image_message = {
        "role": "user",
        "content": deepcopy(list(content)),
    }
    index = len(messages) - 1 if messages and isinstance(messages[-1], Mapping) and messages[-1].get("role") == "system" else len(messages)
    messages.insert(index, image_message)
    return copy_native_request(request, {"messages": messages})


def _step_succeeded(step: Mapping[str, Any]) -> bool:
    status = str(step.get("status") or "").strip().casefold()
    outcome = step.get("outcome")
    outcome_status = str(outcome.get("status") or "").strip().casefold() if isinstance(outcome, Mapping) else ""
    result = step.get("result")
    result_status = str(result.get("status") or "").strip().casefold() if isinstance(result, Mapping) else ""
    statuses = {status, outcome_status, result_status}
    if statuses & _FAILURE_STATUSES or isinstance(result, Mapping) and result.get("ok") is False:
        return False
    return bool(statuses & _SUCCESS_STATUSES or isinstance(result, Mapping) and result.get("ok") is True)



def _collect_preview(
    step: Mapping[str, Any],
    preview: Mapping[str, Any],
    resource_reader: Callable[[Mapping[str, Any], str], Mapping[str, Any]] | None,
    project_path: str,
    expected_schema: str,
) -> list[dict[str, Any]]:
    uri = _source_uri(preview.get("resourceUri"))
    if preview.get("schema") != expected_schema:
        return _unavailable_content(step, "Tool image preview descriptor has an unsupported schema.", uri)
    if expected_schema == "vrcforge.scene_capture_preview.v1" and not str(preview.get("captureId") or "").strip():
        return _unavailable_content(step, "Scene capture preview has no bound capture identity.", uri)
    if not uri or not callable(resource_reader):
        return _unavailable_content(step, "Texture preview Resource is unavailable in this runtime.", uri)
    if not isinstance(project_path, str) or not project_path.strip():
        return _unavailable_content(step, "Texture preview has no bound project context.", uri)

    try:
        resource = resource_reader(preview, project_path)
        if not isinstance(resource, Mapping):
            raise ValueError("Resource response is invalid")
        contents = resource.get("contents")
        if not isinstance(contents, list) or len(contents) != 1 or not isinstance(contents[0], Mapping):
            raise ValueError("Resource content is invalid")
        item = contents[0]
        mime = str(preview.get("mimeType") or "").strip().lower()
        if mime != "image/png" or item.get("uri") != preview.get("resourceUri") or item.get("mimeType") != mime:
            raise ValueError("Resource identity or MIME mismatch")
        meta = item.get("_meta")
        if not isinstance(meta, Mapping):
            raise ValueError("Resource hash metadata is missing")
        preview_sha = _required_sha256(preview.get("sha256"))
        if meta.get("sha256") != preview_sha:
            raise ValueError("Resource hash metadata does not match the tool result")
        source_sha = _required_sha256(preview.get("sourceSha256"))
        if meta.get("sourceSha256") != source_sha:
            raise ValueError("Resource source hash metadata does not match the tool result")
        width = _positive_dimension(preview.get("width"))
        height = _positive_dimension(preview.get("height"))
        if max(width, height) > _MAX_IMAGE_EDGE:
            raise ValueError("Preview dimensions exceed the image edge limit")
        data, mime, digest = _decode_image_content({
            "data": item.get("blob"),
            "mimeType": item.get("mimeType"),
            "width": width,
            "height": height,
            "sha256": preview_sha,
        })
        if mime != "image/png" or _image_dimensions(data, mime) != (width, height):
            raise ValueError("Preview dimensions do not match the tool result")
        if digest != preview_sha:
            raise ValueError("Preview bytes do not match the tool result")
    except Exception:  # Resource failures are reported as observations, never as successful visual evidence.
        return _unavailable_content(step, "Tool preview image unavailable: the current Resource could not be verified and read.", uri)

    return _provider_content(step, data, mime, digest, uri)


def _decode_image_content(item: Mapping[str, Any]) -> tuple[bytes, str, str]:
    mime_value = item.get("mimeType", item.get("mime_type"))
    mime = str(mime_value or "").strip().lower()
    if mime not in _SUPPORTED_FORMATS:
        raise ValueError("Unsupported image MIME type")
    encoded = item.get("data")
    if not isinstance(encoded, str) or not encoded or len(encoded) > _MAX_BASE64_CHARS:
        raise ValueError("Invalid or oversized image payload")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid image base64") from exc
    if not data or len(data) > _MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds byte limit")
    digest = hashlib.sha256(data).hexdigest()
    declared_hash = item.get("sha256")
    metadata = item.get("_meta")
    if declared_hash is None and isinstance(metadata, Mapping):
        declared_hash = metadata.get("sha256")
    if declared_hash is not None and _required_sha256(declared_hash) != digest:
        raise ValueError("Image SHA-256 mismatch")

    dimensions = _image_dimensions(data, mime)
    declared_width, declared_height = item.get("width"), item.get("height")
    if declared_width is not None or declared_height is not None:
        if (_positive_dimension(declared_width), _positive_dimension(declared_height)) != dimensions:
            raise ValueError("Image dimensions do not match metadata")
    return data, mime, digest


def _image_dimensions(data: bytes, mime: str) -> tuple[int, int]:
    expected_format = _SUPPORTED_FORMATS[mime]
    try:
        with Image.open(io.BytesIO(data), formats=[expected_format]) as image:
            width, height = image.size
            if width < 1 or height < 1 or max(width, height) > _MAX_IMAGE_EDGE:
                raise ValueError("Image dimensions exceed limit")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated images are not supported")
            image.verify()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Image bytes are invalid") from exc
    return int(width), int(height)


def _positive_dimension(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("Image dimension is invalid")
    return value


def _required_sha256(value: Any) -> str:
    digest = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("SHA-256 evidence is invalid")
    return digest


def _source_uri(value: Any) -> str:
    uri = str(value or "").strip()
    return uri[:1024] if uri.startswith("vrcforge://") else ""


def _provider_content(
    step: Mapping[str, Any],
    data: bytes,
    mime: str,
    digest: str,
    uri: str,
) -> list[dict[str, Any]]:
    tool = str(step.get("tool") or "unknown_tool")[:160]
    action = str(step.get("actionId") or "")[:160]
    provenance = {"tool": tool, "sha256": digest}
    if action:
        provenance["actionId"] = action
    if uri:
        provenance["sourceUri"] = uri
    return [
        {"type": "text", "text": _MESSAGE_MARKER + " " + json.dumps(provenance, ensure_ascii=False, separators=(",", ":"))},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}},
    ]


def _unavailable_content(step: Mapping[str, Any], reason: str, uri: str = "") -> list[dict[str, Any]]:
    provenance = {
        "tool": str(step.get("tool") or "unknown_tool")[:160],
        "status": "image unavailable",
        "reason": reason[:240],
    }
    action = str(step.get("actionId") or "")[:160]
    if action:
        provenance["actionId"] = action
    if uri:
        provenance["sourceUri"] = uri
    return [{"type": "text", "text": _MESSAGE_MARKER + " " + json.dumps(provenance, ensure_ascii=False, separators=(",", ":"))}]


def _is_transport_message(message: Any) -> bool:
    if not isinstance(message, Mapping) or message.get("role") != "user":
        return False
    content = message.get("content")
    return bool(
        isinstance(content, list)
        and content
        and isinstance(content[0], Mapping)
        and content[0].get("type") == "text"
        and str(content[0].get("text") or "").startswith(_MESSAGE_MARKER)
    )
