"""Actual Python binder and exact Core apply-precondition method, not a whole Unity handler."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import types
import pytest
from test_curve_fx_authoring_runtime_contract import method
from test_renderer_material_slot_contract import _preview
ROOT=Path(__file__).resolve().parents[1]

def source(path):
    ref=os.environ.get("VRCFORGE_MATERIAL_SLOT_GIT_REF")
    return subprocess.check_output(["git","show",f"{ref}:{path}"],cwd=ROOT,text=True,encoding="utf-8") if ref else (ROOT/path).read_text(encoding="utf-8")

def binder():
    module=types.ModuleType("actual_slot_binder")
    exec(compile(source("renderer_material_slot_assignment.py"),"renderer_material_slot_assignment.py","exec"),module.__dict__)
    return module

def test_actual_binder_distinguishes_null_from_missing():
    m=binder(); preview=_preview()
    wrapper=m.build_wrapper_arguments({"projectPath":str(ROOT),"rendererPath":preview["rendererPath"],"rendererComponentId":preview["rendererComponentId"],"slotIndex":0,"newMaterialAssetPath":preview["newMaterialAssetPath"]})
    preview["beforeMaterial"]=None
    bound,approval=m.bind_authoritative_preview(wrapper,preview)
    assert bound["arguments"]["expectedBeforeMaterialIsNull"] is True
    assert approval["change"]["beforeMaterial"] is None
    assert "expectedBeforeMaterialGuid" not in bound["arguments"]
    applied=dict(preview,preview=False,mutationStarted=True,applied=True,committed=True,sceneSaved=True,persistedReadback=True,sceneFileDigest="2"*64)
    assert m.validate_apply_result(bound["arguments"],applied)["beforeMaterial"] is None
    missing_apply=dict(applied);missing_apply.pop("beforeMaterial")
    with pytest.raises(m.RendererMaterialSlotError):m.validate_apply_result(bound["arguments"],missing_apply)
    with pytest.raises(m.RendererMaterialSlotError):m.validate_apply_result(bound["arguments"],dict(applied,beforeMaterial=_preview()["beforeMaterial"]))
    missing=dict(preview);missing.pop("beforeMaterial")
    with pytest.raises(m.RendererMaterialSlotError):m.bind_authoritative_preview(wrapper,missing)
    for invalid in ({},False,""):
        invalid_preview=dict(preview,beforeMaterial=invalid)
        with pytest.raises(m.RendererMaterialSlotError):m.bind_authoritative_preview(wrapper,invalid_preview)
    normal,_=m.bind_authoritative_preview(wrapper,_preview())
    assert "expectedBeforeMaterialIsNull" not in normal["arguments"]
    assert normal["arguments"]["expectedBeforeMaterialGuid"]=="d"*32

def test_actual_core_sealed_null_precondition(tmp_path):
    raw=source("Assets/VRCForge/Editor/RendererMaterialSlotTool.cs")
    methods="\n".join(method(raw,s) for s in ("private static string Required(","private static void RequireApplyFields("))
    program=r'''using System;using System.Linq;using Newtonsoft.Json.Linq;
class Shader {public string name="shader";}
class Material {public Shader shader=new Shader();public int renderQueue=2000;}
class MaterialEvidence {public string Path="Assets/Old.mat",Guid="old",Digest="oldhash";}
class SavedSceneSnapshot {public string Path="Assets/Scene.unity",Guid="scene";public int Handle=1;}
class Identity {public string componentType="Renderer";public int componentIndex=0;}
class Target {public Identity Identity=new Identity();}
class Probe {
METHODS
static bool Reject(JObject p,Material m,MaterialEvidence before){try{RequireApplyFields(p,new Target(),new SavedSceneSnapshot(),"renderer",0,m,before,new MaterialEvidence{Guid="new",Digest="newhash"});return false;}catch(InvalidOperationException){return true;}}
static JObject Request(){return new JObject{["expectedScenePath"]="Assets/Scene.unity",["expectedSceneGuid"]="scene",["expectedSceneHandle"]=1,["expectedRendererComponentType"]="Renderer",["expectedRendererComponentIndex"]=0,["rendererComponentId"]="renderer",["slotIndex"]=0,["expectedNewMaterialGuid"]="new",["expectedNewMaterialFileDigest"]="newhash"};}
static void Check(bool ok,string label){if(!ok)throw new Exception(label);}
static int Main(){
var p=Request();p["expectedBeforeMaterialIsNull"]=true;Check(!Reject(p,null,null),"sealed empty slot must accept");
Check(Reject(Request(),null,null),"missing null evidence rejects");
foreach(var value in new JToken[]{new JValue(false),new JValue("true"),JValue.CreateNull()}){p=Request();p["expectedBeforeMaterialIsNull"]=value;Check(Reject(p,null,null),"invalid null evidence rejects");}
p=Request();p["expectedBeforeMaterialIsNull"]=true;p["expectedBeforeMaterialGuid"]="old";Check(Reject(p,null,null),"contradictory evidence rejects");
p=Request();p["expectedBeforeMaterialIsNull"]=true;Check(Reject(p,new Material(),new MaterialEvidence()),"newly occupied slot rejects");
p=Request();p["expectedBeforeMaterialAssetPath"]="Assets/Old.mat";p["expectedBeforeMaterialGuid"]="old";p["expectedBeforeMaterialFileDigest"]="oldhash";p["expectedBeforeMaterialShader"]="shader";p["expectedBeforeMaterialRenderQueue"]=2000;
Check(!Reject(p,new Material(),new MaterialEvidence()),"existing nonempty contract accepts");Check(Reject(p,null,null),"newly emptied slot rejects");
Console.WriteLine("PASS null and existing material identity preconditions");return 0;}
}'''.replace("METHODS",methods)
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"));refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"));dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs:pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1];newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    built=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stdout+built.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    run=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
