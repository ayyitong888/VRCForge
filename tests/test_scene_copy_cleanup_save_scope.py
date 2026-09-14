"""Run actual owned-cleanup C# methods; the seam models an unrelated dirty asset."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_owned_cleanup_does_not_flush_unrelated_dirty_asset(tmp_path):
    base = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / "AppData/Local/Microsoft/dotnet")))
    compilers = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    host = base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not host.is_file() or not compilers or not refs:
        pytest.skip("Local .NET 8 SDK required")
    core = (ROOT / "Assets/VRCForge/Editor/SceneObjectCopyCore.cs").read_text(encoding="utf-8")
    actual = method(core, "internal static bool DeleteOwnedAsset(") + method(core, "internal static bool DeleteOwnedStagingFolder(")
    program = r'''
using System; using System.IO;
enum ImportAssetOptions {ForceSynchronousImport}
enum AssetPathToGUIDOptions {OnlyExistingAssets}
class StableAssetEvidence {internal string Digest;}
class StagingFolderLease {internal string FolderPath;}
static class AssetDatabase {
 internal static string Unrelated; internal static bool Dirty=true;
 internal static void SaveAssets(){File.WriteAllText(Unrelated,"unsaved change");Dirty=false;}
 internal static bool DeleteAsset(string p){if(Directory.Exists(p))Directory.Delete(p);else File.Delete(p);return true;}
 internal static void Refresh(ImportAssetOptions x){}
 internal static string AssetPathToGUID(string p,AssetPathToGUIDOptions x)=>Directory.Exists(p)?"owned":"";
}
static class Probe {
 internal static string ToAbsoluteAssetPath(string p)=>p;
 internal static bool SafeSiblingFileExists(string p,string suffix)=>File.Exists(p+suffix);
 internal static void VerifyOwnedStagingFolder(StagingFolderLease l){}
 internal static void RejectProjectPathReparsePoints(string p){}
 internal static bool AssetOrMetaExists(string p)=>File.Exists(p)||File.Exists(p+".meta");
 internal static StableAssetEvidence ReadStableAssetEvidence(string p,string label)=>new StableAssetEvidence{Digest=File.ReadAllText(p)};
 internal static bool StableAssetEvidenceMatches(StableAssetEvidence a,StableAssetEvidence b,bool exact)=>a.Digest==b.Digest;
 // ACTUAL_METHODS
 static void Check(bool condition,string label){if(!condition)throw new Exception(label);}
 static int Main(string[] args){
  AssetDatabase.Unrelated=Path.Combine(args[0],"unrelated.mat");File.WriteAllText(AssetDatabase.Unrelated,"persisted baseline");
  var owned=Path.Combine(args[0],"owned.asset");File.WriteAllText(owned,"owned");
  Check(!DeleteOwnedAsset(owned,new StableAssetEvidence{Digest="changed"})&&File.Exists(owned),"changed asset preserved");
  Check(DeleteOwnedAsset(owned,new StableAssetEvidence{Digest="owned"})&&!File.Exists(owned),"owned asset removed");
  Check(AssetDatabase.Dirty&&File.ReadAllText(AssetDatabase.Unrelated)=="persisted baseline","asset cleanup flushed unrelated dirty asset");
  var folder=Path.Combine(args[0],"staging");Directory.CreateDirectory(folder);File.WriteAllText(Path.Combine(folder,"unknown"),"keep");
  Check(!DeleteOwnedStagingFolder(new StagingFolderLease{FolderPath=folder}),"nonempty staging preserved");
  File.Delete(Path.Combine(folder,"unknown"));
  Check(DeleteOwnedStagingFolder(new StagingFolderLease{FolderPath=folder})&&!Directory.Exists(folder),"empty owned staging removed");
  Check(AssetDatabase.Dirty&&File.ReadAllText(AssetDatabase.Unrelated)=="persisted baseline","folder cleanup flushed unrelated dirty asset");
  Console.WriteLine("PASS cleanup ownership and unrelated dirty state");return 0;
 }
}
'''.replace("// ACTUAL_METHODS", actual)
    source = tmp_path / "Probe.cs"
    source.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(host), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [str(source)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(host), str(dll), str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
