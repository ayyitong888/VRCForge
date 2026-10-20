from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

import dashboard_server as server
from native_tool_images import collect_tool_images
from responses_native_protocol import build_responses_input
from scene_capture_preview import publish_scene_capture_preview
from runtime_planner_service import PlannerTurnMetadata, RuntimePlannerService
from tests.native_planner_fixture import NativePlannerFixture
from test_native_context_guard import _Catalog, _Desktop, _NativeModel, _Turn
from test_texture_asset_preview import ASSET, preview_env


@pytest.fixture
def image_sources(preview_env, tmp_path):
    project, texture_path, _core, registry, _invoke = preview_env
    context_id = "runtime:native-tool-image-continuity"
    gateway_context = server.AGENT_GATEWAY._mcp_resource_context.set(context_id)
    registry_context = registry.context.set(context_id)
    try:
        texture = server.get_asset_info_sync({
            "projectPath": str(project),
            "assetPath": ASSET,
            "includePreview": True,
            "previewMaxSize": 32,
        })["preview"]

        capture_dir = tmp_path / "approved-captures"
        capture_dir.mkdir()
        scenes = []
        for index, color in enumerate(((250, 20, 30, 255), (20, 240, 40, 255), (10, 30, 245, 255))):
            image_path = capture_dir / f"scene-{index}.png"
            Image.new("RGBA", (48, 32), color).save(image_path, format="PNG")
            scenes.append(publish_scene_capture_preview(
                registry,
                image_path,
                str(project),
                {"sessionId": "session-fixture", "approvalId": f"approval-{index}"},
                context_id,
            ))
        yield project, texture_path, registry, texture, scenes
    finally:
        registry.context.reset(registry_context)
        server.AGENT_GATEWAY._mcp_resource_context.reset(gateway_context)


def _step(tool, result, action_id):
    return {
        "tool": tool,
        "kind": "skill",
        "status": "executed",
        "actionId": action_id,
        "outcome": {"status": "ok"},
        "result": {"ok": True, **result},
    }


def _scene_step(preview, action_id):
    return _step("vrcforge_capture_screenshot", {"preview": preview}, action_id)


def _texture_step(preview, action_id):
    return _step("vrcforge_get_asset_info", {"preview": preview}, action_id)


def _image_parts(content):
    return [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]


def _source_captions(content):
    return [
        part["text"]
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
        and "VRCForge tool image observation" in str(part.get("text") or "")
    ]


@pytest.mark.parametrize("scene_first", [True, False])
def test_latest_scene_and_texture_survive_tool_order_and_duplicate_texture_reads(image_sources, scene_first):
    project, _texture_path, _registry, texture, scenes = image_sources
    scene = _scene_step(scenes[0], "scene-latest")
    texture_old = _texture_step(texture, "texture-old")
    texture_latest = _texture_step(texture, "texture-latest")
    steps = [scene, texture_old, texture_latest] if scene_first else [texture_old, texture_latest, scene]

    content = collect_tool_images(steps, server._read_native_tool_image_resource, str(project))

    images = _image_parts(content)
    captions = _source_captions(content)
    assert len(images) == len(captions) == 2
    assert sum(texture["resourceUri"] in caption for caption in captions) == 1
    assert sum(scenes[0]["resourceUri"] in caption for caption in captions) == 1
    texture_caption = next(caption for caption in captions if texture["resourceUri"] in caption)
    assert "texture-latest" in texture_caption
    assert all(len(base64.b64decode(part["image_url"]["url"].partition(",")[2], validate=True)) <= 1024 * 1024
               for part in images)


def test_repeated_scene_source_deduplicates_and_latest_reference_replaces_old(image_sources):
    project, _texture_path, _registry, texture, scenes = image_sources
    steps = [
        _scene_step(scenes[0], "scene-old"),
        _texture_step(texture, "texture-once"),
        _scene_step(scenes[0], "scene-latest"),
    ]

    content = collect_tool_images(steps, server._read_native_tool_image_resource, str(project))

    captions = _source_captions(content)
    images = _image_parts(content)
    assert len(captions) == len(images) == 2
    scene_caption = next(caption for caption in captions if scenes[0]["resourceUri"] in caption)
    assert "scene-latest" in scene_caption
    assert "scene-old" not in scene_caption


def test_many_texture_repeats_do_not_crowd_out_distinct_scene_images(image_sources):
    project, _texture_path, _registry, texture, scenes = image_sources
    steps = [_scene_step(scenes[0], "scene-keep")]
    steps.extend(_texture_step(texture, f"texture-repeat-{index}") for index in range(8))
    steps.extend(_scene_step(preview, f"scene-extra-{index}") for index, preview in enumerate(scenes[1:]))

    content = collect_tool_images(steps, server._read_native_tool_image_resource, str(project))

    captions = _source_captions(content)
    images = _image_parts(content)
    assert len(captions) == len(images) == 4
    assert any(texture["resourceUri"] in caption for caption in captions)
    assert all(any(preview["resourceUri"] in caption for caption in captions) for preview in scenes)
    assert all(len(base64.b64decode(part["image_url"]["url"].partition(",")[2], validate=True)) <= 1024 * 1024
               for part in images)


def test_collection_caps_at_four_distinct_resources(image_sources, tmp_path):
    project, _texture_path, registry, _texture, scenes = image_sources
    capture_dir = tmp_path / "more-approved-captures"
    capture_dir.mkdir()
    previews = list(scenes)
    for index, color in enumerate(((100, 110, 120, 255), (130, 140, 150, 255))):
        extra_path = capture_dir / f"extra-scene-{index}.png"
        Image.new("RGBA", (24, 16), color).save(extra_path, format="PNG")
        previews.append(publish_scene_capture_preview(
            registry,
            extra_path,
            str(project),
            {"sessionId": "session-fixture", "approvalId": f"approval-extra-{index}"},
            "runtime:native-tool-image-continuity",
        ))
    steps = [_scene_step(preview, f"scene-{index}") for index, preview in enumerate(previews)]

    content = collect_tool_images(steps, server._read_native_tool_image_resource, str(project))

    captions = _source_captions(content)
    images = _image_parts(content)
    assert len(captions) == len(images) == 4
    assert all(any(preview["resourceUri"] in caption for caption in captions) for preview in previews[1:])
    assert all(previews[0]["resourceUri"] not in caption for caption in captions)
    assert all(len(base64.b64decode(part["image_url"]["url"].partition(",")[2], validate=True)) <= 1024 * 1024
               for part in images)


def test_latest_authentication_failure_is_unavailable_and_never_reuses_older_same_identity(image_sources):
    project, _texture_path, _registry, texture, _scenes = image_sources
    steps = [
        _texture_step(texture, "texture-valid-old"),
        _texture_step(texture, "texture-invalid-latest"),
    ]
    reads = []
    gateway_context = server.AGENT_GATEWAY._mcp_resource_context.set("runtime:unauthorized-context")
    try:
        def record_read(preview, project_path):
            reads.append(preview["resourceUri"])
            return server._read_native_tool_image_resource(preview, project_path)

        content = collect_tool_images(steps, record_read, str(project))
    finally:
        server.AGENT_GATEWAY._mcp_resource_context.reset(gateway_context)

    assert not _image_parts(content)
    assert len(content) == 1
    assert "image unavailable" in content[0]["text"].lower()
    assert "texture-invalid-latest" in content[0]["text"]
    assert reads == [texture["resourceUri"]]


def test_latest_texture_revision_replaces_same_base_resource_and_keeps_scene(image_sources):
    project, texture_path, _registry, original_texture, scenes = image_sources
    Image.new("RGBA", (64, 32), (210, 40, 220, 255)).save(texture_path, format="PNG")
    latest_texture = server.get_asset_info_sync({
        "projectPath": str(project),
        "assetPath": ASSET,
        "includePreview": True,
        "previewMaxSize": 32,
    })["preview"]
    old_uri = urlsplit(original_texture["resourceUri"])
    latest_uri = urlsplit(latest_texture["resourceUri"])
    assert (old_uri.scheme, old_uri.netloc, old_uri.path) == (latest_uri.scheme, latest_uri.netloc, latest_uri.path)
    assert parse_qs(old_uri.query)["revision"] != parse_qs(latest_uri.query)["revision"]

    reads = []

    def record_read(preview, project_path):
        reads.append(preview["resourceUri"])
        return server._read_native_tool_image_resource(preview, project_path)

    content = collect_tool_images(
        [
            _texture_step(original_texture, "texture-old-revision"),
            _scene_step(scenes[0], "scene-stays"),
            _texture_step(latest_texture, "texture-new-revision"),
        ],
        record_read,
        str(project),
    )

    captions = _source_captions(content)
    images = _image_parts(content)
    assert len(captions) == len(images) == 2
    texture_caption = next(caption for caption in captions if latest_texture["resourceUri"] in caption)
    assert "texture-new-revision" in texture_caption
    assert original_texture["resourceUri"] not in texture_caption
    assert scenes[0]["resourceUri"] in " ".join(captions)
    assert set(reads) == {latest_texture["resourceUri"], scenes[0]["resourceUri"]}
    assert original_texture["resourceUri"] not in reads


def test_tampered_latest_texture_hash_is_unavailable_without_fallback_but_scene_remains(image_sources):
    project, _texture_path, _registry, texture, scenes = image_sources
    tampered_latest = dict(texture)
    tampered_latest["sha256"] = "0" * 64
    reads = []

    def record_read(preview, project_path):
        reads.append(preview["resourceUri"])
        return server._read_native_tool_image_resource(preview, project_path)

    content = collect_tool_images(
        [
            _texture_step(texture, "texture-valid-old"),
            _scene_step(scenes[0], "scene-valid"),
            _texture_step(tampered_latest, "texture-tampered-new"),
        ],
        record_read,
        str(project),
    )

    captions = _source_captions(content)
    images = _image_parts(content)
    assert len(images) == 1
    assert len(captions) == 2
    unavailable = next(caption for caption in captions if "image unavailable" in caption.lower())
    assert "texture-tampered-new" in unavailable
    assert "texture-valid-old" not in " ".join(captions)
    assert scenes[0]["resourceUri"] in " ".join(captions)
    assert scenes[0]["resourceUri"] in reads


def test_real_native_planner_and_responses_input_receive_two_images_without_history_payload(image_sources):
    project, _texture_path, _registry, texture, scenes = image_sources
    steps = [
        _scene_step(scenes[0], "scene-provider"),
        _texture_step(texture, "texture-provider"),
    ]
    model = _NativeModel()
    planner = RuntimePlannerService(
        catalog=_Catalog(),
        desktop=_Desktop(),
        model=model,
        turn=_Turn(PlannerTurnMetadata(verified_context_limit=100_000)),
        tool_image_reader=server._read_native_tool_image_resource,
    )
    native = NativePlannerFixture([{"role": "user", "content": "Compare the scene and texture previews."}])
    usage = {}

    with planner.bind_turn({"provider": "fixture", "model": "native"}):
        planner.plan_agent_turn(
            "Compare the scene and texture previews.",
            {"projectPath": str(project)},
            {},
            loop_state=steps,
            context_usage=usage,
            native_turn=native,
        )

    assert len(model.requests) == 1
    request = model.requests[0]
    messages = request["messages"]
    request_images = [part for message in messages if isinstance(message.get("content"), list)
                      for part in message["content"] if isinstance(part, dict) and part.get("type") == "image_url"]
    assert len(request_images) == 2
    responses_input = build_responses_input(messages)
    response_images = [part for item in responses_input for part in item.get("content", [])
                       if isinstance(part, dict) and part.get("type") == "input_image"]
    assert len(response_images) == 2
    assert "data:image/" not in json.dumps(native.messages())
    assert all(preview["resourceUri"] not in json.dumps(native.messages()) for preview in [texture, scenes[0]])
