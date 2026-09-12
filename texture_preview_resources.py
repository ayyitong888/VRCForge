"""Source-image previews captured after authoritative asset-info, never arbitrary file Resources.

Source handles are bounded and closed within capture. Thumbnail bytes belong to
the existing authenticated Gateway Resource registry and its immutable lifetime.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
from PIL import Image
from prepared_file_imports import _stat_identity, capture_directory, verify_directory

RESOURCE_TYPE = "texture_asset_preview"
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PIXELS = 16 * 1024 * 1024
MAX_PREVIEW_BYTES = 1024 * 1024
INTERPRETATION = "Encoded source image RGBA thumbnail; not Unity importer, GPU, shader or material output."


def _read_bounded(path, maximum):
    identity = _stat_identity(path, kind="Texture preview source file")
    if not 0 < identity["size"] <= maximum:
        raise ValueError("Texture preview source exceeds its byte limit.")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (identity["device"], identity["inode"], identity["size"], identity["mtimeNs"]):
            raise ValueError("Texture preview source changed while opening.")
        content = handle.read(maximum + 1)
        closed = os.fstat(handle.fileno())
    if len(content) != identity["size"] or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (closed.st_dev, closed.st_ino, closed.st_size, closed.st_mtime_ns) or _stat_identity(path, kind="Texture preview source file") != identity:
        raise ValueError("Texture preview source changed while reading.")
    return content


def publish_texture_preview(registry, params, asset_info):
    maximum = params.get("previewMaxSize", 256)
    if type(maximum) is not int or not 16 <= maximum <= 1024:
        raise ValueError("previewMaxSize must be an integer from 16 to 1024.")
    if asset_info.get("ok") is not True or asset_info.get("error") or asset_info.get("assetType") != "UnityEngine.Texture2D":
        raise ValueError("Texture preview requires successful Core asset-info for a Texture2D.")
    project = params.get("projectPath")
    if not isinstance(project, str) or not project or not Path(project).is_absolute():
        raise ValueError("Texture preview requires an explicit absolute projectPath.")
    root = Path(os.path.abspath(project))
    asset_path = asset_info.get("assetPath")
    guid = str(asset_info.get("guid") or "").lower()
    if not isinstance(asset_path, str) or not asset_path.startswith("Assets/") or any(part in {"", ".", ".."} for part in asset_path.split("/")) or any(char in asset_path for char in ("\\", ":", "\x00")):
        raise ValueError("Texture preview requires an exact confined Assets path.")
    if not re.fullmatch(r"[0-9a-f]{32}", guid):
        raise ValueError("Texture preview requires a Core asset GUID.")
    requested_path = params.get("assetPath") or params.get("asset_path")
    if requested_path and requested_path != asset_path:
        raise ValueError("Core assetPath differs from requested texture.")
    if params.get("guid") and str(params["guid"]).lower() != guid:
        raise ValueError("Core asset GUID differs from requested texture.")
    source = root / asset_path
    if source.suffix.lower() not in {".png", ".bmp", ".jpg", ".jpeg"}:
        raise ValueError("Texture preview supports persistent PNG, BMP and JPEG source images only.")
    # Existing directory identity helpers reject links/reparse points. Recheck
    # every ancestor after the bounded reads before publishing any pixel data.
    ancestors = [capture_directory(parent, label="Texture preview ancestor") for parent in source.parents]
    raw = _read_bounded(source, MAX_SOURCE_BYTES)
    meta = _read_bounded(Path(str(source) + ".meta"), 128 * 1024)
    if re.findall(r"(?m)^guid:\s*([0-9a-fA-F]{32})\s*$", meta.decode("utf-8-sig")) != [guid]:
        raise ValueError("Texture .meta GUID does not match the Core asset identity.")
    for identity in ancestors:
        verify_directory(identity, label="Texture preview ancestor")
    try:
        with Image.open(io.BytesIO(raw), formats=["PNG", "BMP", "JPEG"]) as source_image:
            width, height = source_image.size
            if width * height > MAX_SOURCE_PIXELS or getattr(source_image, "n_frames", 1) != 1:
                raise ValueError("Texture preview source exceeds the pixel limit or has multiple frames.")
            source_image.load()
            thumbnail = source_image.convert("RGBA")
        with thumbnail:
            thumbnail.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            thumbnail.save(output, format="PNG")
            png = output.getvalue()
            preview_width, preview_height = thumbnail.size
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Texture preview source is not a supported decodable image.") from exc
    if len(png) > MAX_PREVIEW_BYTES:
        raise ValueError("Texture preview exceeds 1 MiB; request a smaller previewMaxSize.")
    source_hash = hashlib.sha256(raw).hexdigest()
    png_hash = hashlib.sha256(png).hexdigest()
    identity = {"projectRoot": str(root), "assetPath": asset_path, "assetGuid": guid}
    data = {"schema": "vrcforge.texture_asset_preview.v1", "assetPath": asset_path, "assetGuid": guid,
            "sourceSha256": source_hash, "dependencyHash": asset_info.get("dependencyHash"),
            "sourceWidth": width, "sourceHeight": height, "width": preview_width, "height": preview_height,
            "mimeType": "image/png", "sha256": png_hash, "interpretation": INTERPRETATION,
            "pngBase64": base64.b64encode(png).decode("ascii")}
    stable_id = hashlib.sha256(json.dumps([str(root), guid, maximum], separators=(",", ":")).encode()).hexdigest()
    record = registry.publish(base_uri=f"vrcforge://texture-preview/{stable_id}", name=f"Texture preview: {asset_path}",
        resource_type=RESOURCE_TYPE, data=data, identity=identity, source_mode="captured_asset_source_image",
        refresh_rule="Immutable source-image preview; explicitly call get_asset_info includePreview for new evidence.",
        description=INTERPRETATION)
    return {key: value for key, value in data.items() if key != "pngBase64"} | {"resourceUri": record["uri"], "revision": record["revision"], "contentHash": record["contentHash"]}


def read_texture_preview(envelope):
    data = envelope["data"]
    if data.get("schema") != "vrcforge.texture_asset_preview.v1" or data.get("mimeType") != "image/png":
        raise ValueError("Invalid stored texture preview.")
    encoded = data.get("pngBase64", "")
    if not isinstance(encoded, str) or len(encoded) > (MAX_PREVIEW_BYTES + 2) // 3 * 4:
        raise ValueError("Stored texture preview exceeds its byte limit.")
    png = base64.b64decode(encoded, validate=True)
    if len(png) > MAX_PREVIEW_BYTES or hashlib.sha256(png).hexdigest() != data.get("sha256"):
        raise ValueError("Stored texture preview hash mismatch.")
    with Image.open(io.BytesIO(png), formats=["PNG"]) as image:
        if image.size != (data.get("width"), data.get("height")) or not 1 <= max(image.size) <= 1024:
            raise ValueError("Stored texture preview dimensions mismatch.")
        image.verify()
    return {"contents": [{"uri": envelope["uri"], "mimeType": "image/png", "blob": encoded,
        "_meta": {"identity": envelope["identity"], "sourceSha256": data["sourceSha256"], "sha256": data["sha256"],
                  "revision": envelope["revision"], "contentHash": envelope["contentHash"],
                  "capturedAt": envelope["capturedAt"], "historical": True, "interpretation": INTERPRETATION}}]}
