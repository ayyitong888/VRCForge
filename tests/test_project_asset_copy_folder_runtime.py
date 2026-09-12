"""Execute production rollback methods with local files and a narrow AssetDatabase test seam.
This does not substitute for Unity compilation or live failure-injection acceptance.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_generated_animation_copy_runtime_contract import extract

ROOT = Path(__file__).resolve().parents[1]

def test_actual_folder_recovery_methods(tmp_path):
 dotnet=shutil.which("dotnet")
 base=Path(os.environ.get("ProgramFiles", "C:/Program Files"))/"dotnet"
 compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
 refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
 if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK required")
 source=(ROOT/"Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs").read_text(encoding="utf-8-sig")
 core=(ROOT/"Assets/VRCForge/Editor/SceneObjectCopyCore.cs").read_text(encoding="utf-8-sig")
 methods="\n".join(extract(source,s) for s in ["private static bool CleanupFailedApply", "private sealed class CreatedFolder", "private static string ReadFolderMetaDigest", "private static void VerifyCreatedFolder"])
 cleanup=extract(core,"internal static bool DeleteOwnedStagingFolder")
 seam=r'''
using System; using System.IO; using System.Linq; using System.Collections.Generic; using System.Security.Cryptography; using System.Globalization;
class StagingFolderLease { internal string FolderPath; internal string FolderGuid; internal string FolderIdentity; }
class StableAssetEvidence { internal string Bytes; }
class ProjectAssetCopySnapshot { internal string DestinationPath; }
class ProjectAssetCopyException : Exception { internal ProjectAssetCopyException(string s):base(s){} }
enum AssetPathToGUIDOptions { OnlyExistingAssets }
enum ImportAssetOptions { ForceSynchronousImport }
static class AssetDatabase {
 internal static string AssetPathToGUID(string p, AssetPathToGUIDOptions o) { return Directory.Exists(p)?"guid":""; }
 internal static bool DeleteAsset(string p) { Directory.Delete(p, false); File.Delete(p+".meta"); return true; }
 internal static void SaveAssets(){} internal static void Refresh(ImportAssetOptions o){}
}
static class SceneObjectCopyCore {
 internal static bool AssetOrMetaExists(string p) { return File.Exists(p)||Directory.Exists(p)||File.Exists(p+".meta"); }
 internal static bool DeleteOwnedAsset(string p, StableAssetEvidence e) { if(File.ReadAllText(p)!=e.Bytes)return false; File.Delete(p); return true; }
 internal static string ToAbsoluteAssetPath(string p) { return Path.GetFullPath(p); }
 internal static bool SafeSiblingFileExists(string p,string s) { return File.Exists(p+s); }
 internal static void RejectProjectPathReparsePoints(string p) { if((File.GetAttributes(p)&FileAttributes.ReparsePoint)!=0) throw new Exception("link"); }
 internal static void VerifyOwnedStagingFolder(StagingFolderLease l) { if(!Directory.Exists(l.FolderPath)||l.FolderIdentity!="identity"||l.FolderGuid!="guid") throw new Exception("identity drift"); }
'''
 runner=r'''
 static int failures;
 static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok) failures++;}
 static CreatedFolder Create(string p) { Directory.CreateDirectory(p); File.WriteAllText(p+".meta","original metadata"); return new CreatedFolder { Lease=new StagingFolderLease{FolderPath=p,FolderGuid="guid",FolderIdentity="identity"},MetaDigest=ReadFolderMetaDigest(p)}; }
 static int Main(string[] args) {
  string root=args[0]; Directory.CreateDirectory(root);
  var outer=Create(Path.Combine(root,"partial")); var absent=new ProjectAssetCopySnapshot{DestinationPath=Path.Combine(outer.Lease.FolderPath,"copy.anim")};
  Check(CleanupFailedApply(absent,null,new List<CreatedFolder>{outer},false)&&!Directory.Exists(outer.Lease.FolderPath),"partial creation failure cleans owned empty ancestor");
  outer=Create(Path.Combine(root,"savefail")); var inner=Create(Path.Combine(outer.Lease.FolderPath,"Materials")); var snap=new ProjectAssetCopySnapshot{DestinationPath=Path.Combine(inner.Lease.FolderPath,"copy.anim")}; File.WriteAllText(snap.DestinationPath,"owned");
  Check(CleanupFailedApply(snap,new StableAssetEvidence{Bytes="owned"},new List<CreatedFolder>{outer,inner},false)&&!Directory.Exists(outer.Lease.FolderPath),"save failure removes owned asset then folders in reverse");
  outer=Create(Path.Combine(root,"userfile")); File.WriteAllText(Path.Combine(outer.Lease.FolderPath,"user.txt"),"keep"); snap=new ProjectAssetCopySnapshot{DestinationPath=Path.Combine(outer.Lease.FolderPath,"copy.anim")};
  Check(!CleanupFailedApply(snap,null,new List<CreatedFolder>{outer},false)&&File.Exists(Path.Combine(outer.Lease.FolderPath,"user.txt")),"user file preserves folder and reports cleanup required");
  outer=Create(Path.Combine(root,"metadata")); File.WriteAllText(outer.Lease.FolderPath+".meta","user metadata"); snap=new ProjectAssetCopySnapshot{DestinationPath=Path.Combine(outer.Lease.FolderPath,"copy.anim")};
  Check(!CleanupFailedApply(snap,null,new List<CreatedFolder>{outer},false)&&Directory.Exists(outer.Lease.FolderPath),"changed metadata preserved");
  outer=Create(Path.Combine(root,"identity")); outer.Lease.FolderIdentity="changed"; snap=new ProjectAssetCopySnapshot{DestinationPath=Path.Combine(outer.Lease.FolderPath,"copy.anim")};
  Check(!CleanupFailedApply(snap,null,new List<CreatedFolder>{outer},false)&&Directory.Exists(outer.Lease.FolderPath),"changed folder identity preserved");
  outer=Create(Path.Combine(root,"unknown")); snap=new ProjectAssetCopySnapshot{DestinationPath=Path.Combine(outer.Lease.FolderPath,"copy.anim")};
  Check(!CleanupFailedApply(snap,null,new List<CreatedFolder>{outer},true)&&Directory.Exists(outer.Lease.FolderPath),"unverified creation preserved");
  File.WriteAllText(snap.DestinationPath,"user modified");
  Check(!CleanupFailedApply(snap,new StableAssetEvidence{Bytes="owned"},new List<CreatedFolder>{outer},false)&&File.ReadAllText(snap.DestinationPath)=="user modified","changed copied asset preserved");
  return failures==0?0:1;
 }
'''
 program=seam+cleanup+"} class Probe {"+methods+runner+"}"
 (tmp_path/"Probe.cs").write_text(program,encoding="utf-8")
 dll=tmp_path/"Probe.dll"
 command=[dotnet,str(compilers[-1]),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[str(tmp_path/"Probe.cs")]
 result=subprocess.run(command,capture_output=True,text=True,timeout=60)
 assert result.returncode==0,result.stdout+result.stderr
 (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
 result=subprocess.run([dotnet,str(dll),str(tmp_path/"scratch")],capture_output=True,text=True,timeout=30)
 assert result.returncode==0,result.stdout+result.stderr
 assert result.stdout.count("PASS ")==7
