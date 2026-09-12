import pytest
import prepared_shader_tuning_writes as guards
from mcp_tool_descriptor import identity_scope


def target(scope="avatar"):
 return {"executionTarget":{"scope":scope,"avatar":{"exactHierarchyPath":"Scene/A"},"scene":{"assetPath":"Assets/Main.unity","guid":"1"*32}}}


def inventory():
 return {"materials":[{"material_id":"same","item_path":"Scene/A/Body","renderer_scene_path":"Assets/Main.unity","renderer_scene_guid":"1"*32,"renderer_component_id":"a"*64}]}


def test_asset_and_semantic_minimum_scopes():
 assert identity_scope("vrcforge_set_material_texture",write=True)=="project"
 assert identity_scope("vrcforge_set_texture_import_settings",write=True)=="project"
 assert identity_scope("vrcforge_apply_shader_tuning",write=True)=="avatar"
 assert identity_scope("vrcforge_restore_shader_tuning",write=True)=="avatar"


@pytest.mark.parametrize("scope",["avatar","component"])
def test_valid_avatar_and_old_component_scope(scope):
 assert guards.require_avatar_material_scope(target(scope),"Scene/A",inventory(),[{"material_id":"same"}])


@pytest.mark.parametrize("kind",["avatar","scene","root","duplicate"])
def test_same_material_id_does_not_authorize_other_avatar(kind):
 facts=inventory();args=target()
 if kind=="avatar":args["executionTarget"]["avatar"]["exactHierarchyPath"]="Other/A"
 if kind=="scene":facts["materials"][0]["renderer_scene_guid"]="2"*32
 if kind=="root":facts["materials"][0]["item_path"]="Other/A/Body"
 if kind=="duplicate":facts["materials"].append(dict(facts["materials"][0]))
 with pytest.raises(RuntimeError):guards.require_avatar_material_scope(args,"Scene/A",facts,[{"material_id":"same"}])


@pytest.mark.parametrize("scope",["avatar","component"])
def test_restore_prepared_scan_and_success_preserve_existing_component(monkeypatch,scope):
 import dashboard_server as d
 from types import SimpleNamespace
 from prepared_unity_execution import build_prepared_execution_plan
 from test_shader_persisted_receipt import receipt
 changes=[{"material_id":"same","semantic_property":"smoothness","after":0.2}]
 monkeypatch.setattr(d.DASHBOARD_RUNTIME,"shader_undo_stack",{"Scene/A":[changes]})
 monkeypatch.setattr(d,"load_dashboard_settings",lambda request:SimpleNamespace())
 scans=[]
 monkeypatch.setattr(d,"scan_shader_materials_direct",lambda *a,**kw:(scans.append(kw) or inventory()))
 actual=receipt([{**changes[0],"before":0.8}]);actual["avatarPath"]="Scene/A"
 monkeypatch.setattr(d,"apply_shader_material_tuning_direct",lambda *a:actual)
 args={**target(scope),"avatarPath":"Scene/A"}
 prepared,_=d.prepare_shader_material_restore_request(args,None)
 assert [row[0] for row in build_prepared_execution_plan(prepared)]==["vrc_scan_avatar_materials","vrc_apply_material_tuning"]
 assert d.restore_shader_material_plan_approved_sync(prepared)["verified"] is True
 assert len(scans)==2 and not d.DASHBOARD_RUNTIME.shader_undo_stack["Scene/A"]


def test_restore_same_material_id_in_other_scene_rejects_before_write(monkeypatch):
 import dashboard_server as d
 from types import SimpleNamespace
 changes=[{"material_id":"same","semantic_property":"smoothness","after":0.2}]
 monkeypatch.setattr(d.DASHBOARD_RUNTIME,"shader_undo_stack",{"Scene/A":[changes]})
 monkeypatch.setattr(d,"load_dashboard_settings",lambda request:SimpleNamespace())
 facts=inventory()
 monkeypatch.setattr(d,"scan_shader_materials_direct",lambda *a,**kw:facts)
 prepared,_=d.prepare_shader_material_restore_request({**target(),"avatarPath":"Scene/A"},None)
 facts["materials"][0]["renderer_scene_guid"]="2"*32
 monkeypatch.setattr(d,"apply_shader_material_tuning_direct",lambda *a:pytest.fail("Wrong scene must not write"))
 with pytest.raises(Exception,match="outside"):d.restore_shader_material_plan_approved_sync(prepared)
 assert d.DASHBOARD_RUNTIME.shader_undo_stack["Scene/A"]==[changes]


def test_receipt_avatar_must_match_approved_avatar():
 with pytest.raises(RuntimeError):guards.require_shader_receipt_avatar(target(),"Scene/A",{"avatarPath":"Other/A"})


def test_semantic_public_metadata_preserves_detailed_targets():
 from mcp_tool_descriptor import standardize_tool_descriptor
 import jsonschema
 for scope in ("avatar","component"):
  descriptor=standardize_tool_descriptor({"name":"vrcforge_restore_shader_tuning","inputSchema":{"type":"object"}},write=True,add_identity_schema=True)
  value={**target(scope)["executionTarget"],"schema":"vrcforge.execution_target.v1","namespace":"test","project":{},"editor":{}}
  jsonschema.validate({"executionTarget":value},descriptor["inputSchema"])
  assert descriptor["requiredIdentity"]["scope"]=="avatar"


@pytest.mark.parametrize("scope",["avatar","component"])
def test_apply_prepared_target_material_facts_and_receipt(monkeypatch,scope):
 import dashboard_server as d
 from test_prepared_shader_tuning_writes import _state, _arguments, _core_applied_result
 state=_state();facts=inventory();facts["materials"][0]["material_id"]="mat_skin";state["scopeInventory"]=facts
 monkeypatch.setattr(d,"_prepare_shader_tuning_apply_state",lambda request:state)
 monkeypatch.setattr(d.DASHBOARD_RUNTIME,"shader_undo_stack",{})
 result=_core_applied_result();result["avatarPath"]="Scene/A"
 monkeypatch.setattr(d,"apply_shader_material_tuning_direct",lambda *a:result)
 prepared,_=d.prepare_shader_material_apply_request({**_arguments(),**target(scope)},None)
 assert d.apply_shader_material_plan_approved_sync(prepared)["verified"] is True
 facts["materials"][0]["renderer_component_id"]="changed"
 with pytest.raises(Exception,match="scope.*drifted"):d.apply_shader_material_plan_approved_sync(prepared)
