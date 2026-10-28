"""Replay the MA writer's nested reference conversion with the original JSON shape."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = "Assets/VRCForge/Editor/MAComponentWriter.cs"


def _csharp_block(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    quoted = False
    verbatim = False
    escaped = False
    line_comment = False
    block_comment = False
    pos = opening
    while pos < len(source):
        char = source[pos]
        next_char = source[pos + 1] if pos + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                pos += 1
        elif quoted:
            if escaped:
                escaped = False
            elif not verbatim and char == "\\":
                escaped = True
            elif char == '"':
                if verbatim and next_char == '"':
                    pos += 1
                else:
                    quoted = False
        elif char == "/" and next_char == "/":
            line_comment = True
            pos += 1
        elif char == "/" and next_char == "*":
            block_comment = True
            pos += 1
        elif char == '"':
            quoted = True
            verbatim = pos > 0 and source[pos - 1] == "@"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:pos + 1]
        pos += 1
    raise AssertionError(f"unterminated C# block: {signature}")


def _source() -> str:
    ref = os.environ.get("VRCFORGE_MA_NESTED_REFERENCE_GIT_REF")
    if ref:
        result = subprocess.run(
            ["git", "show", f"{ref}:{SOURCE_PATH}"], cwd=ROOT,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout
    return (ROOT / SOURCE_PATH).read_text(encoding="utf-8-sig")


def _compile_and_run(source: str, output: Path) -> subprocess.CompletedProcess[str]:
    dotnet = shutil.which("dotnet")
    dotnet_root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack are required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    if not newtonsoft.is_file():
        pytest.skip("SDK Newtonsoft.Json assembly unavailable")

    scalar_signature = (
        "private static object ConvertScalar(JToken token, Type targetType, Transform avatarRoot, string fieldPath)"
        if "private static object ConvertScalar(JToken token, Type targetType, Transform avatarRoot, string fieldPath)" in source
        else "private static object ConvertScalar(JToken token, Type targetType)"
    )
    methods = [_csharp_block(source, scalar_signature)]
    common_signatures = (
        "private enum ReferenceKind",
        "private static ReferenceKind ClassifyReference",
        "private static bool TryResolveReference",
        "private static void ApplyReference",
        "private static bool IsSceneComponent",
        "private static GameObject ResolveSceneObject",
        "private static string GetTransformPath",
        "private static string RelativePath",
        "private static string NormalizePath",
        "private static object GetMemberValue",
        "private static void SetMemberValue",
        "private class AssignmentPlan",
    )
    for signature in common_signatures:
        methods.append(_csharp_block(source, signature))
    # Old source does not have these two nested-reference helpers. Their absence is
    # only a compilation adaptation; the test still runs the same success assertions.
    for signature in (
        "private sealed class NestedReferenceJsonConverter",
        "private static void SetAvatarObjectReference",
    ):
        if signature in source:
            methods.append(_csharp_block(source, signature))

    stubs = r'''
using System; using System.Collections.Generic; using System.Linq; using System.Reflection;
using Newtonsoft.Json; using Newtonsoft.Json.Linq; using UnityEngine; using UnityEditor;
namespace UnityEngine {
 public class Object { public string name; }
 public struct Scene { public bool isLoaded; public bool IsValid()=>true; }
 public class Component : Object { public GameObject gameObject; public Transform transform=>gameObject==null?null:gameObject.transform; }
 public class GameObject : Object {
  public Transform transform; public Scene scene=new Scene{isLoaded=true}; private readonly Dictionary<Type,Component> components=new Dictionary<Type,Component>();
  public GameObject(string n){name=n;transform=new Transform{name=n,gameObject=this};Resources.Items.Add(transform);}
  public Component GetComponent(Type t)=>components.TryGetValue(t,out var value)?value:null;
  public void AddComponent(Component c){c.gameObject=this;components[c.GetType()]=c;}
 }
 public class Transform : Component {
  public Transform parent;
  public bool IsChildOf(Transform root){for(var t=parent;t!=null;t=t.parent)if(ReferenceEquals(t,root))return true;return false;}
  public Transform Find(string path)=>null;
 }
 public class Material : Object {}
 public class Texture : Object {}
 public static class Resources { public static readonly List<Transform> Items=new List<Transform>(); public static T[] FindObjectsOfTypeAll<T>()=>Items.Cast<T>().ToArray(); }
}
namespace UnityEditor {
 using UnityEngine;
 public static class AssetDatabase {
  public static readonly Dictionary<string,Object> Assets=new Dictionary<string,Object>();
  public static Object LoadAssetAtPath(string path,Type type)=>Assets.TryGetValue(path,out var asset)&&type.IsInstanceOfType(asset)?asset:null;
 }
 public static class EditorUtility { public static bool IsPersistent(Object value)=>false; }
}
[Serializable] class AvatarObjectReference { public string referencePath; public GameObject targetObject; public void Set(GameObject target){targetObject=target;referencePath=target.name;} }
class MaterialSwitchObject { public AvatarObjectReference Object; public Material Material; public int MaterialIndex; public ChangeType ChangeType; }
enum ChangeType { Set, Add }
class Holder : Component { public AvatarObjectReference Object; public Material Material; }
'''
    runner = r'''
 static int failures;
 static void Check(bool value,string name){Console.WriteLine((value?"PASS ":"FAIL ")+name);if(!value)failures++;}
 static GameObject root, body;
 static JObject Request(string materialPath)=>JObject.Parse("{ 'Objects':[{'Object':{'referencePath':'Body'},'MaterialIndex':0,'ChangeType':'Set','Material':'"+materialPath+"'}] }");
 static int Main(){
  root=new GameObject("Avatar"); body=new GameObject("Body"); body.transform.parent=root.transform;
  var material=new Material{name="Test body"}; UnityEditor.AssetDatabase.Assets["Assets/TestBody.mat"]=material;
  var token=Request("Assets/TestBody.mat")["Objects"];
  List<MaterialSwitchObject> rows=null; Exception positiveError=null;
  try{rows=(List<MaterialSwitchObject>)ConvertField(token,typeof(List<MaterialSwitchObject>),root.transform,"fields.Objects");}catch(Exception ex){positiveError=ex;}
  if(positiveError!=null)Console.WriteLine("Original nested request conversion exception: "+positiveError);
  Check(positiveError==null&&rows!=null&&rows.Count==1&&ReferenceEquals(rows[0].Material,material)&&rows[0].MaterialIndex==0&&rows[0].ChangeType==ChangeType.Set,"original nested MaterialSwitchObject JSON resolves scene and asset references");
  Check(rows!=null&&rows.Count==1&&rows[0].Object.referencePath=="Body"&&ReferenceEquals(rows[0].Object.targetObject,body),"nested AvatarObjectReference resolves to the requested avatar object");
  var cleared=(List<MaterialSwitchObject>)ConvertField(JObject.Parse("{'Objects':[{'Object':null,'Material':null,'MaterialIndex':0}]}" )["Objects"],typeof(List<MaterialSwitchObject>),root.transform,"fields.Objects");
  Check(cleared[0].Object==null&&cleared[0].Material==null,"explicit nested null clears both reference members");
  var unchanged=new MaterialSwitchObject{Material=material}; var target=new MaterialSetter{Objects=new List<MaterialSwitchObject>{unchanged}}; var before=target.Objects;
  string error=null; try{ConvertField(Request("Assets/Missing.mat")["Objects"],typeof(List<MaterialSwitchObject>),root.transform,"fields.Objects");}catch(Exception ex){error=ex.ToString();}
  Check(error!=null&&error.Contains("fields.Objects[0].Material")&&error.Contains("Assets/Missing.mat")&&ReferenceEquals(target.Objects,before),"missing asset reports JSON path and rejects before target mutation");
  UnityEditor.AssetDatabase.Assets["Assets/Wrong.asset"]=new Texture{name="wrong"}; error=null;
  try{ConvertField(Request("Assets/Wrong.asset")["Objects"],typeof(List<MaterialSwitchObject>),root.transform,"fields.Objects");}catch(Exception ex){error=ex.ToString();}
  Check(error!=null&&error.Contains("fields.Objects[0].Material")&&error.Contains("Assets/Wrong.asset"),"wrong-type asset is rejected with exact field path");
  error=null; try{ConvertField(JObject.Parse("{'Objects':[{'Object':{'referencePath':'MissingBody'},'MaterialIndex':0,'Material':null}]}" )["Objects"],typeof(List<MaterialSwitchObject>),root.transform,"fields.Objects");}catch(Exception ex){error=ex.ToString();}
  Check(error!=null&&error.Contains("fields.Objects[0].Object")&&error.Contains("MissingBody"),"missing nested scene object is rejected with exact field path");
  var holder=new Holder(); var objectPlan=new AssignmentPlan{member=typeof(Holder).GetField("Object"),memberType=typeof(AvatarObjectReference),raw="Body",kind=ClassifyReference(typeof(AvatarObjectReference))};
  string refError; Check(TryResolveReference(objectPlan,root.transform,out refError),"top-level AvatarObjectReference preflight still resolves"); ApplyReference(holder,objectPlan);
  Check(ReferenceEquals(holder.Object.targetObject,body),"top-level AvatarObjectReference apply still uses Set");
  var materialPlan=new AssignmentPlan{member=typeof(Holder).GetField("Material"),memberType=typeof(Material),raw="Assets/TestBody.mat",kind=ClassifyReference(typeof(Material))};
  Check(TryResolveReference(materialPlan,root.transform,out refError),"top-level material reference preflight still resolves"); ApplyReference(holder,materialPlan);
  Check(ReferenceEquals(holder.Material,material),"top-level material reference apply still loads the typed asset");
  Check((int)ConvertField(JToken.FromObject(7),typeof(int),root.transform,"fields.number")==7,"scalar conversion remains unchanged");
  return failures==0?0:1;
 }
'''
    program = stubs + "\nclass MaterialSetter { public List<MaterialSwitchObject> Objects; }\nclass Probe {\n" + "\n".join(methods) + "\n" + runner + "\n}\n"
    if "private static object ConvertScalar(JToken token, Type targetType, Transform avatarRoot, string fieldPath)" not in source:
        program = program.replace(
            "static int failures;",
            "private static object ConvertField(JToken token, Type targetType, Transform avatarRoot, string fieldPath) => ConvertScalar(token,targetType);\n static int failures;",
        )
    else:
        program = program.replace(
            "static int failures;",
            "private static object ConvertField(JToken token, Type targetType, Transform avatarRoot, string fieldPath) => ConvertScalar(token,targetType,avatarRoot,fieldPath);\n static int failures;",
        )

    output.mkdir(parents=True, exist_ok=True)
    source_file = output / "Probe.cs"
    source_file.write_text(program, encoding="utf-8")
    assembly = output / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{assembly}"]
    command += [f"-r:{path}" for path in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(source_file)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, output / "Newtonsoft.Json.dll")
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    return subprocess.run([dotnet, str(assembly)], capture_output=True, text=True, timeout=30)


def test_ma_writer_nested_reference_conversion_runtime(tmp_path: Path) -> None:
    source = _source()
    completed = _compile_and_run(source, tmp_path / "csharp")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("PASS ") == 11, completed.stdout
    assert source.index("assignment.convertedValue = ConvertScalar") < source.index("// ---- APPLY")
