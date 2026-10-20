"""Runtime coverage for bounded safe-backup storage paths."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from test_curve_fx_authoring_runtime_contract import method


ROOT = Path(__file__).resolve().parents[1]
LONG_ASSET = (
    "Assets/FacialTracking_AvatarXX/Animation/Binary/"
    "Animator_cf600331d188874428d4edc2927781cc/"
    "v2BrowInnerUp_False_0_Positive_cf600331d188874428d4edc2927781cc.anim"
)


def _compile_and_run(tmp_path: Path) -> None:
    source = (ROOT / "Assets/VRCForge/Editor/ConsoleTools.cs").read_text(encoding="utf-8")
    signatures = [
        "private static SafeBackupPayload CreateBackup",
        "private static void AddFile",
        "private static object BuildTransaction",
        "private static ProjectIdentity BuildProjectIdentity",
        "private static string ComputeSha256",
        "private static string StableHash",
        "private static bool IsInside",
        "private class SafeBackupPayload",
        "private class ProjectIdentity",
        "private class BackupFileItem",
        "private class SafeBackupSummary",
        "private sealed class SafeBackupTransactionException",
    ]
    # The production long-path fix owns copying, hashing, and partial-file
    # cleanup in a focused helper. Include it when present so this harness
    # continues compiling the real CreateBackup implementation.
    for signature in (
        "private static void WriteOwnedBackupFile",
        "private static void CopyBackupFile",
        "private static string CopyFileToBackup",
        "private static void CopyFileToBackup",
    ):
        if signature in source:
            signatures.append(signature)
            break
    bodies = "\n".join(method(source, signature) for signature in signatures)
    code = r'''using System;using System.IO;using System.Linq;using System.Text;using System.Collections.Generic;using System.Security.Cryptography;using Newtonsoft.Json;
class Application {public static string dataPath;public static string unityVersion="fixture";}
class AssetDatabase {public static void Refresh(){}}
class Probe {
class CreateSafeBackupParameters {public string backupRoot;public bool? refreshAssets=false;}
static string Root;
static string GetProjectRoot()=>Root;
static string ResolveProjectPath(string p,string r)=>Path.Combine(r,"Backups");
static List<string> ResolveRequestedAssetPaths(CreateSafeBackupParameters p,List<string> w)=>new List<string>{LongAsset};
static void AddAssetPathToBackupMap(string root,string asset,Dictionary<string,BackupFileItem> map,List<string> warnings){AddFile(root,asset,map);AddFile(root,asset+".meta",map);}
const string LongAsset="''' + LONG_ASSET + r'''";
''' + bodies + r'''
static void Main(string[] args){
Root=args[0];Application.dataPath=Path.Combine(Root,"Assets");Directory.CreateDirectory(Application.dataPath);
var source=Path.Combine(Root,LongAsset);Directory.CreateDirectory(Path.GetDirectoryName(source));
File.WriteAllText(source,"animation-data");File.WriteAllText(source+".meta","guid: exact-meta");
var result=CreateBackup(new CreateSafeBackupParameters());
if(result.files.Count!=2)throw new Exception("asset and meta were not both preserved");
foreach(var item in result.files){
 if(item.project_relative_path!=LongAsset && item.project_relative_path!=LongAsset+".meta")throw new Exception("manifest lost exact project path");
 if(!item.backup_relative_path.StartsWith("files/",StringComparison.Ordinal))throw new Exception("backup path escaped files root");
 var stored=Path.Combine(result.backup_path,item.backup_relative_path);
 if(stored.Length>240)throw new Exception("backup storage path is not bounded: "+stored.Length);
 if(!File.Exists(stored))throw new Exception("flattened backup file missing");
 var expected=item.is_meta?"guid: exact-meta":"animation-data";
 if(File.ReadAllText(stored)!=expected)throw new Exception("flattened backup content mismatch");
}
var manifest=JsonConvert.DeserializeObject<SafeBackupPayload>(File.ReadAllText(Path.Combine(result.backup_path,"backup.json")));
if(manifest.files.Any(item=>item.project_relative_path!=LongAsset && item.project_relative_path!=LongAsset+".meta"))throw new Exception("persisted manifest path changed");
var partial=Path.Combine(Root,"partial.bin");bool partialCreated=false;string partialDigest="";bool partialFailed=false;
try{WriteOwnedBackupFile(partial,output=>{var bytes=Encoding.UTF8.GetBytes("partial-data");output.Write(bytes,0,bytes.Length);throw new IOException("injected callback failure");},out partialCreated,out partialDigest);}catch(IOException){partialFailed=true;}
if(!partialFailed||!partialCreated||!File.Exists(partial))throw new Exception("partial callback failure ownership was lost");
if(partialDigest!=ComputeSha256(partial))throw new Exception("partial callback digest does not match actual bytes");
var existing=Path.Combine(Root,"existing.bin");File.WriteAllText(existing,"user-owned");bool existingCreated=true;string existingDigest="unexpected";bool createNewRejected=false;
try{WriteOwnedBackupFile(existing,output=>output.WriteByte(1),out existingCreated,out existingDigest);}catch(IOException){createNewRejected=true;}
if(!createNewRejected||existingCreated||existingDigest!=""||File.ReadAllText(existing)!="user-owned")throw new Exception("CreateNew did not preserve existing file ownership");
}}
'''
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    dotnet = shutil.which("dotnet")
    (tmp_path / "Probe.cs").write_text(code, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    built = subprocess.run(
        [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}", f"-r:{newtonsoft}"]
        + [f"-r:{path}" for path in refs.glob("*.dll")]
        + [str(tmp_path / "Probe.cs")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(
        json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}),
        encoding="utf-8",
    )
    ran = subprocess.run([dotnet, str(dll), str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_safe_backup_flattens_long_storage_path_and_preserves_manifest_paths(tmp_path: Path) -> None:
    _compile_and_run(tmp_path)
    backup_path = next((tmp_path / "Backups").iterdir())
    source_path = tmp_path / Path(LONG_ASSET)
    source_path.write_text("mutated-after-backup", encoding="utf-8")
    from test_safe_backup_integrity_runtime import test_restore_rejects_damaged_backup_before_writing

    test_restore_rejects_damaged_backup_before_writing(
        tmp_path, False, long_flat=True, external_backup=backup_path
    )
    assert source_path.read_text(encoding="utf-8") == "animation-data"
