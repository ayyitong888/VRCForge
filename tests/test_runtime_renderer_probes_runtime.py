"""Run actual production property readers with explicit Unity storage seams."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_actual_property_readers_and_whole_block_precedence(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/RuntimeObservationTool.cs").read_text(encoding="utf-8")
    actual = "\n".join(method(source, signature) for signature in (
        "internal static string ProbePropertyType", "internal static JObject ReadProbeProperty", "internal static JArray ReadProbeProperties"))
    seam = r'''
using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;
namespace UnityEngine.Rendering {enum ShaderPropertyType {Float,Range,Vector,Color,Texture,Int}}
struct Vector4 {public float x,y,z,w;public Vector4(float a,float b,float c,float d){x=a;y=b;z=c;w=d;}}
struct Vector2 {public float x,y;public Vector2(float a,float b){x=a;y=b;}}
class Texture {public string name;}
class Shader {
 internal static List<string> Ids=new List<string>();
 public static int PropertyToID(string n){if(!Ids.Contains(n))Ids.Add(n);return Ids.IndexOf(n);}
 public string[] Names={"_Float","_Range","_Vector","_Color","_Mask","_Integer"};
 public UnityEngine.Rendering.ShaderPropertyType[] Types={UnityEngine.Rendering.ShaderPropertyType.Float,UnityEngine.Rendering.ShaderPropertyType.Range,UnityEngine.Rendering.ShaderPropertyType.Vector,UnityEngine.Rendering.ShaderPropertyType.Color,UnityEngine.Rendering.ShaderPropertyType.Texture,UnityEngine.Rendering.ShaderPropertyType.Int};
 public int FindPropertyIndex(string n)=>Array.IndexOf(Names,n);public UnityEngine.Rendering.ShaderPropertyType GetPropertyType(int i)=>Types[i];
}
class Material {
 public Shader shader=new Shader(); public Texture texture=new Texture{name="asset"};
 public float GetFloat(int id)=>7f;public Vector4 GetVector(int id)=>new Vector4(1,2,3,4); public Vector4 GetColor(int id)=>new Vector4(.1f,.2f,.3f,.4f);
 public Texture GetTexture(int id)=>texture;public Vector2 GetTextureScale(string n)=>new Vector2(2,3);public Vector2 GetTextureOffset(string n)=>new Vector2(4,5);
}
class MaterialPropertyBlock {
 public Dictionary<int,object> Data=new Dictionary<int,object>();public bool isEmpty=>Data.Count==0;
 public bool HasFloat(int id)=>Data.ContainsKey(id)&&Data[id] is float;public bool HasVector(int id)=>Data.ContainsKey(id)&&Data[id] is Vector4;public bool HasTexture(int id)=>Data.ContainsKey(id)&&Data[id] is Texture;
 public float GetFloat(int id)=>(float)Data[id]; public Vector4 GetVector(int id)=>(Vector4)Data[id];public Texture GetTexture(int id)=>(Texture)Data[id];
 public void Set(string name,object value){Data[Shader.PropertyToID(name)]=value;}
}
'''
    runner = r'''
static JObject ProbeObject(Texture t)=>new JObject{["name"]=t?.name??"null"};
static void Check(bool ok,string message){if(!ok)throw new Exception(message);}
static int Main(){
var m=new Material();var r=new MaterialPropertyBlock();var i=new MaterialPropertyBlock();
var names=new[]{"_Float","_Range","_Vector","_Color","_Mask","_Mask_ST"};
var baseline=ReadProbeProperties(m,r,i,names);
Check((float)baseline[0]["value"]==7f&&(string)baseline[0]["valueSource"]=="material","material fallback");
Check((string)baseline[4]["value"]["name"]=="asset","texture identity");
Check((float)baseline[5]["value"][0]==2&&(float)baseline[5]["value"][3]==5&&(string)baseline[5]["valueSource"]=="material_texture_scale_offset","implicit texture ST");
r.Set("_Float",9f);r.Set("_Vector",new Vector4(9,8,7,6));r.Set("_Color",new Vector4(.9f,.8f,.7f,.6f));r.Set("_Mask",new Texture{name="renderer"});
var renderer=ReadProbeProperties(m,r,i,names);
Check((float)renderer[0]["value"]==9f&&(string)renderer[0]["valueSource"]=="renderer_property_block","renderer block");
Check((float)renderer[2]["value"][3]==6&&(float)renderer[3]["value"][0]==.9f&&(string)renderer[4]["value"]["name"]=="renderer","vector color texture override");
i.Set("_Unrelated",1f);
var suppressed=ReadProbeProperties(m,r,i,names);
Check((float)suppressed[0]["value"]==7f&&(string)suppressed[0]["valueSource"]=="material","indexed WHOLE block must suppress renderer fallback");
Check((float)suppressed[2]["value"][0]==1&&(string)suppressed[4]["value"]["name"]=="asset","indexed suppresses vector and texture");
i.Set("_Float",0f);i.Set("_Range",.25f);i.Set("_Mask_ST",new Vector4(6,7,8,9));
var indexed=ReadProbeProperties(m,r,i,names);
Check((float)indexed[0]["value"]==0f&&(string)indexed[0]["valueSource"]=="material_property_block","explicit indexed zero");
Check((float)indexed[1]["value"]==.25f&&(float)indexed[5]["value"][0]==6&&(string)indexed[5]["valueSource"]=="material_property_block","range and generated ST overrides");
foreach(var name in new[]{"_Missing","_Missing_ST","_Float_ST","_Integer"}){bool rejected=false;try{ReadProbeProperties(m,r,i,new[]{name});}catch(ArgumentException){rejected=true;}Check(rejected,"undeclared or unsupported property accepted "+name);}
Check(ReadProbeProperties(m,r,i,Array.Empty<string>()).Count==0,"empty legacy");
Console.WriteLine("PASS material/render/index precedence, whole-block missing fallback, zero/range/vector/color/texture/ST, declaration rejection, legacy empty");return 0;}
'''
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    cs.write_text(seam + "class Probe {" + actual + runner + "}", encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:exe", f"/out:{dll}", *[f"/r:{p}" for p in refs.glob("*.dll")], f"/r:{newtonsoft}", str(cs)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}))
    result = subprocess.run([str(base / "dotnet.exe"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_probe_methods_compile_against_unity_2022_3_api(tmp_path):
    """API compile only: this never loads Unity or runs native calls."""
    import pytest
    managed = Path("E:/unity/Unity 2022.3.22f1/Editor/Data/Managed")
    if not managed.exists():
        pytest.skip("Unity 2022.3 reference assemblies unavailable")
    source = (ROOT / "Assets/VRCForge/Editor/RuntimeObservationTool.cs").read_text(encoding="utf-8")
    actual = "\n".join(method(source, signature) for signature in (
        "internal sealed class RendererProbe", "internal static List<RendererProbe> PrepareRendererProbes",
        "internal static string ProbePropertyType", "internal static JObject ProbeObject", "internal static JObject ReadProbeProperty",
        "internal static JObject ReadRendererProbe", "internal static JArray ReadProbeProperties",
        "internal static JArray ReadRendererBlendShapes"))
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "UnityApiProbe.cs"
    cs.write_text("using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;using UnityEngine;using UnityEditor;class Probe {" + actual + "}", encoding="utf-8")
    assemblies = [managed / "UnityEngine" / name for name in ("UnityEngine.CoreModule.dll", "UnityEditor.CoreModule.dll")]
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:library", f"/out:{tmp_path / 'UnityApiProbe.dll'}", *[f"/r:{p}" for p in refs.glob("*.dll")], *[f"/r:{p}" for p in assemblies], f"/r:{newtonsoft}", str(cs)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
