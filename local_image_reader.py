"""Read one authorized local or archive image as MCP ImageContent."""
from __future__ import annotations

import base64
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Mapping
from PIL import Image

from chat_attachment_vault import (
    ARCHIVE_MAX_BYTES,
    INSPECT_MAX_ENTRIES,
    ChatAttachmentVaultError,
    _is_unsafe_archive_member,
    classify_upload,
    guard_vault_archive,
)
from texture_preview_resources import (
    MAX_PREVIEW_BYTES,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_PIXELS,
    _read_bounded,
)

TOOL_NAME = "vrcforge_read_local_image"
TOOL_DESCRIPTION = (
    "when-to-use: read a user-authorized local image file, or one image member of an explicitly selected ZIP or "
    "UnityPackage, so its pixels can inform this task. For an archive, provide path and entryPath; for a normal "
    "image, provide path only. The source is bounded to 32 MiB and 16 megapixels; the tool creates an in-memory "
    "PNG preview with a maximum edge of 1024 pixels and a 1 MiB transport limit. Optionally set previewMaxSize "
    "from 16 to 1024 to request a smaller preview. when-NOT-to-use: do not read unrelated files, expand an archive to disk, inspect "
    "Unity assets, or use this for text; use read_text_file for text and Unity texture preview tools for project "
    "assets. Negative example: do not search the whole drive for an image when the user named one package path."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1, "description": "One image file path, or an authorized ZIP/UnityPackage path when entryPath is supplied."},
        "entryPath": {"type": "string", "minLength": 1, "maxLength": 1024, "description": "Exact relative member path inside a ZIP or UnityPackage; omit for a direct image file."},
        "projectPath": {"type": "string", "description": "Absolute existing source root required by external MCP callers; internal calls use a server-minted read scope."},
        "previewMaxSize": {"type": "integer", "minimum": 16, "maximum": 1024, "default": 1024, "description": "Maximum edge in pixels for the in-memory provider PNG preview. Oversized previews are reduced until they fit the 1 MiB image transport limit."},
    },
}

MAX_IMAGE_SOURCE_BYTES = MAX_SOURCE_BYTES
MAX_ARCHIVE_MEMBER_PATH = 1024
MAX_PREVIEW_EDGE = 1024
MIN_PREVIEW_EDGE = 16
_MIME_BY_SIGNATURE = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


class LocalImageReadError(ValueError):
    """A bounded image read failed before any provider input was created."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def read_local_image(params: Mapping[str, Any], *, allowed_roots: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Read one image file or one image member under server-authorized roots.

    The return value uses MCP ImageContent in ``content`` so the existing
    native_tool_images collector can validate and bind pixels to the next
    native provider request without a second vision pipeline.
    """
    from general_agent_tools import _authorized_path, _sensitive_file

    raw_path = str(params.get("path") or "").strip()
    entry_path_value = params.get("entryPath", params.get("entry_path"))
    entry_path = str(entry_path_value or "").strip()
    if not raw_path:
        raise LocalImageReadError("path is required.", reason="path_required")
    if entry_path and len(entry_path) > MAX_ARCHIVE_MEMBER_PATH:
        raise LocalImageReadError("entryPath exceeds the inspection limit.", reason="entry_path_too_long")
    preview_max_size = params.get("previewMaxSize", MAX_PREVIEW_EDGE)
    if type(preview_max_size) is not int or not MIN_PREVIEW_EDGE <= preview_max_size <= MAX_PREVIEW_EDGE:
        raise LocalImageReadError("previewMaxSize must be an integer from 16 to 1024.", reason="preview_size_invalid")
    try:
        source = _authorized_path(raw_path, allowed_roots)
    except PermissionError:
        # Preserve the exact General Agent denial so the Gateway can apply its
        # existing stop-and-explain scope-denial behavior.
        raise
    except (OSError, ValueError) as exc:
        raise LocalImageReadError("Image path could not be resolved in the authorized read scope.", reason="path_unavailable") from exc
    if not source.is_file():
        raise LocalImageReadError("path must identify a regular file.", reason="not_a_file")
    if _sensitive_file(source):
        raise LocalImageReadError("Sensitive files are not readable by the General Agent.", reason="sensitive_file")

    try:
        if entry_path:
            data = _read_archive_member(source, entry_path)
        else:
            data = _read_file_limited(source, MAX_IMAGE_SOURCE_BYTES)
        image, source_mime, width, height = make_image_content(data, preview_max_size)
    except LocalImageReadError:
        raise
    except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile, ChatAttachmentVaultError) as exc:
        reason = getattr(exc, "reason", "image_read_failed")
        raise LocalImageReadError("The selected image could not be read safely.", reason=str(reason)) from exc

    source_name = source.name
    summary = (
        f"Read image {source_name} ({source_mime}, {len(data)} source bytes); "
        f"created {image['mimeType']} preview {width}x{height} ({len(base64.b64decode(image['data']))} bytes)."
    )
    if entry_path:
        summary = (
            f"Read image member {entry_path} from {source_name} ({source_mime}, {len(data)} source bytes); "
            f"created {image['mimeType']} preview {width}x{height} ({len(base64.b64decode(image['data']))} bytes)."
        )
    return {
        "ok": True,
        "status": "ok",
        "summary": summary,
        "sourceName": source_name,
        "sourceBytes": len(data),
        "sourceMimeType": source_mime,
        "previewWidth": width,
        "previewHeight": height,
        **({"entryPath": entry_path} if entry_path else {}),
        "content": [image],
    }


def _read_file_limited(path: Path, limit: int) -> bytes:
    try:
        if path.stat().st_size == 0:
            raise LocalImageReadError("Image file is empty.", reason="empty_image")
        return _read_bounded(path, limit)
    except LocalImageReadError:
        raise
    except (OSError, ValueError) as exc:
        if "exceeds its byte limit" in str(exc):
            raise LocalImageReadError(f"Image source exceeds the {limit}-byte source limit.", reason="image_source_too_large") from exc
        raise LocalImageReadError("Image file could not be read.", reason="file_unreadable") from exc


def _read_archive_member(path: Path, entry_path: str) -> bytes:
    target = entry_path.replace("\\", "/")
    if not target or _is_unsafe_archive_member(target):
        raise LocalImageReadError("Archive entry path is unsafe.", reason="unsafe_entry_path")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError as exc:
        raise LocalImageReadError("Archive file could not be read.", reason="archive_unreadable") from exc
    if size > ARCHIVE_MAX_BYTES:
        raise LocalImageReadError(f"Archive exceeds the {ARCHIVE_MAX_BYTES}-byte limit.", reason="archive_too_large")
    try:
        upload = classify_upload(path.name, head)
        kind = upload["kind"]
        guard_vault_archive(path, kind)
    except ChatAttachmentVaultError as exc:
        raise LocalImageReadError(str(exc), reason=exc.reason) from exc

    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo(target)
                if info.is_dir():
                    raise LocalImageReadError("Archive entry is a directory.", reason="entry_is_directory")
                if info.file_size > MAX_IMAGE_SOURCE_BYTES:
                    raise LocalImageReadError(f"Archive image entry exceeds the {MAX_IMAGE_SOURCE_BYTES}-byte source limit.", reason="image_source_too_large")
                with archive.open(info) as handle:
                    data = handle.read(MAX_IMAGE_SOURCE_BYTES + 1)
        except KeyError as exc:
            raise LocalImageReadError("Archive image entry was not found.", reason="entry_not_found") from exc
        except zipfile.BadZipFile as exc:
            raise LocalImageReadError("Archive is not a readable ZIP file.", reason="bad_archive") from exc
    elif kind == "unitypackage":
        try:
            with tarfile.open(path, mode="r:gz") as archive:
                member = None
                for index, candidate in enumerate(archive, start=1):
                    if index > INSPECT_MAX_ENTRIES:
                        raise LocalImageReadError("Archive entry count exceeds the inspection limit.", reason="entry_count_cap")
                    if candidate.name.replace("\\", "/") == target:
                        member = candidate
                        break
                if member is None:
                    raise LocalImageReadError("Archive image entry was not found.", reason="entry_not_found")
                if not member.isfile():
                    raise LocalImageReadError("Archive entry is not a regular file.", reason="entry_is_directory")
                if member.size > MAX_IMAGE_SOURCE_BYTES:
                    raise LocalImageReadError(f"Archive image entry exceeds the {MAX_IMAGE_SOURCE_BYTES}-byte source limit.", reason="image_source_too_large")
                handle = archive.extractfile(member)
                if handle is None:
                    raise LocalImageReadError("Archive image entry could not be read.", reason="entry_not_found")
                with handle:
                    data = handle.read(MAX_IMAGE_SOURCE_BYTES + 1)
        except tarfile.TarError as exc:
            raise LocalImageReadError("Archive is not a readable UnityPackage.", reason="bad_archive") from exc
    else:
        raise LocalImageReadError("Only ZIP and UnityPackage archives are supported.", reason="unsupported_archive")
    if len(data) > MAX_IMAGE_SOURCE_BYTES:
        raise LocalImageReadError(f"Archive image entry exceeds the {MAX_IMAGE_SOURCE_BYTES}-byte source limit.", reason="image_source_too_large")
    if not data:
        raise LocalImageReadError("Archive image entry is empty.", reason="empty_image")
    return data


def make_image_content(data: bytes, preview_max_size: int) -> tuple[dict[str, Any], str, int, int]:
    """Validate bounded source bytes and normalize one still image for tool transport.

    File/archive and HTTP callers enforce their own source-byte limits before
    sharing this pixel, format, animation, and preview-size validation.
    """
    mime = ""
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = next((candidate for signature, candidate in _MIME_BY_SIGNATURE if data.startswith(signature)), "")
    if not mime:
        raise LocalImageReadError("File content is not a supported still image.", reason="unsupported_image")
    expected_format = {
        "image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF",
        "image/bmp": "BMP", "image/webp": "WEBP",
    }[mime]
    try:
        with Image.open(io.BytesIO(data), formats=[expected_format]) as original:
            width, height = original.size
            if width < 1 or height < 1 or width * height > MAX_SOURCE_PIXELS or getattr(original, "n_frames", 1) != 1:
                raise LocalImageReadError("Image source exceeds the pixel limit or is animated.", reason="image_dimensions_invalid")
            original.load()
            rgba = original.convert("RGBA")
        with rgba:
            edge = preview_max_size
            preview_bytes = b""
            preview_width = preview_height = 0
            while edge >= MIN_PREVIEW_EDGE:
                with rgba.copy() as thumbnail:
                    thumbnail.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    thumbnail.save(output, format="PNG")
                    candidate = output.getvalue()
                    candidate_width, candidate_height = thumbnail.size
                if 0 < len(candidate) <= MAX_PREVIEW_BYTES:
                    preview_bytes = candidate
                    preview_width, preview_height = candidate_width, candidate_height
                    break
                if edge == MIN_PREVIEW_EDGE:
                    break
                edge = max(MIN_PREVIEW_EDGE, edge // 2)
    except LocalImageReadError:
        raise
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        raise LocalImageReadError("Image failed format, size, dimension, or animation validation.", reason="invalid_image") from exc
    if not preview_bytes:
        raise LocalImageReadError("Image preview cannot fit the 1 MiB provider image transport limit.", reason="preview_too_large")
    return {
        "type": "image",
        "data": base64.b64encode(preview_bytes).decode("ascii"),
        "mimeType": "image/png",
        "sha256": hashlib.sha256(preview_bytes).hexdigest(),
        "width": preview_width,
        "height": preview_height,
    }, mime, preview_width, preview_height
