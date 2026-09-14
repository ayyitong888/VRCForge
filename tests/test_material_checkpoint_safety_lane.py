"""Execute production safety predicate; not live Unity acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_material_checkpoint_safety_lane(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
    names = ["IsStrictSafetyControlRequest", "HasCompleteSafetyControlLiveBinding", "HasExactKeys", "HasString", "HasNonEmptyString", "HasBoolean", "HasTrueBoolean", "HasBoundedInteger", "HasStringArray"]
    methods = []
    for name in names:
        start = source.index("        private static bool " + name + "(")
        end = source.index("\n        }", start) + len("\n        }")
        methods.append(source[start:end])
    program = '''using System; using System.Linq; using System.Collections.Generic; using Newtonsoft.Json.Linq;
class Probe {
''' + "\n".join(methods) + '''
static void Check(bool value,string name){if(!value)throw new Exception(name);}
static JObject Prep(int n){return new JObject { ["projectPath"]="D:/Actual", ["materialBaselineOnly"]=true,
["checkpointAssetPaths"]=new JArray(Enumerable.Range(0,n).Select(i=>(object)("Assets/M"+i+".mat"))) };}
static JObject Reload(int n,bool material){return new JObject { ["projectPath"]="D:/Actual",["phase"]="reload",
["scenePaths"]=new JArray(),["activeScenePath"]="",["refreshAssets"]=false,
["assetBaseline"]=new JArray(Enumerable.Range(0,n).Select(i=>(object)(material
? new JObject {["assetPath"]="Assets/M"+i+".mat",["assetGuid"]=new string('a',32),["materialStateDigest"]=new string('b',64)}
: new JObject {["assetPath"]="Assets/M"+i+".asset",["assetGuid"]=new string('a',32),["serializedState"]=new JObject()}))) };}
static int Main(){
Check(IsStrictSafetyControlRequest("vrc_prepare_checkpoint",Prep(1)),"material prepare rejected");
Check(IsStrictSafetyControlRequest("vrc_prepare_checkpoint",Prep(128)),"128 material prepare rejected");
Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",Prep(129)),"129 allowed");
Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",Prep(0)),"empty material mode allowed");
var bad=Prep(1);bad["materialBaselineOnly"]="true";Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",bad),"string flag allowed");
bad=Prep(1);bad["checkpointAssetPaths"]=new JArray("Assets/Menu.asset");Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",bad),"nonmaterial mode allowed");
bad=Prep(1);bad["extra"]=true;Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",bad),"unknown argument allowed");
Check(IsStrictSafetyControlRequest("vrc_reload_after_checkpoint_restore",Reload(128,true)),"material restore rejected");
Check(IsStrictSafetyControlRequest("vrc_reload_after_checkpoint_restore",Reload(32,false)),"legacy restore rejected");
Check(!IsStrictSafetyControlRequest("vrc_reload_after_checkpoint_restore",Reload(33,false)),"legacy limit widened");
bad=Reload(1,true);bad["assetBaseline"][0]["materialStateDigest"]="invalid";Check(!IsStrictSafetyControlRequest("vrc_reload_after_checkpoint_restore",bad),"bad digest allowed");
bad=Reload(1,true);bad["phase"]="prepare_restore";Check(!IsStrictSafetyControlRequest("vrc_reload_after_checkpoint_restore",bad),"wrong phase allowed");
Console.WriteLine("PASS 12");return 0;}}
'''
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    cmd = [shutil.which("dotnet"), str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}", f"-r:{newtonsoft}"]
    cmd += [f"-r:{p}" for p in refs.glob("*.dll")] + [str(cs)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([shutil.which("dotnet"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS 12" in result.stdout
