from types import SimpleNamespace
import pytest
import jsonschema
import dashboard_server
from unity_execution_plans_scene import build_scene_execution_plan
from unity_shared_input_schemas import ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA


def batch():
    return {"projectPath": "D:/Project", "clipPath": "Assets/Probe.anim", "curves": [
        {"bindingPath": "Body", "componentType": "SkinnedMeshRenderer", "propertyName": "blendShape.Smile", "keys": [{"time": 0, "value": 0}, {"time": 1, "value": 1}]},
        {"bindingPath": "Hat", "propertyName": "m_IsActive", "constantFloat": 1}]}


def test_public_schema_accepts_single_clip_batch():
    jsonschema.validate(batch(), ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA)


@pytest.mark.parametrize("preview", [True, False])
def test_existing_handler_and_plan_preserve_curves_exactly(monkeypatch, preview):
    calls = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda settings, tool, args, **kwargs: calls.append((tool, args)) or SimpleNamespace(payload={"ok": True}))
    result = dashboard_server.write_animation_curve_sync(batch(), preview=preview)
    assert result["ok"] is True
    plan = build_scene_execution_plan("vrcforge_write_animation_curve", batch())
    assert len(plan) == 1 and plan[0][0] == "vrc_write_animation_curve"
    assert calls == [(plan[0][0], {**plan[0][1], "preview": preview})]
    assert calls[0][1]["curves"] == batch()["curves"]


@pytest.mark.parametrize("extra", [{"propertyName": "m_IsActive"}, {"overwriteExisting": True}, {"keys": []}, {"action": "delete_curve"}])
def test_batch_is_mutually_exclusive_with_single_fields(extra):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**batch(), **extra}, ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA)


def test_old_single_missing_property_still_rejects():
    assert dashboard_server.write_animation_curve_sync({"clipPath": "Assets/Probe.anim"})["ok"] is False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"projectPath": "D:/Project", "clipPath": "Assets/Probe.anim"}, ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA)
