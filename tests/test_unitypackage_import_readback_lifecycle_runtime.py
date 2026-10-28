"""Execute the production importer class offline; Unity lifecycle remains a live gate."""
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
 public static class EditorApplication {public static bool isCompiling,isUpdating;}
 public static class SessionState {
  public static System.Collections.Generic.Dictionary<string,string> Values=new System.Collections.Generic.Dictionary<string,string>();
  public static void SetString(string k,string v)=>Values[k]=v;
  public static string GetString(string k,string fallback)=>Values.TryGetValue(k,out var v)?v:fallback;
  public static void EraseString(string k)=>Values.Remove(k);
 }
 public static class AssetDatabase {
  public static event System.Action<string> importPackageStarted,importPackageCompleted,importPackageCancelled;
  public static event System.Action<string,string> importPackageFailed;
  public static System.Action<string[]> onImportPackageItemsCompleted;
  public static bool Available; public static int Reads,Imports,Saves,Refreshes; public static ImportAssetOptions RefreshOptions; public static System.Action RefreshAction;
  public static void SaveAssets() {Saves++;}
  public static void Refresh(ImportAssetOptions o) {Refreshes++;RefreshOptions=o;RefreshAction?.Invoke();}
  public static System.Type GetMainAssetTypeAtPath(string p) {Reads++;return Available?typeof(string):null;}
  public static string AssetPathToGUID(string p)=>Available?new string('a',32):"";
  public static void ImportPackage(string p,bool i) {Imports++;}
 }
}
namespace UnityEditor.PackageManager {}
namespace VRCForge.Core.MCP {
 public class VRCForgeCommandAttribute:System.Attribute {public string Summary{get;set;} public VRCForgeCommandAttribute(string toolId){}}
 public class VRCForgeInputAttribute:System.Attribute {public bool IsRequired{get;set;} public VRCForgeInputAttribute(string description){}}
 public static class VRCForgeToolResult { public static object FailedWithException(string code,string message,System.Exception exception,object payload=null)=>Failed(message); public static object FailedWithException(string message,System.Exception exception,object payload=null)=>Failed(message);
  public static object Waiting(string m,double d,object o)=>o;
  public static object Completed(string m,object o)=>o;
  public static object Failed(string m)=>m;
 }
}
namespace VRCForge.Editor {
 public static class CheckpointPrepareTool {
  public static string ProjectRoot()=>System.Environment.GetEnvironmentVariable("VRCFORGE_TEST_PROJECT_ROOT")??"D:/Fixture";
  public static void ValidateProject(Newtonsoft.Json.Linq.JObject o){}
  public static void EnsureEditorReady(){}
 }
}
'''

RUNNER = r'''
class Runner {
 static System.Type T=typeof(VRCForge.Editor.UnityPackageImporterTool);
 static System.Reflection.BindingFlags Flags=System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Static;
 static object Call(string name,params object[] args)=>T.GetMethod(name,Flags).Invoke(null,args);
 static object Get(object job,string name)=>job.GetType().GetProperty(name).GetValue(job);
 static void Put(object job,string name,object value)=>job.GetType().GetProperty(name).SetValue(job,value);
 static string Id(object job)=>(string)Get(job,"jobId");
 static void Check(bool ok,string message){if(!ok)throw new System.Exception(message);}
 static string Active=>(string)T.GetField("activeJobId",Flags).GetValue(null);
 static System.Collections.IDictionary Jobs=>(System.Collections.IDictionary)T.GetField("Jobs",Flags).GetValue(null);
 static void WriteProjectAsset(string assetPath){var path=System.IO.Path.Combine(UnityEditorProjectRoot(),assetPath.Replace('/',System.IO.Path.DirectorySeparatorChar));System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path));System.IO.File.WriteAllBytes(path,new byte[]{1});}
 static void WriteFixtureAsset()=>WriteProjectAsset("Assets/Fixture.asset");
 static void Reset(){Jobs.Clear();T.GetField("activeJobId",Flags).SetValue(null,"");UnityEditor.SessionState.Values.Clear();UnityEditor.AssetDatabase.Available=false;UnityEditor.AssetDatabase.Reads=0;UnityEditor.AssetDatabase.RefreshAction=null;UnityEditor.EditorApplication.isCompiling=false;UnityEditor.EditorApplication.isUpdating=false;var assets=System.IO.Path.Combine(UnityEditorProjectRoot(),"Assets");if(System.IO.Directory.Exists(assets))System.IO.Directory.Delete(assets,true);System.IO.Directory.CreateDirectory(assets);}
 static object Job(){
  var j=System.Activator.CreateInstance(T.GetNestedType("ImportJob",System.Reflection.BindingFlags.NonPublic),true);
  Put(j,"jobId",System.Guid.NewGuid().ToString("N"));Put(j,"projectPath",UnityEditorProjectRoot());Put(j,"mutationStarted",true);Put(j,"startedForThisJob",true);Put(j,"importEventPackageName","fixture");Put(j,"expectedEventPackageName","fixture");Put(j,"expectedAssetPaths",new System.Collections.Generic.List<string>{"Assets/Fixture.asset"});Put(j,"status","running");
  Jobs[Id(j)]=j;T.GetField("activeJobId",Flags).SetValue(null,Id(j));UnityEditor.SessionState.SetString("VRCForge.UnityPackageImport.ActiveJob",Id(j));Call("PersistJob",j);return j;
 }
 static Newtonsoft.Json.Linq.JObject Poll(object j){Put(j,"readbackAttemptedUtc",System.DateTime.UtcNow.AddSeconds(-3));return(Newtonsoft.Json.Linq.JObject)Call("PollJob",Id(j));}
 static void Reload(){Jobs.Clear();T.GetField("activeJobId",Flags).SetValue(null,"");Call("RestorePersistedActiveJob");}
 static void Exhaust(object j){for(int i=0;i<5&&Get(j,"result")==null;i++)Poll(j);}
 static int Main(string[] args){try{Reset();var j=Job();switch(args[0]){
  case "permanent":
   WriteFixtureAsset();Call("OnImportCompleted","fixture");Exhaust(j);var r=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(r!=null,"completed import with permanent missing asset must terminate");Check(!(bool)r["ok"]&&!(bool)r["pending"],"failure must be terminal");Check((string)r["commitState"]=="unknown"&&(bool)r["checkpointRecoveryRequired"]&&!(bool)r["retryable"],"failure must preserve unknown recovery");Check(r["committed"].Type==Newtonsoft.Json.Linq.JTokenType.Null,"commit must not be fabricated");Check(Active=="","matching active lock must release");Check(UnityEditor.AssetDatabase.Reads==3,"exactly three failed idle reads");break;
  case "transient":
   WriteFixtureAsset();Call("OnImportCompleted","fixture");UnityEditor.AssetDatabase.Available=true;var success=Poll(j);Check((bool)success["ok"]&&!(bool)success["pending"]&&Active=="","transient asset must complete");break;
  case "running":
   Exhaust(j);Check(Get(j,"result")==null&&Active==Id(j)&&UnityEditor.AssetDatabase.Reads==0,"ordinary running job cannot expire or read prematurely");break;
  case "busy":
   Call("OnImportCompleted","fixture");int before=UnityEditor.AssetDatabase.Reads;UnityEditor.EditorApplication.isCompiling=true;Exhaust(j);Check(Get(j,"result")==null&&UnityEditor.AssetDatabase.Reads==before,"compilation consumes no readback budget");UnityEditor.EditorApplication.isCompiling=false;UnityEditor.EditorApplication.isUpdating=true;Exhaust(j);Check(UnityEditor.AssetDatabase.Reads==before,"updating consumes no budget");UnityEditor.EditorApplication.isUpdating=false;Exhaust(j);Check(Get(j,"result")!=null,"idle failure must eventually terminate");break;
  case "reload_budget":
   WriteFixtureAsset();Call("OnImportCompleted","fixture");Poll(j);string id=Id(j);Reload();j=Jobs[id];Exhaust(j);Check(Get(j,"result")!=null&&UnityEditor.AssetDatabase.Reads==3,"reload must preserve completed evidence and two consumed failures");break;
  case "legacy_completed":
   WriteFixtureAsset();UnityEditor.AssetDatabase.Available=true;Put(j,"status","readback_pending");Put(j,"readbackFailureCode","unitypackage_async_readback_pending");Put(j,"readbackFailurePath","Assets/Fixture.asset");Put(j,"readbackFailureReason","missing");Put(j,"readbackAttemptedUtc",System.DateTime.UtcNow.AddMinutes(-2));Call("PersistJob",j);string oldId=Id(j);Reload();j=Jobs[oldId];Exhaust(j);Check(Get(j,"result")!=null&&(bool)((Newtonsoft.Json.Linq.JObject)Get(j,"result"))["ok"],"strict legacy completed state must recover bounded readback");break;
  case "legacy_ambiguous":
   Put(j,"status","readback_pending");Put(j,"restoredAfterDomainReload",true);Put(j,"readbackFailureCode","unitypackage_restored_readback_pending");Call("PersistJob",j);string unknownId=Id(j);Reload();j=Jobs[unknownId];Put(j,"restoredUtc",System.DateTime.UtcNow.AddMinutes(-1));Exhaust(j);Check(Get(j,"result")==null&&Active==unknownId,"ambiguous legacy reload must not be terminalized");break;
  case "wrong_job":
   Call("OnImportCompleted","unrelated");Check(UnityEditor.AssetDatabase.Reads==0&&Get(j,"result")==null&&Active==Id(j),"wrong event must not advance job");Call("OnImportCompleted","fixture");var other=Job();Exhaust(j);Check(Get(j,"result")!=null&&Active==Id(other),"terminal old job must not release newer lock");break;
  case "completed_before_refresh":
   bool persisted=false;UnityEditor.AssetDatabase.RefreshAction=()=>{var p=Newtonsoft.Json.Linq.JObject.Parse(UnityEditor.SessionState.GetString("VRCForge.UnityPackageImport.Job."+Id(j),"{}"));persisted=p["importCompletedObserved"]?.Value<bool>()==true;};Call("OnImportCompleted","fixture");Check(persisted,"completed event fact must persist before refresh/reload");break;
  case "completed_no_global_save":
   WriteFixtureAsset();UnityEditor.AssetDatabase.Available=true;Call("OnImportCompleted","fixture");
   Check(UnityEditor.AssetDatabase.Saves==0,"completed import must not globally save loaded assets");
   Check(UnityEditor.AssetDatabase.Refreshes==1&&UnityEditor.AssetDatabase.RefreshOptions==(UnityEditor.ImportAssetOptions.ForceSynchronousImport|UnityEditor.ImportAssetOptions.ForceUpdate),"completed import must retain forced synchronous refresh");
   var complete=(Newtonsoft.Json.Linq.JObject)Get(j,"result");Check(complete!=null&&(bool)complete["ok"]&&Active==""&&UnityEditor.AssetDatabase.Reads==1,"completed import must retain expected asset readback and release matching lock");break;
  case "completed_without_expected_assets":
   Put(j,"expectedAssetPaths",new System.Collections.Generic.List<string>());Call("OnImportCompleted","fixture");Exhaust(j);var missingInventory=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(missingInventory!=null&&!(bool)missingInventory["ok"]&&!(bool)missingInventory["pending"],"completion without an expected asset inventory must fail terminally");
   Check((string)missingInventory["reason"]=="unitypackage_completed_readback_failed"&&missingInventory["committed"].Type==Newtonsoft.Json.Linq.JTokenType.Null&&(string)missingInventory["commitState"]=="unknown"&&(bool)missingInventory["checkpointRecoveryRequired"],"empty inventory failure must preserve unknown mutation and recovery facts");
   Check((bool)missingInventory["mutationStarted"]&&((string)missingInventory["readbackFailureReason"]).Contains("expected asset path inventory is empty")&&Active=="","terminal failure must explain the missing inventory and release the matching lock");break;
  case "completed_without_expected_assets_null":
   Put(j,"expectedAssetPaths",null);Call("OnImportCompleted","fixture");Exhaust(j);var nullInventory=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(nullInventory!=null&&!(bool)nullInventory["ok"]&&nullInventory["committed"].Type==Newtonsoft.Json.Linq.JTokenType.Null&&(string)nullInventory["commitState"]=="unknown"&&(bool)nullInventory["checkpointRecoveryRequired"],"legacy null inventory must not hang or claim success after mutation");
   Check((bool)nullInventory["mutationStarted"]&&((string)nullInventory["readbackFailureReason"]).Contains("expected asset path inventory is empty")&&Active=="","legacy null inventory must terminate with recovery and a clear reason");break;
  case "restored_without_expected_assets":
   Put(j,"expectedAssetPaths",null);Put(j,"status","readback_pending");Put(j,"importCompletedObserved",true);Put(j,"readbackAttemptedUtc",System.DateTime.UtcNow.AddMinutes(-1));Call("PersistJob",j);string emptyLegacyId=Id(j);Reload();j=Jobs[emptyLegacyId];Put(j,"restoredUtc",System.DateTime.UtcNow.AddSeconds(-3));Exhaust(j);var restoredEmpty=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(restoredEmpty!=null&&!(bool)restoredEmpty["ok"]&&restoredEmpty["committed"].Type==Newtonsoft.Json.Linq.JTokenType.Null&&(string)restoredEmpty["commitState"]=="unknown"&&(bool)restoredEmpty["checkpointRecoveryRequired"]&&Active=="","restored legacy job without inventory must terminate as unknown and release the lock");break;
  case "asset_database_entry_without_file":
   Put(j,"expectedAssetPaths",new System.Collections.Generic.List<string>{"Assets/Fixture.png"});System.IO.File.WriteAllBytes(System.IO.Path.Combine(UnityEditorProjectRoot(),"Assets","Fixture.png.meta"),new byte[]{1});UnityEditor.AssetDatabase.Available=true;Call("OnImportCompleted","fixture");Exhaust(j);var missingFile=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(missingFile!=null&&!(bool)missingFile["ok"]&&(string)missingFile["commitState"]=="unknown"&&(bool)missingFile["checkpointRecoveryRequired"],"AssetDatabase metadata without an imported file must not report success");
   Check(((string)missingFile["readbackFailureReason"]).Contains("fileExists=false")&&((string)missingFile["readbackFailureReason"]).Contains("Assets/Fixture.png")&&UnityEditor.AssetDatabase.Reads==0,"missing texture file must return the exact path and stop before accepting metadata alone");break;
  case "texture_file_exists":
   Put(j,"expectedAssetPaths",new System.Collections.Generic.List<string>{"Assets/Fixture.png"});WriteProjectAsset("Assets/Fixture.png");UnityEditor.AssetDatabase.Available=true;Call("OnImportCompleted","fixture");var textureReadback=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(textureReadback!=null&&(bool)textureReadback["ok"]&&((Newtonsoft.Json.Linq.JArray)textureReadback["expectedAssets"]).Count==1&&(string)textureReadback["expectedAssets"][0]["assetPath"]=="Assets/Fixture.png","an imported texture file plus AssetDatabase identity should complete");break;
  case "directory_without_asset_file":
   Put(j,"expectedAssetPaths",new System.Collections.Generic.List<string>{"Assets/Fixture.png"});UnityEditor.AssetDatabase.Available=true;var directoryPath=System.IO.Path.Combine(UnityEditorProjectRoot(),"Assets","Fixture.png");System.IO.Directory.CreateDirectory(directoryPath);Call("OnImportCompleted","fixture");Exhaust(j);var directoryReadback=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(directoryReadback!=null&&!(bool)directoryReadback["ok"]&&((string)directoryReadback["readbackFailureReason"]).Contains("fileExists=false"),"a directory at the expected asset path must not satisfy file readback");break;
  case "empty_inventory_rejected":
   Jobs.Clear();T.GetField("activeJobId",Flags).SetValue(null,"");UnityEditor.SessionState.Values.Clear();
   var packagePath=System.IO.Path.Combine(System.IO.Path.GetTempPath(),System.Guid.NewGuid().ToString("N")+".unitypackage");var packageBytes=new byte[]{1,2,3};System.IO.File.WriteAllBytes(packagePath,packageBytes);
   string hash;using(var sha=System.Security.Cryptography.SHA256.Create())hash=System.BitConverter.ToString(sha.ComputeHash(packageBytes)).Replace("-","").ToLowerInvariant();
   try { var request=new Newtonsoft.Json.Linq.JObject{{"unityPackagePath",packagePath},{"projectPath","D:/Fixture"},{"expectedSha256",hash},{"expectedSize",packageBytes.Length},{"expectedAssetPaths",new Newtonsoft.Json.Linq.JArray()}};var rejected=VRCForge.Editor.UnityPackageImporterTool.HandleCommand(request);Check((string)rejected=="unitypackage_identity_failed","empty inventory needs a clear preflight rejection"); }
   finally {System.IO.File.Delete(packagePath);}
   Check(UnityEditor.AssetDatabase.Imports==0&&Jobs.Count==0&&Active=="","empty inventory must be rejected before dispatch or job mutation");break;
  case "selected_unobserved":
   var absent=(Newtonsoft.Json.Linq.JObject)Call("BuildPendingPayload",j);Check(absent["selectedItemsEvidence"]!=null,"pending result must expose selection observation");Check(!(bool)absent["selectedItemsEvidence"]["observed"]&&absent["selectedItemsEvidence"]["items"].Type==Newtonsoft.Json.Linq.JTokenType.Null,"no callback is not an empty selection");break;
  case "selected_empty":
   Check(UnityEditor.AssetDatabase.onImportPackageItemsCompleted!=null,"production callback must subscribe");UnityEditor.AssetDatabase.onImportPackageItemsCompleted(new string[0]);
   var empty=(Newtonsoft.Json.Linq.JObject)Call("BuildPendingPayload",j);Check((bool)empty["selectedItemsEvidence"]["observed"]&&empty["selectedItemsEvidence"]["items"] is Newtonsoft.Json.Linq.JArray&&((Newtonsoft.Json.Linq.JArray)empty["selectedItemsEvidence"]["items"]).Count==0,"empty callback must stay observed empty");Check(Get(j,"result")==null&&UnityEditor.AssetDatabase.Reads==0&&Active==Id(j),"selection callback cannot complete or verify a job");break;
  case "selected_raw_reload":
   WriteFixtureAsset();Check(UnityEditor.AssetDatabase.onImportPackageItemsCompleted!=null,"production callback must subscribe");var rawItems=new string[]{"Assets/Exact Name.mat"," raw\\item ",null};UnityEditor.AssetDatabase.onImportPackageItemsCompleted(rawItems);rawItems[0]="mutated by caller";string selectedId=Id(j);Reload();j=Jobs[selectedId];
   var selected=(Newtonsoft.Json.Linq.JObject)Call("BuildPendingPayload",j);var ev=selected["selectedItemsEvidence"];Check((bool)ev["observed"]&&(string)ev["items"][0]=="Assets/Exact Name.mat"&&(string)ev["items"][1]==" raw\\item "&&ev["items"][2].Type==Newtonsoft.Json.Linq.JTokenType.Null,"copied raw items must survive reload without normalization");Check((string)ev["meaning"]=="selected_items_not_written_assets"&&(string)ev["attribution"]=="active_started_job_without_callback_identity"&&ev["observedUtc"].Type==Newtonsoft.Json.Linq.JTokenType.String,"public evidence must state correlation limits and time");
   UnityEditor.AssetDatabase.Available=true;Call("OnImportCompleted","fixture");var selectedResult=(Newtonsoft.Json.Linq.JObject)Get(j,"result");Check(Newtonsoft.Json.Linq.JToken.DeepEquals(ev,selectedResult["selectedItemsEvidence"]),"terminal result must retain selection evidence");var persistedSelection=(Newtonsoft.Json.Linq.JObject)Call("LoadPersistedJob",Id(j));Check(Newtonsoft.Json.Linq.JToken.DeepEquals(Newtonsoft.Json.Linq.JToken.Parse(ev.ToString()),persistedSelection["result"]["selectedItemsEvidence"]),"terminal evidence must persist");break;
  case "selected_not_started_or_terminal":
   Check(UnityEditor.AssetDatabase.onImportPackageItemsCompleted!=null,"production callback must subscribe");Put(j,"startedForThisJob",false);UnityEditor.AssetDatabase.onImportPackageItemsCompleted(new[]{"unrelated"});Put(j,"startedForThisJob",true);var beforeSelection=(Newtonsoft.Json.Linq.JObject)Call("BuildPendingPayload",j);Check(!(bool)beforeSelection["selectedItemsEvidence"]["observed"],"not-started job must ignore callback");Call("OnImportFailed","fixture","failed");var terminalBefore=((Newtonsoft.Json.Linq.JObject)Get(j,"result")).ToString();UnityEditor.AssetDatabase.onImportPackageItemsCompleted(new[]{"late"});Check(terminalBefore==((Newtonsoft.Json.Linq.JObject)Get(j,"result")).ToString()&&Active=="","late unbound callback must not rewrite terminal result or lock");break;
  case "running_reload":
   string runningId=Id(j);Reload();j=Jobs[runningId];Put(j,"restoredUtc",System.DateTime.UtcNow.AddMinutes(-1));Exhaust(j);Check(Get(j,"result")==null&&Active==runningId,"running reload missing asset is not completed evidence");break;
  case "event_failure":
   Call("OnImportFailed","fixture","failure");Check(Get(j,"result")!=null&&Active=="","Unity failed event remains terminal");break;
  case "event_cancel":
   Call("OnImportCancelled","fixture");Check(Get(j,"result")!=null&&Active=="","Unity cancelled event remains terminal");break;
 }Check(UnityEditor.AssetDatabase.Imports==0,"poll/reload must not replay ImportPackage");System.Console.WriteLine(args[0]+":pass");return 0;}catch(System.Exception e){System.Console.Error.WriteLine(e);return 1;}}
 static string UnityEditorProjectRoot()=>VRCForge.Editor.CheckpointPrepareTool.ProjectRoot();
}
'''


@pytest.fixture(scope="module")
def importer_probe(tmp_path_factory):
    output = tmp_path_factory.mktemp("import-readback-lifecycle")
    project_root = output / "UnityProject"
    (project_root / "Assets").mkdir(parents=True)
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not references:
        pytest.skip("Local .NET SDK required; no Unity is launched")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source_path = Path(os.environ.get("VRCFORGE_IMPORTER_TEST_SOURCE", str(SOURCE)))
    source = source_path.read_text(encoding="utf-8")
    first_class = source.split("    [InitializeOnLoad]", 2)
    assert len(first_class) == 3
    production = first_class[0] + "    [InitializeOnLoad]" + first_class[1] + "}\n"
    cs = output / "Probe.cs"
    cs.write_text(production + STUBS + RUNNER, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:8.0", f"-out:{dll}", *[f"-r:{p}" for p in references[-1].glob("*.dll")], f"-r:{newtonsoft}", str(cs)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, output / newtonsoft.name)
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    os.environ["VRCFORGE_TEST_PROJECT_ROOT"] = str(project_root)
    return base / "dotnet.exe", dll


@pytest.mark.parametrize("case", ["permanent", "transient", "running", "busy", "reload_budget", "legacy_completed", "legacy_ambiguous", "wrong_job", "completed_before_refresh", "completed_no_global_save", "completed_without_expected_assets", "completed_without_expected_assets_null", "restored_without_expected_assets", "asset_database_entry_without_file", "texture_file_exists", "directory_without_asset_file", "empty_inventory_rejected", "running_reload", "event_failure", "event_cancel", "selected_unobserved", "selected_empty", "selected_raw_reload", "selected_not_started_or_terminal"])
def test_import_readback_lifecycle(importer_probe, case):
    dotnet, dll = importer_probe
    result = subprocess.run([str(dotnet), str(dll), case], capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr
