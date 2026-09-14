"""Execute the production copy batch handler with a narrow Unity seam, not Unity."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT=Path(__file__).resolve().parents[1]


def test_actual_copy_batch_handler_faults(tmp_path):
    base=Path(os.environ.get("DOTNET_ROOT", str(Path.home()/"AppData/Local/Microsoft/dotnet")))
    compilers=sorted((base/"sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    dotnet=base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not dotnet.is_file(): dotnet=Path(shutil.which("dotnet") or "")
    if not dotnet.is_file() or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityProjectAssetCopyBatch.cs").read_text(encoding="utf-8")
    seam=r'''
namespace UnityEngine {class Object {internal string name="name",path="";internal int hideFlags;internal bool main;} class Material:Object {}}
namespace UnityEditor.AssetImporters {sealed class ImportLog:UnityEngine.Object {}}
namespace UnityEditor.Animations {class AnimatorState:UnityEngine.Object {}}
namespace UserAssets {class ImportLog:UnityEngine.Object {}}
namespace UnityEditor {
 [Flags] enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
 static class AssetDatabase {
  internal static int Count, FailAt, Saves, GlobalSaves;internal static bool SaveFails, UnknownOnFailure, UnrelatedDirty;internal static string Drift="",LayoutMode="",UnrelatedPath;
  internal static UnityEngine.Object LoadMainAssetAtPath(string p)=>new UnityEngine.Material{main=true,path=p};
  internal static UnityEngine.Object[] LoadAllAssetsAtPath(string p){
   var all=new List<UnityEngine.Object>{new UnityEngine.Material{main=true,hideFlags=p=="dest2"&&Drift=="layout"?1:0}};
   if(p=="source"&&LayoutMode=="importlog")all.Add(new UnityEditor.AssetImporters.ImportLog{name="Import Logs",hideFlags=1});
   if(p=="source"&&LayoutMode=="missing-real")all.Add(new UnityEditor.Animations.AnimatorState{name="State",hideFlags=1});
   if(p=="source"&&LayoutMode=="same-name-user")all.Add(new UserAssets.ImportLog{name="Import Logs",hideFlags=1});
   if(LayoutMode=="multisub"){all.Add(new UnityEditor.Animations.AnimatorState{name="A"});all.Add(new UnityEditor.Animations.AnimatorState{name="B"});if(p!="source")all.Reverse();}
   return all.ToArray();
  }
  internal static bool IsMainAsset(UnityEngine.Object obj)=>obj.main;
  internal static bool CopyAsset(string s,string d){Count++;if(Count==FailAt){if(UnknownOnFailure)VRCForge.Editor.SceneObjectCopyCore.Files[d]=new VRCForge.Editor.StableAssetEvidence("unknown");return false;}VRCForge.Editor.SceneObjectCopyCore.Files[d]=new VRCForge.Editor.StableAssetEvidence(d);return true;}
  internal static void SaveAssets(){GlobalSaves++;Saves++;if(UnrelatedDirty){System.IO.File.WriteAllText(UnrelatedPath,"unsaved user edit");UnrelatedDirty=false;}if(SaveFails)throw new InvalidOperationException("save failed");}
  internal static void SaveAssetIfDirty(UnityEngine.Object obj){if(obj==null||!VRCForge.Editor.SceneObjectCopyCore.Files.ContainsKey(obj.path))throw new InvalidOperationException("invalid save target");Saves++;if(SaveFails)throw new InvalidOperationException("save failed");}
  internal static void Refresh(ImportAssetOptions o){}
  internal static string CreateFolder(string p,string n)=>throw new InvalidOperationException("Folder creation outside this fixture");
  internal static string GUIDToAssetPath(string guid)=>guid;
  internal static void ImportAsset(string p,ImportAssetOptions o){if(p!="dest2")return;var e=VRCForge.Editor.SceneObjectCopyCore.Files[p];if(Drift=="file")e.File.Digest="changed";if(Drift=="meta")e.Meta.Digest="changed-meta";if(Drift=="guid")e.Guid="source";}
  internal static Type GetMainAssetTypeAtPath(string p)=>p=="dest2"&&Drift=="type"?typeof(int):typeof(string);
 }
}
namespace VRCForge.Core.MCP {static class VRCForgeToolResult {
 internal static object Completed(string s,object p)=>JObject.FromObject(p);
 internal static object FailedWithCode(string c,string s,object p)=>JObject.FromObject(p);
}}
namespace VRCForge.Editor {
 class StagingFolderLease {internal string RootPath,FolderPath,FolderGuid,FolderIdentity;}
 class ProjectAssetCopyException:Exception {internal ProjectAssetCopyException(string s):base(s){}}
 class StableFileEvidence {internal string Digest="bytes",Identity="identity";internal uint LinkCount=1;internal ulong Length=5;}
 class StableAssetEvidence {internal string Guid;internal StableFileEvidence File=new StableFileEvidence(),Meta=new StableFileEvidence();internal StableAssetEvidence(string g){Guid=g;}}
 static class SceneObjectCopyCore {
  internal static Dictionary<string,StableAssetEvidence> Files=new Dictionary<string,StableAssetEvidence>();
  internal static bool MatchesCurrentProject(string p)=>p=="project";
  internal static bool AssetOrMetaExists(string p)=>Files.ContainsKey(p);
  internal static bool DeleteOwnedAsset(string p,StableAssetEvidence e){if(!StableAssetEvidenceMatches(Files[p],e,true))return false;Files.Remove(p);return true;}
  // PRODUCTION_EVIDENCE_MATCHERS
  internal static void VerifyFolderIdentity(string p,string g,string i,string label){}
  internal static string ReadDirectoryIdentity(string p,string label)=>"identity";
 }
 static class DuplicateProjectAssetTool {
  internal const string ResultSchema="schema",Operation="copy",GeneratedRoot="Assets/VRCForgeGenerated";
  internal class ProjectAssetCopySnapshot {
   internal string SourcePath,DestinationPath,SourceMainAssetType="System.String",SourceObjectLayoutDigest="layout",PreviewDigest;
   internal string[] MissingFolders=new string[0];internal string ParentFolderPath="parent",ParentFolderGuid="guid",ParentFolderIdentity="identity";
   internal string ExistingAncestorPath="parent",ExistingAncestorGuid="guid",ExistingAncestorIdentity="identity";
   internal StableAssetEvidence SourceEvidence=new StableAssetEvidence("source");
   internal object ToPreviewPayload()=>new {previewDigest=PreviewDigest};
  }
  internal static string NormalizeSourcePath(string p)=>p;
  internal static string NormalizeDestinationPath(string p)=>p;
  internal static ProjectAssetCopySnapshot BuildSnapshot(string s,string d){if(d=="invalid"||SceneObjectCopyCore.AssetOrMetaExists(d))throw new InvalidOperationException("invalid target");return new ProjectAssetCopySnapshot{SourcePath=s,DestinationPath=d,SourceObjectLayoutDigest=ComputeObjectLayoutDigest(s),PreviewDigest=d.PadRight(64,'0'),MissingFolders=d=="missing-parent"?new[]{"parent"}:new string[0]};}
  internal static void VerifySnapshotCurrent(ProjectAssetCopySnapshot s){BuildSnapshot(s.SourcePath,s.DestinationPath);}
  internal static StableAssetEvidence ReadCreatedEvidenceWithRetry(string p){var e=SceneObjectCopyCore.Files[p];return new StableAssetEvidence(e.Guid){File=new StableFileEvidence{Digest=e.File.Digest,Identity=e.File.Identity,LinkCount=e.File.LinkCount,Length=e.File.Length},Meta=new StableFileEvidence{Digest=e.Meta.Digest,Identity=e.Meta.Identity,LinkCount=e.Meta.LinkCount,Length=e.Meta.Length}};}
  // PRODUCTION_LAYOUT_DIGEST
  internal static void VerifySourceUnchanged(ProjectAssetCopySnapshot s){}
  internal static object SourcePayload(ProjectAssetCopySnapshot s)=>new {assetPath=s.SourcePath};
  private class CreatedFolder {internal StagingFolderLease Lease;internal string MetaDigest;}
  private static string NormalizeHex(string value,int length,string label)=>value;
  private static string ReadFolderMetaDigest(string p)=>"meta";
  private static void VerifyCreatedFolder(CreatedFolder f){}
  private static bool CleanupFailedApply(ProjectAssetCopySnapshot s,StableAssetEvidence e,List<CreatedFolder> folders,bool unknown)=>e!=null&&SceneObjectCopyCore.DeleteOwnedAsset(s.DestinationPath,e);
  private static object Failure(Exception e,bool started,bool cleanup,string phase)=>new JObject{["verified"]=false,["mutationStarted"]=started,["cleanupRequired"]=cleanup,["phase"]=phase};
  internal static object RunSingle(ProjectAssetCopySnapshot snapshot)=>Apply(snapshot);
  // PRODUCTION_SINGLE_APPLY
 }
 public class Runner {
  static int failures;
  static void Check(bool b,string name){Console.WriteLine((b?"PASS ":"FAIL ")+name);if(!b)failures++;}
  static void Reset(){SceneObjectCopyCore.Files.Clear();UnityEditor.AssetDatabase.Count=0;UnityEditor.AssetDatabase.FailAt=0;UnityEditor.AssetDatabase.Saves=0;UnityEditor.AssetDatabase.GlobalSaves=0;UnityEditor.AssetDatabase.UnrelatedDirty=true;System.IO.File.WriteAllText(UnityEditor.AssetDatabase.UnrelatedPath,"original disk material");UnityEditor.AssetDatabase.SaveFails=false;UnityEditor.AssetDatabase.UnknownOnFailure=false;UnityEditor.AssetDatabase.Drift="";UnityEditor.AssetDatabase.LayoutMode="";}
  static JObject Request(string last="dest2")=>new JObject{["copies"]=new JArray(new JObject{["sourceAssetPath"]="source",["destinationAssetPath"]="dest1"},new JObject{["sourceAssetPath"]="source",["destinationAssetPath"]=last}),["preview"]=true};
  static JObject Seal(JObject q){var p=(JObject)UnityProjectAssetCopyBatch.HandleCommand(q);q["preview"]=false;q["expectedProjectPath"]="project";q["expectedPreviewDigest"]=p["previewDigest"];q["expectedCopies"]=p["copies"];return q;}
  static JObject Run(JObject q)=>(JObject)UnityProjectAssetCopyBatch.HandleCommand(q);
  public static int Main(string[] args){
   UnityEditor.AssetDatabase.UnrelatedPath=System.IO.Path.Combine(args[0],"unrelated.mat");
   Reset();var result=Run(Seal(Request()));Check(result["verified"].Value<bool>()&&SceneObjectCopyCore.Files.Count==2,"successful batch");
   Check(UnityEditor.AssetDatabase.GlobalSaves==0&&UnityEditor.AssetDatabase.Saves==2&&UnityEditor.AssetDatabase.UnrelatedDirty&&System.IO.File.ReadAllText(UnityEditor.AssetDatabase.UnrelatedPath)=="original disk material","batch saves only copied targets and preserves unrelated dirty material");
   Reset();result=(JObject)DuplicateProjectAssetTool.RunSingle(DuplicateProjectAssetTool.BuildSnapshot("source","dest1"));Check(result["verified"].Value<bool>()&&SceneObjectCopyCore.Files.Count==1,"successful single copy production apply");
   Check(UnityEditor.AssetDatabase.GlobalSaves==0&&UnityEditor.AssetDatabase.Saves==1&&UnityEditor.AssetDatabase.UnrelatedDirty&&System.IO.File.ReadAllText(UnityEditor.AssetDatabase.UnrelatedPath)=="original disk material","single saves only copied target and preserves unrelated dirty material");
   Reset();result=Run(Request("invalid"));Check(!result["mutationStarted"].Value<bool>()&&UnityEditor.AssetDatabase.Count==0,"invalid final preflight zero copy");
   Reset();result=Run(Request("missing-parent"));Check(!result["mutationStarted"].Value<bool>(),"missing parent rejected");
   Reset();var q=Seal(Request());q["expectedPreviewDigest"]="stale";result=Run(q);Check(!result["mutationStarted"].Value<bool>(),"stale preview zero mutation");
   Reset();q=Seal(Request());SceneObjectCopyCore.Files["dest2"]=new StableAssetEvidence("user");result=Run(q);Check(!result["mutationStarted"].Value<bool>()&&SceneObjectCopyCore.Files.Count==1,"occupied final target preserved");
   Reset();q=Seal(Request());UnityEditor.AssetDatabase.FailAt=2;result=Run(q);Check(result["restored"].Value<bool>()&&SceneObjectCopyCore.Files.Count==0,"second native copy fails restores first");
   Reset();q=Seal(Request());UnityEditor.AssetDatabase.SaveFails=true;result=Run(q);Check(result["restored"].Value<bool>()&&SceneObjectCopyCore.Files.Count==0,"save failure restores all copies");
   Reset();q=Seal(Request());UnityEditor.AssetDatabase.FailAt=2;UnityEditor.AssetDatabase.UnknownOnFailure=true;result=Run(q);Check(result["checkpointRecoveryRequired"].Value<bool>()&&SceneObjectCopyCore.Files.Count==1&&SceneObjectCopyCore.Files.ContainsKey("dest2"),"unknown partial copy preserved prior owned cleaned");
   Reset();var evidence=new StableAssetEvidence("own");SceneObjectCopyCore.Files["first"]=evidence;SceneObjectCopyCore.Files["last"]=new StableAssetEvidence("user");Check(!UnityProjectAssetCopyBatch.CleanupOwned(new[]{"first","last"},new Dictionary<string,StableAssetEvidence>{{"first",evidence},{"last",new StableAssetEvidence("old")}})&&SceneObjectCopyCore.Files.Count==1,"changed final does not stop prior cleanup");
   foreach(var drift in new[]{"layout","type","file","meta","guid"}){
    Reset();q=Seal(Request());UnityEditor.AssetDatabase.Drift=drift;result=Run(q);
    var d=result["failureDetails"] as JObject;
    Check(d!=null&&d["rowIndex"].Value<int>()==1&&d["sourceAssetPath"].Value<string>()=="source"&&d["destinationAssetPath"].Value<string>()=="dest2"&&d["failedPredicates"] is JArray&&d["expected"] is JObject&&d["actual"] is JObject,"diagnostic row and evidence "+drift);
    var predicate=drift=="layout"?"objectLayoutDigest":drift=="type"?"mainAssetType":drift=="file"?"fileDigest":drift=="meta"?"metaDigest":"guid";
    Check(d!=null&&d["failedPredicates"].Values<string>().Contains(predicate),"specific failed predicate "+drift);
    if(drift=="file")Check(d!=null&&d["expected"]["fileDigest"].Value<string>()=="bytes"&&d["actual"]["fileDigest"].Value<string>()=="changed","sealed versus actual digest");
    var changed=drift=="file"||drift=="meta"||drift=="guid";
    Check(result["restored"].Value<bool>()==!changed&&result["checkpointRecoveryRequired"].Value<bool>()==changed&&result["attemptedPaths"].Count()==2&&SceneObjectCopyCore.Files.Count==(changed?1:0),"ownership cleanup unchanged "+drift);
   }
   Reset();UnityEditor.AssetDatabase.LayoutMode="importlog";result=Run(Seal(Request()));Check(result["verified"].Value<bool>()&&SceneObjectCopyCore.Files.Count==2,"importer diagnostic absence does not reject authoring copy");
   foreach(var mode in new[]{"missing-real","same-name-user"}){Reset();UnityEditor.AssetDatabase.LayoutMode=mode;result=Run(Seal(Request()));Check(!result["verified"].Value<bool>()&&result["restored"].Value<bool>()&&SceneObjectCopyCore.Files.Count==0,"missing authoring object rejected and zero artifacts "+mode);}
   Reset();UnityEditor.AssetDatabase.LayoutMode="multisub";result=Run(Seal(Request()));Check(result["verified"].Value<bool>()&&SceneObjectCopyCore.Files.Count==2,"multiple real subassets retain order-independent layout");
   return failures==0?0:1;
  }
 }
}
'''
    core=(ROOT/"Assets/VRCForge/Editor/SceneObjectCopyCore.cs").read_text(encoding="utf-8")
    seam=seam.replace("// PRODUCTION_EVIDENCE_MATCHERS",method(core,"internal static bool StableAssetEvidenceMatches(")+method(core,"private static bool StableFileEvidenceMatches("))
    copy=(ROOT/"Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs").read_text(encoding="utf-8")
    layout=method(copy,"internal static string ComputeObjectLayoutDigest(")
    if "internal static bool IsCopyLayoutObject(" in copy:
        layout+=method(copy,"internal static bool IsCopyLayoutObject(")
    seam=seam.replace("// PRODUCTION_LAYOUT_DIGEST",layout)
    seam=seam.replace("// PRODUCTION_SINGLE_APPLY",method(copy,"private static object Apply("))
    program="using System.Globalization;\nusing System.IO;\n"+source+seam
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll),str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==32
