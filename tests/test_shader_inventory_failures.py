from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import dashboard_server as dashboard
from external_tool_result_contract import build_external_tool_error


FAILURE = {
    "ok": False,
    "error": "Only a managed execution may write assets.",
    "errorCode": "managed_write_required",
    "failureLayer": "unity_core_pre_route",
    "failurePhase": "before_tool_routing",
    "toolRoutingStarted": False,
    "mutationStarted": False,
    "committed": False,
}


@pytest.mark.parametrize("material_ids", [None, ["mat_shirt"]])
def test_actual_scan_request_matches_an_exact_core_read_shape(monkeypatch, tmp_path, material_ids):
    """Cross the Python/C# boundary that rejected the real default scan."""
    seen = {}
    monkeypatch.setattr(dashboard, "build_dashboard_artifact_path", lambda *_: tmp_path / "scan.json")
    def invoke(_settings, _tool, arguments):
        seen.update(arguments)
        return SimpleNamespace(payload={"materials": []})
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", invoke)
    dashboard.scan_shader_materials_direct(SimpleNamespace(unity_mcp_timeout_seconds=30), "Avatar", material_ids=material_ids)
    source = (Path(__file__).parents[1] / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8-sig")
    gate = source[source.index("private static bool IsStrictNoWritePayloadRead"):]
    gate = gate[gate.index('string.Equals(toolName, "vrc_scan_avatar_materials"'):gate.index('string.Equals(toolName, "vrc_scan_avatar_items"')]
    exact_shapes = [set(re.findall(r'"([A-Za-z]+)"', match)) for match in re.findall(r'HasExactKeys\((.*?)\)', gate, re.S)]
    assert set(seen) in exact_shapes, f"Gateway sends {set(seen)}, but Core only allows {exact_shapes}"
    assert seen["outputPath"] == "" and seen["refreshAssets"] is False


def test_failed_scan_is_not_persisted_as_an_inventory(monkeypatch, tmp_path):
    path = tmp_path / "scan.json"
    monkeypatch.setattr(dashboard, "build_dashboard_artifact_path", lambda *_: path)
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", lambda *_: SimpleNamespace(payload=FAILURE))
    settings = SimpleNamespace(unity_mcp_timeout_seconds=30)
    with pytest.raises(dashboard.UnityMcpError) as failure:
        dashboard.scan_shader_materials_direct(settings, "Avatar")
    assert failure.value.cause_code == "managed_write_required"
    assert failure.value.raw_result == FAILURE
    assert settings.unity_mcp_timeout_seconds == 30
    assert not path.exists()


def test_scan_failure_survives_http_and_external_tool_projection(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard, "build_dashboard_artifact_path", lambda *_: tmp_path / "scan.json")
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", lambda *_: SimpleNamespace(payload=FAILURE))
    monkeypatch.setattr(dashboard, "emit_log", lambda *_: None)
    with pytest.raises(HTTPException) as failure:
        dashboard.scan_shader_materials_sync(dashboard.ShaderMaterialScanRequest(avatar_path="Avatar"))
    error = build_external_tool_error(exception=failure.value, operation_kind="read")
    assert error["errorCode"] == "managed_write_required"
    assert error["failureLayer"] == "unity_core_pre_route"
    assert error["mutationStarted"] is False
    assert error["rawResult"] == FAILURE


def test_failed_supplied_inventory_never_reaches_planner_or_history(monkeypatch):
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _: SimpleNamespace())
    monkeypatch.setattr(dashboard, "emit_log", lambda *_: None)
    planner, history = Mock(), Mock()
    monkeypatch.setattr(dashboard, "create_material_tuning_plan", planner)
    monkeypatch.setattr(dashboard, "save_shader_tuning_history_record", history)
    with pytest.raises(HTTPException):
        dashboard.generate_shader_material_plan_sync(dashboard.ShaderMaterialPlanRequest(
            avatar_path="Avatar", instruction="Add dissolve", inventory=FAILURE,
        ))
    planner.assert_not_called()
    history.assert_not_called()


def test_plan_validation_rejects_failed_inventory_instead_of_skipping_all_changes():
    with pytest.raises(dashboard.UnityMcpError):
        dashboard.validate_shader_material_tuning_plan({"changes": []}, FAILURE)


def test_animation_key_limit_reaches_core(monkeypatch):
    scan = Mock(return_value={"ok": True})
    monkeypatch.setattr(dashboard, "run_unity_artifact_scan_sync", scan)
    dashboard.scan_animation_bindings_sync({"clipPaths": ["Assets/Test.anim"], "maxKeysPerBinding": 17})
    assert scan.call_args.args[3]["maxKeysPerBinding"] == 17
