"""The model must see the face scope requested by both planning entry points."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import dashboard_server as app
from dashboard_api_models import DashboardRequest


@pytest.mark.parametrize("entry", ["prepared", "plan"])
@pytest.mark.parametrize("field", ["scope", "filterScope"])
def test_static_scope_reaches_planner_without_expression_or_unknown_rows(monkeypatch, entry, field):
    export = {"summary": {}, "avatars": [{"avatarPath": "Avatar", "avatarName": "Avatar",
        "sceneName": "Scene", "renderers": [{"rendererName": "Face", "rendererPath": "Avatar/Face",
        "blendshapes": [{"name": name, "index": i, "currentWeight": 0} for i, name in enumerate([
            "----------FACEOPTION----------", "jaw_wide", "custom_cheek",
            "----------Expression----------", "smile", "vrc.v_aa"]) ]}]}]}
    before = deepcopy(export)
    monkeypatch.setattr(app, "load_dashboard_settings", lambda _: SimpleNamespace())
    monkeypatch.setattr(app, "load_dashboard_export_payload", lambda *_: (export, "replay", False))
    monkeypatch.setattr(app, "remember_loaded_avatar", lambda *_: None)
    monkeypatch.setattr(app.AVATAR_TUNING_STORES, "load_locked_blendshapes", lambda _: [])
    monkeypatch.setattr(app, "build_reference_image_context", lambda _: None)

    class ModelReached(Exception):
        pass

    def model(_settings, payload, _instruction, **_kwargs):
        names = [shape["name"] for avatar in payload["avatars"]
                 for renderer in avatar["renderers"] for shape in renderer["blendshapes"]]
        assert names == ["jaw_wide"]
        assert payload["summary"]["blendshapeCount"] == 1
        raise ModelReached

    monkeypatch.setattr(app, "create_blendshape_plan", model)
    request = {field: "face_static", "instruction": "adjust the static jaw shape"}
    with pytest.raises(ModelReached):
        if entry == "prepared":
            app._prepare_face_tuning_state(request)
        else:
            app._run_face_tuning_adapter(DashboardRequest(**request), False)
    assert export == before


def test_scope_filter_preserves_default_and_unknown_review():
    from vrchat_blendshape_agent import filter_planning_payload_to_face_blendshapes
    payload = {"avatars": [{"renderers": [{"rendererName": "Face", "blendshapes": [
        {"name": "custom_cheek"}, {"name": "smile"}, {"name": "vrc.v_aa"}]}]}]}
    assert filter_planning_payload_to_face_blendshapes(payload)["summary"]["blendshapeCount"] == 2
    unknown = filter_planning_payload_to_face_blendshapes(payload, scope="face_unknown")
    assert [s["name"] for s in unknown["avatars"][0]["renderers"][0]["blendshapes"]] == ["custom_cheek"]
