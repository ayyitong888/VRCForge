"""Execute production C# methods against small Unity data stubs (not Unity).

This checks numerical construction and layer mutation, not persistence or MCP.
The harness uses the installed SDK compiler directly; no restore/build servers.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs"


def method(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


STUBS = r'''
using System; using System.Linq; using System.Collections.Generic; using Newtonsoft.Json.Linq;
public enum WeightedMode {None=0,In=1,Out=2,Both=3}
public struct Keyframe { public float time,value,inTangent,outTangent,inWeight,outWeight; public WeightedMode weightedMode;
 public Keyframe(float t,float v){time=t;value=v;inTangent=outTangent=inWeight=outWeight=0;weightedMode=WeightedMode.None;} }
public class AnimationCurve {public Keyframe[] keys;public int preWrapMode,postWrapMode;public int length=>keys.Length;
 public AnimationCurve(params Keyframe[] k){keys=k;} public static AnimationCurve Constant(float a,float b,float v)=>new AnimationCurve(new Keyframe(a,v));}
public class AnimatorControllerLayer {public string name;public float defaultWeight;}
public class AnimatorController {public AnimatorControllerLayer[] layers=new AnimatorControllerLayer[0];
 public void AddLayer(string n){layers=layers.Concat(new[]{new AnimatorControllerLayer{name=n}}).ToArray();}}
public static class Mathf {public static bool Approximately(float a,float b)=>Math.Abs(a-b)<0.00001f;}
'''

RUNNER = r'''
 static int failures=0; static void Check(bool v,string name){Console.WriteLine((v?"PASS ":"FAIL ")+name);if(!v)failures++;}
 static bool Reject(JObject p){try{BuildCurve(p);return false;}catch(InvalidOperationException){return true;}}
 public static int Main(){
  var p=JObject.Parse(@"{'keys':[{'time':0,'value':0,'outWeight':0.75,'inWeight':0.25,'weightedMode':'Both'},{'time':1,'value':1,'inWeight':0.2,'outWeight':0.8,'weightedMode':'Both'}]}");
  var curve=BuildCurve(p);
  Check(curve.keys[0].weightedMode==WeightedMode.Both && curve.keys[0].outWeight==0.75f && curve.keys[1].inWeight==0.2f,"weighted keys preserve exact single precision values");
  Check(Reject(JObject.Parse(@"{'keys':[{'weightedMode':'Bogus'}]}")),"invalid weighted mode rejects");
  Check(Reject(JObject.Parse(@"{'keys':[{'time':0,'value':0},{'time':0,'value':1}]}")),"duplicate time rejects");
  Check(Reject(JObject.Parse(@"{'keys':[{'inWeight':2}]}")),"out of range weight rejects");
  var c=new AnimatorController();c.AddLayer("PreviewOnly");c.layers[0].defaultWeight=0;
  EnsureLayer(c,"PreviewOnly");Check(c.layers[0].defaultWeight==0,"existing muted layer remains muted");
  EnsureLayer(c,"NewLayer");Check(c.layers[1].defaultWeight==1,"new layer retains active default");
  return failures==0?0:1;
 }
'''


def compile_and_run(source: str, output: Path) -> subprocess.CompletedProcess:
    dotnet = shutil.which("dotnet")
    dotnet_root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    sdks = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not sdks or not refs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack are required for the C# isolation harness")
    compiler = sdks[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    if not newtonsoft.is_file():
        pytest.skip("SDK Newtonsoft.Json assembly unavailable")
    output.mkdir(parents=True, exist_ok=True)
    curve = source[source.index("public static class WriteAnimationCurveTool"):source.index("public static class ManageExpressionParametersTool")]
    fx = source[source.index("public static class ManageFxAnimatorTool"):]
    selected = [method(curve,"private static AnimationCurve BuildCurve"),method(fx,"private static AnimatorControllerLayer EnsureLayer")]
    for signature in ("internal static void RequireFinite","private static float ReadWeight"):
        if signature in curve: selected.append(method(curve,signature))
    program = STUBS + "public class Probe {\n" + "\n".join(selected) + RUNNER + "\n}"
    (output / "Probe.cs").write_text(program, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}",str(output / "Probe.cs")]
    compiled = subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, output / "Newtonsoft.Json.dll")
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    return subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)


def test_actual_curve_builder_and_layer_mutation_regressions(tmp_path):
    completed = compile_and_run(SOURCE_PATH.read_text(encoding="utf-8-sig"), tmp_path / "csharp")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("PASS ") == 6
