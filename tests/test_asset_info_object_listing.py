"""Real GetAssetInfoTool C# execution with bounded Unity read seams; not Unity."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from jsonschema import Draft202012Validator
from test_curve_fx_authoring_runtime_contract import method
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]


def test_public_renderer_listing_schema():
    validator = Draft202012Validator(UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_get_asset_info'])
    validator.validate({'assetPath': 'Assets/a.prefab', 'includeRendererMaterials': True, 'rendererOffset': 1, 'rendererLimit': 2})
    for field, value in [('rendererLimit', 0), ('rendererLimit', 129), ('rendererOffset', -1), ('rendererOffset', 4097), ('includeRendererMaterials', 1)]:
        assert list(validator.iter_errors({'assetPath': 'Assets/a.prefab', field: value}))


def test_backend_forwards_renderer_listing_parameters(monkeypatch):
    import dashboard_server as server
    calls = []
    monkeypatch.setattr(server, 'load_dashboard_settings', lambda _: {})
    monkeypatch.setattr(server, 'invoke_unity_mcp', lambda settings, tool, args: calls.append(args) or {})
    monkeypatch.setattr(server, 'extract_tool_result_payload', lambda result: {'rendererMaterialListing': {'renderers': []}})
    server.get_asset_info_sync({'assetPath': 'Assets/a.prefab', 'includeRendererMaterials': True, 'rendererOffset': 2, 'rendererLimit': 3})
    assert calls[-1] == {'assetPath': 'Assets/a.prefab', 'guid': '', 'includeRendererMaterials': True, 'rendererOffset': 2, 'rendererLimit': 3}


def test_public_object_listing_schema():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_get_asset_info']
    Draft202012Validator(schema).validate({'assetPath': 'Assets/a.mat', 'includeObjects': True, 'objectOffset': 1, 'objectLimit': 2})
    for field, value in [('objectLimit', 0), ('objectLimit', 129), ('objectOffset', -1), ('objectOffset', 4097), ('includeObjects', 1)]:
        assert list(Draft202012Validator(schema).iter_errors({'assetPath': 'Assets/a.mat', field: value}))


def test_backend_forwards_optional_listing_parameters(monkeypatch):
    import dashboard_server as server
    calls = []
    monkeypatch.setattr(server, 'load_dashboard_settings', lambda _: {})
    monkeypatch.setattr(server, 'invoke_unity_mcp', lambda settings, tool, args: calls.append(args) or {})
    monkeypatch.setattr(server, 'extract_tool_result_payload', lambda result: {'objectListing': {'objects': []}})
    server.get_asset_info_sync({'assetPath': 'Assets/a.mat', 'includeObjects': True, 'objectOffset': 2, 'objectLimit': 3})
    assert calls[-1] == {'assetPath': 'Assets/a.mat', 'guid': '', 'includeObjects': True, 'objectOffset': 2, 'objectLimit': 3}
    server.get_asset_info_sync({'assetPath': 'Assets/a.mat'})
    assert calls[-1] == {'assetPath': 'Assets/a.mat', 'guid': ''}


def test_public_imported_text_search_schema_is_bounded_and_identity_bound():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_get_asset_info']
    validator = Draft202012Validator(schema)
    validator.validate({
        'projectPath': 'D:/Project', 'assetPath': 'Assets/a.shader',
        'localFileId': '-9223372036854775808',
        'importedTextSearch': {'literal': 'Shader Source', 'maxMatches': 2, 'contextCharacters': 4},
    })
    invalid = [
        {'assetPath': 'Assets/a.shader', 'localFileId': '1'},
        {'assetPath': 'Assets/a.shader', 'importedTextSearch': {'literal': 'x'}, 'localFileId': '1'},
        {'projectPath': 'D:/Project', 'assetPath': 'Assets/a.shader', 'localFileId': '1', 'importedTextSearch': {'literal': ''}},
        {'projectPath': 'D:/Project', 'assetPath': 'Assets/a.shader', 'localFileId': '1', 'importedTextSearch': {'literal': 'x', 'maxMatches': 33}},
        {'projectPath': 'D:/Project', 'assetPath': 'Assets/a.shader', 'localFileId': '1', 'importedTextSearch': {'literal': 'x', 'contextCharacters': 2049}},
        {'projectPath': 'D:/Project', 'assetPath': 'Assets/a.shader', 'localFileId': 'not-an-id', 'importedTextSearch': {'literal': 'x'}},
        {'projectPath': 'D:/Project', 'assetPath': 'Assets/a.shader', 'localFileId': '1', 'importedTextSearch': {'literal': 'x', 'unexpected': True}},
    ]
    for request in invalid:
        assert list(validator.iter_errors(request)), request


def test_backend_forwards_exact_imported_text_search_parameters(monkeypatch):
    import dashboard_server as server
    calls = []
    monkeypatch.setattr(server, 'load_dashboard_settings', lambda _: {})
    monkeypatch.setattr(server, 'invoke_unity_mcp', lambda settings, tool, args: calls.append(args) or {})
    monkeypatch.setattr(server, 'extract_tool_result_payload', lambda result: {'importedTextSearch': {'totalMatches': 0}})
    request = {
        'assetPath': 'Assets/a.shader', 'localFileId': '-123',
        'importedTextSearch': {'literal': 'Shader Source', 'maxMatches': 2, 'contextCharacters': 4},
    }
    server.get_asset_info_sync(request)
    assert calls[-1] == {
        'assetPath': 'Assets/a.shader', 'guid': '', 'localFileId': '-123',
        'importedTextSearch': request['importedTextSearch'],
    }


def test_production_asset_info_listing(tmp_path):
    base = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / 'AppData/Local/Microsoft/dotnet')))
    compilers = sorted((base / 'sdk').glob('8.*/Roslyn/bincore/csc.dll'))
    refs = sorted((base / 'packs/Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))
    dotnet = str(base / 'dotnet.exe') if (base / 'dotnet.exe').is_file() else shutil.which('dotnet')
    if not dotnet or not compilers or not refs:
        pytest.skip('Local .NET 8 SDK required')
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / 'Newtonsoft.Json.dll'
    code = (ROOT / 'Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs').read_text(encoding='utf-8')
    copy = (ROOT / 'Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs').read_text(encoding='utf-8')
    preamble = '''using System; using System.Linq; using System.Collections.Generic; using System.Text; using System.Security.Cryptography; using System.Globalization; using Newtonsoft.Json.Linq; using UnityEngine; using UnityEditor; using VRCForge.Core.MCP;
'''
    seam = r'''
namespace UnityEngine {
 [Flags] public enum HideFlags {None=0,HideInHierarchy=1}
     public class Object {public string name="material"; public HideFlags hideFlags; public int id;public bool persistent=true;public string path=""; public int GetInstanceID()=>id;}
     public class Shader:Object {} public class Material:Object {public Shader shader;}
     public class TextAsset:Object {public string text="";}
 public class Mesh:Object {} public class MeshFilter:Component {public Mesh sharedMesh;}
 public class Renderer:Component {public Material[] sharedMaterials=new Material[0];}
 public class SkinnedMeshRenderer:Renderer {public Mesh sharedMesh;}
 public class MeshRenderer:Renderer {}
 public class GameObject:Object {
  public Transform transform;public List<Component> components=new List<Component>();public Renderer[] renderers=new Renderer[0];public static int Enumerations;
  public GameObject(){transform=new Transform{gameObject=this};}
  public T[] GetComponents<T>()=>components.OfType<T>().ToArray();
  public T GetComponent<T>() where T:class=>components.OfType<T>().FirstOrDefault();
  public T[] GetComponentsInChildren<T>(bool includeInactive){if(!includeInactive)throw new Exception("inactive omitted");Enumerations++;return renderers.OfType<T>().ToArray();}
 }
 public class Transform {public int childCount=0;public GameObject gameObject;public string name=>gameObject.name;public Transform parent;public int siblingIndex;public int GetSiblingIndex()=>siblingIndex;}
 public class Component:Object {public GameObject gameObject;public Transform transform=>gameObject.transform;public T[] GetComponents<T>()=>gameObject.GetComponents<T>();public T GetComponent<T>() where T:class=>gameObject.GetComponent<T>();}
}
namespace UnityEditor {
 public static class EditorApplication {public static bool isCompiling=false;}
 public class AssetImporter {public static AssetImporter GetAtPath(string path)=>null;}
 public enum PrefabAssetType {NotAPrefab,Regular}
 public static class PrefabUtility {public static PrefabAssetType GetPrefabAssetType(UnityEngine.Object obj)=>obj is GameObject?PrefabAssetType.Regular:PrefabAssetType.NotAPrefab;}
     public static class EditorUtility {public static bool IsPersistent(UnityEngine.Object obj)=>obj.persistent;}
     public class MonoScript:UnityEngine.Object {public Type GetClass()=>null;}
 public static class AssetDatabase {
  public static Type DefaultImporterType, OverrideImporterType;
  public static Type GetDefaultImporter(string path)=>DefaultImporterType;
  public static Type GetImporterOverride(string path)=>OverrideImporterType;
  public static UnityEngine.Object Main=new Material{id=1};public static UnityEngine.Object[] Objects; public static int Loads;
  public static UnityEngine.Object LoadMainAssetAtPath(string path)=>Main;
  public static Type GetMainAssetTypeAtPath(string path)=>typeof(Material);
  public static string AssetPathToGUID(string path)=>"guid";
  public static string GetAssetDependencyHash(string path)=>"dependency";
  public static UnityEngine.Object[] LoadAllAssetsAtPath(string path){Loads++;return Objects;}
  public static bool IsMainAsset(UnityEngine.Object obj)=>ReferenceEquals(obj,Main);
      public static string GetAssetPath(UnityEngine.Object obj)=>obj.persistent?(string.IsNullOrEmpty(obj.path)?"Assets/a.mat":obj.path):"";
      public static bool TryGetGUIDAndLocalFileIdentifier(UnityEngine.Object obj,out string guid,out long id){guid=obj.persistent?"guid":"";id=obj.id==0?9007199254740993L:obj.id;return obj.persistent;}
  public static void ImportAsset(string p){throw new Exception("FORBIDDEN import");}
  public static void Refresh(){throw new Exception("FORBIDDEN refresh");}
  public static void SaveAssets(){throw new Exception("FORBIDDEN save");}
 }
}
namespace UnityEditor.Compilation {
 public enum AssembliesType {Editor}
 public class Assembly {public string name;public string[] defines;}
 public static class CompilationPipeline {
  public static string Name;public static Assembly[] Assemblies;
  public static string GetAssemblyNameFromScriptPath(string path)=>Name;
  public static Assembly[] GetAssemblies(AssembliesType type)=>Assemblies;
 }
}
namespace UnityEditor.AssetImporters {public sealed class ImportLog:UnityEngine.Object {}}
namespace VRCForge.Core.MCP {
 [AttributeUsage(AttributeTargets.Property)] public class VRCForgeInput:Attribute {public bool IsRequired{get;set;}public VRCForgeInput(string text){}}
 public static class VRCForgeToolResult {public static object Completed(string message,object data)=>JObject.FromObject(data);public static object Failed(string message)=>new JObject{["ok"]=false,["error"]=message};}
}
namespace VRCForge.Editor {
 public static class AssetPrefabCore {public static string ResolveAssetPath(string path,string guid)=>"Assets/a.mat";}
 public class ProjectAssetCopyException:Exception {public ProjectAssetCopyException(string s):base(s){}}
 public class Runner {
  static int failures;
  static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
  static JObject Run(JObject p)=>(JObject)GetAssetInfoTool.HandleCommand(p);
  static JObject Request()=>new JObject{["assetPath"]="Assets/a.mat",["includeObjects"]=true,["objectOffset"]=0,["objectLimit"]=2};
  public static int Main(){
   AssetDatabase.Main=new MonoScript{id=1};
   var script=Run(new JObject{["assetPath"]="Assets/a.cs"});
   Check(script["monoScriptCompilation"]!=null&&script["monoScriptClass"].Type==JTokenType.Null&&script["monoScriptCompilation"]["defines"].Type==JTokenType.Null&&!script["monoScriptCompilation"]["definesComplete"].Value<bool>(),"unmapped script retains unknown class and defines");
   UnityEditor.Compilation.CompilationPipeline.Name="Example.Editor.dll";
   UnityEditor.Compilation.CompilationPipeline.Assemblies=new[]{new UnityEditor.Compilation.Assembly{name="Other",defines=new[]{"WRONG_ASSEMBLY"}},new UnityEditor.Compilation.Assembly{name="Example.Editor",defines=new[]{"UNITY_EDITOR","EXAMPLE_FEATURE_DISABLED"}}};
   script=Run(new JObject{["assetPath"]="Assets/a.cs"});
   Check(script["monoScriptCompilation"]!=null&&script["monoScriptClass"].Type==JTokenType.Null&&script["monoScriptCompilation"]["assemblyFound"].Value<bool>()&&script["monoScriptCompilation"]["definesComplete"].Value<bool>()&&script["monoScriptCompilation"]["defines"].Values<string>().SequenceEqual(new[]{"UNITY_EDITOR","EXAMPLE_FEATURE_DISABLED"}),"exact Editor assembly defines are separate from loaded class");
   Check(script["monoScriptCompilation"]["loadedAssemblyCount"]?.Value<int>()==0&&script["monoScriptCompilation"]["loadedTypesComplete"]?.Value<bool>()==false&&script["monoScriptCompilation"]["fileNameTypeMatches"]?.Type==JTokenType.Null,"unloaded assembly type candidates remain unknown");
   var dynamicAssembly=System.Reflection.Emit.AssemblyBuilder.DefineDynamicAssembly(new System.Reflection.AssemblyName("Example.Editor"),System.Reflection.Emit.AssemblyBuilderAccess.Run);
   var dynamicModule=dynamicAssembly.DefineDynamicModule("main");dynamicModule.DefineType("Existing.a",System.Reflection.TypeAttributes.Public,typeof(AssetImporter)).CreateType();
   script=Run(new JObject{["assetPath"]="Assets/a.cs"});
   Check(script["monoScriptCompilation"]["loadedAssemblyCount"]?.Value<int>()==1&&script["monoScriptCompilation"]["loadedTypesComplete"]?.Value<bool>()==true&&script["monoScriptCompilation"]["fileNameTypeMatches"]?[0]?["fullName"]?.Value<string>()=="Existing.a"&&script["monoScriptCompilation"]["fileNameTypeMatches"]?[0]?["isAssetImporter"]?.Value<bool>()==true&&script["monoScriptClass"].Type==JTokenType.Null,"loaded importer type candidate is independent of unknown MonoScript binding");
   UnityEditor.Compilation.CompilationPipeline.Name="Example.Editor";script=Run(new JObject{["assetPath"]="Assets/a.cs"});Check(script["monoScriptCompilation"]!=null&&script["monoScriptCompilation"]["assemblyFound"].Value<bool>(),"assembly dotted name is retained when no dll suffix is present");
   UnityEditor.Compilation.CompilationPipeline.Assemblies=new[]{new UnityEditor.Compilation.Assembly{name="Other",defines=new[]{"WRONG_ASSEMBLY"}}};
   script=Run(new JObject{["assetPath"]="Assets/a.cs"});Check(script["monoScriptCompilation"]!=null&&!script["monoScriptCompilation"]["assemblyFound"].Value<bool>()&&!script["monoScriptCompilation"]["definesComplete"].Value<bool>(),"unmatched assembly does not borrow another assembly defines");
   UnityEditor.Compilation.CompilationPipeline.Assemblies=new[]{new UnityEditor.Compilation.Assembly{name="Example.Editor",defines=Enumerable.Repeat(new string('x',1000),128).ToArray()}};
   Check(Run(new JObject{["assetPath"]="Assets/a.cs"})["ok"]?.Value<bool>()==false,"script compilation response bound rejects oversized defines");
   AssetDatabase.Main=new Material{id=1};
   var extra=new Material{id=2,name="hidden",hideFlags=HideFlags.HideInHierarchy,persistent=false};
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,extra,extra,null};
   var imported=new TextAsset{id=7,name="Shader Source",text="α Shader Source omega Shader Source"};AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,imported};
   var diagnostic=Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="7",["importedTextSearch"]=new JObject{["literal"]="Shader Source"}})["importedTextSearch"] as JObject;
   Check(diagnostic!=null&&diagnostic["totalMatches"].Value<int>()==2&&diagnostic["importedTextSha256"]!=null&&diagnostic["offsetUnit"].Value<string>()=="UTF-16 code units"&&diagnostic["textLengthUtf8Bytes"].Value<int>()>diagnostic["textLengthCharacters"].Value<int>()&&diagnostic["textKind"].Value<string>().Contains("imported TextAsset.text"),"exact imported TextAsset search reports bounded matches and provenance");
   var combinedDiagnostic=Run(new JObject{["assetPath"]="Assets/a.mat",["includeObjects"]=true,["objectLimit"]=2,["localFileId"]="7",["importedTextSearch"]=new JObject{["literal"]="Shader Source"}});
   Check(combinedDiagnostic["objectListing"]!=null&&combinedDiagnostic["importedTextSearch"]!=null,"listing and imported search compose in one response");
   Check(Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="999",["importedTextSearch"]=new JObject{["literal"]="x"}})["ok"]?.Value<bool>()==false,"missing imported child is rejected");
   Check(Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="1",["importedTextSearch"]=new JObject{["literal"]="x"}})["ok"]?.Value<bool>()==false,"main asset is rejected as imported child");
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,new TextAsset{id=8,text="a"},new TextAsset{id=8,text="b"}};
   Check(Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="8",["importedTextSearch"]=new JObject{["literal"]="x"}})["ok"]?.Value<bool>()==false,"duplicate local ID is rejected");
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,new TextAsset{id=9,persistent=false,text="a"}};
   Check(Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="9",["importedTextSearch"]=new JObject{["literal"]="x"}})["ok"]?.Value<bool>()==false,"nonpersistent imported child is rejected");
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,new TextAsset{id=11,path="Assets/other.mat",text="x"}};
   Check(Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="11",["importedTextSearch"]=new JObject{["literal"]="x"}})["ok"]?.Value<bool>()==false,"cross-path imported child is rejected");
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,new TextAsset{id=10,text=string.Concat(Enumerable.Repeat("é",2500000))}};
   Check(Run(new JObject{["assetPath"]="Assets/a.mat",["localFileId"]="10",["importedTextSearch"]=new JObject{["literal"]="x"}})["ok"]?.Value<bool>()==false,"UTF8 text bound is enforced before digest allocation");
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,extra,extra,null};
   AssetDatabase.Loads=0;
   var old=Run(new JObject{["assetPath"]="Assets/a.mat"});Check(old["objectListing"]==null&&AssetDatabase.Loads==0&&old["assetType"].Value<string>()=="UnityEngine.Material","default response unchanged and no enumeration");
   Check(old["defaultImporterType"]?.Type==JTokenType.Null&&old["overrideImporterType"]?.Type==JTokenType.Null,"unknown importer registration stays null");
   AssetDatabase.DefaultImporterType=typeof(AssetImporter);AssetDatabase.OverrideImporterType=typeof(MonoScript);
   var registration=Run(new JObject{["assetPath"]="Assets/a.mat"});Check(registration["defaultImporterType"]?.Value<string>()=="UnityEditor.AssetImporter"&&registration["overrideImporterType"]?.Value<string>()=="UnityEditor.MonoScript"&&registration["importerType"].Type==JTokenType.Null,"registered importer types remain separate from missing current importer");
   AssetDatabase.DefaultImporterType=null;AssetDatabase.OverrideImporterType=null;
   var result=Run(Request());var listing=result["objectListing"] as JObject;
   Check(listing!=null&&listing["total"].Value<int>()==3&&listing["count"].Value<int>()==2&&listing["offset"].Value<int>()==0&&listing["truncated"].Value<bool>()&&listing["objects"].Count()==2&&listing["nextOffset"].Value<int>()==2,"bounded pagination preserves duplicates");
   Check(AssetDatabase.Loads==1,"one enumeration for listing and digest");
   Check(listing!=null&&listing["objectLayoutDigest"].Value<string>()==CopyDigest.ComputeObjectLayoutDigest("Assets/a.mat"),"same actual production copy digest");
   var q=Request();q["objectOffset"]=2;var next=Run(q)["objectListing"] as JObject;
   Check(next!=null&&next["offset"].Value<int>()==2&&next["count"].Value<int>()==1&&next["nextOffset"].Type==JTokenType.Null&&!next["truncated"].Value<bool>(),"last page reports completion");
   var combined=listing==null||next==null?new JArray():new JArray(listing["objects"].Concat(next["objects"]));
   Check(combined.Count==3&&combined.Count(x=>x["instanceId"].Value<int>()==2)==2&&combined.Any(x=>x["persistent"].Value<bool>()==false),"duplicate and transient identities remain visible");
   Check(combined.Any(x=>x["localFileId"]?.Value<string>()=="1"&&x["isMain"].Value<bool>()&&x["assetPath"].Value<string>()=="Assets/a.mat")&&combined.All(x=>x["layoutEntry"]!=null&&x["type"]!=null&&x["hideFlags"]!=null),"exact local id and original layout entries exposed");
   var loads=AssetDatabase.Loads;q=Request();q["objectLimit"]=129;Check(Run(q)["ok"]?.Value<bool>()==false&&AssetDatabase.Loads==loads,"invalid limit rejected before enumeration");
   q=Request();q["objectOffset"]=-1;Check(Run(q)["ok"]?.Value<bool>()==false&&AssetDatabase.Loads==loads,"negative offset rejected");
   q=Request();q["objectLimit"]=1.5;Check(Run(q)["ok"]?.Value<bool>()==false&&AssetDatabase.Loads==loads,"fractional limit rejected");
   AssetDatabase.Objects=new UnityEngine.Object[4097];Check(Run(Request())["ok"]?.Value<bool>()==false,"total enumeration bound enforced");
   AssetDatabase.Objects=new[]{new Material{id=3,name=new string('x',9000)}};Check(Run(Request())["ok"]?.Value<bool>()==false,"oversized entry rejected without truncating evidence");
   AssetDatabase.Objects=Enumerable.Range(0,128).Select(i=>(UnityEngine.Object)new Material{id=i+3,name=new string('x',2000)}).ToArray();q=Request();q["objectLimit"]=128;Check(Run(q)["ok"]?.Value<bool>()==false,"256 KiB response bound rejects rather than truncates");
   AssetDatabase.Objects=new UnityEngine.Object[]{AssetDatabase.Main,new UnityEditor.AssetImporters.ImportLog{id=77,name="Import Logs",hideFlags=HideFlags.HideInHierarchy}};
   var logListing=Run(Request())["objectListing"] as JObject;
   Check(logListing!=null&&logListing["total"].Value<int>()==2&&logListing["objects"].Any(x=>x["instanceId"].Value<int>()==77&&x["includedInCopyLayout"]?.Value<bool>()==false&&x["exclusionReason"]?.Value<string>()=="unity_import_diagnostic"),"public listing retains ImportLog with precise scope annotation");
   Check(logListing!=null&&logListing["objectLayoutScope"]?.Value<string>()=="authoring_objects_excluding_unity_import_logs"&&logListing["allObjectLayoutDigest"]!=null&&logListing["allObjectLayoutDigest"].Value<string>()!=logListing["objectLayoutDigest"].Value<string>()&&logListing["objectLayoutDigest"].Value<string>()==CopyDigest.ComputeObjectLayoutDigest("Assets/a.mat"),"full raw digest preserved separately from copy authoring digest");
   q=new JObject{["assetPath"]="Assets/a.prefab",["includeRendererMaterials"]=true,["rendererLimit"]=2};
   Check(Run(q)["ok"]?.Value<bool>()==false&&GameObject.Enumerations==0,"non-prefab rejects renderer inspection");
   var root=new GameObject{name="Root"};var a=new GameObject{name="Shoes"};var b=new GameObject{name="Shoes"};a.transform.parent=root.transform;b.transform.parent=root.transform;b.transform.siblingIndex=1;
   var skin=new SkinnedMeshRenderer{gameObject=a,sharedMesh=new Mesh{name="skin"},sharedMaterials=new Material[]{new Material{name="shoe",shader=new Shader{name="lilToon"}},null}};
   var second=new SkinnedMeshRenderer{gameObject=a};var mesh=new MeshRenderer{gameObject=b};var filter=new MeshFilter{gameObject=b,sharedMesh=new Mesh{name="static"}};
   a.components.Add(skin);a.components.Add(new MeshRenderer{gameObject=a});a.components.Add(second);b.components.Add(mesh);b.components.Add(filter);root.renderers=new Renderer[]{skin,second,mesh};AssetDatabase.Main=root;
   var beforeEnumeration=GameObject.Enumerations;Run(new JObject{["assetPath"]="Assets/a.prefab"});Check(GameObject.Enumerations==beforeEnumeration,"default prefab read does not enumerate renderers");
   var page=Run(q)["rendererMaterialListing"] as JObject;Check(page!=null,"renderer page is exposed");if(page==null)return 1;
   var rows=page["renderers"];Check(page["total"].Value<int>()==3&&page["count"].Value<int>()==2&&page["nextOffset"].Value<int>()==2&&page["truncated"].Value<bool>(),"renderer page bound and continuation");
   Check(rows[0]["relativePath"].Value<string>()=="Shoes"&&rows[0]["siblingIndexPath"].Value<string>()=="0"&&rows[0]["rendererComponentIndex"].Value<int>()==0&&rows[1]["rendererComponentIndex"].Value<int>()==1&&rows[0]["rendererComponentType"].Value<string>()=="UnityEngine.SkinnedMeshRenderer","exact relative hierarchy and duplicate component index");
   Check(rows[0]["mesh"]["name"].Value<string>()=="skin"&&rows[0]["mesh"]["guid"].Value<string>()=="guid"&&rows[0]["mesh"]["localFileId"].Value<string>()=="9007199254740993","mesh persistent identity");
   var slots=rows[0]["materials"];Check(slots.Count()==2&&slots[0]["slotIndex"].Value<int>()==0&&slots[0]["material"]["name"].Value<string>()=="shoe"&&slots[0]["material"]["assetPath"].Value<string>()=="Assets/a.mat"&&slots[0]["material"]["guid"].Value<string>()=="guid"&&slots[0]["shader"]["name"].Value<string>()=="lilToon"&&slots[1]["material"].Type==JTokenType.Null,"exact slots retain shader identity and null assignment");
   q["rendererOffset"]=2;page=Run(q)["rendererMaterialListing"] as JObject;Check(page["count"].Value<int>()==1&&!page["truncated"].Value<bool>()&&page["nextOffset"].Type==JTokenType.Null&&page["renderers"][0]["siblingIndexPath"].Value<string>()=="1"&&page["renderers"][0]["mesh"]["name"].Value<string>()=="static","duplicate sibling path and MeshFilter mesh on final page");
   q["rendererOffset"]=3;page=Run(q)["rendererMaterialListing"] as JObject;Check(page["count"].Value<int>()==0&&!page["truncated"].Value<bool>(),"past-end page explicitly empty");
   beforeEnumeration=GameObject.Enumerations;q["rendererLimit"]=129;Check(Run(q)["ok"]?.Value<bool>()==false&&GameObject.Enumerations==beforeEnumeration,"renderer limit rejects before enumeration");
   q["rendererLimit"]=1.5;Check(Run(q)["ok"]?.Value<bool>()==false,"renderer fractional limit rejected");q["rendererLimit"]=1;q["rendererOffset"]=-1;Check(Run(q)["ok"]?.Value<bool>()==false,"renderer negative offset rejected");q["rendererOffset"]=0;q["includeRendererMaterials"]=1;Check(Run(q)["ok"]?.Value<bool>()==false,"renderer nonboolean option rejected");q["includeRendererMaterials"]=true;
   root.renderers=Enumerable.Repeat<Renderer>(skin,4097).ToArray();Check(Run(q)["ok"]?.Value<bool>()==false,"renderer enumeration cap rejects explicitly");root.renderers=new Renderer[]{skin};skin.sharedMaterials=Enumerable.Repeat(new Material{name=new string('x',4096)},128).ToArray();Check(Run(q)["ok"]?.Value<bool>()==false,"renderer response size rejects rather than truncates slots");
   return failures==0?0:1;
  }
 }
}
'''
    helper=method(copy,'internal static bool IsCopyLayoutObject(') if 'internal static bool IsCopyLayoutObject(' in copy else ''
    program = preamble + seam.replace('CopyDigest.ComputeObjectLayoutDigest','DuplicateProjectAssetTool.ComputeObjectLayoutDigest') + '\nnamespace VRCForge.Editor {\n' + method(code, 'public static class GetAssetInfoTool') + '\npublic static class DuplicateProjectAssetTool {\n' + method(copy, 'internal static string ComputeObjectLayoutDigest(') + helper + '\n}}'
    cs=tmp_path/'Probe.cs'; cs.write_text(program,encoding='utf-8');dll=tmp_path/'Probe.dll'
    command=[dotnet,str(compiler),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}']+[f'-r:{p}' for p in refs[-1].glob('*.dll')]+[f'-r:{newtonsoft}',str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/'Newtonsoft.Json.dll')
    (tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}),encoding='utf-8')
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count('PASS ')==47
