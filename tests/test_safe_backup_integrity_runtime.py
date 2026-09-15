"""Production restore class with Unity/path-boundary doubles, real file I/O."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('damaged', [False, True])
def test_restore_rejects_damaged_backup_before_writing(tmp_path, damaged, failure_mode=None):
    source = (ROOT / 'Assets/VRCForge/Editor/PrefabTools.cs').read_text(encoding='utf-8')
    stubs = r'''
namespace UnityEngine {public static class Application {public static string dataPath;}}
namespace UnityEditor {public static class AssetDatabase {public static void Refresh(){}}}
namespace VRCForge.Core.MCP {
class VRCForgeCommandAttribute:System.Attribute {public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;}}
class VRCForgeInputAttribute:System.Attribute {public VRCForgeInputAttribute(string text){} public bool IsRequired{get;set;}}
}
namespace VRCForge.Editor {
class VRCForgeOutputPathGuard {public static string ResolveManagedProjectPath(string path,string fallback,string scope,string label)=>System.IO.Path.GetFullPath(System.IO.Path.Combine(System.IO.Directory.GetParent(UnityEngine.Application.dataPath).FullName,string.IsNullOrWhiteSpace(path)?fallback:path));}
class VRCForgeToolResult {public bool Success;public string Message;public object Payload;public static object Completed(string m,object p)=>new VRCForgeToolResult{Success=true,Message=m,Payload=p};public static object Failed(string m,object p=null)=>new VRCForgeToolResult{Success=false,Message=m,Payload=p};}
class Probe {
static string Hash(byte[] bytes){using(var h=System.Security.Cryptography.SHA256.Create())return System.BitConverter.ToString(h.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
static void Main(string[] args){
var root=args[0];var damaged=args[1]=="True";UnityEngine.Application.dataPath=System.IO.Path.Combine(root,"Assets");System.IO.Directory.CreateDirectory(UnityEngine.Application.dataPath);
var target=System.IO.Path.Combine(root,"Assets/file.asset");var original=System.Text.Encoding.UTF8.GetBytes("original");System.IO.File.WriteAllBytes(target,original);
var backup=System.IO.Path.Combine(root,"Library/VRCForge/Backups/test");System.IO.Directory.CreateDirectory(backup);
System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"file.asset"),damaged?"damaged":"original");
var manifest=Newtonsoft.Json.Linq.JObject.FromObject(new{type="vrcforge_safe_backup",backup_id="test",project_identity=new{project_root_hash=Hash(System.Text.Encoding.UTF8.GetBytes(root.Replace("\\","/").ToLowerInvariant()))},files=new[]{new{project_relative_path="Assets/file.asset",backup_relative_path="file.asset",sha256=Hash(original)}}});
System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"backup.json"),manifest.ToString());
foreach(var confirm in new[]{false,true}){
var r=(VRCForgeToolResult)PrefabTools.HandleCommand(Newtonsoft.Json.Linq.JObject.FromObject(new{backupPath=backup,confirmRestore=confirm,refreshAssets=false}));
if(r.Success==damaged)throw new System.Exception("wrong integrity outcome: "+r.Message);
if(System.IO.File.ReadAllText(target)!="original")throw new System.Exception("restore corrupted target");
}
}}
}
'''
    if failure_mode:
        stubs = stubs.replace('damaged?"damaged":"original"', '"replacement"')
        stubs = stubs.replace('sha256=Hash(original)}}', 'sha256=Hash(System.Text.Encoding.UTF8.GetBytes("replacement"))}}')
        stubs = stubs.replace('System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"backup.json"),manifest.ToString());', r'''
var second=System.IO.Path.Combine(root,"Assets/second.asset");System.IO.File.WriteAllText(second,"second-original");
System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"second.asset"),"second-replacement");
((Newtonsoft.Json.Linq.JArray)manifest["files"]).Add(Newtonsoft.Json.Linq.JObject.FromObject(new{project_relative_path="Assets/second.asset",backup_relative_path="second.asset",sha256=Hash(System.Text.Encoding.UTF8.GetBytes("second-replacement"))}));
System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"backup.json"),manifest.ToString());
using var locked=new System.IO.FileStream(second,System.IO.FileMode.Open,System.IO.FileAccess.Read,System.IO.FileShare.Read);
''')
        stubs = stubs.replace('new[]{false,true}', 'new[]{true}')
        stubs = stubs.replace('confirmRestore=confirm,refreshAssets=false', 'confirmRestore=confirm,refreshAssets=false,allowOverwriteChanged=true')
        stubs = stubs.replace('if(r.Success==damaged)', 'if(r.Success)')
        stubs = stubs.replace('if(System.IO.File.ReadAllText(target)', 'if(Newtonsoft.Json.Linq.JObject.FromObject(r.Payload).Value<string>("commitState")!="rolled_back")throw new System.Exception("missing verified rollback receipt");\nif(System.IO.File.ReadAllText(target)')
        stubs = stubs.replace('if(System.IO.File.ReadAllText(target)!="original")', 'if(System.IO.Directory.GetDirectories(backup,".restore-*").Length!=0 || System.IO.File.ReadAllText(second)!="second-original" || System.IO.File.ReadAllText(target)!="original")')
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compiler = sorted((base / 'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs = sorted((base / 'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    newtonsoft = compiler.parents[2] / 'Newtonsoft.Json.dll'
    dotnet = shutil.which('dotnet')
    (tmp_path / 'Probe.cs').write_text(source + stubs, encoding='utf-8')
    dll = tmp_path / 'Probe.dll'
    command = [dotnet, str(compiler), '-nologo', '-target:exe', '-nostdlib+', f'-out:{dll}', f'-r:{newtonsoft}'] + [f'-r:{p}' for p in refs.glob('*.dll')] + [str(tmp_path / 'Probe.cs')]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / 'Newtonsoft.Json.dll')
    (tmp_path / 'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'netcoreapp3.1', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '3.1.0'}}}), encoding='utf-8')
    ran = subprocess.run([dotnet, str(dll), str(tmp_path), str(damaged)], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_later_os_write_failure_restores_earlier_file(tmp_path):
    test_restore_rejects_damaged_backup_before_writing(tmp_path, False, failure_mode='locked')
