from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_gateway
import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, prepared_call


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")


def test_external_capture_schema_exposes_strict_face_or_avatar_framing() -> None:
    schema = agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_capture_screenshot"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["projectPath"]
    assert schema["properties"]["angle"]["enum"] == ["front", "side_left", "side_right", "back", "bottom"]
    assert schema["properties"]["framing"]["enum"] == ["face", "avatar"]
    assert schema["properties"]["captureScope"]["enum"] == ["face", "avatar"]
    for field in ("pitch", "yaw", "roll"):
        assert schema["properties"][field]["minimum"] == -180.0
        assert schema["properties"][field]["maximum"] == 180.0
    assert schema["properties"]["captureMode"]["enum"] == ["auto", "scene_view", "game_view"]


def test_screenshot_tools_are_registered_as_approved_context_writes_not_direct_reads() -> None:
    assert "def capture_scene_view_direct" not in SOURCE
    assert "def capture_blendshape_visual_proof" not in SOURCE
    assert "Capture is deferred to a separately approved screenshot write." in SOURCE
    assert 'AGENT_GATEWAY.register_tool("vrcforge_capture_screenshot"' not in SOURCE
    registry = next(
        node
        for node in ast.parse(SOURCE).body
        if isinstance(node, ast.FunctionDef) and node.name == "register_agent_gateway_tools"
    )
    for tool_name in ("vrcforge_capture_screenshot", "vrcforge_capture_multi_screenshot"):
        registration = next(
            node
            for node in ast.walk(registry)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register_write_handler"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == tool_name
        )
        block = ast.get_source_segment(SOURCE, registration) or ""
        assert "request_preparer=" in block
        assert "requires_approved_execution_context=True" in block
        assert "approved_execution_plan_builder=build_prepared_execution_plan" in block
        assert 'approval_category="visual-capture"' in block
        assert "allow_future_category=True" in block
        assert tool_name in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[tool_name]
        assert handler.pre_write_checkpoint_required is False
        assert handler.checkpoint_prepare_handler is None
        assert handler.approval_category == "visual-capture"
        assert handler.allow_future_category is True
    assert "vrcforge_vision_audit" in dashboard_server.AGENT_GATEWAY._tools
    assert "vrcforge_vision_audit_multi" in dashboard_server.AGENT_GATEWAY._tools


def test_capture_manifest_is_local_artifact_only_and_unity_asset_writes_keep_checkpoint() -> None:
    capture_handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_capture_screenshot"]
    capture_policy = dashboard_server.AGENT_GATEWAY.approval_transactions._write_handler_rollback_policy(capture_handler)
    assert capture_policy["kind"] == "local_artifact_overwrite"
    assert capture_policy["required"] is False
    assert capture_policy["preWriteCheckpointRequired"] is False
    assert capture_policy["checkpointScope"] == []
    assert capture_policy["artifactRoots"] == ["dashboard/latest"]

    asset_handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_set_material_shader"]
    asset_policy = dashboard_server.AGENT_GATEWAY.approval_transactions._write_handler_rollback_policy(asset_handler)
    assert asset_handler.pre_write_checkpoint_required is True
    assert asset_policy["kind"] == "unity_project_checkpoint"
    assert asset_policy["preWriteCheckpointRequired"] is True


def test_single_capture_preparer_freezes_only_the_fixed_dashboard_output_path() -> None:
    prepared, preview = dashboard_server.prepare_capture_screenshot_request(
        {"avatar_path": "Scene/Hero", "width": 512, "height": 384}, None
    )
    tool_name, arguments = prepared_call(prepared)
    expected_path = str((dashboard_server.DASHBOARD_ARTIFACTS_DIR / "latest" / "vision_capture.png").resolve())
    assert tool_name == "vrc_capture_scene_view"
    assert arguments["outputPath"] == expected_path
    assert arguments["setRotation"] is False
    assert preview["outputPaths"] == [expected_path]


def test_single_capture_preparer_supports_one_named_atomic_angle() -> None:
    prepared, preview = dashboard_server.prepare_capture_screenshot_request(
        {"avatar_path": "Scene/Hero", "angle": "SIDE_LEFT"}, None
    )
    tool_name, arguments = prepared_call(prepared)

    assert tool_name == "vrc_capture_scene_view"
    assert arguments["setRotation"] is True
    assert arguments["yaw"] == 90.0
    assert arguments["captureScope"] == "face"
    assert preview["angle"] == "side_left"


def test_named_front_capture_is_eye_level_not_top_down() -> None:
    prepared, preview = dashboard_server.prepare_capture_screenshot_request(
        {"avatar_path": "Scene/Hero", "angle": "front"}, None
    )
    tool_name, arguments = prepared_call(prepared)

    assert tool_name == "vrc_capture_scene_view"
    assert arguments["setRotation"] is True
    assert arguments["pitch"] == 0.0
    assert arguments["yaw"] == 0.0
    assert arguments["roll"] == 0.0
    assert preview["angle"] == "front"


def test_named_bottom_capture_is_a_true_underneath_view() -> None:
    prepared, preview = dashboard_server.prepare_capture_screenshot_request(
        {"avatar_path": "Scene/Hero", "angle": "bottom", "captureScope": "face"}, None
    )
    tool_name, arguments = prepared_call(prepared)

    assert tool_name == "vrc_capture_scene_view"
    assert arguments["setRotation"] is True
    assert arguments["pitch"] == -90.0
    assert arguments["yaw"] == 0.0
    assert arguments["roll"] == 0.0
    assert arguments["captureScope"] == "face"
    assert preview["angle"] == "bottom"
    assert preview["rotation"] == {"pitch": -90.0, "yaw": 0.0, "roll": 0.0}
    assert preview["captureScope"] == "face"


def test_single_capture_preparer_forwards_explicit_rotation_and_scope() -> None:
    prepared, preview = dashboard_server.prepare_capture_screenshot_request(
        {
            "avatarPath": "Scene/Hero",
            "pitch": -82.5,
            "yaw": 12.25,
            "roll": -3.0,
            "captureScope": "face",
        },
        None,
    )
    tool_name, arguments = prepared_call(prepared)

    assert tool_name == "vrc_capture_scene_view"
    assert arguments["setRotation"] is True
    assert arguments["pitch"] == -82.5
    assert arguments["yaw"] == 12.25
    assert arguments["roll"] == -3.0
    assert arguments["captureScope"] == "face"
    assert prepared["rotation"] == {"pitch": -82.5, "yaw": 12.25, "roll": -3.0}
    assert preview["rotation"] == prepared["rotation"]
    assert preview["captureScope"] == "face"


def test_single_capture_preparer_rejects_ambiguous_or_unsafe_rotation() -> None:
    with pytest.raises(ValueError, match="angle cannot be combined"):
        dashboard_server.prepare_capture_screenshot_request(
            {"angle": "bottom", "pitch": -90.0}, None
        )

    with pytest.raises(ValueError):
        dashboard_server.prepare_capture_screenshot_request({"pitch": -181.0}, None)

    with pytest.raises(ValueError, match="framing and captureScope"):
        dashboard_server.prepare_capture_screenshot_request(
            {"framing": "avatar", "captureScope": "face"}, None
        )


def test_single_capture_preparer_allows_explicit_full_avatar_framing_for_named_angle() -> None:
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {"avatarPath": "Scene/Hero", "angle": "front", "framing": "avatar"}, None
    )
    _tool_name, arguments = prepared_call(prepared)

    assert arguments["avatarPath"] == "Scene/Hero"
    assert arguments["captureScope"] == "avatar"
    assert prepared["framing"] == "avatar"


def test_multi_capture_preparer_allows_explicit_full_avatar_framing() -> None:
    prepared, _preview = dashboard_server.prepare_capture_multi_screenshot_request(
        {"avatarPath": "Scene/Hero", "angles": ["front", "back"], "framing": "avatar"}, None
    )

    assert prepared["framing"] == "avatar"
    for index in range(2):
        _tool_name, arguments = prepared_call(prepared, index)
        assert arguments["captureScope"] == "avatar"


def test_multi_capture_defaults_to_five_level_named_angles() -> None:
    prepared, preview = dashboard_server.prepare_capture_multi_screenshot_request({}, None)

    expected = ["front", "side_left", "side_right", "back", "bottom"]
    assert preview["angles"] == expected
    assert [Path(prepared_call(prepared, index)[1]["outputPath"]).stem.removeprefix("vision_") for index in range(5)] == expected
    assert dashboard_server.VisionAuditMultiRequest(imagePaths=[]).angles == expected
    schema = agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_capture_multi_screenshot"]
    assert schema["properties"]["angles"]["default"] == expected
    assert schema["properties"]["angles"]["maxItems"] == 5


def test_capture_preparer_rejects_unknown_public_framing() -> None:
    with pytest.raises(ValueError):
        dashboard_server.prepare_capture_screenshot_request({"framing": "scene"}, None)


def test_single_capture_preparer_preserves_scene_view_mode_during_play_mode() -> None:
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {
            "avatarPath": "Scene/Hero",
            "requirePlayMode": True,
            "captureMode": "scene_view",
        },
        None,
    )
    _tool_name, arguments = prepared_call(prepared)

    assert arguments["requirePlayMode"] is True
    assert arguments["captureMode"] == "scene_view"


@pytest.mark.parametrize("avatar_key", ["avatar_path", "avatarPath"])
def test_single_capture_preparer_accepts_honest_avatar_path_aliases(avatar_key: str) -> None:
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {avatar_key: "Scene/Hero", "requirePlayMode": True}, None
    )
    _tool_name, arguments = prepared_call(prepared)

    assert arguments["avatarPath"] == "Scene/Hero"
    assert arguments["requirePlayMode"] is True


@pytest.mark.parametrize("avatar_key", ["avatar_path", "avatarPath"])
def test_multi_capture_preparer_accepts_honest_avatar_path_aliases(avatar_key: str) -> None:
    prepared, _preview = dashboard_server.prepare_capture_multi_screenshot_request(
        {avatar_key: "Scene/Hero", "angles": ["front"]}, None
    )
    _tool_name, arguments = prepared_call(prepared)

    assert arguments["avatarPath"] == "Scene/Hero"


def test_single_capture_preparer_rejects_unknown_named_angle() -> None:
    with pytest.raises(RuntimeError, match="Unsupported screenshot capture angle"):
        dashboard_server.prepare_capture_screenshot_request({"angle": "diagonal"}, None)


def test_multi_capture_preparer_deduplicates_only_fixed_angles_and_freezes_each_call() -> None:
    prepared, preview = dashboard_server.prepare_capture_multi_screenshot_request(
        {"angles": ["front", "BACK", "front", "side_left"]}, None
    )
    assert preview["angles"] == ["front", "back", "side_left"]
    for index, angle in enumerate(preview["angles"]):
        tool_name, arguments = prepared_call(prepared, index)
        assert tool_name == "vrc_capture_scene_view"
        assert arguments["outputPath"] == str((dashboard_server.DASHBOARD_ARTIFACTS_DIR / "latest" / f"vision_{angle}.png").resolve())
        assert arguments["setRotation"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        {"angles": ["../../outside"]},
        {"angles": ["front", 7]},
        {"angles": ["front"], "outputPath": "C:/outside.png"},
        {"angles": ["front"], "capture_scope": "../../outside"},
        {"angles": ["front"], "set_rotation": False},
        {"angles": ["front"], "status_only": True},
        {"angles": ["front"], "_vrcforge_approved_execution": {}},
        {"angles": ["front"], PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {"calls": []}},
    ],
)
def test_capture_preparer_rejects_unknown_angles_output_paths_and_reserved_fields(arguments: dict[str, object]) -> None:
    with pytest.raises(RuntimeError):
        dashboard_server.prepare_capture_multi_screenshot_request(arguments, None)


def test_execution_handlers_recompute_and_strictly_compare_prepared_calls() -> None:
    single_source = SOURCE[SOURCE.index("def _execute_prepared_scene_view_capture"):SOURCE.index("def capture_avatar_screenshot_approved_sync")]
    assert "prepared_call(arguments, index)" in single_source
    assert "tool_name != expected_tool or tool_arguments != expected_arguments" in single_source
    assert "Unity returned a screenshot path outside the approved capture plan." in single_source


def test_single_capture_handler_accepts_windows_slash_normalization_and_requires_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request({}, None)
    output_path = tmp_path / "latest" / "vision_capture.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, _tool, _arguments, preserve_tool_error=False: dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"imagePath": str(output_path).replace("\\", "/")}},
        ),
    )

    result = dashboard_server.capture_avatar_screenshot_approved_sync(prepared)

    assert result["ok"] is True
    assert Path(result["imagePath"]) == output_path.resolve()


def test_single_capture_handler_reads_back_frozen_rotation_and_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {
            "avatarPath": "Scene/Hero",
            "pitch": -90.0,
            "yaw": 0.0,
            "roll": 0.0,
            "captureScope": "face",
        },
        None,
    )
    output_path = tmp_path / "latest" / "vision_capture.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, _tool, arguments, preserve_tool_error=False: dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "imagePath": arguments["outputPath"],
                    "pitch": arguments["pitch"],
                    "yaw": arguments["yaw"],
                    "roll": arguments["roll"],
                    "captureScope": arguments["captureScope"],
                }
            },
        ),
    )

    result = dashboard_server.capture_avatar_screenshot_approved_sync(prepared)

    assert result["ok"] is True
    assert result["rotation"] == {"pitch": -90.0, "yaw": 0.0, "roll": 0.0}
    assert result["captureScope"] == "face"


def test_single_capture_handler_propagates_core_failure_and_never_reuses_stale_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request({}, None)
    output_path = tmp_path / "latest" / "vision_capture.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"stale-png")
    failure = {
        "success": False,
        "errorCode": "unity_core_unhandled_exception",
        "error": "NullReferenceException in capture routing",
        "failureLayer": "unity_core_dispatch",
        "failurePhase": "request_dispatch_exception",
        "mutationStarted": None,
        "committed": None,
        "commitState": "unknown",
    }

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, _tool, _arguments, preserve_tool_error=False: dashboard_server.McpResult(
            exit_code=1,
            stdout="core failure",
            stderr="",
            payload={"isError": True, "structuredContent": failure},
        ),
    )

    result = dashboard_server.capture_avatar_screenshot_approved_sync(prepared)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["errorCode"] == "unity_core_unhandled_exception"
    assert result["failureLayer"] == "unity_core_dispatch"
    assert "imagePath" not in result


def test_multi_capture_handler_issues_one_task_owned_managed_receipt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    managed = tmp_path / "latest"
    managed.mkdir(parents=True)
    authority = dashboard_server.ManagedVisualCaptureAuthority(managed)
    monkeypatch.setattr(dashboard_server, "MANAGED_VISUAL_CAPTURE_AUTHORITY", authority)
    binding = {
        "taskId": "task-1",
        "sessionId": "session-1",
        "approvalId": "approval-1",
        "requestedActionId": "action-capture-1",
    }
    monkeypatch.setattr(
        dashboard_server,
        "current_approved_unity_execution",
        lambda: SimpleNamespace(diagnostic_context=lambda: dict(binding)),
    )
    prepared, _preview = dashboard_server.prepare_capture_multi_screenshot_request(
        {"angles": ["front", "back"], "avatar_path": "Scene/Hero"}, None
    )
    for angle in ("front", "back"):
        (managed / f"vision_{angle}.png").write_bytes(angle.encode("ascii"))

    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_project_path="D:/Unity/Project"),
    )
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, _tool, arguments, preserve_tool_error=False: dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"imagePath": arguments["outputPath"]}},
        ),
    )

    result = dashboard_server.capture_avatar_multi_screenshot_approved_sync(prepared)
    consumed = authority.consume(result["captureReceipt"], binding=binding)

    assert result["ok"] is True
    assert result["captureEvidenceId"] == consumed["captureEvidenceId"]
    assert result["data"]["angles"] == ["front", "back"]
    assert result["evidence"] == consumed["evidence"]


def test_multi_capture_runs_each_angle_and_preserves_first_core_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    prepared, _preview = dashboard_server.prepare_capture_multi_screenshot_request({}, None)
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_project_path="D:/Unity/Project"),
    )
    calls: list[str] = []
    failure = {
        "success": False,
        "errorCode": "camera_readback_failed",
        "error": "free camera evidence was not returned",
        "failureLayer": "unity_core_dispatch",
        "failurePhase": "camera_evidence_readback",
        "causeChain": [{"code": "missing_camera_evidence", "message": "cameraEvidence missing"}],
        "mutationStarted": True,
        "committed": False,
        "commitState": "rolled_back",
    }

    def invoke(_settings, _tool, arguments, preserve_tool_error=False):
        angle = Path(arguments["outputPath"]).stem.removeprefix("vision_")
        calls.append(angle)
        output_path = Path(arguments["outputPath"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if angle == "side_left":
            return dashboard_server.McpResult(
                exit_code=1,
                stdout="core failure",
                stderr="",
                payload={"isError": True, "structuredContent": failure},
            )
        output_path.write_bytes(angle.encode("ascii"))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"imagePath": str(output_path)}},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.capture_avatar_multi_screenshot_approved_sync(prepared)

    assert calls == ["front", "side_left", "side_right", "back", "bottom"]
    assert result["ok"] is False
    assert result["errorCode"] == failure["errorCode"]
    assert result["failureLayer"] == failure["failureLayer"]
    assert result["failurePhase"] == failure["failurePhase"]
    assert result["causeChain"] == failure["causeChain"]
    assert result["mutationStarted"] is True
    assert result["committed"] is False
    assert result["commitState"] == "rolled_back"
    assert result["failedCount"] == 1
    assert result["completedCount"] == 4
    assert result["failures"][0]["angle"] == "side_left"


def test_dashboard_multi_audit_sends_exact_angles_with_managed_paths() -> None:
    dashboard_source = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    audit_source = dashboard_source[
        dashboard_source.index("async function auditMultiVision"):
        dashboard_source.index("function onVisionAngleTabClick")
    ]
    assert "image_paths: state.multiScreenshots.map" in audit_source
    assert "angles: state.multiScreenshots.map(item => item.angle)" in audit_source


@pytest.mark.parametrize("dimension", [255, 2049])
def test_capture_dimensions_match_core_bounds(dimension: int) -> None:
    with pytest.raises(ValueError):
        dashboard_server.prepare_capture_screenshot_request({"width": dimension}, None)


def test_free_camera_contract_requires_finite_vectors_and_projection_specific_optics() -> None:
    base = {
        "cameraMode": "free",
        "cameraPosition": {"x": 0.0, "y": 1.0, "z": 2.0},
        "targetPosition": {"x": 0.0, "y": 0.0, "z": 0.0},
        "upVector": {"x": 0.0, "y": 1.0, "z": 0.0},
        "projection": "perspective",
        "fieldOfView": 55.0,
    }
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(base, None)
    assert prepared["_vrcforge_prepared_unity_execution"]["calls"][0]["arguments"]["cameraMode"] == "free"
    with pytest.raises(ValueError, match="free cameraMode requires"):
        dashboard_server.prepare_capture_screenshot_request({"cameraMode": "free"}, None)
    with pytest.raises(ValueError, match="excludes fieldOfView"):
        dashboard_server.prepare_capture_screenshot_request({**base, "projection": "orthographic", "orthographicSize": 2.0}, None)
    with pytest.raises(ValueError, match="finite"):
        dashboard_server.prepare_capture_screenshot_request({**base, "cameraPosition": {"x": float("nan"), "y": 1.0, "z": 2.0}}, None)


def test_free_capture_core_call_omits_all_framed_rotation_arguments() -> None:
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {
            "cameraMode": "free",
            "cameraPosition": {"x": 0.0, "y": 1.0, "z": 2.0},
            "targetPosition": {"x": 0.0, "y": 0.0, "z": 0.0},
            "upVector": {"x": 0.0, "y": 1.0, "z": 0.0},
            "projection": "orthographic",
            "orthographicSize": 2.0,
        },
        None,
    )
    call = prepared["_vrcforge_prepared_unity_execution"]["calls"][0]["arguments"]
    assert call["cameraMode"] == "free"
    assert call["captureScope"] == "avatar"
    assert not ({"setRotation", "pitch", "yaw", "roll"} & set(call))


def test_named_angles_are_level_and_external_schema_has_strict_free_camera_branch() -> None:
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request({"angle": "back"}, None)
    call = prepared["_vrcforge_prepared_unity_execution"]["calls"][0]["arguments"]
    assert call["pitch"] == 0.0
    schema = agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_capture_screenshot"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["cameraPosition"]["$ref"] == "#/$defs/vector3"


def _free_camera_evidence(arguments: dict[str, object]) -> dict[str, object]:
    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return {
        "position": arguments["cameraPosition"],
        "target": arguments["targetPosition"],
        "basis": {
            "right": {"x": 1.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
            "forward": {"x": 0.0, "y": 0.0, "z": -1.0},
        },
        "quaternion": {"x": 0.0, "y": 1.0, "z": 0.0, "w": 0.0},
        "projection": arguments["projection"],
        "fieldOfView": arguments["fieldOfView"],
        "aspect": float(arguments["width"]) / float(arguments["height"]),
        "nearClip": 0.01,
        "farClip": 1000.0,
        "matrix": {
            "cameraToWorld": identity,
            "worldToCamera": identity,
            "projection": identity,
            "gpuProjection": identity,
            "viewProjection": identity,
        },
        "matrixOrder": "row_major",
        "coordinateSpace": "unity_world",
    }


def test_free_capture_requires_and_returns_strict_real_camera_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {
            "cameraMode": "free",
            "cameraPosition": {"x": 0.0, "y": 1.0, "z": 2.0},
            "targetPosition": {"x": 0.0, "y": 1.0, "z": 0.0},
            "upVector": {"x": 0.0, "y": 1.0, "z": 0.0},
            "projection": "perspective",
            "fieldOfView": 40.0,
            "width": 800,
            "height": 400,
        },
        None,
    )
    output_path = tmp_path / "latest" / "vision_capture.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())

    def invoke(_settings, _tool, arguments, preserve_tool_error=False):
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"imagePath": arguments["outputPath"], "cameraEvidence": _free_camera_evidence(arguments)}},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.capture_avatar_screenshot_approved_sync(prepared)
    assert result["cameraEvidence"]["position"] == {"x": 0.0, "y": 1.0, "z": 2.0}
    assert result["cameraEvidence"]["matrixOrder"] == "row_major"


def test_free_capture_rejects_camera_readback_drift(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    prepared, _preview = dashboard_server.prepare_capture_screenshot_request(
        {
            "cameraMode": "free",
            "cameraPosition": {"x": 0.0, "y": 1.0, "z": 2.0},
            "targetPosition": {"x": 0.0, "y": 1.0, "z": 0.0},
            "upVector": {"x": 0.0, "y": 1.0, "z": 0.0},
            "projection": "perspective",
            "fieldOfView": 40.0,
        },
        None,
    )
    output_path = tmp_path / "latest" / "vision_capture.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())

    def invoke(_settings, _tool, arguments, preserve_tool_error=False):
        evidence = _free_camera_evidence(arguments)
        evidence["position"] = {"x": 9.0, "y": 1.0, "z": 2.0}
        return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"imagePath": arguments["outputPath"], "cameraEvidence": evidence}})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    with pytest.raises(RuntimeError, match="position outside"):
        dashboard_server.capture_avatar_screenshot_approved_sync(prepared)
