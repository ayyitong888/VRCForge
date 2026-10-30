"""Run the single-asset Core save handler against a tiny AssetDatabase double."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_single_asset_save_runtime(tmp_path):
    dotnet_root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    packs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet = shutil.which("dotnet")
    if not dotnet or not compilers or not packs:
        pytest.skip("Local .NET SDK/reference pack required")

    stubs = r'''
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
namespace UnityEngine { public class Object { public string path; public bool dirty; public int saves; } }
namespace UnityEditor {
 public class SceneAsset : UnityEngine.Object {}
 public static class EditorUtility { public static bool IsDirty(UnityEngine.Object o)=>o.dirty; }
 public static class AssetDatabase {
  public static readonly Dictionary<string,UnityEngine.Object> Assets=new Dictionary<string,UnityEngine.Object>();
  public static int Loads, Saves; public static bool ThrowOnSave, LeaveDirty;
  public static UnityEngine.Object LoadMainAssetAtPath(string p){Loads++;Assets.TryGetValue(p,out var o);return o;}
  public static string GetAssetPath(UnityEngine.Object o)=>o.path;
  public static void SaveAssetIfDirty(UnityEngine.Object o){Saves++;o.saves++;if(ThrowOnSave)throw new Exception("save failed");if(!LeaveDirty)o.dirty=false;}
  public static void SaveAssets(){throw new Exception("global save is forbidden");}
 }
}
namespace VRCForge.Core.MCP {
 [AttributeUsage(AttributeTargets.Class)] public sealed class VRCForgeCommandAttribute:Attribute {public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;}}
 [AttributeUsage(AttributeTargets.Property)] public sealed class VRCForgeInputAttribute:Attribute {public VRCForgeInputAttribute(string text){} public bool IsRequired{get;set;} public string DefaultLiteral{get;set;}}
 public sealed class VRCForgeToolResult { public string Kind,Code,Message;public object Payload; private VRCForgeToolResult(string k,string c,string m,object p){Kind=k;Code=c;Message=m;Payload=p;}
  public static VRCForgeToolResult Completed(string m,object p=null)=>new VRCForgeToolResult("completed","",m,p);
  public static VRCForgeToolResult RejectedBeforeMutation(string c,string m,string l,string p)=>new VRCForgeToolResult("rejected",c,m,new {mutationStarted=false,committed=false,commitState="not_started"});
  public static VRCForgeToolResult FailedWithCode(string c,string m,object p=null)=>new VRCForgeToolResult("failed",c,m,p);
 }
}
'''
    runner = r'''
using System;using System.Reflection;using Newtonsoft.Json.Linq;using UnityEditor;using UnityEngine;
class Probe {
 static int failures;static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 static object Field(object o,string n)=>o.GetType().GetField(n)?.GetValue(o)??o.GetType().GetProperty(n)?.GetValue(o);
 static VRCForge.Core.MCP.VRCForgeToolResult Call(string path,bool preview=false)=> (VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.Generic.UnityAssetSaveTool.HandleCommand(new JObject{{"assetPath",path},{"preview",preview}});
 static int Main(){
  var target=new UnityEngine.Object{path="Assets/Menu.asset",dirty=true};var other=new UnityEngine.Object{path="Assets/Other.asset",dirty=true};AssetDatabase.Assets[target.path]=target;AssetDatabase.Assets[other.path]=other;
  var preview=Call(target.path,true);Check((string)Field(preview,"Kind")=="completed"&&target.dirty&&target.saves==0&&AssetDatabase.Saves==0&& (string)Field(Field(preview,"Payload"),"schema")=="vrcforge.asset_save.v1"&&(string)Field(Field(preview,"Payload"),"commitState")=="not_started","preview does not write");
  AssetDatabase.Loads=0;var saved=Call(target.path);Check((string)Field(saved,"Kind")=="completed"&&!target.dirty&&target.saves==1&&AssetDatabase.Saves==1,"dirty target saved exactly once");Check(other.dirty&&other.saves==0,"unrelated dirty asset remains untouched");Check((bool)Field(Field(saved,"Payload"),"saved")&&(bool)Field(Field(saved,"Payload"),"committed")&&(string)Field(Field(saved,"Payload"),"schema")=="vrcforge.asset_save.v1"&&(string)Field(Field(saved,"Payload"),"commitState")=="committed","save receipt reports actual clean state");
  var loads=AssetDatabase.Loads;var clean=Call(target.path);Check((bool)Field(Field(clean,"Payload"),"noChange")&&target.saves==1&&AssetDatabase.Loads==loads+1&&(string)Field(Field(clean,"Payload"),"commitState")=="no_change"&&(bool)Field(Field(clean,"Payload"),"committed"),"clean asset no-op");
  var missing=Call("Assets/Missing.asset");Check((string)Field(missing,"Code")=="asset_not_found","missing path rejected clearly");
  AssetDatabase.Assets["Assets/Test.unity"]=new SceneAsset{path="Assets/Test.unity",dirty=true};var scene=Call("Assets/Test.unity");Check((string)Field(scene,"Code")=="scene_requires_scene_save","scene directs caller to scene_save");
  target.dirty=true;AssetDatabase.ThrowOnSave=true;var uncertain=Call(target.path);Check((string)Field(uncertain,"Code")=="asset_save_unconfirmed"&&(bool)Field(Field(uncertain,"Payload"),"mutationStarted"),"save exception reports mutation uncertainty");
  return failures==0?0:1;
 }
}
'''
    source = ROOT / "Assets/VRCForge/Editor/Generic/UnityAssetSaveTool.cs"
    cs = tmp_path / "Probe.cs"
    source_text = "\n".join(line for line in source.read_text(encoding="utf-8").splitlines() if not line.startswith("using "))
    runner_text = "\n".join(line for line in runner.splitlines() if not line.startswith("using "))
    imports = "using System;using System.Collections.Generic;using System.Linq;using System.Reflection;using Newtonsoft.Json.Linq;using UnityEditor;using UnityEngine;using VRCForge.Core.MCP;\n"
    cs.write_text(imports + stubs + "\n" + source_text + "\n" + runner_text, encoding="utf-8")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    output = tmp_path / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{output}"]
    command += [f"-r:{path}" for path in packs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([dotnet, str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 8
