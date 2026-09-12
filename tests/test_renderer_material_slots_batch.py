from pathlib import Path
import jsonschema
import pytest
from unity_shared_input_schemas import RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA
from renderer_material_slot_assignment import build_wrapper_arguments, build_preview_arguments


def request():
 return {"projectPath":"D:/Unity", "executionTarget":{"scope":"scene","scene":{"absolutePath":"D:/Unity/Assets/Main.unity","guid":"1"*32,"digest":"2"*64}}, "assignments":[{"rendererPath":"Avatar/Body", "rendererComponentId":"a"*64,"slotIndex":0,"newMaterialAssetPath":"Assets/New.mat"}]}


def test_existing_slot_schema_accepts_bounded_batch():
 jsonschema.validate({k:v for k,v in request().items() if k!="executionTarget"},RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA)


def test_existing_slot_forwarding_preserves_assignments():
 wrapped=build_wrapper_arguments(request())
 assert wrapped["arguments"]["assignments"]==request()["assignments"]
 assert build_preview_arguments(wrapped["arguments"])["assignments"]==request()["assignments"]


def test_batch_core_is_separate_with_existing_dispatch():
 core=Path("Assets/VRCForge/Editor/RendererMaterialSlotTool.cs").read_text(encoding="utf-8")
 assert "UnityRendererMaterialSlotsBatch.HandleCommand(parameters)" in core
 assert Path("Assets/VRCForge/Editor/Generic/UnityRendererMaterialSlotsBatch.cs").is_file()


from copy import deepcopy
from renderer_material_slot_assignment import bind_authoritative_preview, validate_apply_result, RendererMaterialSlotError


def preview():
 row=request()["assignments"][0]
 old={"name":"Old","shader":"lilToon","renderQueue":2000,"assetPath":"Assets/Old.mat","assetGuid":"b"*32,"fileDigest":"c"*64}
 new={**old,"name":"New","assetPath":"Assets/New.mat","assetGuid":"d"*32,"fileDigest":"e"*64}
 before={"rendererPath":row["rendererPath"],"rendererComponentId":row["rendererComponentId"],"rendererComponentType":"UnityEngine.SkinnedMeshRenderer","rendererComponentIndex":0,"slots":["Assets/Old.mat|"+"b"*32,"null","Assets/Keep.mat|"+"f"*32]}
 after=deepcopy(before);after["slots"][0]="Assets/New.mat|"+"d"*32
 plan={"scene":{"scenePath":"Assets/Main.unity","sceneGuid":"1"*32,"sceneHandle":1,"sceneFileDigest":"2"*64,"sceneMetaDigest":"3"*64,"sceneMetaIdentity":"4"*64},"beforeRenderers":[before],"afterRenderers":[after],"assignments":[{**row,"beforeMaterial":old,"newMaterial":new}]}
 return {"schema":"vrcforge.renderer_material_slot.v1","batch":True,"preview":True,"verified":True,"mutationStarted":False,"applied":False,"committed":False,"sceneSaved":False,"persistedReadback":False,"plan":plan}


def test_batch_preview_seals_exact_arrays_and_project_for_one_existing_apply():
 canonical, approval=bind_authoritative_preview(build_wrapper_arguments(request()),preview())
 assert canonical["arguments"]["expectedProjectPath"]=="D:/Unity"
 assert canonical["arguments"]["expectedBatchPlan"]==preview()["plan"]
 assert approval["assignmentCount"]==1 and approval["rendererCount"]==1


@pytest.mark.parametrize("mutation", [
 lambda p:p["plan"]["afterRenderers"][0]["slots"].__setitem__(1,"changed"),
 lambda p:p["plan"]["assignments"][0].update(slotIndex=1),
 lambda p:p["plan"]["assignments"][0]["newMaterial"].update(assetPath="Assets/Other.mat"),
 lambda p:p["plan"]["scene"].update(sceneGuid="bad"),
 lambda p:p.update(sceneSaved=True),
 lambda p:p["plan"]["beforeRenderers"].append(deepcopy(p["plan"]["beforeRenderers"][0])),
])
def test_batch_preview_rejects_drift_and_spoofed_evidence(mutation):
 payload=preview();mutation(payload)
 with pytest.raises(RendererMaterialSlotError):bind_authoritative_preview(build_wrapper_arguments(request()),payload)


def test_null_selected_before_material_is_supported():
 payload=preview();payload["plan"]["assignments"][0]["beforeMaterial"]=None;payload["plan"]["beforeRenderers"][0]["slots"][0]="null"
 assert bind_authoritative_preview(build_wrapper_arguments(request()),payload)[1]["assignmentCount"]==1


def test_batch_explicit_plan_is_not_refreshed():
 wrapped=build_wrapper_arguments(request());wrapped["arguments"]["expectedBatchPlan"]={"stale":True}
 with pytest.raises(RendererMaterialSlotError,match="expectedBatchPlan"):bind_authoritative_preview(wrapped,preview())


def applied():
 result=preview();result.update(preview=False,mutationStarted=True,applied=True,committed=True,sceneSaved=True,persistedReadback=True,commitState="committed",commitStateKnown=True,assignmentCount=1,rendererCount=1)
 result["readback"]={"scene":deepcopy(result["plan"]["scene"]),"renderers":deepcopy(result["plan"]["afterRenderers"])}
 result["readback"]["scene"]["sceneFileDigest"]="5"*64
 return result


def test_batch_apply_requires_independent_saved_arrays():
 canonical,_=bind_authoritative_preview(build_wrapper_arguments(request()),preview())
 assert validate_apply_result(canonical["arguments"],applied())["persistedReadback"] is True


@pytest.mark.parametrize("mutation", [lambda p:p.update(persistedReadback=False),lambda p:p["readback"]["scene"].update(sceneHandle=2),lambda p:p["readback"]["scene"].update(sceneFileDigest="2"*64),lambda p:p["readback"]["renderers"][0]["slots"].__setitem__(2,"changed"),lambda p:p.update(assignmentCount=2)])
def test_batch_apply_rejects_bad_persistence_or_untouched_slot_drift(mutation):
 canonical,_=bind_authoritative_preview(build_wrapper_arguments(request()),preview());result=applied();mutation(result)
 with pytest.raises(RendererMaterialSlotError):validate_apply_result(canonical["arguments"],result)


@pytest.mark.parametrize("field,value", [("rendererPath","Avatar/Body"),("slotIndex",0),("newMaterialAssetPath","Assets/X.mat")])
def test_batch_and_single_fields_are_mutually_exclusive(field,value):
 args=request();args[field]=value
 with pytest.raises(jsonschema.ValidationError):jsonschema.validate(args,RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA)
 with pytest.raises(RendererMaterialSlotError):build_wrapper_arguments(args)



def test_existing_authoritative_adapter_and_scene_plan_use_one_core_call(tmp_path):
 from authoritative_unity_writes import prepare_authoritative_unity_write, validate_authoritative_unity_write_result
 import dashboard_server
 args=request();args["projectPath"]=str(tmp_path);(tmp_path/"Assets").mkdir();args["executionTarget"]["scene"]["absolutePath"]=str(tmp_path/"Assets/Main.unity");calls=[]
 canonical,approval=prepare_authoritative_unity_write(build_wrapper_arguments(args),None,lambda tool,p:(calls.append((tool,p)) or preview()))
 assert len(calls)==1 and calls[0][0]=="vrc_set_renderer_material_slot"
 assert calls[0][1]["assignments"]==args["assignments"]
 assert calls[0][1]["expectedProjectPath"]==str(tmp_path)
 plan=dashboard_server.build_unity_mcp_write_execution_plan(canonical)
 assert len(plan)==1 and plan[0][0]=="vrc_set_renderer_material_slot"
 assert plan[0][1]["assignments"]==args["assignments"] and plan[0][1]["expectedProjectPath"]==str(tmp_path)
 assert validate_authoritative_unity_write_result(canonical,applied())["verified"] is True
 handler=dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_set_renderer_material_slot"]
 assert handler.pre_write_checkpoint_required is True and handler.requires_approved_execution_context is True


def test_batch_identity_scope_is_parameter_aware():
 from mcp_tool_descriptor import identity_scope
 assert identity_scope("vrcforge_set_renderer_material_slot",write=True,arguments=request())=="scene"
 assert identity_scope("vrcforge_set_renderer_material_slot",write=True,arguments={"rendererPath":"Avatar/Body"})=="component"
 assert identity_scope("vrcforge_set_material_shader",write=True,arguments={"materialAssetPath":"Assets/New.mat"})=="project"
 assert identity_scope("vrcforge_set_material_shader",write=True,arguments={"rendererPath":"Avatar/Body"})=="component"

@pytest.mark.parametrize("target", [
 {"scope":"component"},
 {"scope":"project"},
 {"scope":"scene","scene":{"absolutePath":"D:/Unity/Assets/Other.unity","guid":"1"*32,"digest":"2"*64}},
 {"scope":"avatar","scene":{"absolutePath":"D:/Unity/Assets/Main.unity","guid":"1"*32,"digest":"2"*64},"avatar":{"exactHierarchyPath":"OtherAvatar"}},
])
def test_batch_rejects_outside_execution_namespace(target):
 args=request();args["executionTarget"]=target
 with pytest.raises(RendererMaterialSlotError):bind_authoritative_preview(build_wrapper_arguments(args),preview())


def test_avatar_envelope_accepts_only_its_root():
 args=request();args["executionTarget"].update(scope="avatar",avatar={"exactHierarchyPath":"Avatar"})
 assert bind_authoritative_preview(build_wrapper_arguments(args),preview())[1]["assignmentCount"]==1


def test_scope_uses_prepared_envelope_not_idle_outer_fields():
 from mcp_tool_descriptor import identity_scope
 assert identity_scope("vrcforge_set_renderer_material_slot",arguments={"assignments":[],"arguments":{"rendererPath":"Avatar/Body"}})=="component"
 assert identity_scope("vrcforge_set_renderer_material_slot",arguments={"arguments":{"assignments":[]}})=="scene"
 assert identity_scope("vrcforge_set_material_shader",arguments={"materialAssetPath":"Assets/X.mat","arguments":{"rendererPath":"Avatar/Body"}})=="component"
 assert identity_scope("vrcforge_set_material_shader",arguments={"arguments":{"materialAssetPath":"Assets/X.mat"}})=="project"


def test_public_parameter_scope_metadata_and_schema():
 from mcp_tool_descriptor import standardize_tool_descriptor
 for name,values,scope in [("vrcforge_set_renderer_material_slot",{"assignments":[]},"scene"),("vrcforge_set_material_shader",{"materialAssetPath":"Assets/New.mat"},"project")]:
  descriptor=standardize_tool_descriptor({"name":name,"inputSchema":{"type":"object"}},write=True,add_identity_schema=True)
  assert descriptor["requiredIdentity"]["parameterScope"]
  target={"schema":"vrcforge.execution_target.v1","namespace":"test","scope":scope,"project":{},"editor":{}}
  jsonschema.validate({**values,"executionTarget":target},descriptor["inputSchema"])
  target["scope"]="component"
  with pytest.raises(jsonschema.ValidationError):jsonschema.validate({**values,"executionTarget":target},descriptor["inputSchema"])
