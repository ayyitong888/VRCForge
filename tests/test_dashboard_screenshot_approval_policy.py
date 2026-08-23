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
    assert schema["properties"]["framing"]["enum"] == ["face", "avatar"]
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
        assert handler.checkpoint_prepare_handler is dashboard_server.prepare_authoritative_unity_checkpoint_sync
        assert handler.approval_category == "visual-capture"
        assert handler.allow_future_category is True
    assert "vrcforge_vision_audit" in dashboard_server.AGENT_GATEWAY._tools
    assert "vrcforge_vision_audit_multi" in dashboard_server.AGENT_GATEWAY._tools


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
        lambda _settings, _tool, _arguments: dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"imagePath": str(output_path).replace("\\", "/")}},
        ),
    )

    result = dashboard_server.capture_avatar_screenshot_approved_sync(prepared)

    assert result["ok"] is True
    assert Path(result["imagePath"]) == output_path.resolve()


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
        lambda _settings, _tool, _arguments: dashboard_server.McpResult(
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
        lambda _settings, _tool, arguments: dashboard_server.McpResult(
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
