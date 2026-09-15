"""Production backup creation and real files, with explicit Unity discovery doubles."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_consecutive_backups_preserve_every_snapshot(tmp_path):
    source = (ROOT / 'Assets/VRCForge/Editor/ConsoleTools.cs').read_text(encoding='utf-8')
    bodies = '\n'.join(method(source, signature) for signature in (
        'private static SafeBackupPayload CreateBackup', 'private static object BuildTransaction',
        'private static string ComputeSha256', 'private class SafeBackupPayload',
        'private class ProjectIdentity', 'private class BackupFileItem',
        'private class SafeBackupSummary', 'private sealed class SafeBackupTransactionException',
    ))
    code = r'''using System;using System.IO;using System.Linq;using System.Text;using System.Collections.Generic;using System.Security.Cryptography;using Newtonsoft.Json;
class Application {public static string dataPath;}
class AssetDatabase {public static void Refresh(){}}
class Probe {
class CreateSafeBackupParameters {public string backupRoot;public bool? refreshAssets=false;}
static string Root;
static string GetProjectRoot()=>Root;
static string ResolveProjectPath(string p,string r)=>Path.Combine(r,"Backups");
static List<string> ResolveRequestedAssetPaths(CreateSafeBackupParameters p,List<string> w)=>new List<string>{"Assets/source.asset"};
static void AddAssetPathToBackupMap(string root,string asset,Dictionary<string,BackupFileItem> map,List<string> warnings){map.Add(asset,new BackupFileItem{project_relative_path=asset,backup_relative_path="files/"+asset});}
static ProjectIdentity BuildProjectIdentity(string r)=>new ProjectIdentity();
static bool IsInside(string p,string c)=>false;
''' + bodies + r'''
static void Main(string[] args){
Root=args[0];Application.dataPath=Path.Combine(Root,"Assets");Directory.CreateDirectory(Application.dataPath);
var source=Path.Combine(Application.dataPath,"source.asset");var seen=new HashSet<string>();var snapshots=new List<string>();
for(int i=0;i<4;i++){
File.WriteAllText(source,"version-"+i);var result=CreateBackup(new CreateSafeBackupParameters());
if(!seen.Add(result.backup_id))throw new Exception("backup id collision overwrote an earlier snapshot");
snapshots.Add(result.backup_path);
for(int j=0;j<snapshots.Count;j++)if(File.ReadAllText(Path.Combine(snapshots[j],"files/Assets/source.asset"))!="version-"+j)throw new Exception("earlier backup changed");
}
}}
'''
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compiler = sorted((base / 'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs = sorted((base / 'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    newtonsoft = compiler.parents[2] / 'Newtonsoft.Json.dll'
    dotnet = shutil.which('dotnet')
    (tmp_path / 'Probe.cs').write_text(code, encoding='utf-8')
    dll = tmp_path / 'Probe.dll'
    built = subprocess.run([dotnet, str(compiler), '-nologo', '-target:exe', '-nostdlib+', f'-out:{dll}', f'-r:{newtonsoft}'] + [f'-r:{p}' for p in refs.glob('*.dll')] + [str(tmp_path / 'Probe.cs')], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / 'Newtonsoft.Json.dll')
    (tmp_path / 'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'netcoreapp3.1', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '3.1.0'}}}), encoding='utf-8')
    ran = subprocess.run([dotnet, str(dll), str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stdout + ran.stderr
