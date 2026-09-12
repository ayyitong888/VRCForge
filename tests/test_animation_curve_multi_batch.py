import copy
import pytest
import jsonschema
import dashboard_server
from unity_shared_input_schemas import ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA as SCHEMA
from unity_execution_plans_scene import build_scene_execution_plan

def request():
    return {"projectPath":"D:/Project", "clips":[{"clipPath":"Assets/A.anim","curves":[{"propertyName":"m_IsActive","constantFloat":1}]}]}

def test_multi_clip_schema_and_exact_plan():
    value=request()
    jsonschema.validate(value,SCHEMA)
    plan=build_scene_execution_plan("vrcforge_write_animation_curve",value)
    assert plan==[("vrc_write_animation_curve",{"clips":value["clips"],"preview":False})]

@pytest.mark.parametrize("field,value",[("clipPath","Assets/A.anim"),("curves",[]),("propertyName","m_IsActive"),("action","delete_curve")])
def test_multi_rejects_mixed_single_fields(field,value):
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({**request(),field:value},SCHEMA)

def test_multi_requires_nonempty_clips():
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({"projectPath":"D:/Project","clips":[]},SCHEMA)


@pytest.mark.parametrize("preview",[True,False])
def test_real_handler_forwards_multi_clip_once(monkeypatch,preview):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(dashboard_server,"load_dashboard_settings",lambda _:SimpleNamespace())
    monkeypatch.setattr(dashboard_server,"invoke_unity_mcp",lambda settings,tool,args,**kw:calls.append((tool,args)) or SimpleNamespace(payload={"ok":True}))
    assert dashboard_server.write_animation_curve_sync(request(),preview=preview)["ok"] is True
    assert calls==[("vrc_write_animation_curve",{"clips":request()["clips"],"preview":preview})]
