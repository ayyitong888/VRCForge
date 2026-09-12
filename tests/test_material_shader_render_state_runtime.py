"""Execute production shader mutation slices against a reset-on-assignment seam.

This models documented Unity queue reset and the observed tag loss; it is not
evidence that Unity's native serialization or a live shader compiled correctly.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("lane", ["single", "batch"])
def test_shader_assignment_preserves_authored_render_state(tmp_path, lane):
    source = (ROOT / "Assets/VRCForge/Editor/MaterialShaderTool.cs").read_text(encoding="utf-8")
    batch = (ROOT / "Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs").read_text(encoding="utf-8")
    helpers = []
    for signature in ["internal static JObject CaptureMaterialRenderState", "internal static void RestoreMaterialRenderState", "internal static void VerifyMaterialRenderState"]:
        if signature in source:
            helpers.append(method(source, signature))
    if lane == "single":
        mutation = source.split("target.material.shader = shader;", 1)[1].split("EditorUtility.SetDirty(target.material);", 1)[0]
        mutation = "target.material.shader = shader;" + mutation
        mutation = mutation.replace("target.material", "material").replace("mutationApplied = true;", "")
    else:
        mutation = batch.split("edit.Material.shader = edit.Shader;", 1)[1].split("edit.ExpectedJson = EditorJsonUtility.ToJson(edit.Material);", 1)[0]
        mutation = "edit.Material.shader = edit.Shader;" + mutation
        mutation = mutation.replace("edit.Material", "material").replace("edit.Shader", "shader").replace("edit.BeforeRenderState", "beforeRenderState").replace("MaterialShaderTool.", "")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet = shutil.which("dotnet")
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    seam = r'''
class Shader {internal string Name="new";}
class Material {
 internal JObject Json; private Shader current;
 internal Shader shader {get=>current;set {current=value;Json["m_Shader"]=value.Name;Json["m_CustomRenderQueue"]=-1;Json["stringTagMap"]=new JObject();}}
}
static class EditorJsonUtility {
 internal static bool LoseTag;
 internal static string ToJson(Material m)=>new JObject{["Material"]=m.Json.DeepClone()}.ToString(Newtonsoft.Json.Formatting.None);
 internal static void FromJsonOverwrite(string json, Material m) {foreach(var p in ((JObject)JObject.Parse(json)["Material"]).Properties())if(!LoseTag||p.Name!="stringTagMap")m.Json[p.Name]=p.Value.DeepClone();}
}
'''
    check_extra = ""
    if helpers:
        check_extra = r'''
 var missing=Make(2460);missing.Json.Remove("stringTagMap");Check(Reject(()=>CaptureMaterialRenderState(missing)),"missing tag map rejects before write");
 missing=Make(2460);missing.Json["m_CustomRenderQueue"]="bad";Check(Reject(()=>CaptureMaterialRenderState(missing)),"noninteger queue rejects before write");
 var corrupted=Make(2460);var state=CaptureMaterialRenderState(corrupted);corrupted.shader=new Shader();EditorJsonUtility.LoseTag=true;Check(Reject(()=>RestoreMaterialRenderState(corrupted,state)),"lost tag restoration rejects");EditorJsonUtility.LoseTag=false;
 corrupted=Make(2460);state=CaptureMaterialRenderState(corrupted);corrupted.Json["m_CustomRenderQueue"]=2000;Check(Reject(()=>VerifyMaterialRenderState(corrupted,state)),"saved queue drift rejects");
 corrupted=Make(2460);state=CaptureMaterialRenderState(corrupted);corrupted.Json["stringTagMap"]=new JObject();Check(Reject(()=>VerifyMaterialRenderState(corrupted,state)),"saved tag drift rejects");
 var nativeArray=Make(2460);nativeArray.Json["stringTagMap"]=new JArray(new JObject{["first"]="RenderType",["second"]="TransparentCutout"});var arrayBefore=nativeArray.Json["stringTagMap"].DeepClone();Assign(nativeArray,new Shader());Check(JToken.DeepEquals(nativeArray.Json["stringTagMap"],arrayBefore),"array map representation remains exact");
 var unknown=Make(2460);unknown.Json["m_Name"]="PRIVATE_MATERIAL_VALUE";unknown.Json.Remove("stringTagMap");try{CaptureMaterialRenderState(unknown);Check(false,"missing native map shape");}catch(InvalidOperationException ex){Check(ex.Message.Contains("topLevelPropertyNames")&&ex.Message.Contains("m_Name")&&ex.Message.Contains("Missing")&&ex.Message.Contains("Integer")&&!ex.Message.Contains("PRIVATE_MATERIAL_VALUE")&&!ex.Message.Contains("literal"),"missing shape diagnosis contains names/types only");}
 unknown=Make(2460);unknown.Json["stringTagMap"]=JValue.CreateNull();try{CaptureMaterialRenderState(unknown);Check(false,"null native map shape");}catch(InvalidOperationException ex){Check(ex.Message.Contains("Null")&&ex.Message.Contains("stringTagMapType"),"null shape distinct from missing");}
'''
    runner = r'''
 static int failures;
 static void Check(bool pass,string label){Console.WriteLine((pass?"PASS ":"FAIL ")+label);if(!pass)failures++;}
 static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
 static Material Make(int queue)=>new Material{Json=new JObject{["m_Shader"]="old",["m_CustomRenderQueue"]=queue,["stringTagMap"]=new JObject{["RenderType"]="TransparentCutout",["CustomTag"]="literal"},["m_SavedProperties"]=new JObject{["_TransparentMode"]=2},["m_ValidKeywords"]=new JArray("KEEP")}};
 static void Assign(Material material,Shader shader){
 var beforeRenderState=new JObject{["m_CustomRenderQueue"]=material.Json["m_CustomRenderQueue"].DeepClone(),["stringTagMap"]=material.Json["stringTagMap"].DeepClone()};
 MUTATION
 }
 public static int Main(){
 foreach(var queue in new[]{2460,-1}){var m=Make(queue);Assign(m,new Shader());Check(m.Json["m_CustomRenderQueue"].Value<int>()==queue,"queue "+queue);Check(m.Json["stringTagMap"]["RenderType"]?.Value<string>()=="TransparentCutout","RenderType "+queue);Check(m.Json["stringTagMap"]["CustomTag"]?.Value<string>()=="literal","custom tag "+queue);Check(m.shader.Name=="new"&&m.Json["m_Shader"].Value<string>()=="new","new shader remains");Check(m.Json["m_SavedProperties"]["_TransparentMode"].Value<int>()==2&&m.Json["m_ValidKeywords"][0].Value<string>()=="KEEP","other state unchanged");}
 var empty=Make(-1);empty.Json["stringTagMap"]=new JObject();Assign(empty,new Shader());Check(!empty.Json["stringTagMap"].HasValues,"empty map remains empty");
 EXTRA
 return failures==0?0:1;
 }
'''.replace("MUTATION", mutation).replace("EXTRA", check_extra)
    program = "using System;using System.Linq;using Newtonsoft.Json.Linq;" + seam + "public class Probe {" + "\n".join(helpers) + runner + "}"
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"] + [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [f"-r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    run = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


def test_render_state_is_checked_in_preflight_and_saved_readback():
    single = (ROOT / "Assets/VRCForge/Editor/MaterialShaderTool.cs").read_text(encoding="utf-8")
    batch = (ROOT / "Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs").read_text(encoding="utf-8")
    assert single.index("CaptureMaterialRenderState(target.material)") < single.index("target.material.shader = shader;")
    assert "VerifyMaterialRenderState(readback, beforeRenderState)" in single
    assert batch.index("BeforeRenderState = MaterialShaderTool.CaptureMaterialRenderState(material)") < batch.index("edit.Material.shader = edit.Shader;")
    assert "MaterialShaderTool.VerifyMaterialRenderState(persisted, edit.BeforeRenderState)" in batch
