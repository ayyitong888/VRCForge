from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

import dashboard_server as server
from mcp_resource_registry import McpResourceRegistry
from native_tool_images import collect_tool_images
from scene_capture_preview import publish_scene_capture_preview, read_scene_capture_preview


@pytest.fixture
def published_scene_capture(tmp_path, monkeypatch):
    project = tmp_path / "UnityProject"
    project.mkdir()
    capture_dir = tmp_path / "approved-captures"
    capture_dir.mkdir()
    image_path = capture_dir / "capture.png"
    Image.new("RGBA", (48, 32), (240, 80, 20, 255)).save(image_path, format="PNG")

    registry = McpResourceRegistry(tmp_path / "registry")
    context_id = "runtime:scene-capture-fixture"
    old_registry = server.AGENT_GATEWAY._mcp_resources
    monkeypatch.setattr(server.AGENT_GATEWAY, "_mcp_resources", registry)
    gateway_context = server.AGENT_GATEWAY._mcp_resource_context.set(context_id)
    registry_context = registry.context.set(context_id)
    try:
        preview = publish_scene_capture_preview(
            registry,
            image_path,
            str(project),
            {"sessionId": "session-fixture", "approvalId": "approval-fixture"},
            context_id,
        )
        yield project, registry, context_id, preview
    finally:
        registry.context.reset(registry_context)
        server.AGENT_GATEWAY._mcp_resource_context.reset(gateway_context)
        server.AGENT_GATEWAY._mcp_resources = old_registry


def _step(result):
    return {
        "tool": "vrcforge_capture_screenshot",
        "status": "executed",
        "actionId": "capture-action-fixture",
        "outcome": {"status": "ok"},
        "result": result,
    }


def test_published_scene_preview_flows_through_dashboard_reader_into_native_model_image(published_scene_capture):
    project, _registry, _context_id, preview = published_scene_capture
    result = {"ok": True, "preview": preview}
    content = collect_tool_images(
        [_step(result)], server._read_native_tool_image_resource, str(project)
    )

    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert preview["resourceUri"] in content[0]["text"]
    assert "capture-action-fixture" in content[0]["text"]
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    decoded = base64.b64decode(data_url.partition(",")[2], validate=True)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.size == (preview["width"], preview["height"])
        assert image.getpixel((0, 0)) == (240, 80, 20, 255)


@pytest.mark.parametrize("invalid", ["other-context", "other-project", "wrong-capture-id"])
def test_scene_preview_reader_rejects_context_project_and_capture_mismatch(
    published_scene_capture, tmp_path, invalid
):
    project, _registry, _context_id, original = published_scene_capture
    preview = dict(original)
    project_path = str(project)
    token = None
    if invalid == "other-context":
        token = server.AGENT_GATEWAY._mcp_resource_context.set("runtime:other-context")
    elif invalid == "other-project":
        other_project = tmp_path / "OtherUnityProject"
        other_project.mkdir()
        project_path = str(other_project)
    elif invalid == "wrong-capture-id":
        preview["captureId"] = "00000000-0000-0000-0000-000000000000"

    try:
        content = collect_tool_images(
            [_step({"ok": True, "preview": preview})],
            server._read_native_tool_image_resource,
            project_path,
        )
    finally:
        if token is not None:
            server.AGENT_GATEWAY._mcp_resource_context.reset(token)

    assert len(content) == 1
    assert "image unavailable" in content[0]["text"].lower()


@pytest.mark.parametrize("source_hash", [None, "not-a-source-digest"])
def test_scene_preview_does_not_require_unused_source_digest(published_scene_capture, source_hash):
    _project, registry, _context_id, preview = published_scene_capture
    envelope = registry.validate_reference(
        preview["resourceUri"], expected_type="scene_capture_preview", context_id=_context_id,
        expected_identity={"captureId": preview["captureId"]},
    )
    if source_hash is None:
        envelope["data"].pop("sourceSha256", None)
    else:
        envelope["data"]["sourceSha256"] = source_hash

    resource = read_scene_capture_preview(envelope)
    assert resource["contents"][0]["uri"] == preview["resourceUri"]
    assert resource["contents"][0]["mimeType"] == "image/png"
    if source_hash is None:
        assert "sourceSha256" not in resource["contents"][0]["_meta"]
    else:
        assert resource["contents"][0]["_meta"]["sourceSha256"] == source_hash


def test_multi_capture_preview_transport_does_not_consume_capture_receipt(
    published_scene_capture, monkeypatch
):
    project, _registry, _context_id, preview = published_scene_capture
    monkeypatch.setattr(
        server.MANAGED_VISUAL_CAPTURE_AUTHORITY,
        "consume",
        lambda *_args, **_kwargs: pytest.fail("Native image transport must not consume the visual audit receipt."),
    )
    result = {
        "ok": True,
        "captureReceipt": "opaque-audit-capability-not-used-by-image-reader",
        "captures": [{"angle": "front", "preview": preview}],
    }
    step = _step(result)
    step["tool"] = "vrcforge_capture_multi_screenshot"

    content = collect_tool_images(
        [step], server._read_native_tool_image_resource, str(project)
    )

    assert len(content) == 2
    assert preview["resourceUri"] in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
