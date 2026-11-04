"""Execute production MA field discovery with Json.NET and SDK-shaped type seams.

Shapes match MA 1.18.7 BlendshapeBinding and ToggledObject; no Unity execution.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_ma_nested_field_reference_runtime import _csharp_block

ROOT = Path(__file__).resolve().parents[1]


def test_ma_field_discovery_describes_nested_writable_input(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/MAComponentWriter.cs").read_text(encoding="utf-8-sig")
    methods = "\n".join(_csharp_block(source, signature) for signature in (
        "private static JObject DescribeWritableFields(",
        "private static JObject DescribeFieldInput(",
        "private static Type GetMemberType(",
    ))
    program = r'''
using System; using System.Linq; using System.Reflection; using System.Collections.Generic;
using Newtonsoft.Json.Linq; using Newtonsoft.Json.Serialization;
using UnityEngine;
namespace UnityEngine {
 public class Object {} public class Component:Object {} public class GameObject:Object {}
 public enum WrapMode {Default, Once, Loop, PingPong, ClampForever}
 public enum WeightedMode {None, In, Out, Both}
 public struct Keyframe { public float time {get;set;} public float value {get;set;}
  public float inTangent {get;set;} public float outTangent {get;set;}
  public float inWeight {get;set;} public float outWeight {get;set;} public WeightedMode weightedMode {get;set;} }
 public class AnimationCurve {public Keyframe[] keys {get;set;} public WrapMode preWrapMode {get;set;}
  public WrapMode postWrapMode {get;set;} public int length=>0;}
}
namespace nadena.dev.modular_avatar.core {
 public class AvatarObjectReference {public string referencePath;}
 public struct BlendshapeBinding {public AvatarObjectReference ReferenceMesh; public string Blendshape;
  public string LocalBlendshape; public bool RemapCurveIsValid; public UnityEngine.AnimationCurve RemapCurve;}
 public struct ToggledObject {public AvatarObjectReference Object;public bool Active;}
 public class AvatarTagComponent:UnityEngine.Component {public bool Enabled;}
 public class ModularAvatarBlendshapeSync:AvatarTagComponent {public List<BlendshapeBinding> Bindings;}
 public class ModularAvatarObjectToggle:AvatarTagComponent {private List<ToggledObject> m_objects;
  public List<ToggledObject> Objects {get=>m_objects;set=>m_objects=value;}}
 public class References:AvatarTagComponent {public UnityEngine.GameObject Target;public UnityEngine.Component Component;
  public UnityEngine.WrapMode Mode; public readonly int Immutable=1; public int GetterOnly=>0;}
}
class Probe {
 private static readonly IContractResolver FieldContractResolver=new DefaultContractResolver();
 METHODS
 static void Check(bool condition,string message){if(!condition)throw new Exception(message);}
 static void Main(){
  var sync=DescribeWritableFields(typeof(nadena.dev.modular_avatar.core.ModularAvatarBlendshapeSync));
  var row=sync["Bindings"]["items"]["properties"];
  Check((string)sync["Bindings"]["type"]=="array","Bindings is not an array");
  Check((string)row["ReferenceMesh"]["properties"]["referencePath"]["type"]=="string","nested avatar reference path missing");
  Check((string)row["RemapCurve"]["properties"]["keys"]["type"]=="array","curve keys missing");
  Check((string)row["RemapCurve"]["properties"]["keys"]["items"]["properties"]["time"]["type"]=="number","key time not numeric");
  Check(row["RemapCurve"]["properties"]["preWrapMode"]["enum"].Values<string>().Contains("Loop"),"wrap enum missing");
  Check(row["RemapCurve"]["properties"]["length"]==null,"read-only curve length advertised as writable");
  var toggle=DescribeWritableFields(typeof(nadena.dev.modular_avatar.core.ModularAvatarObjectToggle));
  Check((string)toggle["Objects"]["items"]["properties"]["Object"]["properties"]["referencePath"]["type"]=="string","toggle reference path missing");
  Check((string)toggle["Objects"]["items"]["properties"]["Active"]["type"]=="boolean","toggle active missing");
  Check(toggle["m_objects"]==null,"private storage advertised");
  var refs=DescribeWritableFields(typeof(nadena.dev.modular_avatar.core.References));
  Check((string)refs["Target"]["type"]=="string" && (string)refs["Component"]["type"]=="string","Unity object references must use paths");
  Check(refs["Mode"]["enum"].Values<string>().Contains("PingPong"),"enum names missing");
  Check(refs["Immutable"]==null && refs["GetterOnly"]==null,"read-only members advertised");
  Check((string)sync["Enabled"]["type"]=="boolean","inherited MA fields missing");
  Console.WriteLine("MA nested writable field discovery passed");
 }
}
'''.replace("METHODS", methods)
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
    command = [str(dotnet), str(compiler), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
    command += [f"-r:{path}" for path in refs[-1].glob("*.dll")]
    result = subprocess.run([*command, f"-r:{newtonsoft}", str(cs)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, tmp_path / newtonsoft.name)
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MA nested writable field discovery passed" in result.stdout
