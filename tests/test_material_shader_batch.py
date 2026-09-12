from pathlib import Path
from material_shader_assignment import build_wrapper_arguments,build_preview_arguments

def test_assignments_forwarded():
 rows=[{"materialAssetPath":"Assets/A.mat","shaderName":"Hidden/lilToonCutout"}]
 wrapped=build_wrapper_arguments({"assignments":rows})
 assert build_preview_arguments(wrapped["arguments"])["assignments"]==rows

def test_dispatch_exists():
 assert "UnityMaterialShaderBatch.HandleCommand" in Path("Assets/VRCForge/Editor/MaterialShaderTool.cs").read_text(encoding="utf-8")

from copy import deepcopy
import pytest
from material_shader_assignment import bind_authoritative_preview,validate_apply_result,_batch_digest,MaterialShaderAssignmentError
from test_material_shader_assignment import preview_payload

def batch():
 rows=[];previews=[]
 for i in range(2):
  p=preview_payload();p.update(rendererPath="",rendererScenePath="",rendererSceneGuid="",rendererSceneHandle=-1,rendererComponentId="",rendererComponentType="",rendererComponentIndex=-1,slotIndex=-1,materialAssetPath=f"Assets/A{i}.mat",materialAssetGuid=str(i+1)*32)
  previews.append(p);rows.append({"materialAssetPath":p["materialAssetPath"],"shaderName":p["requestedShader"]})
 return build_wrapper_arguments({"projectPath":"D:/Unity","assignments":rows}),{"schema":previews[0]["schema"],"batch":True,"ok":True,"preview":True,"verified":True,"saved":False,"changed":False,"assignments":previews,"previewDigest":_batch_digest(previews)}

def result(preview):
 out=deepcopy(preview);out.update(preview=False,changed=True,saved=True,persistedReadback=True,committed=True,commitState="committed")
 for row in out["assignments"]:
  row.update(preview=False,changed=True,saved=True,persistedReadback=True,materialFileDigestAfter="f"*64)
  row["readback"]={"materialAssetPath":row["materialAssetPath"],"materialAssetGuid":row["materialAssetGuid"],"materialFileDigest":"f"*64,"shaderName":row["requestedShader"],"shaderAssetPath":row["shaderAssetPath"],"shaderAssetGuid":row["shaderAssetGuid"]}
 return out

def test_batch_strict_preview_and_complete_receipt():
 wrapper,p=batch();canonical,_=bind_authoritative_preview(wrapper,p)
 assert validate_apply_result(canonical["arguments"],result(p))["persistedReadback"] is True
 actual=result(p);actual["assignments"][1]["readback"]["shaderAssetGuid"]="0"*32
 with pytest.raises(MaterialShaderAssignmentError):validate_apply_result(canonical["arguments"],actual)

@pytest.mark.parametrize("kind",["duplicate","renderer","count","size","mixed"])
def test_invalid_batch_rejects_before_preview(kind):
 wrapper,p=batch();args=wrapper["arguments"]
 if kind=="duplicate":args["assignments"][1]["materialAssetPath"]=args["assignments"][0]["materialAssetPath"]
 if kind=="renderer":args["assignments"][1]["rendererPath"]="Avatar/Body"
 if kind=="count":args["assignments"]*=65
 if kind=="size":args["expectedAssignments"]=["x"*(512*1024)]
 if kind=="mixed":args["materialAssetPath"]="Assets/X.mat"
 with pytest.raises(MaterialShaderAssignmentError):build_preview_arguments(args)

def test_explicit_batch_preview_lock_not_replaced():
 wrapper,p=batch();wrapper["expectedPreviewDigest"]="0"*64
 with pytest.raises(MaterialShaderAssignmentError):bind_authoritative_preview(wrapper,p)

def test_single_receipt_behavior_preserved():
 old={"legacy":True};assert validate_apply_result({"materialAssetPath":"Assets/A.mat"},old) is old


@pytest.mark.parametrize("envelope",["arguments","params"])
def test_only_new_batch_receipt_is_strict(envelope):
 from authoritative_unity_writes import authoritative_unity_write_has_strict_result
 assert not authoritative_unity_write_has_strict_result({"toolName":"vrc_set_material_shader",envelope:{"materialAssetPath":"Assets/A.mat","shaderName":"Toon"}})
 assert authoritative_unity_write_has_strict_result({"toolName":"vrc_set_material_shader",envelope:{"assignments":[]}})

def test_public_shader_batch_schema_and_project_scope():
 from unity_shared_input_schemas import MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA
 from mcp_tool_descriptor import identity_scope
 import jsonschema
 args={"projectPath":"D:/Unity","assignments":[{"materialAssetPath":"Assets/A.mat","shaderName":"Toon"}]}
 jsonschema.validate(args,MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA)
 assert identity_scope("vrcforge_set_material_shader",arguments=args)=="project"
 assert identity_scope("vrcforge_set_material_shader",arguments={"arguments":args})=="project"
 args["rendererPath"]="Avatar/Body"
 with pytest.raises(jsonschema.ValidationError):jsonschema.validate(args,MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA)
