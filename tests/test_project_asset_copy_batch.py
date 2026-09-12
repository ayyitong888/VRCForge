from pathlib import Path
import pytest
from project_asset_copy import build_wrapper_arguments, build_preview_arguments

def request():
 return {"projectPath":"D:/Unity","copies":[{"sourceAssetPath":"Assets/A.mat","destinationAssetPath":"Assets/VRCForgeGenerated/A.mat"}]}

def test_copy_array_forwarded_through_existing_tool():
 wrapped=build_wrapper_arguments(request())
 assert wrapped["arguments"]["copies"]==request()["copies"]
 assert build_preview_arguments(wrapped["arguments"])["copies"]==request()["copies"]

def test_copy_batch_dispatch_is_separate():
 assert "UnityProjectAssetCopyBatch.HandleCommand" in Path("Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs").read_text(encoding="utf-8")

from copy import deepcopy
from project_asset_copy import bind_authoritative_preview, validate_apply_result, compute_preview_digest, _batch_digest, ProjectAssetCopyError
from test_project_asset_copy import preview_payload, apply_payload
import jsonschema


def batch():
 previews=[];rows=[]
 for i in range(2):
  preview=preview_payload();preview["target"]["assetPath"]=f"Assets/VRCForgeGenerated/Copy{i}.controller";preview["previewDigest"]=compute_preview_digest(preview)
  previews.append(preview);rows.append({"sourceAssetPath":preview["source"]["assetPath"],"destinationAssetPath":preview["target"]["assetPath"]})
 wrapper=build_wrapper_arguments({"projectPath":"D:/Unity","copies":rows})
 payload={"schema":previews[0]["schema"],"operation":previews[0]["operation"],"batch":True,"ok":True,"preview":True,"verified":True,"changed":False,"saved":False,"cleanupRequired":False,"mutationCount":0,"copies":previews,"previewDigest":_batch_digest(previews)}
 return wrapper,payload


def test_batch_seals_all_rows_and_validates_each_saved_result():
 wrapper,preview=batch();canonical,approval=bind_authoritative_preview(wrapper,preview)
 assert len(canonical["arguments"]["expectedCopies"])==2 and approval["mutationCount"]==2
 result=deepcopy(preview);result.update(preview=False,changed=True,saved=True,mutationCount=2)
 result["copies"]=[]
 for i,(row,one) in enumerate(zip(wrapper["arguments"]["copies"],preview["copies"])):
  single,_=bind_authoritative_preview({"toolName":wrapper["toolName"],"projectPath":"D:/Unity","arguments":row},one)
  actual=apply_payload(single);actual["target"]["guid"]=str(i+8)*32;result["copies"].append(actual)
 assert validate_apply_result(canonical["arguments"],result)["verified"] is True
 result["copies"][1]["target"]["objectLayoutDigest"]="f"*64
 with pytest.raises(ProjectAssetCopyError):validate_apply_result(canonical["arguments"],result)


@pytest.mark.parametrize("kind",["duplicate","case","intersection","mixed","count","illegal_last","size"])
def test_batch_rejects_invalid_rows_before_preview(kind):
 wrapper,_=batch();args=wrapper["arguments"]
 if kind=="duplicate":args["copies"][1]=deepcopy(args["copies"][0])
 if kind=="case":args["copies"][1]["destinationAssetPath"]=args["copies"][0]["destinationAssetPath"].replace("Copy0","COPY0")
 if kind=="intersection":args["copies"][1]["sourceAssetPath"]=args["copies"][0]["destinationAssetPath"]
 if kind=="mixed":args["sourceAssetPath"]="Assets/X.mat"
 if kind=="count":args["copies"]*=65
 if kind=="illegal_last":args["copies"][-1]["destinationAssetPath"]="Assets/Outside.mat"
 if kind=="size":args["expectedPreviewDigest"]="x"*(512*1024)
 with pytest.raises(ProjectAssetCopyError):build_preview_arguments(args)


@pytest.mark.parametrize("kind",["explicit","parent","last_digest","aggregate_digest"])
def test_batch_seal_rejects_stale_or_unverified_preview(kind):
 wrapper,preview=batch()
 if kind=="explicit":wrapper["expectedPreviewDigest"]="0"*64
 if kind=="parent":preview["copies"][1]=preview_payload(generated_root_exists=False)
 if kind=="last_digest":preview["copies"][1]["source"]["fileDigest"]="f"*64
 if kind=="aggregate_digest":preview["previewDigest"]="0"*64
 with pytest.raises(ProjectAssetCopyError):bind_authoritative_preview(wrapper,preview)


def test_copy_public_schema_is_conditional_and_bounded():
 import agent_gateway
 for name,build in [("vrcforge_duplicate_project_asset",agent_gateway.canonical_unity_write_tool_input_schema),("vrcforge_preview_project_asset_duplicate",agent_gateway.canonical_unity_read_tool_input_schema)]:
  schema=build(name)
  assert schema["properties"]["copies"]["maxItems"]==128
  assert schema["oneOf"][0]["required"]==["sourceAssetPath","destinationAssetPath"]
  values=request();values["executionTarget"]={"schema":"vrcforge.execution_target.v1","namespace":"test","scope":"project","project":{},"editor":{}}
  jsonschema.validate(values,schema)
  values["sourceAssetPath"]="Assets/A.mat"
  with pytest.raises(jsonschema.ValidationError):jsonschema.validate(values,schema)


def test_128_distinct_copies_and_sealed_payload_limit():
 from project_asset_copy import _batch_rows, _batch_size
 rows=[{"sourceAssetPath":"Assets/A.mat","destinationAssetPath":f"Assets/VRCForgeGenerated/A{i}.mat"} for i in range(128)]
 assert len(_batch_rows({"copies":rows}))==128
 with pytest.raises(ProjectAssetCopyError):_batch_rows({"copies":rows+[rows[0]]})
 with pytest.raises(ProjectAssetCopyError):_batch_size({"expectedCopies":["x"*(512*1024)]})
