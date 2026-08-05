from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, prepared_call


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")


def test_screenshot_tools_are_registered_as_approved_context_writes_not_direct_reads() -> None:
    assert "def capture_scene_view_direct" not in SOURCE
    assert "def capture_blendshape_visual_proof" not in SOURCE
    assert "Capture is deferred to a separately approved screenshot write." in SOURCE
    assert 'AGENT_GATEWAY.register_tool("vrcforge_capture_screenshot"' not in SOURCE
    for tool_name in ("vrcforge_capture_screenshot", "vrcforge_capture_multi_screenshot"):
        registration = SOURCE.index(f'"{tool_name}",', SOURCE.index("AGENT_GATEWAY.register_write_handler"))
        block = SOURCE[registration:SOURCE.index("AGENT_GATEWAY.register_write_handler", registration + 1)]
        assert "request_preparer=" in block
        assert "requires_approved_execution_context=True" in block
        assert "approved_execution_plan_builder=build_prepared_execution_plan" in block
        assert tool_name in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[tool_name]
        assert handler.checkpoint_prepare_handler is dashboard_server.prepare_authoritative_unity_checkpoint_sync
    assert 'AGENT_GATEWAY.register_tool("vrcforge_vision_audit"' in SOURCE


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


@pytest.mark.parametrize("dimension", [255, 2049])
def test_capture_dimensions_match_core_bounds(dimension: int) -> None:
    with pytest.raises(ValueError):
        dashboard_server.prepare_capture_screenshot_request({"width": dimension}, None)
