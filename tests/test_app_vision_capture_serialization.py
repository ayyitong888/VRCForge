"""App HTTP capture requests retain public aliases at the approval boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import dashboard_server as dashboard
from agent_gateway import AgentGateway
from prepared_unity_execution import prepared_call


@pytest.fixture
def app_capture_client(tmp_path, monkeypatch):
    """Use production routes, service, transactions and preparers; never execute Core."""
    production_handlers = dashboard.AGENT_GATEWAY._write_handlers
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "approval"
    gateway.save_config(config)
    for name in ("vrcforge_capture_screenshot", "vrcforge_capture_multi_screenshot"):
        gateway._write_handlers[name] = production_handlers[name]
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard, "DASHBOARD_ARTIFACTS_DIR", tmp_path / "artifacts")

    def unexpected_core(*_args, **_kwargs):
        pytest.fail("Capture approval creation must not execute Unity")

    monkeypatch.setattr(dashboard, "invoke_unity_mcp", unexpected_core)
    # Mount the production endpoint functions without unrelated desktop auth/lifespan.
    app = FastAPI()
    app.add_api_route("/api/vision/capture", dashboard.capture_avatar_screenshot, methods=["POST"])
    app.add_api_route("/api/vision/capture-multi", dashboard.capture_avatar_multi_screenshot, methods=["POST"])
    app.add_api_route("/api/vision/capture-status", dashboard.read_vision_capture_status, methods=["POST"])
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, gateway


def _capture_approval(client, gateway, payload, *, multi=False):
    response = client.post("/api/vision/capture-multi" if multi else "/api/vision/capture", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    approval = gateway._approvals[body["approval"]["id"]]
    assert approval["status"] == "pending"
    return approval["arguments"]


FRAMED_REQUEST = {
    "projectPath": "E:/Unity/AvatarProject",
    "avatarPath": "TestAvatar",
    "captureScope": "face",
    "angle": "front",
    "captureMode": "scene_view",
    "requirePlayMode": False,
    "width": 960,
    "height": 960,
}


def test_app_framed_capture_replays_observed_reserved_field_failure(app_capture_client):
    client, gateway = app_capture_client
    prepared = _capture_approval(client, gateway, FRAMED_REQUEST)
    tool, call = prepared_call(prepared)
    assert tool == "vrc_capture_scene_view"
    assert call["avatarPath"] == FRAMED_REQUEST["avatarPath"]
    assert call["captureScope"] == "face"
    assert call["captureMode"] == "scene_view"
    assert call["requirePlayMode"] is False
    assert call["width"] == call["height"] == 960
    assert call["pitch"] == call["yaw"] == call["roll"] == 0


def test_app_capture_original_local_request_replay(app_capture_client):
    replay_path = os.environ.get("VRCFORGE_VISION_CAPTURE_REQUEST_REPLAY")
    if not replay_path:
        pytest.skip("Original machine-local request replay is opt-in")
    raw = Path(replay_path).read_bytes()
    client, gateway = app_capture_client
    prepared = _capture_approval(client, gateway, json.loads(raw))
    _, call = prepared_call(prepared)
    assert call["captureScope"] == "face"
    assert call["captureMode"] == "scene_view"
    assert Path(replay_path).read_bytes() == raw


@pytest.mark.parametrize("payload, expected", [
    ({}, {"captureScope": "avatar", "cameraMode": "framed"}),
    ({"captureScope": "face", "pitch": -12, "yaw": 4, "roll": 3},
     {"captureScope": "face", "pitch": -12, "yaw": 4, "roll": 3}),
    ({"cameraMode": "free", "cameraPosition": {"x": 1, "y": 2, "z": 3},
      "targetPosition": {"x": 0, "y": 1, "z": 0}, "upVector": {"x": 0, "y": 1, "z": 0},
      "projection": "perspective", "fieldOfView": 42, "captureScope": "avatar"},
     {"cameraMode": "free", "fieldOfView": 42, "cameraPosition": {"x": 1, "y": 2, "z": 3},
      "targetPosition": {"x": 0, "y": 1, "z": 0}, "upVector": {"x": 0, "y": 1, "z": 0}}),
    ({"cameraMode": "free", "cameraPosition": {"x": 1, "y": 2, "z": 3},
      "targetPosition": {"x": 0, "y": 1, "z": 0}, "upVector": {"x": 0, "y": 1, "z": 0},
      "projection": "orthographic", "orthographicSize": 1.75},
     {"cameraMode": "free", "projection": "orthographic", "orthographicSize": 1.75}),
])
def test_app_single_capture_alias_families_reach_real_preparer(app_capture_client, payload, expected):
    client, gateway = app_capture_client
    prepared = _capture_approval(client, gateway, {"projectPath": "E:/Unity/AvatarProject", **payload})
    _, call = prepared_call(prepared)
    for key, value in expected.items():
        assert call[key] == value


def test_app_multi_capture_preserves_aliases_and_both_angles(app_capture_client):
    client, gateway = app_capture_client
    prepared = _capture_approval(client, gateway, {
        "projectPath": "E:/Unity/AvatarProject", "avatarPath": "TestAvatar",
        "angles": ["front", "back"], "framing": "avatar", "width": 800, "height": 600,
        "requirePlayMode": True, "captureMode": "game_view",
    }, multi=True)
    for index, yaw in enumerate((0, 180)):
        tool, call = prepared_call(prepared, index)
        assert tool == "vrc_capture_scene_view"
        assert call["avatarPath"] == "TestAvatar"
        assert call["captureScope"] == "avatar"
        assert call["requirePlayMode"] is True
        assert call["captureMode"] == "game_view"
        assert (call["width"], call["height"], call["yaw"]) == (800, 600, yaw)


def test_app_capture_status_preserves_aliases_without_approval(app_capture_client, monkeypatch):
    client, gateway = app_capture_client
    seen = []
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda request: SimpleNamespace())

    def core_status(_settings, tool, arguments):
        seen.append((tool, arguments))
        return dashboard.McpResult(exit_code=0, stdout="", stderr="", payload={"data": {"statusOnly": True}})

    monkeypatch.setattr(dashboard, "invoke_unity_mcp", core_status)
    response = client.post("/api/vision/capture-status", json={
        "projectPath": "E:/Unity/AvatarProject", "requirePlayMode": True, "captureMode": "game_view",
    })
    assert response.status_code == 200, response.text
    assert seen == [("vrc_capture_scene_view", {"statusOnly": True, "requirePlayMode": True, "captureMode": "game_view"})]
    assert not gateway._approvals
