"""Compile actual Avatar reader selectors and exporter against bounded scene doubles."""
from pathlib import Path
import os
import subprocess

from test_wardrobe_avatar_identity_runtime import _extract, _public

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    ref = os.environ.get("VRCFORGE_AVATAR_READER_GIT_REF")
    return (subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True, encoding="utf8")
            if ref else (ROOT / path).read_text(encoding="utf-8-sig"))


def test_avatar_readers_reject_ambiguous_identity_and_list_shapeless_avatars(tmp_path):
    shared = source("Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs")
    classes = []
    for label, path, marker in [
        ("Descriptor", "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs", "internal static VRCAvatarDescriptor ResolveAvatarDescriptor"),
        ("Controls", "Assets/VRCForge/Editor/AvatarControlScanner.cs", "private static Component ResolveAvatarDescriptor"),
        ("Performance", "Assets/VRCForge/Editor/AvatarPerformanceTool.cs", "private static Component ResolveAvatarDescriptor"),
    ]:
        text = source(path)
        methods = [_public(text, marker)]
        for signature in ["private static string GetTransformPath", "private static string NormalizePath", "private static Type FindType", "private static bool IsSceneComponent"]:
            if signature in text:
                methods.append(_extract(text, signature))
        if label == "Descriptor":
            methods += [_extract(text, "internal static string GetTransformPath"), _extract(text, "internal static string NormalizePath")]
        classes.append("static class " + label + " { " + "\n".join(methods) + " }")
    shared_methods = "\n".join(_extract(shared, s) for s in ["internal static VRCAvatarDescriptor ResolveAvatarDescriptor", "internal static string GetTransformPath", "internal static string NormalizePath"])
    exporter = source("Assets/VRCForge/Editor/BlendshapeExporter.cs")
    items = source("Assets/VRCForge/Editor/GameObjectTools.cs")
    item_methods = "\n".join(_public(items, s) for s in ["private static void AddRootIfMatches", "private static string GetTransformPath", "private static string NormalizePath"])
    classes.append("static class ItemRoots {" + item_methods + "}")
    exporter_methods = []
    for signature in ["private static ExportPayload BuildPayload", "private static bool IsSceneObject", "private static Transform FindAvatarRoot", "private static bool HasVrChatAvatarDescriptor", "private static Type FindType", "private static string GetTransformPath", "private static string GetRelativePath", "private static AvatarExport AddAvatarRoot"]:
        if signature in exporter:
            exporter_methods.append(_extract(exporter, signature).replace("private static ExportPayload", "public static ExportPayload", 1))
    for signature in ["private class ExportPayload", "private class ExportSummary", "private class AvatarExport", "private class RendererExport", "private class BlendshapeExport"]:
        exporter_methods.append(_extract(exporter, signature).replace("private class", "public class", 1))
    program = r'''
using System; using System.IO; using System.Linq; using System.Collections.Generic;
using UnityEngine; using UnityEditor; using VRC.SDK3.Avatars.Components;
namespace UnityEngine {
 public class Object {}
 public class Scene { public bool isLoaded=true; public string name="Scene",path="Assets/Scene.unity"; public bool IsValid()=>true; }
 public class GameObject : Object { public Scene scene=new Scene(); public string name; public Transform transform; }
 public class Component : Object { public GameObject gameObject; public Transform transform; public string name=>gameObject.name; }
 public class Transform : Component { public Transform parent; public new string name; public Transform root=>parent==null?this:parent.root;
   public object GetComponent(Type t)=>Resources.Items.OfType<Component>().FirstOrDefault(x=>x.transform==this&&t.IsInstanceOfType(x));
   public T GetComponent<T>() where T:class=>GetComponent(typeof(T)) as T; }
 public class Animator : Component {}
 public class Mesh { public int blendShapeCount=1; public string name="Mesh"; public string GetBlendShapeName(int i)=>"Shape"; }
 public class SkinnedMeshRenderer : Component { public Mesh sharedMesh=new Mesh(); public float GetBlendShapeWeight(int i)=>0; }
 public static class Resources { public static List<Object> Items=new List<Object>(); public static T[] FindObjectsOfTypeAll<T>()=>Items.OfType<T>().ToArray(); public static Object[] FindObjectsOfTypeAll(Type t)=>Items.Where(t.IsInstanceOfType).ToArray(); }
 public static class Application { public static string dataPath="C:/Project/Assets"; }
 public static class Mathf { public static float Clamp01(float v)=>Math.Max(0,Math.Min(1,v)); }
}
namespace UnityEditor { public static class EditorUtility { public static bool IsPersistent(object o)=>false; } }
namespace VRC.SDK3.Avatars.Components { public class VRCAvatarDescriptor : Component {} }
''' + "\n".join(classes) + "\nstatic class AvatarAuthoringCrudCore {" + shared_methods + "}\nstatic class Exporter {" + "\n".join(exporter_methods) + r'''}
static class Probe {
 static int failures;
 static void Check(bool v,string label) { if(!v) { Console.WriteLine("FAIL "+label); failures++; } }
 static VRCAvatarDescriptor Avatar(string path,bool shapes=false) {
  Transform parent=null;
  foreach(var segment in path.Split('/')) { var go=new GameObject {name=segment}; var t=new Transform {name=segment,parent=parent,gameObject=go}; go.transform=t;t.transform=t;parent=t; }
  var d=new VRCAvatarDescriptor {gameObject=parent.gameObject,transform=parent};Resources.Items.Add(d);
  if(shapes) Resources.Items.Add(new SkinnedMeshRenderer {gameObject=parent.gameObject,transform=parent});return d;
 }
 static void Resolver(string label,Func<string,Component> f) {
  foreach(var c in new[]{("",new[]{"Root/A","Other/B"},false),("Avatar",new[]{"One/Avatar","Two/Avatar"},false),("Root/Avatar",new[]{"Root/Avatar","Root/Avatar"},false),("",new[]{"Root/Avatar"},true),("Root/Avatar",new[]{"Root/Avatar","Other/Avatar"},true)}) {
   Resources.Items.Clear();foreach(var path in c.Item2)Avatar(path);
   try { var resolved=f(c.Item1); Check(c.Item3&&(resolved!=null),label+":"+c.Item1); } catch { Check(!c.Item3,label+":"+c.Item1); }
  }
 }
 static int Main() {
  Resolver("descriptor",Descriptor.ResolveAvatarDescriptor);Resolver("controls",Controls.ResolveAvatarDescriptor);Resolver("performance",Performance.ResolveAvatarDescriptor);
  Resources.Items.Clear();Avatar("Shapeless");Check(Exporter.BuildPayload().summary.avatarCount==1,"shapeless avatar listed");
  Resources.Items.Clear();Avatar("A",true);Avatar("B");var p=Exporter.BuildPayload();Check(p.summary.avatarCount==2&&p.summary.blendshapeCount==1,"mixed avatars retain shape counts");
  Resources.Items.Clear();Avatar("Duplicate",true);Avatar("Duplicate",true);try {Exporter.BuildPayload();Check(false,"duplicate root rejected");}catch(InvalidOperationException){}
  Resources.Items.Clear();var first=Avatar("Items").transform;var second=Avatar("Items").transform;
  var roots=new Dictionary<string,Transform>(StringComparer.OrdinalIgnoreCase);ItemRoots.AddRootIfMatches(roots,first,"");ItemRoots.AddRootIfMatches(roots,first,"");Check(roots.Count==1,"same root enumeration allowed");
  try {ItemRoots.AddRootIfMatches(roots,second,"");Check(false,"items duplicate root rejected");}catch(InvalidOperationException){}
  return failures==0?0:1;
 }
}
'''
    (tmp_path / "Program.cs").write_text(program, encoding="utf8")
    project = tmp_path / "probe.csproj"
    project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>', encoding="utf8")
    result = subprocess.run(["dotnet", "run", "--project", str(project), "--nologo"], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
