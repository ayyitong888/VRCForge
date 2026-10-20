import base64
from copy import deepcopy

import pytest
import dashboard_server as server
from test_texture_asset_preview import preview_env, ASSET


@pytest.fixture
def owned_preview(preview_env):
    project, _path, _core, registry, _invoke = preview_env
    gateway_token = server.AGENT_GATEWAY._mcp_resource_context.set("runtime:media-owner")
    registry_token = registry.context.set("runtime:media-owner")
    try:
        preview = server.get_asset_info_sync({"projectPath": str(project), "assetPath": ASSET,
                                             "includePreview": True})["preview"]
        yield project, registry, preview
    finally:
        registry.context.reset(registry_token)
        server.AGENT_GATEWAY._mcp_resource_context.reset(gateway_token)


def test_native_tool_resource_reader_returns_verified_pixels_without_reopening_source(owned_preview):
    project, registry, preview = owned_preview
    expected = registry.read(preview["resourceUri"])
    (project / ASSET).unlink()
    actual = server._read_native_tool_image_resource(preview, str(project))
    assert actual == expected
    assert base64.b64decode(actual["contents"][0]["blob"], validate=True).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("invalid", ["other-session", "no-session", "other-project", "wrong-sha", "wrong-guid", "wrong-schema"])
def test_native_tool_resource_reader_rejects_unowned_or_unbound_preview(owned_preview, tmp_path, invalid):
    project, _registry, original = owned_preview
    preview = deepcopy(original)
    token = None
    if invalid == "other-session":
        token = server.AGENT_GATEWAY._mcp_resource_context.set("runtime:other")
    elif invalid == "no-session":
        token = server.AGENT_GATEWAY._mcp_resource_context.set(None)
    elif invalid == "other-project":
        project = tmp_path / "different-project"
    elif invalid == "wrong-sha":
        preview["sha256"] = "0" * 64
    elif invalid == "wrong-guid":
        preview["assetGuid"] = "0" * 32
    elif invalid == "wrong-schema":
        preview["schema"] = "not-a-preview"
    try:
        with pytest.raises(ValueError):
            server._read_native_tool_image_resource(preview, str(project))
    finally:
        if token is not None:
            server.AGENT_GATEWAY._mcp_resource_context.reset(token)
