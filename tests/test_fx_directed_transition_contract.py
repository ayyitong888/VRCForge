from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import dashboard_server
from unity_execution_plans_scene import build_scene_execution_plan

import unity_read_input_schemas
import unity_write_input_schemas


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8-sig")


def test_directed_transition_uses_real_state_edges_and_preserves_any_state_compatibility() -> None:
    block = SOURCE[SOURCE.index("private static void EnsureTransition"):SOURCE.index("private static List<ConditionSpec>")]

    # Regression: a directed wardrobe edge must be created on the source state,
    # while the omitted source remains the established Any-State operation.
    assert "AddAnyStateTransition(destination)" in block
    assert ".AddTransition(destination)" in block
    assert "Source state not found" in block
    assert "source.RemoveTransition(transitions[directedIndex])" in block
    assert "RemoveAnyStateTransition" in block


def test_transition_preview_validates_and_projects_source_destination_conditions() -> None:
    manage = SOURCE[SOURCE.index("public static class ManageFxAnimatorTool"):]
    assert "ValidateTransitionPreview(controller, @params)" in manage
    assert "ReadConditions(@params)" in manage
    plan_start = SOURCE.index("var plan = new", SOURCE.index("public static class ManageFxAnimatorTool"))
    plan = SOURCE[plan_start:SOURCE.index("if (action == \"delete_parameter\")", plan_start)]
    assert "sourceStateName" in plan
    assert "transitionScope" in plan


def test_public_fx_schema_exposes_optional_exact_source_state() -> None:
    schema = unity_write_input_schemas.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS[
        "vrcforge_manage_fx_animator"
    ]
    source = schema["properties"]["sourceStateName"]
    assert source["type"] == "string"
    assert "source" in source["description"].lower()
    assert "sourceStateName" not in schema["required"]
    preview = unity_read_input_schemas.UNITY_READ_TOOL_INPUT_SCHEMAS[
        "vrcforge_preview_manage_fx_animator"
    ]
    assert preview["properties"]["sourceStateName"] == source


def test_optional_interruption_source_uses_unity_enum_and_is_read_back() -> None:
    schema = unity_write_input_schemas.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_manage_fx_animator"]
    assert schema["properties"]["interruptionSource"]["enum"] == [
        "None", "Source", "Destination", "SourceThenDestination", "DestinationThenSource"
    ]
    assert "ParseInterruptionSource" in SOURCE
    assert "TransitionInterruptionSource.Destination" in SOURCE
    assert "transition.interruptionSource = ParseInterruptionSource" in SOURCE
    assert "interruptionSource = FormatInterruptionSource" in SOURCE
    scan = (ROOT / "Assets/VRCForge/Editor/ComponentTools.cs").read_text(encoding="utf-8-sig")
    assert "interruption_source = stateTransition != null" in scan
    assert "? FormatInterruptionSource(stateTransition.interruptionSource)" in scan
    assert 'private static string FormatInterruptionSource(TransitionInterruptionSource value)' in scan
    assert "ordered_interruption = stateTransition != null && stateTransition.orderedInterruption" in scan


def test_optional_ordered_interruption_is_schema_and_read_back_contract() -> None:
    schema = unity_write_input_schemas.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_manage_fx_animator"]
    assert schema["properties"]["orderedInterruption"] == {"type": "boolean"}
    assert "orderedInterruption" in SOURCE
    assert "transition.orderedInterruption" in SOURCE


@pytest.mark.parametrize("source_field", ["sourceStateName", "source_state_name"])
@pytest.mark.parametrize("preview", [True, False])
def test_directed_source_survives_real_handler_and_approved_plan(monkeypatch, source_field, preview) -> None:
    calls = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _: SimpleNamespace())
    def invoke(_settings, tool, arguments, **_context):
        calls.append((tool, arguments))
        return SimpleNamespace(payload={"ok": True})
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    arguments = {
        "action": "ensure_transition", "layerName": "GeneralMotion",
        source_field: "Idle", "destinationStateName": "RaiseArm",
        "hasExitTime": True, "exitTime": 1.0,
    }
    dashboard_server.manage_fx_animator_sync(arguments, preview=preview)
    plan = build_scene_execution_plan("vrcforge_manage_fx_animator", arguments)
    assert calls[0][1].get("sourceStateName") == "Idle"
    assert plan[0][1].get("sourceStateName") == "Idle"
    assert calls[0] == (plan[0][0], {**plan[0][1], "preview": preview})


def test_omitting_source_retains_legacy_any_state_request() -> None:
    arguments = {"action": "ensure_transition", "layerName": "GeneralMotion", "stateName": "RaiseArm"}
    plan = build_scene_execution_plan("vrcforge_manage_fx_animator", arguments)
    assert "sourceStateName" not in plan[0][1]
