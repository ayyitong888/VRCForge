from __future__ import annotations

import base64
import io
import random
import tarfile
import zipfile

import pytest
from PIL import Image

import chat_attachment_vault
import dashboard_server
from local_image_reader import (
    INPUT_SCHEMA,
    MAX_IMAGE_SOURCE_BYTES,
    TOOL_DESCRIPTION,
    LocalImageReadError,
    read_local_image,
)
from native_tool_images import bind_tool_images, collect_tool_images
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


def _png(color=(28, 130, 220, 255)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGBA", (24, 16), color).save(stream, format="PNG")
    return stream.getvalue()


def _as_native_image(result):
    step = {
        "tool": "vrcforge_read_local_image",
        "status": "succeeded",
        "outcome": {"status": "ok"},
        "result": result,
    }
    content = collect_tool_images([step], resource_reader=None, project_path="")
    return [item for item in content if item.get("type") == "image_url"]


def _read_via_registered_tool(params, allowed_root):
    tool = dashboard_server.AGENT_GATEWAY._tools["vrcforge_read_local_image"]
    assert "when-to-use:" in tool.description
    assert "when-NOT-to-use:" in tool.description
    return tool.handler({**params, "_generalAllowedRoots": [str(allowed_root)]})


def _bound_provider_message(result):
    request = bind_tool_images(
        {"messages": [{"role": "user", "content": "Inspect this image."}, {"role": "system", "content": "state"}]},
        _as_native_image(result),
    )
    return request["messages"][-2]["content"]


def test_local_author_image_becomes_native_provider_image_content(tmp_path):
    image_path = tmp_path / "author-front.png"
    expected = _png()
    image_path.write_bytes(expected)

    result = _read_via_registered_tool({"path": str(image_path)}, tmp_path)

    assert result["ok"] is True
    assert result["content"][0]["type"] == "image"
    assert result["content"][0]["mimeType"] == "image/png"
    assert len(_as_native_image(result)) == 1
    assert base64.b64decode(_as_native_image(result)[0]["image_url"]["url"].split(",", 1)[1]) == expected
    provider_content = _bound_provider_message(result)
    assert any(part.get("type") == "image_url" for part in provider_content)


def test_zip_image_member_is_read_in_memory_and_reaches_existing_collector(tmp_path):
    package = tmp_path / "author-package.zip"
    expected = _png((210, 80, 30, 255))
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README-assets/front.png", expected)

    result = _read_via_registered_tool(
        {"path": str(package), "entryPath": "README-assets/front.png"}, tmp_path,
    )

    assert result["entryPath"] == "README-assets/front.png"
    assert len(_as_native_image(result)) == 1
    assert base64.b64decode(result["content"][0]["data"]) == expected
    provider_image = next(part for part in _bound_provider_message(result) if part.get("type") == "image_url")
    assert base64.b64decode(provider_image["image_url"]["url"].split(",", 1)[1]) == expected
    assert not (tmp_path / "README-assets").exists()


def test_source_over_native_transport_limit_is_previewed_and_bound_to_provider(tmp_path):
    width = height = 1200
    pixels = random.Random(11).randbytes(width * height * 3)
    stream = io.BytesIO()
    Image.frombytes("RGB", (width, height), pixels).save(stream, format="PNG")
    source = stream.getvalue()
    assert 1024 * 1024 < len(source) <= MAX_IMAGE_SOURCE_BYTES
    image_path = tmp_path / "large-source.png"
    image_path.write_bytes(source)

    result = _read_via_registered_tool({"path": str(image_path)}, tmp_path)

    assert result["sourceBytes"] == len(source)
    assert result["sourceMimeType"] == "image/png"
    assert max(result["previewWidth"], result["previewHeight"]) <= 1024
    preview = base64.b64decode(result["content"][0]["data"], validate=True)
    assert len(preview) <= 1024 * 1024
    with Image.open(io.BytesIO(preview), formats=["PNG"]) as decoded:
        assert decoded.size == (result["previewWidth"], result["previewHeight"])
        decoded.verify()
    provider_content = _bound_provider_message(result)
    image_part = next(part for part in provider_content if part.get("type") == "image_url")
    assert base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1]) == preview


def test_source_limit_is_distinct_from_preview_transport_limit(tmp_path):
    oversized = tmp_path / "oversized-source.png"
    with oversized.open("wb") as handle:
        handle.truncate(MAX_IMAGE_SOURCE_BYTES + 1)

    with pytest.raises(LocalImageReadError, match="source limit") as error:
        read_local_image({"path": str(oversized)}, allowed_roots=[str(tmp_path)])

    assert error.value.reason == "image_source_too_large"


def test_empty_source_keeps_its_specific_error(tmp_path):
    empty = tmp_path / "empty.png"
    empty.touch()

    with pytest.raises(LocalImageReadError) as error:
        read_local_image({"path": str(empty)}, allowed_roots=[str(tmp_path)])

    assert error.value.reason == "empty_image"


def test_preview_max_size_schema_is_explicit_and_bounded():
    preview_property = INPUT_SCHEMA["properties"]["previewMaxSize"]
    assert preview_property["minimum"] == 16
    assert preview_property["maximum"] == 1024
    assert "1 MiB transport limit" in TOOL_DESCRIPTION


def test_unitypackage_image_member_is_read_without_materialization(tmp_path):
    package = tmp_path / "author-package.unitypackage"
    expected = _png((50, 180, 90, 255))
    info = tarfile.TarInfo("README-assets/front.png")
    info.size = len(expected)
    with tarfile.open(package, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(expected))

    result = _read_via_registered_tool(
        {"path": str(package), "entryPath": "README-assets/front.png"}, tmp_path,
    )

    provider_image = next(part for part in _bound_provider_message(result) if part.get("type") == "image_url")
    assert base64.b64decode(provider_image["image_url"]["url"].split(",", 1)[1]) == expected
    assert base64.b64decode(result["content"][0]["data"]) == expected
    assert not (tmp_path / "README-assets").exists()


def test_path_outside_authorized_roots_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())

    with pytest.raises(PermissionError, match="outside every authorized root"):
        read_local_image({"path": str(outside)}, allowed_roots=[str(allowed)])


def test_archive_bomb_guard_rejects_before_member_bytes_are_returned(tmp_path, monkeypatch):
    package = tmp_path / "compressed.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README-assets/front.png", _png())
        archive.writestr("unrequested/repetitive.bin", b"0" * 4096)
    monkeypatch.setattr(chat_attachment_vault, "INSPECT_BOMB_MIN_UNCOMPRESSED_BYTES", 1)
    monkeypatch.setattr(chat_attachment_vault, "INSPECT_BOMB_RATIO", 2.0)

    with pytest.raises(LocalImageReadError) as error:
        read_local_image(
            {"path": str(package), "entryPath": "README-assets/front.png"},
            allowed_roots=[str(tmp_path)],
        )

    assert error.value.reason == "bomb_ratio"


def test_tool_schema_is_exported_and_documents_both_read_modes():
    assert UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_read_local_image"] == INPUT_SCHEMA
    assert INPUT_SCHEMA["required"] == ["path"]
    assert "entryPath" in INPUT_SCHEMA["properties"]
