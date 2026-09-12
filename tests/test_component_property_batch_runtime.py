"""Compile/run the batch reader with explicit scene identity seams; no live Unity."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentPropertyBatch.cs"

SEAMS = r'''
using System;using System.Linq;using System.Reflection;using System.Collections.Generic;using Newtonsoft.Json.Linq;
namespace UnityEngine {
 public class Object { }
 public class Scene {public string path="Assets/Main.unity";public bool isLoaded=true;public bool IsValid()=>true;}
 public class GameObject:Object {public Scene scene;public Transform transform;public Component component;}
 public class Component:Object {public GameObject gameObject;}
 public class Transform:Component {public string path;public bool IsChildOf(Transform t)=>path.StartsWith(t.path+"/");}
 public class Renderer:Component {public static int materialReads;public Object material {get{materialReads++;return new Object();}}}
 public class MeshFilter:Component {public static int meshReads;public Object mesh {get{meshReads++;return new Object();}}public Object sharedMesh=new Object();}
 public class Collider:Component {public static int materialReads;public Object material {get{materialReads++;return new Object();}}public Object sharedMaterial=new Object();}
 public struct Vector2 {} public struct Vector3 {} public struct Vector4 {} public struct Quaternion {}
 public struct Color {} public struct Bounds {} public struct Matrix4x4 {}
 public static class Time {public static int frameCount=42;}
}
namespace UnityEditor {
 public struct GlobalObjectId {
  public string value;public static Dictionary<string,UnityEngine.Object> Objects=new Dictionary<string,UnityEngine.Object>();
  public static bool TryParse(string s,out GlobalObjectId id){id=new GlobalObjectId{value=s};return !string.IsNullOrEmpty(s);}
  public static UnityEngine.Object GlobalObjectIdentifierToObjectSlow(GlobalObjectId id)=>Objects.TryGetValue(id.value,out var obj)?obj:null;
 }
 public static class EditorUtility {public static bool IsPersistent(UnityEngine.Object obj)=>false;}
 public static class AssetDatabase {public static string AssetPathToGUID(string p)=>"scene";}
}
namespace VRCForge.Core.MCP {
 public static class VRCForgeToolResult {
  public static object Completed(string s,object o)=>JObject.FromObject(o);
  public static object FailedWithCode(string c,string m)=>new JObject{["errorCode"]=c,["message"]=m};
 }
}
namespace VRCForge.Editor {
 public static class ComponentCrudCore {
  public static int Reads;
  public static string GetHierarchyPath(UnityEngine.Transform t)=>t.path;
  public static UnityEngine.Component ResolveComponent(UnityEngine.GameObject go,Type t,int i){if(i!=0)throw new Exception("index");return go.component;}
  public static MemberInfo ResolveMember(Type t,string n)=>(MemberInfo)t.GetField(n)??t.GetProperty(n)??throw new Exception("missing");
  public static object GetMemberValue(object obj,MemberInfo m){Reads++;return m is FieldInfo f?f.GetValue(obj):((PropertyInfo)m).GetValue(obj);}
  READ_ONLY_MEMBER
  public static Type GetMemberType(MemberInfo m)=>m is FieldInfo f?f.FieldType:((PropertyInfo)m).PropertyType;
  public static object DescribeValue(object o)=>o;
 }
}
public class FakeRenderer:UnityEngine.Renderer {public float threshold=.123456789f;public float broken=>throw new Exception("getter failed");public int[] collection=new[]{1,2};}
'''

RUNNER = r'''
class Runner {
 static void Check(bool ok,string message){if(!ok)throw new Exception(message);}
 static JObject Query(int i,params string[] names)=>new JObject{
  ["gameObjectPath"]="Avatar/Part"+i,["componentType"]=typeof(FakeRenderer).FullName,["componentIndex"]=0,
  ["objectGlobalObjectId"]="obj"+i,["componentGlobalObjectId"]="comp"+i,["scenePath"]="Assets/Main.unity",["sceneGuid"]="scene",
  ["avatarGlobalObjectId"]="avatar",["avatarPath"]="Avatar",["propertyNames"]=new JArray(names)};
 static JObject Read(params JObject[] rows)=>(JObject)VRCForge.Editor.UnityComponentPropertyBatch.Handle(new JObject{["queries"]=new JArray(rows)});
 static void Setup(){
  var scene=new UnityEngine.Scene();var avatar=new UnityEngine.GameObject{scene=scene};avatar.transform=new UnityEngine.Transform{path="Avatar",gameObject=avatar};
  UnityEditor.GlobalObjectId.Objects["avatar"]=avatar;
  for(var i=0;i<2;i++){var go=new UnityEngine.GameObject{scene=scene};go.transform=new UnityEngine.Transform{path="Avatar/Part"+i,gameObject=go};
   go.component=new FakeRenderer{gameObject=go};UnityEditor.GlobalObjectId.Objects["obj"+i]=go;UnityEditor.GlobalObjectId.Objects["comp"+i]=go.component;}
 }
 static int Main(){Setup();
  var scalarSchema=JObject.Parse(@"{""type"":""object"",""additionalProperties"":false,""required"":[""gameObjectPath"",""componentType"",""propertyPath""],""properties"":{""gameObjectPath"":{""type"":""string""},""componentType"":{""type"":""string""},""propertyPath"":{""type"":""string""},""componentIndex"":{""type"":""integer""},""maxItems"":{""type"":""integer""},""queries"":{""type"":""array""}}}");
  Console.WriteLine("SCHEMA "+VRCForge.Editor.UnityComponentPropertyBatch.CreateInputSchema(scalarSchema).ToString(Newtonsoft.Json.Formatting.None));
  Console.WriteLine("QUERY "+Query(0,"threshold").ToString(Newtonsoft.Json.Formatting.None));
  var result=Read(Query(0,"threshold","broken"),Query(1,"threshold","missing"));
  Check((int)result["successCount"]==2&&(int)result["failureCount"]==2&&!(bool)result["allSucceeded"],"partial counts");
  Check((int)result["unityFrame"]==42&&(int)result["completedUnityFrame"]==42&&!string.IsNullOrEmpty((string)result["sampleId"]),"same sample");
  Check((string)result["results"][1]["componentGlobalObjectId"]=="comp1"&&(bool)result["results"][1]["identityVerified"],"identity echo");
  Check((float)result["results"][0]["properties"][0]["propertyValue"]==.123456789f,"numeric value");
  Check((string)result["results"][0]["properties"][1]["error"]["message"]=="getter failed","exact item error");
  var stale=Query(1,"threshold");stale["componentGlobalObjectId"]="gone";
  result=Read(Query(0,"threshold"),stale);
  Check((int)result["successCount"]==1&&(int)result["failureCount"]==1&&!(bool)result["results"][1]["identityVerified"],"stale identity partial");
  var wrong=Query(1,"threshold");wrong["gameObjectPath"]="Other/Part1";
  result=Read(wrong);Check((int)result["successCount"]==0,"path cannot replace identity");
  wrong=Query(1,"threshold");wrong["componentIndex"]=1;
  result=Read(wrong);Check((int)result["failureCount"]==1,"component index identity");
  result=Read(Query(0,"material","collection"));
  Check((int)result["failureCount"]==2&&UnityEngine.Renderer.materialReads==0,"no material instantiation/collection expansion");
  var filter=new UnityEngine.MeshFilter{gameObject=(UnityEngine.GameObject)UnityEditor.GlobalObjectId.Objects["obj1"]};
  filter.gameObject.component=filter;UnityEditor.GlobalObjectId.Objects["comp1"]=filter;
  var filterQuery=Query(1,"mesh","sharedMesh");filterQuery["componentType"]=typeof(UnityEngine.MeshFilter).FullName;
  result=Read(filterQuery);
  Check((int)result["failureCount"]==1&&(int)result["successCount"]==1&&UnityEngine.MeshFilter.meshReads==0,"MeshFilter.mesh must not instantiate; sharedMesh remains readable");
  var collider=new UnityEngine.Collider{gameObject=filter.gameObject};collider.gameObject.component=collider;UnityEditor.GlobalObjectId.Objects["comp1"]=collider;
  var colliderQuery=Query(1,"material","sharedMaterial");colliderQuery["componentType"]=typeof(UnityEngine.Collider).FullName;
  result=Read(colliderQuery);
  Check((int)result["failureCount"]==1&&(int)result["successCount"]==1&&UnityEngine.Collider.materialReads==0,"Collider.material must not instantiate; sharedMaterial remains readable");
  var before=VRCForge.Editor.ComponentCrudCore.Reads;
  wrong=Query(1,"rootBone.localToWorldMatrix");result=Read(Query(0,"threshold"),wrong);
  Check((string)result["errorCode"]=="component_property_batch_invalid"&&VRCForge.Editor.ComponentCrudCore.Reads==before,"all shape checks before any reads");
  wrong=Query(1,"threshold");wrong["sceneGuid"]="other";result=Read(Query(0,"threshold"),wrong);
  Check((string)result["errorCode"]=="component_property_batch_invalid"&&VRCForge.Editor.ComponentCrudCore.Reads==before,"no mixed scene sample");
  var overBudget=new JArray();for(var i=0;i<33;i++){var q=Query(0,Enumerable.Range(0,16).Select(n=>"field"+n).ToArray());q["componentGlobalObjectId"]="budget"+i;overBudget.Add(q);}
  result=(JObject)VRCForge.Editor.UnityComponentPropertyBatch.Handle(new JObject{["queries"]=overBudget});
  Check((string)result["errorCode"]=="component_property_batch_invalid"&&VRCForge.Editor.ComponentCrudCore.Reads==before,"512 property budget before reads");
  Console.WriteLine("PASS partial, identity, precision, same sample, read-only getters, preflight");return 0;
 }
}
'''


def toolchain():
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip("Local .NET SDK required")
    return base, compilers[-1], refs[-1], compilers[-1].parents[2] / "Newtonsoft.Json.dll"


def compile_probe(tmp_path, extra_source, references=(), executable=True):
    base, compiler, refs, newtonsoft = toolchain()
    cs = tmp_path / "Probe.cs"
    if "READ_ONLY_MEMBER" in extra_source:
        source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")
        start = source.index("internal static object GetReadOnlyMemberValue(")
        end = source.index("internal static void SetMemberValue(", start)
        extra_source = extra_source.replace("READ_ONLY_MEMBER", source[start:end])
        extra_source = "using UnityEngine;\n" + extra_source
    cs.write_text(extra_source, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:" + ("exe" if executable else "library"), f"/out:{dll}"]
    command += [f"/r:{p}" for p in refs.glob("*.dll")]
    command += [f"/r:{p}" for p in references] + [f"/r:{newtonsoft}", str(SOURCE), str(cs)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return base, newtonsoft, dll


def test_component_property_batch_runs_actual_reader(tmp_path):
    base, newtonsoft, dll = compile_probe(tmp_path, SEAMS + RUNNER)
    shutil.copy2(newtonsoft, tmp_path / newtonsoft.name)
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}
    }}), encoding="utf-8")
    result = subprocess.run([str(base / "dotnet.exe"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    schema = json.loads(next(line.removeprefix("SCHEMA ") for line in result.stdout.splitlines() if line.startswith("SCHEMA ")))
    query = json.loads(next(line.removeprefix("QUERY ") for line in result.stdout.splitlines() if line.startswith("QUERY ")))
    scalar = {"gameObjectPath": "Avatar/Part0", "componentType": "Transform", "propertyPath": "position"}
    jsonschema.validate(scalar, schema)
    jsonschema.validate({"queries": [query]}, schema)
    invalid = [
        {**scalar, "queries": [query]}, {"queries": [query], "maxItems": 3},
        {**scalar, "unknown": True}, {"queries": [{**query, "unknown": True}]},
        {"queries": []}, {"queries": [query] * 129},
        {"queries": [{**query, "propertyNames": [f"field{i}" for i in range(17)]}]},
        {"queries": [{**query, "propertyNames": ["rootBone.localToWorldMatrix"]}]},
    ]
    invalid += [{k: v for k, v in scalar.items() if k != missing} for missing in scalar]
    for value in invalid:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, schema)
    core_server = (ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
    assert "UnityComponentPropertyBatch.CreateInputSchema(descriptor.CreateInputSchema())" in core_server


def test_component_property_batch_compiles_with_unity_2022_3_api(tmp_path):
    managed = Path("E:/unity/Unity 2022.3.22f1/Editor/Data/Managed/UnityEngine")
    if not managed.exists():
        pytest.skip("Unity reference assemblies required")
    seams = r'''
using System;using System.Reflection;using UnityEngine;
namespace VRCForge.Editor {static class ComponentCrudCore {
 internal static string GetHierarchyPath(Transform t)=>null;
 internal static Component ResolveComponent(GameObject go,Type t,int i)=>null;
 internal static MemberInfo ResolveMember(Type t,string n)=>null;
 internal static object GetMemberValue(object obj,MemberInfo m)=>null;
 internal static object GetReadOnlyMemberValue(object obj,MemberInfo m)=>null;
 internal static Type GetMemberType(MemberInfo m)=>null;
 internal static object DescribeValue(object obj)=>null;
}}
namespace VRCForge.Core.MCP {static class VRCForgeToolResult {
 internal static object Completed(string s,object o)=>null;
 internal static object FailedWithCode(string c,string m)=>null;
}}
'''
    compile_probe(tmp_path, seams, [managed / "UnityEngine.CoreModule.dll", managed / "UnityEditor.CoreModule.dll"], executable=False)
