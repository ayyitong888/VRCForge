from types import SimpleNamespace
import jsonschema
import pytest
import dashboard_server
from unity_shared_input_schemas import MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA
from unity_execution_plans_scene import build_scene_execution_plan


def batch():
    return {"projectPath": "D:/Project", "controllerPath": "Assets/FX.controller", "edits": [
        {"action": "ensure_state", "layerName": "Layer", "stateName": "New", "motionClipPath": "Assets/New.anim"},
        {"action": "ensure_transition", "layerName": "Layer", "sourceStateName": "Idle", "destinationStateName": "New"}]}


def test_schema_supports_single_controller_edits():
    jsonschema.validate(batch(), MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA)


@pytest.mark.parametrize("preview", [True, False])
def test_handler_and_plan_forward_exact_edits(monkeypatch, preview):
    calls=[]
    monkeypatch.setattr(dashboard_server,"load_dashboard_settings",lambda _:SimpleNamespace())
    monkeypatch.setattr(dashboard_server,"invoke_unity_mcp",lambda settings,tool,args,**kwargs:calls.append((tool,args)) or SimpleNamespace(payload={"ok":True}))
    assert dashboard_server.manage_fx_animator_sync(batch(),preview=preview)["ok"] is True
    plan=build_scene_execution_plan("vrcforge_manage_fx_animator",batch())
    assert len(plan)==1 and calls==[(plan[0][0],{**plan[0][1],"preview":preview})]
    assert calls[0][1]["edits"]==batch()["edits"]


@pytest.mark.parametrize("extra",[{"action":"ensure_layer"},{"layerName":"Other"},{"avatarPath":"Avatar"}])
def test_single_and_batch_fields_are_exclusive(extra):
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({**batch(),**extra},MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA)


def test_single_without_action_still_rejects():
    assert dashboard_server.manage_fx_animator_sync({"controllerPath":"Assets/FX.controller"})["ok"] is False


def test_optional_interruption_source_is_schema_and_plan_forwarded():
    arguments = {
        "action": "ensure_transition", "layerName": "GeneralMotion",
        "sourceStateName": "DissolveHalf", "destinationStateName": "DissolveDone",
        "interruptionSource": "SourceThenDestination",
    }
    jsonschema.validate({"projectPath": "D:/Project", **arguments}, MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA)
    plan = build_scene_execution_plan("vrcforge_manage_fx_animator", arguments)
    assert plan[0][1]["interruptionSource"] == "SourceThenDestination"


def test_batch_ordered_interruption_false_is_schema_and_plan_forwarded():
    arguments = {
        "projectPath": "D:/Project", "controllerPath": "Assets/FX.controller",
        "edits": [{"action": "ensure_transition", "layerName": "GeneralMotion",
                   "sourceStateName": "DissolveHalf", "destinationStateName": "DissolveDone",
                   "orderedInterruption": False}],
    }
    jsonschema.validate(arguments, MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA)
    plan = build_scene_execution_plan("vrcforge_manage_fx_animator", arguments)
    assert plan[0][1]["edits"][0]["orderedInterruption"] is False
