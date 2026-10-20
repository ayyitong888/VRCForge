from __future__ import annotations

import pytest
from PIL import Image

from chat_resource_image import read_chat_scene_image_resource
from mcp_resource_registry import McpResourceError, McpResourceRegistry
from scene_capture_preview import publish_scene_capture_preview
import dashboard_server as server
from starlette.requests import Request
from fastapi.testclient import TestClient


def _scene_resource(tmp_path):
    project = tmp_path / "UnityProject"
    project.mkdir()
    image_path = tmp_path / "approved" / "capture.png"
    image_path.parent.mkdir()
    Image.new("RGBA", (12, 8), (120, 40, 220, 255)).save(image_path, format="PNG")
    session_id = "session-chat-image"
    client_turn_id = "turn-chat-image"
    registry = McpResourceRegistry(tmp_path / "registry")
    registry_token = registry.context.set(f"runtime:{session_id}")
    try:
        preview = publish_scene_capture_preview(
            registry,
            image_path,
            str(project),
            {"sessionId": session_id, "approvalId": "approval-chat-image"},
            f"runtime:{session_id}",
        )
    finally:
        registry.context.reset(registry_token)
    uri = preview["resourceUri"]
    reply = f"Screenshot:\n\n![preview]({uri})"
    chat = {
        "id": "chat-image",
        "sessionId": session_id,
        "projectPath": str(project),
        "items": [{
            "type": "agent",
            "response": {
                "sessionId": session_id,
                "clientTurnId": client_turn_id,
                "plan": {"reply": reply},
            },
        }],
    }
    return project, registry, uri, session_id, client_turn_id, chat


def test_chat_image_resource_is_read_only_for_exact_chat_turn_and_project(tmp_path):
    project, registry, uri, session_id, client_turn_id, chat = _scene_resource(tmp_path)
    actual = read_chat_scene_image_resource(
        registry,
        uri=uri,
        session_id=session_id,
        client_turn_id=client_turn_id,
        project_path=str(project),
        chat=chat,
    )
    assert actual["uri"] == uri
    assert actual["mimeType"] == "image/png"
    assert len(actual["sha256"]) == 64
    assert actual["pngBase64"]


@pytest.mark.parametrize("change", ["other-turn", "other-session", "other-project", "uri-not-in-reply"])
def test_chat_image_resource_rejects_nonmatching_chat_binding(tmp_path, change):
    project, registry, uri, session_id, client_turn_id, chat = _scene_resource(tmp_path)
    params = {
        "uri": uri,
        "session_id": session_id,
        "client_turn_id": client_turn_id,
        "project_path": str(project),
        "chat": chat,
    }
    if change == "other-turn":
        params["client_turn_id"] = "turn-other"
    elif change == "other-session":
        params["session_id"] = "session-other"
    elif change == "other-project":
        params["project_path"] = str(tmp_path / "OtherProject")
    else:
        params["chat"] = {**chat, "items": []}
    with pytest.raises(ValueError):
        read_chat_scene_image_resource(registry, **params)


def test_chat_image_resource_rejects_untrusted_or_mutated_uri(tmp_path):
    project, registry, uri, session_id, client_turn_id, chat = _scene_resource(tmp_path)
    for untrusted in (
        "https://example.invalid/image.png",
        uri.replace("store=", "store=0"),
        uri + "&extra=1",
        uri.replace("context=runtime%3A", "context=runtime%3Aother-"),
    ):
        with pytest.raises((ValueError, McpResourceError)):
            read_chat_scene_image_resource(
                registry,
                uri=untrusted,
                session_id=session_id,
                client_turn_id=client_turn_id,
                project_path=str(project),
                chat=chat,
            )


def test_authenticated_chat_image_endpoint_uses_scoped_chat_and_registry(tmp_path, monkeypatch):
    project, registry, uri, session_id, client_turn_id, chat = _scene_resource(tmp_path)
    monkeypatch.setattr(server.AGENT_GATEWAY, "_mcp_resources", registry)
    lookups = []

    def load_chat(chat_id, scope):
        lookups.append((chat_id, scope))
        return chat if chat_id == chat["id"] and scope == str(project) else None

    monkeypatch.setattr(server, "_load_handoff_chat", load_chat)
    request = server.ChatResourceImageRequest(
        uri=uri,
        chatId=chat["id"],
        projectPath=str(project),
        sessionId=session_id,
        clientTurnId=client_turn_id,
    )
    result = server.app_agent_resource_image(request)
    assert result["ok"] is True
    assert result["mimeType"] == "image/png"
    assert result["sha256"]
    assert lookups == [(chat["id"], str(project))]
    auth_scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/app/agent/resource-image",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
    }
    assert server.app_route_requires_auth(Request(auth_scope))


def test_chat_image_http_route_is_registered_and_requires_app_auth(tmp_path, monkeypatch):
    project, registry, uri, session_id, client_turn_id, chat = _scene_resource(tmp_path)
    monkeypatch.setattr(server.AGENT_GATEWAY, "_mcp_resources", registry)
    monkeypatch.setattr(server, "APP_AUTH_REQUIRED", True)
    monkeypatch.setattr(server, "APP_SESSION_TOKEN", "isolated-chat-resource-test-token")
    monkeypatch.setattr(server, "_load_handoff_chat", lambda chat_id, scope: (
        chat if chat_id == chat["id"] and scope == str(project) else None
    ))
    body = {
        "uri": uri,
        "chatId": chat["id"],
        "projectPath": str(project),
        "sessionId": session_id,
        "clientTurnId": client_turn_id,
    }
    # Exercise dispatch, not just the handler: a staged function without its
    # @app.post decorator previously passed the direct-call tests but returned 404.
    client = TestClient(server.app, client=("127.0.0.1", 1234))
    try:
        denied = client.post("/api/app/agent/resource-image", json=body)
        assert denied.status_code == 401
        response = client.post(
            "/api/app/agent/resource-image",
            json=body,
            headers={"Authorization": "Bearer isolated-chat-resource-test-token"},
        )
        assert response.status_code == 200
        assert response.json()["uri"] == uri
        assert response.json()["pngBase64"]
    finally:
        client.close()
