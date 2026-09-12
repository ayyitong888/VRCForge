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
 public static class VRCForgeToolResult {
  public static object Waiting(string m,double d,object o)=>o;
  public static object Completed(string m,object o)=>o;
  public static object Failed(string m)=>m;
 }
}
namespace VRCForge.Editor {
 public static class CheckpointPrepareTool {
  public static string ProjectRoot()=>"D:/Fixture";
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
 static void Reset(){Jobs.Clear();T.GetField("activeJobId",Flags).SetValue(null,"");UnityEditor.SessionState.Values.Clear();UnityEditor.AssetDatabase.Available=false;UnityEditor.AssetDatabase.Reads=0;UnityEditor.AssetDatabase.RefreshAction=null;UnityEditor.EditorApplication.isCompiling=false;UnityEditor.EditorApplication.isUpdating=false;}
 static object Job(){
  var j=System.Activator.CreateInstance(T.GetNestedType("ImportJob",System.Reflection.BindingFlags.NonPublic),true);
  Put(j,"jobId",System.Guid.NewGuid().ToString("N"));Put(j,"projectPath","D:/Fixture");Put(j,"mutationStarted",true);Put(j,"startedForThisJob",true);Put(j,"importEventPackageName","fixture");Put(j,"expectedEventPackageName","fixture");Put(j,"expectedAssetPaths",new System.Collections.Generic.List<string>{"Assets/Fixture.asset"});Put(j,"status","running");
  Jobs[Id(j)]=j;T.GetField("activeJobId",Flags).SetValue(null,Id(j));UnityEditor.SessionState.SetString("VRCForge.UnityPackageImport.ActiveJob",Id(j));Call("PersistJob",j);return j;
 }
 static Newtonsoft.Json.Linq.JObject Poll(object j){Put(j,"readbackAttemptedUtc",System.DateTime.UtcNow.AddSeconds(-3));return(Newtonsoft.Json.Linq.JObject)Call("PollJob",Id(j));}
 static void Reload(){Jobs.Clear();T.GetField("activeJobId",Flags).SetValue(null,"");Call("RestorePersistedActiveJob");}
 static void Exhaust(object j){for(int i=0;i<5&&Get(j,"result")==null;i++)Poll(j);}
 static int Main(string[] args){try{Reset();var j=Job();switch(args[0]){
  case "permanent":
   Call("OnImportCompleted","fixture");Exhaust(j);var r=(Newtonsoft.Json.Linq.JObject)Get(j,"result");
   Check(r!=null,"completed import with permanent missing asset must terminate");Check(!(bool)r["ok"]&&!(bool)r["pending"],"failure must be terminal");Check((string)r["commitState"]=="unknown"&&(bool)r["checkpointRecoveryRequired"]&&!(bool)r["retryable"],"failure must preserve unknown recovery");Check(r["committed"].Type==Newtonsoft.Json.Linq.JTokenType.Null,"commit must not be fabricated");Check(Active=="","matching active lock must release");Check(UnityEditor.AssetDatabase.Reads==3,"exactly three failed idle reads");break;
  case "transient":
   Call("OnImportCompleted","fixture");UnityEditor.AssetDatabase.Available=true;var success=Poll(j);Check((bool)success["ok"]&&!(bool)success["pending"]&&Active=="","transient asset must complete");break;
  case "running":
   Exhaust(j);Check(Get(j,"result")==null&&Active==Id(j)&&UnityEditor.AssetDatabase.Reads==0,"ordinary running job cannot expire or read prematurely");break;
  case "busy":
   Call("OnImportCompleted","fixture");int before=UnityEditor.AssetDatabase.Reads;UnityEditor.EditorApplication.isCompiling=true;Exhaust(j);Check(Get(j,"result")==null&&UnityEditor.AssetDatabase.Reads==before,"compilation consumes no readback budget");UnityEditor.EditorApplication.isCompiling=false;UnityEditor.EditorApplication.isUpdating=true;Exhaust(j);Check(UnityEditor.AssetDatabase.Reads==before,"updating consumes no budget");UnityEditor.EditorApplication.isUpdating=false;Exhaust(j);Check(Get(j,"result")!=null,"idle failure must eventually terminate");break;
  case "reload_budget":
   Call("OnImportCompleted","fixture");Poll(j);string id=Id(j);Reload();j=Jobs[id];Exhaust(j);Check(Get(j,"result")!=null&&UnityEditor.AssetDatabase.Reads==3,"reload must preserve completed evidence and two consumed failures");break;
  case "legacy_completed":
   Put(j,"status","readback_pending");Put(j,"readbackFailureCode","unitypackage_async_readback_pending");Put(j,"readbackFailurePath","Assets/Fixture.asset");Put(j,"readbackFailureReason","missing");Put(j,"readbackAttemptedUtc",System.DateTime.UtcNow.AddMinutes(-2));Call("PersistJob",j);string oldId=Id(j);Reload();j=Jobs[oldId];Exhaust(j);Check(Get(j,"result")!=null,"strict legacy completed state must recover bounded readback");break;
  case "legacy_ambiguous":
   Put(j,"status","readback_pending");Put(j,"restoredAfterDomainReload",true);Put(j,"readbackFailureCode","unitypackage_restored_readback_pending");Call("PersistJob",j);string unknownId=Id(j);Reload();j=Jobs[unknownId];Put(j,"restoredUtc",System.DateTime.UtcNow.AddMinutes(-1));Exhaust(j);Check(Get(j,"result")==null&&Active==unknownId,"ambiguous legacy reload must not be terminalized");break;
  case "wrong_job":
   Call("OnImportCompleted","unrelated");Check(UnityEditor.AssetDatabase.Reads==0&&Get(j,"result")==null&&Active==Id(j),"wrong event must not advance job");Call("OnImportCompleted","fixture");var other=Job();Exhaust(j);Check(Get(j,"result")!=null&&Active==Id(other),"terminal old job must not release newer lock");break;
  case "completed_before_refresh":
   bool persisted=false;UnityEditor.AssetDatabase.RefreshAction=()=>{var p=Newtonsoft.Json.Linq.JObject.Parse(UnityEditor.SessionState.GetString("VRCForge.UnityPackageImport.Job."+Id(j),"{}"));persisted=p["importCompletedObserved"]?.Value<bool>()==true;};Call("OnImportCompleted","fixture");Check(persisted,"completed event fact must persist before refresh/reload");break;
  case "completed_no_global_save":
   UnityEditor.AssetDatabase.Available=true;Call("OnImportCompleted","fixture");
   Check(UnityEditor.AssetDatabase.Saves==0,"completed import must not globally save loaded assets");
   Check(UnityEditor.AssetDatabase.Refreshes==1&&UnityEditor.AssetDatabase.RefreshOptions==(UnityEditor.ImportAssetOptions.ForceSynchronousImport|UnityEditor.ImportAssetOptions.ForceUpdate),"completed import must retain forced synchronous refresh");
   var complete=(Newtonsoft.Json.Linq.JObject)Get(j,"result");Check(complete!=null&&(bool)complete["ok"]&&Active==""&&UnityEditor.AssetDatabase.Reads==1,"completed import must retain expected asset readback and release matching lock");break;
  case "selected_unobserved":
   var absent=(Newtonsoft.Json.Linq.JObject)Call("BuildPendingPayload",j);Check(absent["selectedItemsEvidence"]!=null,"pending result must expose selection observation");Check(!(bool)absent["selectedItemsEvidence"]["observed"]&&absent["selectedItemsEvidence"]["items"].Type==Newtonsoft.Json.Linq.JTokenType.Null,"no callback is not an empty selection");break;
  case "selected_empty":
   Check(UnityEditor.AssetDatabase.onImportPackageItemsCompleted!=null,"production callback must subscribe");UnityEditor.AssetDatabase.onImportPackageItemsCompleted(new string[0]);
   var empty=(Newtonsoft.Json.Linq.JObject)Call("BuildPendingPayload",j);Check((bool)empty["selectedItemsEvidence"]["observed"]&&empty["selectedItemsEvidence"]["items"] is Newtonsoft.Json.Linq.JArray&&((Newtonsoft.Json.Linq.JArray)empty["selectedItemsEvidence"]["items"]).Count==0,"empty callback must stay observed empty");Check(Get(j,"result")==null&&UnityEditor.AssetDatabase.Reads==0&&Active==Id(j),"selection callback cannot complete or verify a job");break;
  case "selected_raw_reload":
   Check(UnityEditor.AssetDatabase.onImportPackageItemsCompleted!=null,"production callback must subscribe");var rawItems=new string[]{"Assets/Exact Name.mat"," raw\\item ",null};UnityEditor.AssetDatabase.onImportPackageItemsCompleted(rawItems);rawItems[0]="mutated by caller";string selectedId=Id(j);Reload();j=Jobs[selectedId];
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
}
'''


@pytest.fixture(scope="module")
def importer_probe(tmp_path_factory):
    output = tmp_path_factory.mktemp("import-readback-lifecycle")
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
    return base / "dotnet.exe", dll


@pytest.mark.parametrize("case", ["permanent", "transient", "running", "busy", "reload_budget", "legacy_completed", "legacy_ambiguous", "wrong_job", "completed_before_refresh", "completed_no_global_save", "running_reload", "event_failure", "event_cancel", "selected_unobserved", "selected_empty", "selected_raw_reload", "selected_not_started_or_terminal"])
def test_import_readback_lifecycle(importer_probe, case):
    dotnet, dll = importer_probe
    result = subprocess.run([str(dotnet), str(dll), case], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
