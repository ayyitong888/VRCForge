"""Bounded immutable image Resources for already approved scene captures."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

from PIL import Image

from prepared_file_imports import capture_directory, verify_directory
from texture_preview_resources import MAX_PREVIEW_BYTES, _read_bounded

RESOURCE_TYPE = "scene_capture_preview"
SCHEMA = "vrcforge.scene_capture_preview.v1"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 16 * 1024 * 1024
MAX_PREVIEW_EDGE = 1024


def publish_scene_capture_preview(registry, image_path, project_path, binding, context_id):
    """Publish one bounded PNG at the exact already-approved capture path."""
    source = Path(image_path)
    if not source.is_absolute():
        raise ValueError("Approved scene capture path must be absolute.")
    if not isinstance(project_path, str) or not project_path or not Path(project_path).is_absolute():
        raise ValueError("Scene capture preview requires an absolute project path.")
    if not isinstance(binding, Mapping):
        raise ValueError("Scene capture preview requires the approved capture binding.")
    session_id = str(binding.get("sessionId") or "").strip()
    approval_id = str(binding.get("approvalId") or "").strip()
    context = str(context_id or "").strip()
    if not session_id or not approval_id or not context:
        raise ValueError("Scene capture preview requires session, approval, and Resource context identity.")

    ancestors = [capture_directory(parent, label="Approved scene capture ancestor") for parent in source.parents]
    raw = _read_bounded(source, MAX_SOURCE_BYTES)
    for identity in ancestors:
        verify_directory(identity, label="Approved scene capture ancestor")
    try:
        with Image.open(io.BytesIO(raw), formats=["PNG"]) as original:
            width, height = original.size
            if width < 1 or height < 1 or width * height > MAX_SOURCE_PIXELS or getattr(original, "n_frames", 1) != 1:
                raise ValueError("Approved scene capture exceeds the pixel limit or is animated.")
            original.load()
            rgba = original.convert("RGBA")
        with rgba:
            with rgba.copy() as thumbnail:
                thumbnail.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                thumbnail.save(output, format="PNG")
                png = output.getvalue()
                preview_width, preview_height = thumbnail.size
            while len(png) > MAX_PREVIEW_BYTES and (preview_width, preview_height) != (1, 1):
                preview_width = max(1, int(preview_width * 0.75))
                preview_height = max(1, int(preview_height * 0.75))
                # Resize each candidate from the source to avoid accumulating blur.
                with rgba.resize((preview_width, preview_height), Image.Resampling.LANCZOS) as thumbnail:
                    output = io.BytesIO()
                    thumbnail.save(output, format="PNG")
                    png = output.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Approved scene capture is not a supported PNG image.") from exc
    if not 0 < len(png) <= MAX_PREVIEW_BYTES:
        raise ValueError("Scene capture PNG preview exceeds 1 MiB.")

    capture_id = str(uuid.uuid4())
    project_root = os.path.abspath(project_path)
    source_hash = hashlib.sha256(raw).hexdigest()
    png_hash = hashlib.sha256(png).hexdigest()
    identity = {
        "projectRoot": project_root,
        "captureId": capture_id,
        "sessionId": session_id,
        "approvalId": approval_id,
    }
    data = {
        "schema": SCHEMA,
        "sourceSha256": source_hash,
        "sha256": png_hash,
        "width": preview_width,
        "height": preview_height,
        "mimeType": "image/png",
        "captureId": capture_id,
        "pngBase64": base64.b64encode(png).decode("ascii"),
    }
    record = registry.publish(
        base_uri=f"vrcforge://scene-capture-preview/{capture_id}",
        name="Approved scene capture preview",
        resource_type=RESOURCE_TYPE,
        data=data,
        identity=identity,
        source_mode="approved_scene_capture",
        refresh_rule="Immutable approved capture; recapture through the supervised screenshot tool for new evidence.",
        description="Bounded PNG preview from one approved scene capture.",
        context_id=context,
    )
    return {key: value for key, value in data.items() if key != "pngBase64"} | {
        "resourceUri": record["uri"],
        "revision": record["revision"],
        "contentHash": record["contentHash"],
    }


def read_scene_capture_preview(envelope, *, include_verified_image=False):
    if not isinstance(envelope, Mapping):
        raise ValueError("Invalid stored scene capture preview envelope.")
    data = envelope.get("data")
    identity = envelope.get("identity")
    if not isinstance(data, Mapping) or not isinstance(identity, Mapping):
        raise ValueError("Invalid stored scene capture preview envelope.")
    capture_id = str(data.get("captureId") or "")
    try:
        uuid.UUID(capture_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid stored scene capture preview identity.") from exc
    if (
        envelope.get("resourceType") != RESOURCE_TYPE
        or
        data.get("schema") != SCHEMA
        or data.get("mimeType") != "image/png"
        or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("sha256") or ""))
        or not capture_id
        or identity.get("captureId") != capture_id
        or not isinstance(identity.get("projectRoot"), str)
        or not Path(identity.get("projectRoot", "")).is_absolute()
        or not identity.get("sessionId")
        or not identity.get("approvalId")
    ):
        raise ValueError("Invalid stored scene capture preview identity or metadata.")
    encoded = data.get("pngBase64")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_PREVIEW_BYTES + 2) // 3) * 4:
        raise ValueError("Stored scene capture preview exceeds its byte limit.")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Stored scene capture preview is not valid base64.") from exc
    if len(png) > MAX_PREVIEW_BYTES or hashlib.sha256(png).hexdigest() != data.get("sha256"):
        raise ValueError("Stored scene capture preview hash mismatch.")
    try:
        with Image.open(io.BytesIO(png), formats=["PNG"]) as image:
            width, height = image.size
            if (
                type(data.get("width")) is not int
                or type(data.get("height")) is not int
                or width != data.get("width")
                or height != data.get("height")
                or not 1 <= max(width, height) <= MAX_PREVIEW_EDGE
                or width * height > MAX_SOURCE_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("Stored scene capture preview dimensions are invalid or the PNG is animated.")
            image.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Stored scene capture preview is not a valid PNG.") from exc
    content = {
        "uri": str(envelope.get("uri") or ""),
        "mimeType": "image/png",
        "blob": encoded,
        "_meta": {
            "identity": dict(identity),
            "sha256": data["sha256"],
            "revision": envelope.get("revision"),
            "contentHash": envelope.get("contentHash"),
            "capturedAt": envelope.get("capturedAt"),
            "historical": True,
        },
    }
    if "sourceSha256" in data:
        content["_meta"]["sourceSha256"] = data["sourceSha256"]
    if include_verified_image:
        from native_tool_images import _VerifiedNativeImage
        content["_verifiedImage"] = _VerifiedNativeImage(png, "image/png", (width, height), content["uri"])
    return {
        "contents": [content]
    }
