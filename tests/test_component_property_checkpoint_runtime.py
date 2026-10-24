"""Run the production checkpoint scope and prefab hooks with bounded native seams."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from test_component_property_batch_runtime import toolchain
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]
RELATIVE = "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs"


def source():
    saved = os.environ.get("VRCFORGE_COMPONENT_CHECKPOINT_SOURCE_PATH")
    if saved:
        return Path(saved).read_text(encoding="utf-8")
    ref = os.environ.get("VRCFORGE_COMPONENT_CHECKPOINT_GIT_REF")
    return subprocess.check_output(["git", "show", f"{ref}:{RELATIVE}"], cwd=ROOT, text=True, encoding="utf-8") if ref else (ROOT / RELATIVE).read_text(encoding="utf-8")


def run(tmp_path, program, references=()):
    base, compiler, refs, newtonsoft = toolchain()
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:exe", f"/out:{dll}"]
    command += [f"/r:{p}" for p in refs.glob("*.dll")]
    command += [f"/r:{p}" for p in references] + [f"/r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    for dependency in (newtonsoft, *references):
        shutil.copy2(dependency, tmp_path / dependency.name)
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}))
    result = subprocess.run([str(base / "dotnet.exe"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_scope_uses_resolved_component_scene_identity_and_rejects_unknown_owner(tmp_path):
    raw = source()
    signatures = ["private static bool IsEngineComponentAssembly("]
    for signature in (
        "private static bool IsPlainSerializedFieldType(",
        "private static bool IsPlainSceneDataField(",
    ):
        if signature in raw:
            signatures.append(signature)
    signatures += [
        "internal static object BuildPropertyCheckpointScope(",
        "internal static void RecordPropertyPrefabOverride(",
    ]
    helpers = "\n".join(method(raw, signature) for signature in signatures)
    program = r'''
using System;using System.Linq;using System.Collections.Generic;using System.Reflection;using Newtonsoft.Json.Linq;using UnityEngine;using UnityEditor;
namespace UnityEngine {
 public class Object {}
 public struct Scene {public string path;public int handle;}
 public class GameObject {public Scene scene;public string id="object";}
 public class Component {public GameObject gameObject;public string id="component";public int value;}
 public class MonoBehaviour:Component {public static int setterCalls;public int custom {get{return 0;}set{setterCalls++;}}}
 public interface ISerializationCallbackReceiver {void OnBeforeSerialize();void OnAfterDeserialize();}
 public class SerializeField:Attribute {} public class SerializeReference:Attribute {}
 public class ExecuteAlways:Attribute {} public class ExecuteInEditMode:Attribute {}
 public struct Vector2{} public struct Vector3{} public struct Vector4{} public struct Quaternion{} public struct Color{}
}
namespace UnityEditor {
 public struct GuidValue {public string value;public override string ToString()=>value;}
 public struct GlobalObjectId {public int identifierType;public ulong targetObjectId;public GuidValue assetGUID;public string value;
  public static string sceneGuid="guid";public static int kind=2;public static ulong localId=1;
  public static GlobalObjectId GetGlobalObjectIdSlow(object o)=>new GlobalObjectId{identifierType=kind,targetObjectId=localId,assetGUID=new GuidValue{value=sceneGuid},value=o is Component c?c.id:((GameObject)o).id};
  public override string ToString()=>value;}
 public static class PrefabUtility {public static bool instance;public static int calls;public static bool IsPartOfPrefabInstance(Component c)=>instance;public static void RecordPrefabInstancePropertyModifications(Component c){calls++;}}
}
class SavedSceneSnapshot {public string Path="Assets/Actual.unity",Guid="guid",FileDigest="digest";public int Handle=17;}
class Probe {
 HELPERS
 static void Check(bool value,string message){if(!value)throw new Exception(message);}
 static void Reject(Component component,MemberInfo member,SavedSceneSnapshot scene){try{BuildPropertyCheckpointScope(component,member,0,"value",scene);throw new Exception("accepted unknown owner");}catch(InvalidOperationException){}}
 static void Main(){var scene=new SavedSceneSnapshot();var go=new GameObject{scene=new Scene{path=scene.Path,handle=17}};var component=new Component{gameObject=go};var member=typeof(Component).GetField("value");
  var scope=JObject.FromObject(BuildPropertyCheckpointScope(component,member,0,"value",scene));
  Check((string)scope["schema"]=="vrcforge.component_property_checkpoint.v1"&&(string)scope["ownerKind"]=="scene","schema");
  Check((string)scope["scenePath"]=="Assets/Actual.unity"&&(string)scope["sceneGuid"]=="guid"&&(string)scope["sceneDigest"]=="digest","actual scene identity");
  Check((string)scope["objectGlobalObjectId"]=="object"&&(string)scope["componentGlobalObjectId"]=="component"&&(int)scope["componentIndex"]==0&&(string)scope["propertyPath"]=="value","component identity");
  Check(scope["archiveAssetPaths"].ToString()==new JArray("Assets/Actual.unity").ToString(),"exact footprint");
  var custom=new MonoBehaviour{gameObject=go};Reject(custom,typeof(MonoBehaviour).GetProperty("custom"),scene);Check(MonoBehaviour.setterCalls==0,"custom setter not called");
  go.scene=new Scene{path="Assets/Caller.unity",handle=17};Reject(component,member,scene);go.scene=new Scene{path=scene.Path,handle=18};Reject(component,member,scene);go.scene=new Scene{path=scene.Path,handle=17};
  GlobalObjectId.sceneGuid="other";Reject(component,member,scene);GlobalObjectId.sceneGuid="guid";GlobalObjectId.localId=0;Reject(component,member,scene);GlobalObjectId.localId=1;GlobalObjectId.kind=1;Reject(component,member,scene);
  PrefabUtility.instance=false;RecordPropertyPrefabOverride(component);Check(PrefabUtility.calls==0,"ordinary scene no prefab write");PrefabUtility.instance=true;RecordPropertyPrefabOverride(component);Check(PrefabUtility.calls==1,"prefab override recorded");
  Console.WriteLine("scope and prefab hooks passed");}
}
'''.replace("HELPERS", helpers)
    assert "scope and prefab hooks passed" in run(tmp_path, program)


def test_engine_assembly_identity_rejects_project_and_prefixed_dynamic_assemblies(tmp_path):
    raw = source()
    helper = method(raw, "private static bool IsEngineComponentAssembly(")
    base, compiler, refs, _ = toolchain()
    # A bounded engine assembly seam tests identity without an installed Editor.
    # Actual Unity types and scene persistence are covered by the live Core gate.
    engine = tmp_path / "engine"
    engine.mkdir()
    core_source = engine / "Core.cs"
    core_source.write_text("namespace UnityEngine { public class Component {} }", encoding="utf-8")
    core = engine / "UnityEngine.CoreModule.dll"
    compiled = subprocess.run([str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:library", f"/out:{core}", *[f"/r:{p}" for p in refs.glob("*.dll")], str(core_source)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    project = tmp_path / "project"
    project.mkdir()
    forged_source = project / "Forged.cs"
    forged_source.write_text("public class Forged {}", encoding="utf-8")
    forged_dll = project / "UnityEngine.ForgedModule.dll"
    compiled = subprocess.run([str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:library", f"/out:{forged_dll}", *[f"/r:{p}" for p in refs.glob("*.dll")], str(forged_source)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    # Query assembly identities only: no native Unity constructors/getters run.
    program = r'''
using System;using System.Reflection;using System.Reflection.Emit;using UnityEngine;
class Probe {HELPER static void Main(){
 if(!IsEngineComponentAssembly(typeof(Component).Assembly))throw new Exception("actual core rejected");
 if(IsEngineComponentAssembly(typeof(Probe).Assembly))throw new Exception("project assembly accepted");
 var forged=AssemblyBuilder.DefineDynamicAssembly(new AssemblyName("UnityEngine.ForgedModule"),AssemblyBuilderAccess.Run);
 if(IsEngineComponentAssembly(forged))throw new Exception("prefix accepted as authority");
 if(IsEngineComponentAssembly(Assembly.LoadFrom(@"FORGED_PATH")))throw new Exception("project DLL prefix accepted as engine module");
 Console.WriteLine("assembly identity passed");}}
'''.replace("HELPER", helper).replace("FORGED_PATH", str(forged_dll).replace('"', '""'))
    assert "assembly identity passed" in run(tmp_path, program, (core,))


def test_scope_guard_precedes_any_property_getter_or_mutation_and_preview_exposes_it():
    raw = source().split("public static class SetPropertyTool", 1)[1]
    assert raw.index("BuildPropertyCheckpointScope(") < raw.index("ComponentCrudCore.GetMemberValue(component, member)")
    preview = raw.split("var previewPayload = new", 1)[1].split("return VRCForgeToolResult.Completed", 1)[0]
    assert "checkpointScope," in preview


def test_registered_read_handler_returns_scope_without_getter_and_keeps_default_reads(tmp_path):
    getter = source().split("public static class GetPropertyTool", 1)[1]
    parameters = method(getter, "public class GetPropertyParameters")
    handler = method(getter, "public static object HandleCommand(JObject @params)")
    program = r'''
using System;using System.Reflection;using System.Collections.Generic;using Newtonsoft.Json.Linq;
class VRCForgeInput:Attribute {public bool IsRequired{get;set;}public VRCForgeInput(string text){}}
class Component {public GameObject gameObject;public int value=7;}
class GameObject {public object transform;public Component component;}
static class VRCForgeToolResult {public static object Failed(string error)=>new JObject{["error"]=error};public static object Completed(string text,object payload)=>JObject.FromObject(payload);}
static class UnityComponentPropertyBatch {public static int calls;public static object Handle(JObject p){calls++;return new JObject{["batch"]=true};}}
static class ComponentCrudCore {
 public static int reads,scopes;public static bool rejectScope;public static GameObject go=new GameObject();
 public static GameObject ResolveGameObject(string p){go.component=new Component{gameObject=go};return go;}
 public static Type ResolveComponentType(string p)=>typeof(Component);
 public static Component ResolveComponent(GameObject g,Type t,int i)=>g.component;
 public static MemberInfo ResolveMember(Type t,string p)=>t.GetField(p);
 public static object ResolveSavedSceneFor(GameObject g)=>new object();
 public static object BuildPropertyCheckpointScope(Component c,MemberInfo m,int i,string p,object scene){scopes++;if(rejectScope)throw new InvalidOperationException("unsupported owner");return new{schema="vrcforge.component_property_checkpoint.v1",scenePath="Assets/Actual.unity"};}
 public static object GetReadOnlyMemberValue(Component c,MemberInfo m){reads++;return c.value;}
 public static object DescribeValue(object value,int max,out int returned,out int total,out bool truncated,out bool incomplete,Dictionary<string,object> enums=null){returned=total=-1;truncated=incomplete=false;return value;}
 public static string GetHierarchyPath(object t)=>"Actual/Component";
 public static Type GetMemberType(MemberInfo m)=>typeof(int);
}
class GetPropertyTool {PARAMETERS HANDLER}
class Probe {
 static JObject Call(JObject args)=>(JObject)GetPropertyTool.HandleCommand(args);
 static JObject Args()=>new JObject{["gameObjectPath"]="Caller/Component",["componentType"]="Component",["propertyPath"]="value"};
 static void Check(bool valid,string message){if(!valid)throw new Exception(message);}
 static void Main(){var p=Args();p["includeCheckpointScope"]=true;var scoped=Call(p);
  Check((string)scoped["checkpointScope"]?["scenePath"]=="Assets/Actual.unity","missing authoritative read scope");
  Check((string)scoped["gameObjectPath"]=="Actual/Component"&&ComponentCrudCore.reads==0&&ComponentCrudCore.scopes==1,"scope must bypass getter and use actual owner");
  var normal=Call(Args());Check((int)normal["propertyValue"]==7&&ComponentCrudCore.reads==1&&ComponentCrudCore.scopes==1&&normal["checkpointScope"]==null,"default read changed");
  ComponentCrudCore.rejectScope=true;var rejected=Call(p);Check(rejected["error"]!=null&&ComponentCrudCore.reads==1,"failed scope must never reach getter");
  var batch=new JObject{["queries"]=new JArray(),["includeCheckpointScope"]=true};Check(Call(batch)["error"]!=null&&UnityComponentPropertyBatch.calls==0,"batch silently ignored scope");
  batch.Remove("includeCheckpointScope");Check((bool)Call(batch)["batch"]&&UnityComponentPropertyBatch.calls==1,"default batch changed");
  Console.WriteLine("read scope and defaults passed");}
}
'''.replace("PARAMETERS", parameters).replace("HANDLER", handler)
    assert "read scope and defaults passed" in run(tmp_path, program)
