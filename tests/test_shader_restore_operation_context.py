"""Real prepared restore dispatch with request-local target; only scan/transport are replaced."""
from types import SimpleNamespace
import pytest
import dashboard_server as d
from operation_context import bind_operation_context
from test_shader_persisted_receipt import receipt

@pytest.mark.parametrize("drift",[False,True])
def test_restore_retains_external_context_scope(monkeypatch,drift):
    avatar="Scene/A"
    target={"scope":"avatar","avatar":{"exactHierarchyPath":avatar},"scene":{"assetPath":"Assets/Scene.unity","guid":"scene-guid"}}
    inventory={"materials":[{"material_id":"mat_skin","item_path":"Scene/A/Body","renderer_scene_path":"Assets/Scene.unity","renderer_scene_guid":"scene-guid","renderer_component_id":"component-id"}]}
    stack={avatar:[[{"material_id":"mat_skin","semantic_property":"smoothness","after":0.2}]]}
    monkeypatch.setattr(d.DASHBOARD_RUNTIME,"shader_undo_stack",stack)
    monkeypatch.setattr(d,"load_dashboard_settings",lambda _:SimpleNamespace())
    scans=[]
    def scan(*a,**kw):scans.append(kw);return inventory
    monkeypatch.setattr(d,"scan_shader_materials_direct",scan)
    calls=[]
    def apply(*args):
        calls.append(args)
        result=receipt([{"material_id":"mat_skin","semantic_property":"smoothness","before":0.8,"after":0.2}])
        result["avatarPath"]=avatar
        return result
    monkeypatch.setattr(d,"apply_shader_material_tuning_direct",apply)
    prepared,_=d.prepare_shader_material_restore_request({"avatar_path":avatar,"executionTarget":target},None)
    prepared.pop("executionTarget")  # Actual agent_approval_transactions external dispatch contract.
    if drift:inventory["materials"][0]["renderer_scene_guid"]="different-scene"
    with bind_operation_context("restore-test",target):
        if drift:
            with pytest.raises(Exception,match="outside the bound scene/Avatar"):
                d.restore_shader_material_plan_approved_sync(prepared)
            assert calls==[] and len(stack[avatar])==1
        else:
            result=d.restore_shader_material_plan_approved_sync(prepared)
            assert result["ok"] is True and result["persistedReadback"] is True
            assert len(calls)==1 and stack[avatar]==[]
    assert len(scans)==2
