"""Execute the production path resolver with loaded-scene seams, without Unity writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT = Path(__file__).resolve().parents[1]
def test_component_target_identity(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8-sig")
    selected = method(source, "internal static GameObject ResolveGameObject(")
    program = r'''
using System;using System.Linq;using System.Collections.Generic;
class Transform {internal string path;}
class GameObject {internal string name;internal Transform transform;}
class GameObjectNotFoundException:InvalidOperationException {internal GameObjectNotFoundException(string s):base(s){}}
class Probe {
 static GameObject[] objects;
 static string NormalizePath(string s)=>(s??"").Replace("\\","/").Trim().Trim('/');
 static string GetHierarchyPath(Transform t)=>t.path;
 static IEnumerable<GameObject> EnumerateSceneGameObjects()=>objects;
 SELECTED
 static GameObject Go(string path)=>new GameObject{name=path.Split('/').Last(),transform=new Transform{path=path}};
 static int failures;
 static void Check(bool b,string label){Console.WriteLine((b?"PASS ":"FAIL ")+label);if(!b)failures++;}
 static bool Reject(string path){try{ResolveGameObject(path);return false;}catch(InvalidOperationException){return true;}}
 static int Main(){
  objects=new[]{Go("Correct/Target")};
  Check(Reject("Wrong/Target"),"missing exact hierarchy must not select unrelated leaf");
  Check(ReferenceEquals(ResolveGameObject("Target"),objects[0]),"unique leaf convenience retained");
  Check(ReferenceEquals(ResolveGameObject("Correct/Target"),objects[0]),"unique full path retained");
  objects=new[]{Go("Same/Target"),Go("Same/Target")};
  Check(Reject("Same/Target"),"duplicate exact paths rejected");
  objects=new[]{Go("A/Target"),Go("B/Target")};
  Check(Reject("Target"),"ambiguous leaf rejected");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    base = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / "AppData/Local/Microsoft/dotnet")))
    compilers = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    dotnet = base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not dotnet.is_file() or not compilers or not refs:
        pytest.skip("Local .NET 8 SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(dotnet), str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 5
