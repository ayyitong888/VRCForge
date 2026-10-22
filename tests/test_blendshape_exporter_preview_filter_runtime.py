"""Compile the exporter ownership filter against authored and transient scene doubles."""
from pathlib import Path
import os
import subprocess

from test_wardrobe_avatar_identity_runtime import _extract

ROOT = Path(__file__).resolve().parents[1]


def _source(path):
    ref = os.environ.get("VRCFORGE_AVATAR_READER_GIT_REF")
    return (subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True, encoding="utf8")
            if ref else (ROOT / path).read_text(encoding="utf-8-sig"))


def test_blendshape_exporter_filters_editor_preview_proxies_but_keeps_authored_duplicates(tmp_path):
    exporter = _source("Assets/VRCForge/Editor/BlendshapeExporter.cs")
    methods = "\n".join(_extract(exporter, signature) for signature in [
        "private static bool IsSceneObject",
        "private static bool IsAuthoredSceneObject",
        "private static bool HasDontSaveInEditor",
    ] if signature in exporter).replace("private static bool IsSceneObject", "public static bool IsSceneObject", 1)
    program = r'''
using System; using System.Collections.Generic; using System.Linq;
using UnityEngine; using UnityEditor; using UnityEditor.SceneManagement;
namespace UnityEngine {
 public class Object { public HideFlags hideFlags; }
 [Flags] public enum HideFlags { None=0, HideInHierarchy=1, DontSaveInEditor=4, DontSave=52 }
 public struct Scene { public bool isLoaded; public string name; public bool IsValid()=>true; }
 public class GameObject : Object { public string name; public bool activeSelf=true; public Scene scene; public Transform transform; }
 public class Component : Object { public GameObject gameObject; public Transform transform; }
 public class Transform : Component { public Transform parent; public string name; }
 public class SkinnedMeshRenderer : Component { }
}
namespace UnityEditor { public static class EditorUtility { public static bool IsPersistent(object o)=>false; } }
namespace UnityEditor.SceneManagement { public static class EditorSceneManager { public static bool IsPreviewScene(Scene scene)=>scene.name=="Preview"; } }
static class Exporter { ''' + methods + r''' }
static class Probe {
 static int failures;
 static void Check(bool value,string label) { if(!value) { Console.WriteLine("FAIL "+label); failures++; } }
 static SkinnedMeshRenderer Renderer(string scene, HideFlags rendererFlags=HideFlags.None, HideFlags gameObjectFlags=HideFlags.None, HideFlags ancestorFlags=HideFlags.None, bool inactive=false) {
  var rootGo=new GameObject {name="Avatar",scene=new Scene {name=scene,isLoaded=true},hideFlags=ancestorFlags};
  var root=new Transform {name="Avatar",gameObject=rootGo}; rootGo.transform=root; root.transform=root;
  var go=new GameObject {name="Face",scene=rootGo.scene,hideFlags=gameObjectFlags,activeSelf=!inactive};
  var child=new Transform {name="Face",gameObject=go,parent=root}; go.transform=child; child.transform=child;
  return new SkinnedMeshRenderer {gameObject=go,transform=child,hideFlags=rendererFlags};
 }
 static int Main() {
      Check(Exporter.IsSceneObject(Renderer("Scene")),"authored renderer accepted");
      Check(Exporter.IsSceneObject(Renderer("Scene",inactive:true)),"inactive authored renderer accepted");
      Check(Exporter.IsSceneObject(Renderer("Scene",gameObjectFlags:HideFlags.HideInHierarchy)),"hidden authored renderer accepted");
      Check(!Exporter.IsSceneObject(Renderer("Scene",gameObjectFlags:HideFlags.DontSaveInEditor)),"proxy renderer GO rejected");
      Check(!Exporter.IsSceneObject(Renderer("Scene",rendererFlags:HideFlags.DontSaveInEditor)),"proxy renderer component rejected");
      Check(!Exporter.IsSceneObject(Renderer("Scene",ancestorFlags:HideFlags.DontSaveInEditor)),"proxy ancestor rejected");
      Check(!Exporter.IsSceneObject(Renderer("Preview")),"preview scene rejected");
  var first=Renderer("Scene"); var second=Renderer("Scene");
      Check(Exporter.IsSceneObject(first)&&Exporter.IsSceneObject(second),"real duplicate authored roots retained");
  return failures==0?0:1;
 }
}
'''
    (tmp_path / "Program.cs").write_text(program, encoding="utf8")
    (tmp_path / "probe.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        '<TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>', encoding="utf8")
    result = subprocess.run(["dotnet", "run", "--project", str(tmp_path / "probe.csproj"), "--nologo"],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
