"""Run the production AssetDatabaseRefreshTool state machine against Unity stubs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/OutfitPackageImporter.cs"

STUBS = r'''
namespace UnityEditor {
 public class InitializeOnLoadAttribute:System.Attribute {}
 [System.Flags] public enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
 public static class EditorApplication { public static bool isCompiling,isUpdating; public static double timeSinceStartup; public static event System.Action update; public static void Tick()=>update?.Invoke(); }
 public static class SessionState { public static System.Collections.Generic.Dictionary<string,string> Values=new(); public static void SetString(string k,string v)=>Values[k]=v; public static string GetString(string k,string f)=>Values.TryGetValue(k,out var v)?v:f; public static void EraseString(string k)=>Values.Remove(k); }
 public class MonoScript:UnityEngine.Object { public static Type LoadedClass=typeof(UnityEditor.AssetImporters.ScriptedImporter); public Type GetClass()=>LoadedClass; }
 public class MonoImporter:AssetImporter {}
 public class AssetImporter { public static AssetImporter GetAtPath(string path){AssetDatabase.ImporterLookups++; if(AssetDatabase.DropInstanceOnSecondLookup&&AssetDatabase.ImporterLookups>1) AssetDatabase.MissingInstance=true; return (AssetDatabase.ReadbackMissing||AssetDatabase.MissingInstance||path==AssetDatabase.MissingInstancePath||(AssetDatabase.RestoreMissingBeforeImport&&AssetDatabase.Imports==0&&path.EndsWith(".lilcontainer")))?null:(path.EndsWith(".cs")?new MonoImporter():(path.EndsWith(".lilcontainer")?new UnityEditor.AssetImporters.ScriptedImporter():new AssetImporter()));} }
 public static class AssetDatabase {
  public static int Imports,Saves,Refreshes,Reads; public static bool ReadbackMissing,MissingInstance,WrongTypeAfterImport; public static string[] Paths={"Assets/A.asset"};
  public static event System.Action<string> importPackageStarted,importPackageCompleted,importPackageCancelled; public static event System.Action<string,string> importPackageFailed; public static System.Action<string[]> onImportPackageItemsCompleted;
  public static void SaveAssets()=>Saves++;
  public static void Refresh()=>Refreshes++;
  public static void ImportAsset(string path,ImportAssetOptions options){Imports++; MissingInstance=false; if(ThrowAfterImport) throw new InvalidOperationException("fixture native import failure"); if(FailAfterImport) ReadbackMissing=true; if(DriftMetaAfterImport) System.IO.File.WriteAllText(System.IO.Path.Combine(System.Environment.GetEnvironmentVariable("FIXTURE_ROOT"),path.Replace('/','\\')+".meta"),"drift"); if(DriftManifestAfterImport) File.WriteAllText(System.IO.Path.Combine(System.Environment.GetEnvironmentVariable("FIXTURE_ROOT"),"Packages/com.example.embedded/package.json"),"{\"name\":\"com.example.embedded\",\"version\":\"2.0.0\"}");}
  public static bool FailAfterImport,ThrowAfterImport,RegistrationDrift,DropInstanceOnSecondLookup,DriftMetaAfterImport,DriftManifestAfterImport,RestoreMissingBeforeImport; public static int ImporterLookups; public static string MissingInstancePath;
  public static Type GetDefaultImporter(string path)=>path.EndsWith(".cs")?typeof(MonoImporter):(path.EndsWith(".lilcontainer")?typeof(UnityEditor.AssetImporters.ScriptedImporter):typeof(AssetImporter)); public static Type GetImporterOverride(string path)=>RegistrationDrift&&Imports>0?typeof(string):null;
  public static string[] GetAllAssetPaths()=>Paths;
  public static System.Type GetMainAssetTypeAtPath(string path){Reads++; return ReadbackMissing?null:(WrongTypeAfterImport&&Imports>0?typeof(int):(path.EndsWith(".cs")?typeof(MonoScript):typeof(string)));}
  public static string AssetPathToGUID(string path)=>path==ScriptGuidPath?new string('b',32):new string('a',32);
  public static string ScriptGuidPath="Assets/Scripted/Importer.cs";
  public static string GUIDToAssetPath(string guid)=>guid==new string('b',32)?ScriptGuidPath:"";
  public static T LoadAssetAtPath<T>(string path) where T:UnityEngine.Object => path==ScriptGuidPath?(T)(UnityEngine.Object)new MonoScript():null;
  public static AssetImporter GetAtPath(string path)=>ReadbackMissing?null:(path.EndsWith(".lilcontainer")?new UnityEditor.AssetImporters.ScriptedImporter():new AssetImporter());
 }
}
namespace UnityEditor.AssetImporters { public class ScriptedImporter:UnityEditor.AssetImporter {} }
namespace UnityEditor.PackageManager { public static class Client { public static object Resolve()=>null; } }
namespace UnityEngine { public class Object {} public static class Debug { public static void Log(object x){} public static void LogError(object x){} } }
namespace VRCForge.Core.MCP {
 public class VRCForgeCommandAttribute:System.Attribute { public string Summary{get;set;} public bool UsesContinuation{get;set;} public string ContinuationAction{get;set;} public int ContinuationTimeoutSeconds{get;set;} public VRCForgeCommandAttribute(string toolId){} }
 public class VRCForgeInputAttribute:System.Attribute { public bool IsRequired{get;set;} public VRCForgeInputAttribute(string description){} }
 public static class VRCForgeToolResult { public static object Waiting(string m,double d,object o)=>o; public static object Completed(string m,object o)=>o; public static object Failed(string m)=>new Newtonsoft.Json.Linq.JObject{{"status","failed"},{"message",m}}; public static object RejectedBeforeMutation(string a,string b,string c,string d,bool e,object f)=>new Newtonsoft.Json.Linq.JObject{{"status","rejected"},{"code",a}}; }
}
namespace VRCForge.Editor {
 public static class CheckpointPrepareTool { public static string ProjectRoot()=>System.Environment.GetEnvironmentVariable("FIXTURE_ROOT"); public static void ValidateProject(Newtonsoft.Json.Linq.JObject p){} public static void EnsureEditorReady(){} }
 public static class MaterialShaderTool { public static void EnsureNoReparseBoundary(string root,string path){} }
 public static class CompileErrorMonitor { public static Newtonsoft.Json.Linq.JObject ReadCoreInfoSnapshot(int n)=>new Newtonsoft.Json.Linq.JObject{{"isCompiling",UnityEditor.EditorApplication.isCompiling},{"captureComplete",true},{"errorCount",0}}; }
 public static class Client { public static object Resolve()=>null; }
 internal static class UnityAsyncJobRegistry {
  static readonly System.Collections.Generic.Dictionary<string,Newtonsoft.Json.Linq.JObject> Jobs=new(); static string active="";
  public static Newtonsoft.Json.Linq.JObject Create(string tool,string project,Newtonsoft.Json.Linq.JObject before,Newtonsoft.Json.Linq.JObject operation,System.TimeSpan timeout){var id=System.Guid.NewGuid().ToString("N");var j=new Newtonsoft.Json.Linq.JObject{{"job_id",id},{"tool",tool},{"status","queued"},{"before",before},{"after",Newtonsoft.Json.Linq.JValue.CreateNull()},{"operation",operation}};Jobs[id]=j;return (Newtonsoft.Json.Linq.JObject)j.DeepClone();}
  public static void SetActive(string tool,string id)=>active=id; public static string GetActive(string tool)=>active;
  public static void ClearActive(string tool,string id){if(active==id)active="";}
  public static void MarkRunning(string id){Jobs[id]["status"]="running";}
  public static Newtonsoft.Json.Linq.JObject ReadOperation(string id)=>Jobs[id]["operation"] as Newtonsoft.Json.Linq.JObject;
  public static Newtonsoft.Json.Linq.JObject Poll(string id)=>Jobs.TryGetValue(id,out var j)?(Newtonsoft.Json.Linq.JObject)j.DeepClone():null;
  public static Newtonsoft.Json.Linq.JObject Complete(string id,System.Func<Newtonsoft.Json.Linq.JObject> read){var after=read();var j=Jobs[id];j["after"]=after;j["status"]="done";ClearActive("vrc_refresh_asset_database",id);return j;}
  public static Newtonsoft.Json.Linq.JObject Fail(string id,string code,string message,bool retryable,System.Func<Newtonsoft.Json.Linq.JObject> read){var j=Jobs[id];j["status"]="failed";j["error"]=new Newtonsoft.Json.Linq.JObject{{"code",code},{"message",message},{"retryable",retryable}};try{j["after"]=read();}catch{} ClearActive("vrc_refresh_asset_database",id);return j;}
 }
}
'''

RUNNER = r'''
class Runner {
 static Type T=typeof(VRCForge.Editor.AssetDatabaseRefreshTool); static System.Reflection.BindingFlags F=System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Static;
 static object Call(string n,params object[] a)=>T.GetMethod(n,F).Invoke(null,a); static void Check(bool ok,string m){if(!ok)throw new Exception(m);}
 static JObject Job(string id){return VRCForge.Editor.UnityAsyncJobRegistry.Poll(id);}
 static string Start(JObject p){var r=(JObject)T.GetMethod("HandleCommand").Invoke(null,new object[]{p}); return r.Value<string>("job_id") ?? throw new Exception("start failed: "+r.ToString());}
 static void Tick(){UnityEditor.EditorApplication.timeSinceStartup=1; UnityEditor.EditorApplication.Tick();}
 static JObject Finish(string id){Tick(); Tick(); Tick(); return Job(id);}
 static JObject Item(string path,string expected)=>new JObject{{"assetPath",path},{"guid",new string('a',32)},{"expectedSourceSha256",expected},{"expectedImporterType","UnityEditor.AssetImporter"}};
 static JObject PItem(string path,string expected)=>new JObject{{"assetPath",path},{"guid",new string('a',32)},{"expectedSourceSha256",expected},{"expectedImporterType","UnityEditor.MonoImporter"}};
 static string Sha(byte[] bytes){using(var s=SHA256.Create())return BitConverter.ToString(s.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
 static JObject ScriptedItem(string root){var path=Path.Combine(root,"Assets","Scripted","Container.lilcontainer");var meta=path+".meta";var old="fileFormatVersion: 2\nguid: "+new string('a',32)+"\nScriptedImporter:\n  script: {instanceID: 0}\n";var restored="fileFormatVersion: 2\nguid: "+new string('a',32)+"\nScriptedImporter:\n  script: {fileID: 11500000, guid: "+new string('b',32)+", type: 3}\n";return new JObject{{"assetPath","Assets/Scripted/Container.lilcontainer"},{"guid",new string('a',32)},{"expectedSourceSha256",Hash(path)},{"expectedImporterType","UnityEditor.AssetImporters.ScriptedImporter"},{"restoreScriptedImporterReference",new JObject{{"scriptGuid",new string('b',32)},{"expectedMetadataSha256",Sha(Encoding.UTF8.GetBytes(old))},{"restoredMetadataSha256",Sha(Encoding.UTF8.GetBytes(restored))}}}};}
 static void SetupScripted(string root){var dir=Path.Combine(root,"Assets","Scripted");Directory.CreateDirectory(dir);var path=Path.Combine(dir,"Container.lilcontainer");File.WriteAllText(path,"stable container");File.WriteAllText(path+".meta","fileFormatVersion: 2\nguid: "+new string('a',32)+"\nScriptedImporter:\n  script: {instanceID: 0}\n");File.WriteAllText(Path.Combine(dir,"Importer.cs"),"class Importer {}\n");File.WriteAllText(Path.Combine(dir,"Importer.cs.meta"),"fileFormatVersion: 2\nguid: "+new string('b',32)+"\n");UnityEditor.AssetDatabase.ScriptGuidPath="Assets/Scripted/Importer.cs";MonoScript.LoadedClass=typeof(UnityEditor.AssetImporters.ScriptedImporter);UnityEditor.AssetDatabase.RestoreMissingBeforeImport=true;}
 static string SetupPackage(string root,bool manifest=true,string name="com.example.embedded"){var dir=Path.Combine(root,"Packages",name);Directory.CreateDirectory(dir);if(manifest)File.WriteAllText(Path.Combine(dir,"package.json"),"{\"name\":\""+name+"\",\"version\":\"1.0.0\"}");var file=Path.Combine(dir,"Editor","Embedded.cs");Directory.CreateDirectory(Path.GetDirectoryName(file));File.WriteAllText(file,"class Embedded {}");File.WriteAllText(file+".meta","fileFormatVersion: 2\nguid: "+new string('a',32)+"\n");return file;}
 static int Main(string[] a){try{
  var root=Environment.GetEnvironmentVariable("FIXTURE_ROOT"); var file=Path.Combine(root,"Assets","A.asset"); Directory.CreateDirectory(Path.GetDirectoryName(file)); File.WriteAllText(file,"stable"); File.WriteAllText(file+".meta","fileFormatVersion: 2\nguid: "+new string('a',32)+"\n");
  var hash=Hash(file); var item=Item("Assets/A.asset",hash);
  if(a[0]=="package_success"||a[0]=="package_missing_manifest"||a[0]=="package_mismatched_name"||a[0]=="package_non_cs"||a[0]=="package_library"||a[0]=="package_queued_meta_drift"||a[0]=="package_queued_manifest_drift"||a[0]=="package_after_meta_drift"||a[0]=="package_after_manifest_drift"){
   var path=SetupPackage(root,a[0]!="package_missing_manifest","com.example.embedded"); if(a[0]=="package_mismatched_name")File.WriteAllText(Path.Combine(root,"Packages","com.example.embedded","package.json"),"{\"name\":\"com.example.wrong\"}"); if(a[0]=="package_non_cs"){path=Path.Combine(root,"Packages","com.example.embedded","Editor","Embedded.txt");File.WriteAllText(path,"text");File.WriteAllText(path+".meta","meta");} if(a[0]=="package_library"){path=Path.Combine(root,"Library","Embedded.cs");Directory.CreateDirectory(Path.GetDirectoryName(path));File.WriteAllText(path,"class Embedded {}");File.WriteAllText(path+".meta","meta");} var pi=PItem(path.Substring(root.Length+1).Replace('\\','/'),Hash(path));
   if(a[0]=="package_success"){var id=Start(new JObject{{"reimportAssets",new JArray(pi)}});Check(id!=null,"package must queue");var done=Finish(id);Check((string)done["status"]=="done","package did not complete");Check(UnityEditor.AssetDatabase.Imports==1&&UnityEditor.AssetDatabase.Saves==0&&UnityEditor.AssetDatabase.Refreshes==0,"package must import once without global refresh");var proof=done["after"]["reimportResults"][0]["after"]["packageScriptIdentity"];Check((string)proof["metadataSha256"]==Hash(path+".meta"),"metadata proof missing");Check((string)proof["manifestSha256"]==Hash(Path.Combine(root,"Packages","com.example.embedded","package.json")),"manifest proof missing");}
   else if(a[0].Contains("queued_meta")){var id=Start(new JObject{{"reimportAssets",new JArray(pi)}});Check(id!=null,"package must pass initial preflight");File.WriteAllText(path+".meta","drift");var done=Finish(id);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==0,"queued meta drift was not rejected");}
   else if(a[0].Contains("queued_manifest")){var id=Start(new JObject{{"reimportAssets",new JArray(pi)}});Check(id!=null,"package must pass initial preflight");File.WriteAllText(Path.Combine(root,"Packages","com.example.embedded","package.json"),"{\"name\":\"com.example.embedded\",\"version\":\"2.0.0\"}");var done=Finish(id);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==0,"queued manifest drift was not rejected");}
   else if(a[0].Contains("after_meta")||a[0].Contains("after_manifest")){if(a[0].Contains("after_meta"))UnityEditor.AssetDatabase.DriftMetaAfterImport=true;else UnityEditor.AssetDatabase.DriftManifestAfterImport=true;var id=Start(new JObject{{"reimportAssets",new JArray(pi)}});var done=Finish(id);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==1,"post-write drift was not detected");}
   else {var r=(JObject)T.GetMethod("HandleCommand").Invoke(null,new object[]{new JObject{{"reimportAssets",new JArray(pi)}}});Check((string)r["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==0,"package rejection missing");}
  }
  else if(a[0].StartsWith("scripted_")){SetupScripted(root);var sp=Path.Combine(root,"Assets","Scripted","Container.lilcontainer");var sm=sp+".meta";var si=ScriptedItem(root);var original=File.ReadAllText(sm);if(a[0]=="scripted_bad_old_hash")si["restoreScriptedImporterReference"]["expectedMetadataSha256"]=new string('c',64);if(a[0]=="scripted_bad_new_hash")si["restoreScriptedImporterReference"]["restoredMetadataSha256"]=new string('c',64);if(a[0]=="scripted_bad_guid")si["restoreScriptedImporterReference"]["scriptGuid"]=new string('c',32);if(a[0]=="scripted_bad_class")MonoScript.LoadedClass=typeof(MonoImporter);if(a[0]=="scripted_non_scripted_type"){si["assetPath"]="Assets/Scripted/Other.asset";var op=Path.Combine(root,"Assets","Scripted","Other.asset");File.WriteAllText(op,"stable");File.WriteAllText(op+".meta",File.ReadAllText(sm));si["expectedSourceSha256"]=Hash(op);si["expectedImporterType"]="UnityEditor.AssetImporter";}if(a[0]=="scripted_nonnull_ref"){File.WriteAllText(sm,File.ReadAllText(sm).Replace("{instanceID: 0}","{fileID: 11500000, guid: "+new string('b',32)+", type: 3}"));si["restoreScriptedImporterReference"]["expectedMetadataSha256"]=Hash(sm);si["restoreScriptedImporterReference"]["restoredMetadataSha256"]=new string('c',64);}if(a[0]=="scripted_queued_metadata_drift"){var q=Start(new JObject{{"reimportAssets",new JArray(si)}});Check(q!=null,"queued metadata drift must queue");File.WriteAllText(sm,"drift");var done=Finish(q);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==0,"queued metadata drift was not rejected");}else if(a[0]=="scripted_queued_class_drift"){var q=Start(new JObject{{"reimportAssets",new JArray(si)}});Check(q!=null,"queued class drift must queue");MonoScript.LoadedClass=typeof(MonoImporter);var done=Finish(q);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==0,"queued class drift was not rejected");}else if(a[0]=="scripted_after_meta_drift"){UnityEditor.AssetDatabase.DriftMetaAfterImport=true;var q=Start(new JObject{{"reimportAssets",new JArray(si)}});var done=Finish(q);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==1,"post-import metadata drift was not detected");Check(File.ReadAllText(sm)=="drift","unknown post-import metadata was unexpectedly overwritten");var recovery=done["after"]["referenceRestorationRecovery"];Check((bool)recovery["metadataCompensated"]==false&&(bool)recovery["checkpointRecoveryRequired"]==true,"unknown metadata recovery proof missing");}else if(a[0]=="scripted_readback_null"||a[0]=="scripted_native_exception"){if(a[0]=="scripted_readback_null")UnityEditor.AssetDatabase.FailAfterImport=true;else UnityEditor.AssetDatabase.ThrowAfterImport=true;var q=Start(new JObject{{"reimportAssets",new JArray(si)}});var done=Finish(q);Check((string)done["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==1,"scripted failure was not reported");Check(File.ReadAllText(sm)==original,"failed scripted restore did not restore original metadata");var recovery=done["after"]["referenceRestorationRecovery"];Check((bool)recovery["metadataCompensated"]&&(bool)recovery["checkpointRecoveryRequired"],"compensated failure must retain recovery facts");Check(!Directory.GetFiles(Path.GetDirectoryName(sm),"*.vrcforge-*.tmp").Any(),"temporary restore file leaked");}else if(a[0]=="scripted_success"){var q=Start(new JObject{{"reimportAssets",new JArray(si)}});Check(q!=null,"scripted restore must queue");var done=Finish(q);Check((string)done["status"]=="done"&&UnityEditor.AssetDatabase.Imports==1,"scripted restore did not complete");var expected="fileFormatVersion: 2\nguid: "+new string('a',32)+"\nScriptedImporter:\n  script: {fileID: 11500000, guid: "+new string('b',32)+", type: 3}\n";Check(File.ReadAllText(sm)==expected,"restored metadata text mismatch");Check(Hash(sm)==(string)si["restoreScriptedImporterReference"]["restoredMetadataSha256"],"restored metadata hash mismatch");Check(Hash(sp)==(string)si["expectedSourceSha256"]&&((string)done["after"]["reimportResults"][0]["after"]["guid"]==new string('a',32)),"source or GUID proof mismatch");Check(UnityEditor.AssetDatabase.Saves==0&&UnityEditor.AssetDatabase.Refreshes==0,"scripted repair caused global Save/Refresh");Check(!Directory.GetFiles(Path.GetDirectoryName(sm),"*.vrcforge-*.tmp").Any(),"temporary restore file leaked");}else{var bad=(JObject)T.GetMethod("HandleCommand").Invoke(null,new object[]{new JObject{{"reimportAssets",new JArray(si)}}});Check((string)bad["status"]=="failed"&&UnityEditor.AssetDatabase.Imports==0,"scripted preflight rejection missing");if(a[0]=="scripted_nonnull_ref")Check(((string)bad["message"]).Contains("Only an exact null"),"nonnull case failed on a different guard");}}
  else if(a[0]=="success"){var id=Start(new JObject{{"reimportAssets",new JArray(item)}});Check(id!=null,"not queued");var done=Finish(id);Check((string)done["status"]=="done","not done");Check(UnityEditor.AssetDatabase.Imports==1,"one exact import expected");Check(UnityEditor.AssetDatabase.Saves==0&&UnityEditor.AssetDatabase.Refreshes==0,"targeted route must not legacy save/refresh");Check(done["after"]["reimportResults"] is JArray,"readback missing");}
  else if(a[0]=="missing_instance"){UnityEditor.AssetDatabase.MissingInstance=true;var r=(JObject)T.GetMethod("HandleCommand").Invoke(null,new object[]{new JObject{{"reimportAssets",new JArray(item)}}});Check((string)r["status"]=="failed","missing instance must fail preflight");Check(UnityEditor.AssetDatabase.Imports==0&&UnityEditor.AssetDatabase.Saves==0&&UnityEditor.AssetDatabase.Refreshes==0,"missing instance mutated Unity");}
  else if(a[0]=="missing_second_instance"){var second=Path.Combine(root,"Assets","B.asset");File.WriteAllText(second,"stable");File.WriteAllText(second+".meta","fixture metadata");UnityEditor.AssetDatabase.MissingInstancePath="Assets/B.asset";var r=(JObject)T.GetMethod("HandleCommand").Invoke(null,new object[]{new JObject{{"reimportAssets",new JArray(item,Item("Assets/B.asset",hash))}}});Check((string)r["status"]=="failed","second missing instance must reject whole batch");Check(UnityEditor.AssetDatabase.Imports==0&&UnityEditor.AssetDatabase.Saves==0&&UnityEditor.AssetDatabase.Refreshes==0,"invalid second instance mutated Unity");}
  else if(a[0]=="queued_instance_missing"){UnityEditor.AssetDatabase.DropInstanceOnSecondLookup=true;var id=Start(new JObject{{"reimportAssets",new JArray(item)}});Check(id!=null,"queued case must pass initial preflight");var done=Finish(id);Check((string)done["status"]=="failed","queued missing instance must fail");Check(UnityEditor.AssetDatabase.Imports==0&&UnityEditor.AssetDatabase.Saves==0&&UnityEditor.AssetDatabase.Refreshes==0,"queued missing instance mutated Unity");}
  else if(a[0]=="preflight"){var bad=Item("Assets/Missing.asset",hash);var r=(JObject)T.GetMethod("HandleCommand").Invoke(null,new object[]{new JObject{{"reimportAssets",new JArray(item,bad)}}});Check((string)r["status"]=="failed","batch should fail preflight");Check(UnityEditor.AssetDatabase.Imports==0,"failed preflight imported an item");}
  else if(a[0]=="drift"){var id=Start(new JObject{{"reimportAssets",new JArray(item)}});File.WriteAllText(file,"drift");var done=Finish(id);Check((string)done["status"]=="failed","drift must fail");Check(UnityEditor.AssetDatabase.Imports==0,"drift imported despite queued identity");}
  else if(a[0]=="readback"||a[0]=="changed_type"||a[0]=="changed_registration"){var id=Start(new JObject{{"reimportAssets",new JArray(item)}});if(a[0]=="readback")UnityEditor.AssetDatabase.FailAfterImport=true;else if(a[0]=="changed_type") UnityEditor.AssetDatabase.WrongTypeAfterImport=true;else UnityEditor.AssetDatabase.RegistrationDrift=true;var done=Finish(id);Check((string)done["status"]=="failed","readback mismatch must fail: "+done);Check(UnityEditor.AssetDatabase.Imports==1,"readback case must import once");Check((string)done["error"]["code"]=="asset_database_reimport_readback_failed","wrong readback code: "+done);}
  else if(a[0]=="legacy"){var id=Start(new JObject());var done=Finish(id);Check((string)done["status"]=="done","legacy route not done");Check(UnityEditor.AssetDatabase.Imports==0&&UnityEditor.AssetDatabase.Saves==1&&UnityEditor.AssetDatabase.Refreshes==1,"legacy SaveAssets+Refresh missing");}
  Console.WriteLine(a[0]+":pass");return 0;
 }catch(Exception e){Console.Error.WriteLine(e);return 1;}}
 static string Hash(string p){using(var s=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(s.ComputeHash(File.ReadAllBytes(p))).Replace("-","").ToLowerInvariant();}
}
'''


@pytest.fixture(scope="module")
def refresh_probe(tmp_path_factory):
    out = tmp_path_factory.mktemp("asset-reimport-runtime")
    dotnet_root = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip("Local .NET SDK required; no Unity is launched")
    compiler = compilers[-1]; newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = SOURCE.read_text(encoding="utf-8-sig")
    marker = '    [VRCForgeCommand(\n        toolId: "vrc_refresh_asset_database"'
    start = source.index(marker)
    production = source[start:source.rfind("\n    }\n}") + len("\n    }")]
    header = "using System; using System.IO; using System.Linq; using System.Collections.Generic; using System.Security.Cryptography; using System.Text; using Newtonsoft.Json.Linq; using UnityEditor; using UnityEngine; using UnityEditor.PackageManager; using VRCForge.Core.MCP;\n"
    cs = out / "Probe.cs"; cs.write_text(header + "namespace VRCForge.Editor {\n" + production + "\n}\n" + STUBS + RUNNER, encoding="utf-8")
    dll = out / "Probe.dll"
    cmd = [str(dotnet_root / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:9.0", f"-out:{dll}", *[f"-r:{p}" for p in refs[-1].glob("*.dll")], f"-r:{newtonsoft}", str(cs)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, out / newtonsoft.name)
    (out / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}), encoding="utf-8")
    return dotnet_root / "dotnet.exe", dll


@pytest.mark.parametrize("case", ["success", "missing_instance", "missing_second_instance", "queued_instance_missing", "preflight", "drift", "readback", "changed_type", "changed_registration", "legacy", "package_success", "package_missing_manifest", "package_mismatched_name", "package_non_cs", "package_library", "package_queued_meta_drift", "package_queued_manifest_drift", "package_after_meta_drift", "package_after_manifest_drift", "scripted_success", "scripted_bad_old_hash", "scripted_bad_new_hash", "scripted_bad_guid", "scripted_bad_class", "scripted_non_scripted_type", "scripted_nonnull_ref", "scripted_queued_metadata_drift", "scripted_queued_class_drift", "scripted_after_meta_drift", "scripted_readback_null", "scripted_native_exception"])
def test_asset_reimport_runtime(refresh_probe, tmp_path, case):
    dotnet, dll = refresh_probe
    env = os.environ.copy(); env["FIXTURE_ROOT"] = str(tmp_path)
    result = subprocess.run([str(dotnet), str(dll), case], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
