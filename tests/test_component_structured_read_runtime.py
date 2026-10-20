import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")


def test_unknown_serializable_values_use_bounded_field_projection():
    start = SOURCE.index("private static object DescribeUnityObject")
    end = SOURCE.index("internal static Type FindType", start)
    bounded = SOURCE[start:end]

    assert "SerializeField" in bounded
    assert "GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)" in bounded
    assert "field.GetCustomAttribute<SerializeField>()" in bounded
    assert "field.GetValue(value)" in bounded
    assert "__incomplete" in bounded
    assert "__truncated" in bounded
    assert "__cycle" in bounded
    assert "depth >= 4" in bounded
    assert "nodesRemaining = 2048" in bounded
    assert "items.Count >= maxItems" in bounded
    assert "GetProperty(" not in bounded
    assert "GetMethod(" not in bounded


def test_baseline_would_return_unknown_dto_type_name():
    baseline = __import__("subprocess").check_output(
        ["git", "show", "HEAD:Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    start = baseline.index("internal static object DescribeValue(object value)")
    end = baseline.index("internal static object DescribeValue(\n", start)
    old = baseline[start:end]
    assert "return value.ToString();" in old
    assert "GetFields(BindingFlags" not in old


def test_structured_read_reports_completeness_separately_from_collection_counts():
    get_property = SOURCE.split("public static class GetPropertyTool", 1)[1]
    assert "out var valueIncomplete" in get_property
    assert "valueTruncated," in get_property
    assert "valueIncomplete" in get_property
    assert "valueItemCount = valueItemCount >= 0 ? (int?)valueItemCount : null" in get_property


@pytest.fixture
def structured_probe(tmp_path):
    base = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / "AppData/Local/Microsoft/dotnet")))
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    dotnet = base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not dotnet.is_file() or not compilers or not refs:
        pytest.skip("Local .NET 8 SDK/reference pack required")

    start = SOURCE.index("private static object DescribeUnityObject")
    end = SOURCE.index("internal static Type FindType", start)
    actual = SOURCE[start:end]
    baseline = __import__("subprocess").check_output(
        ["git", "show", "HEAD:Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    baseline_start = baseline.index("private static object DescribeUnityObject")
    baseline_end = baseline.index("internal static Type FindType", baseline_start)
    baseline_actual = baseline[baseline_start:baseline_end]
    seams = r'''
using System; using System.Collections; using System.Collections.Generic; using System.Reflection; using UnityEngine; using UnityEditor;
namespace UnityEngine {
 [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute {}
 public class Object { public string name=""; public int GetInstanceID()=>7; }
 public class Component:Object { public GameObject gameObject; }
 public class GameObject:Object { public Scene scene=new Scene(); public Transform transform=new Transform(); }
 public class Transform:Component { }
 public struct Vector2 { public float x,y; }
 public struct Vector3 { public float x,y,z; }
 public struct Vector4 { public float x,y,z,w; }
 public struct Quaternion { public float x,y,z,w; }
 public struct Color { public float r,g,b,a; }
 public struct Bounds { public Vector3 center,extents; }
 public struct Matrix4x4 { public float m00,m01,m02,m03,m10,m11,m12,m13,m20,m21,m22,m23,m30,m31,m32,m33; }
 public class Scene { public bool IsValid()=>false; public string path=""; }
}
namespace UnityEditor {
 using UnityEngine;
 public struct GlobalObjectId { public static GlobalObjectId GetGlobalObjectIdSlow(Object value)=>new GlobalObjectId(); }
 public static class AssetDatabase { public static bool Contains(Object value)=>false; public static string GetAssetPath(Object value)=>""; public static bool TryGetGUIDAndLocalFileIdentifier(Object value,out string guid,out long id){guid="";id=0;return false;} }
}
namespace VRCForge.Editor {
 internal static class ComponentCrudCore {
  private static string GetHierarchyPath(UnityEngine.Transform value)=>"";
 ACTUAL
 }
 internal static class BaselineCrud {
  private static string GetHierarchyPath(UnityEngine.Transform value)=>"";
 BASELINE_ACTUAL
 }
 [Serializable] public enum Kind { Float, Int }
 [Serializable] public sealed class ParameterConfig { public string name; public Kind kind; [UnityEngine.SerializeField] private int savedValue; public bool enabled; public float min,max; public string defaultValue; public UnityEngine.Object reference; public int omittedDefault; public ParameterConfig(string n, Kind k, int v){name=n;kind=k;savedValue=v;enabled=true;min=0;max=1;defaultValue="";reference=new UnityEngine.Object();} public int Value => throw new Exception("getter must not run"); }
 [Serializable] public sealed class Cycle { public string name; public Cycle next; }
 [Serializable] public sealed class Chain { public Chain next; }
 public sealed class Unsupported { public int value=1; }
 [Serializable] public class Parent { public string name="parent"; }
 [Serializable] public sealed class Child : Parent { public new string name="child"; }
 class Runner {
  static void Check(bool ok,string message){if(!ok)throw new Exception(message);}
  static int Main(){
   var values=new List<ParameterConfig>(); for(var i=0;i<91;i++) values.Add(new ParameterConfig("p"+i,i%2==0?Kind.Float:Kind.Int,i));
   int returned,total; bool truncated,incomplete;
   var result=ComponentCrudCore.DescribeValue(values,200,out returned,out total,out truncated,out incomplete);
   Check(returned==91 && total==91 && !truncated && !incomplete,"91 typed entries must be complete");
   var rows=(List<object>)result; var first=(Dictionary<string,object>)rows[0]; Check((string)first["name"]=="p0" && (string)first["kind"]=="Float","enum and public field projection");
   Check(first.ContainsKey("savedValue") && first.ContainsKey("reference") && first.ContainsKey("defaultValue"),"private serialized and object reference fields");
   var limited=ComponentCrudCore.DescribeValue(values,10,out returned,out total,out truncated,out incomplete); Check(returned==10 && total==91 && truncated && incomplete,"item bound must stop and signal");
   var cycle=new Cycle{name="root"}; cycle.next=cycle; var cycleResult=(Dictionary<string,object>)ComponentCrudCore.DescribeValue(cycle,20,out returned,out total,out truncated,out incomplete); Check(incomplete && cycleResult.ContainsKey("__incomplete"),"cycle must be explicit");
   var chain=new Chain(); chain.next=new Chain(); chain.next.next=new Chain(); chain.next.next.next=new Chain(); chain.next.next.next.next=new Chain(); var chainResult=(Dictionary<string,object>)ComponentCrudCore.DescribeValue(chain,20,out returned,out total,out truncated,out incomplete); Check(incomplete && (bool)chainResult["__incomplete"],"depth must be explicit");
   var unsupported=(Dictionary<string,object>)ComponentCrudCore.DescribeValue(new Unsupported(),20,out returned,out total,out truncated,out incomplete); Check(incomplete && unsupported.ContainsKey("__type"),"unmarked DTO must not be treated as complete");
   var duplicate=(Dictionary<string,object>)ComponentCrudCore.DescribeValue(new Child(),20,out returned,out total,out truncated,out incomplete); Check(incomplete && duplicate.ContainsKey("__duplicateField"),"inherited duplicate fields must not overwrite");
   var scalar=ComponentCrudCore.DescribeValue(new Vector3{x=1,y=2,z=3},20,out returned,out total,out truncated,out incomplete); Check(!incomplete && scalar != null,"known geometry remains supported");
   var baselineRows=(List<object>)BaselineCrud.DescribeValue(values,200,out returned,out total,out truncated); Check(baselineRows.Count==91 && baselineRows[0] is string && ((string)baselineRows[0]).Contains("ParameterConfig"),"HEAD baseline must fail typed DTO readback");
   Console.WriteLine("PASS bounded production structured projection"); return 0;
  }
 }
}
'''.replace("BASELINE_ACTUAL", baseline_actual).replace(" ACTUAL", " "+actual)
    cs = tmp_path / "Probe.cs"
    cs.write_text(seams, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(dotnet), str(compilers[-1]), "/nologo", "/target:exe", "/langversion:latest", f"/out:{dll}"]
    command += [f"/r:{path}" for path in refs[-1].glob("*.dll")]
    compiled = subprocess.run(command + [str(cs)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}), encoding="utf-8")
    return dotnet, dll


def test_bounded_projection_executes_production_code(structured_probe):
    dotnet, dll = structured_probe
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS bounded production structured projection" in result.stdout
