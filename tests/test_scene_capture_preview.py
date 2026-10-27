from __future__ import annotations

import base64
import hashlib
import io

import pytest
from PIL import Image

import scene_capture_preview as preview_module
from mcp_resource_registry import McpResourceRegistry
from scene_capture_preview import publish_scene_capture_preview, read_scene_capture_preview


def _source(path, *, size=(1280, 720), color=(30, 120, 220, 255)):
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path.read_bytes()


def test_publish_and_read_approved_scene_capture_uses_registry_identity(tmp_path):
    project = tmp_path / "UnityProject"
    project.mkdir()
    capture_dir = tmp_path / "approved-captures"
    capture_dir.mkdir()
    image_path = capture_dir / "capture.png"
    raw = _source(image_path)
    registry = McpResourceRegistry(tmp_path / "registry")

    published = publish_scene_capture_preview(
        registry,
        image_path,
        str(project),
        {"sessionId": "session-fixture", "approvalId": "approval-fixture"},
        "resource-context-fixture",
    )

    assert published["schema"] == "vrcforge.scene_capture_preview.v1"
    assert published["sourceSha256"] == hashlib.sha256(raw).hexdigest()
    assert published["width"] == 1024 and published["height"] == 576
    assert published["mimeType"] == "image/png"
    resource = registry.read(published["resourceUri"])
    content = resource["contents"][0]
    assert content["mimeType"] == "image/png"
    listed = registry.list(context_id="resource-context-fixture")["resources"]
    assert len(listed) == 1 and listed[0]["mimeType"] == "image/png"
    assert registry.list(context_id="other-resource-context")["resources"] == []
    expected_identity = {
        "projectRoot": str(project.resolve()),
        "captureId": published["captureId"],
        "sessionId": "session-fixture",
        "approvalId": "approval-fixture",
    }
    assert content["_meta"]["identity"] == expected_identity
    envelope = {
        "uri": content["uri"],
        "resourceType": "scene_capture_preview",
        "identity": content["_meta"]["identity"],
        "revision": content["_meta"]["revision"],
        "contentHash": content["_meta"]["contentHash"],
        "capturedAt": content["_meta"]["capturedAt"],
        "data": {
            **published,
            "pngBase64": content["blob"],
        },
    }

    result = read_scene_capture_preview(envelope)
    content = result["contents"][0]
    png = base64.b64decode(content["blob"], validate=True)
    assert content["mimeType"] == "image/png"
    assert content["_meta"]["sourceSha256"] == hashlib.sha256(raw).hexdigest()
    assert content["_meta"]["sha256"] == hashlib.sha256(png).hexdigest() == published["sha256"]
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (1024, 576)
        assert image.getpixel((0, 0)) == (30, 120, 220, 255)
    assert image_path.read_bytes() == raw


@pytest.mark.parametrize(
    ("binding", "context_id"),
    [
        ({"sessionId": "session-fixture"}, "resource-context-fixture"),
        ({"approvalId": "approval-fixture"}, "resource-context-fixture"),
        ({"sessionId": "session-fixture", "approvalId": "approval-fixture"}, ""),
    ],
)
def test_publish_requires_approved_session_binding_and_resource_context(tmp_path, binding, context_id):
    project = tmp_path / "UnityProject"
    project.mkdir()
    image_path = tmp_path / "capture.png"
    _source(image_path, size=(32, 24))
    registry = McpResourceRegistry(tmp_path / "registry")

    with pytest.raises(ValueError):
        publish_scene_capture_preview(registry, image_path, str(project), binding, context_id)

    assert registry.generation == 0


@pytest.mark.parametrize("failure", ["source_limit", "pixel_limit", "not_png"])
def test_publish_rejects_oversized_or_invalid_capture_without_publishing(tmp_path, monkeypatch, failure):
    project = tmp_path / "UnityProject"
    project.mkdir()
    image_path = tmp_path / "capture.png"
    _source(image_path, size=(32, 24))
    if failure == "source_limit":
        monkeypatch.setattr(preview_module, "MAX_SOURCE_BYTES", 1)
    elif failure == "pixel_limit":
        monkeypatch.setattr(preview_module, "MAX_SOURCE_PIXELS", 1)
    else:
        image_path.write_bytes(b"not a PNG")
    registry = McpResourceRegistry(tmp_path / "registry")

    with pytest.raises(ValueError):
        publish_scene_capture_preview(
            registry,
            image_path,
            str(project),
            {"sessionId": "session-fixture", "approvalId": "approval-fixture"},
            "resource-context-fixture",
        )

    assert registry.generation == 0


def test_reader_rejects_tampered_png_hash(tmp_path):
    project = tmp_path / "UnityProject"
    project.mkdir()
    image_path = tmp_path / "capture.png"
    _source(image_path, size=(32, 24))
    registry = McpResourceRegistry(tmp_path / "registry")
    published = publish_scene_capture_preview(
        registry,
        image_path,
        str(project),
        {"sessionId": "session-fixture", "approvalId": "approval-fixture"},
        "resource-context-fixture",
    )
    resource = registry.read(published["resourceUri"])
    content = resource["contents"][0]
    envelope = {
        "uri": content["uri"],
        "resourceType": "scene_capture_preview",
        "identity": content["_meta"]["identity"],
        "revision": content["_meta"]["revision"],
        "contentHash": content["_meta"]["contentHash"],
        "capturedAt": content["_meta"]["capturedAt"],
        "data": {**published, "pngBase64": content["blob"], "sha256": "0" * 64},
    }

    with pytest.raises(ValueError, match="hash"):
        read_scene_capture_preview(envelope)


def test_noisy_transparent_capture_shrinks_to_actual_png_byte_budget(tmp_path):
    import random

    project = tmp_path / "UnityProject"
    project.mkdir()
    image_path = tmp_path / "capture.png"
    # Deterministic incompressible RGB, retaining non-opaque alpha.
    image = Image.frombytes("RGBA", (1024, 1024), random.Random(719).randbytes(1024 * 1024 * 4))
    image.save(image_path, format="PNG")
    raw = image_path.read_bytes()
    assert len(raw) > preview_module.MAX_PREVIEW_BYTES
    registry = McpResourceRegistry(tmp_path / "registry")

    published = publish_scene_capture_preview(
        registry, image_path, str(project),
        {"sessionId": "session-fixture", "approvalId": "approval-fixture"},
        "resource-context-fixture",
    )
    png = base64.b64decode(registry.read(published["resourceUri"])["contents"][0]["blob"])
    assert 0 < len(png) <= preview_module.MAX_PREVIEW_BYTES
    assert published["width"] < 1024
    assert published["width"] == published["height"]
    with Image.open(io.BytesIO(png)) as resized:
        assert resized.mode == "RGBA"
        assert resized.getextrema()[3][0] < 255
        expected = image.resize(resized.size, Image.Resampling.LANCZOS)
        assert resized.tobytes() == expected.tobytes()
    assert published["sourceSha256"] == hashlib.sha256(raw).hexdigest()
    assert image_path.read_bytes() == raw


def test_capture_already_within_budget_keeps_existing_png_encoding(tmp_path):
    project = tmp_path / "UnityProject"
    project.mkdir()
    image_path = tmp_path / "capture.png"
    raw = _source(image_path, size=(1280, 720), color=(30, 120, 220, 150))
    with Image.open(io.BytesIO(raw)) as original:
        image = original.convert("RGBA")
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    expected = io.BytesIO()
    image.save(expected, format="PNG")
    registry = McpResourceRegistry(tmp_path / "registry")
    published = publish_scene_capture_preview(
        registry, image_path, str(project),
        {"sessionId": "session-fixture", "approvalId": "approval-fixture"},
        "resource-context-fixture",
    )
    png = base64.b64decode(registry.read(published["resourceUri"])["contents"][0]["blob"])
    assert png == expected.getvalue()
