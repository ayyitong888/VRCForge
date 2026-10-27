"""Run the real slot resolver/batch planner with counted Unity-facing ports.

Counts are calls to production evidence entry points, not Unity IO timings.
The parent Unity fixture covers the actual Core compilation and persistence.
"""
import os
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method


ROOT = Path(__file__).resolve().parents[1]


def compile_and_run(program, output):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = output / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [f"-r:{newtonsoft}", str(cs)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, output / "Newtonsoft.Json.dll")
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    return subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)


def source(path):
    ref = os.environ.get("VRCFORGE_IDENTITY_REUSE_GIT_REF")
    if ref:
        return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True, encoding="utf-8")
    return (ROOT / path).read_text(encoding="utf-8")


def test_renderer_identity_and_scene_evidence_reused_within_each_observation(tmp_path):
    slot = source("Assets/VRCForge/Editor/RendererMaterialSlotTool.cs")
    batch = source("Assets/VRCForge/Editor/Generic/UnityRendererMaterialSlotsBatch.cs")
    selected = "\n".join(method(batch, signature) for signature in (
        "private sealed class Plan", "private static string Text", "private static JObject SceneEvidence",
        "private static JObject RendererEvidence", "internal static JArray BuildExpected",
        "internal static void Verify", "private static Plan Build", "private static JArray ReadRenderers",
    ))
    readback_call = "ReadRenderers(first, saved)" if "Plan plan, SavedSceneSnapshot" in batch else "ReadRenderers(first)"
    program = r'''
using System; using System.Linq; using System.Collections.Generic; using Newtonsoft.Json.Linq;
class Material { public string Path; }
class Renderer { public string Path, Id; public Material[] sharedMaterials; }
class GameObject { public Renderer Value; public T[] GetComponents<T>() { return new[] {(T)(object)Value}; } }
class SavedSceneSnapshot { public string Path, Guid="guid", FileDigest="before", MetaDigest="meta", MetaIdentity="identity"; public int Handle=1; public object Scene; }
class RendererComponentIdentityEvidence { public Renderer renderer; public string rendererPath, componentId, componentType="Renderer"; public int componentIndex; }
static class RendererComponentIdentity {
 public static int Calls;
 public static RendererComponentIdentityEvidence Create(Renderer r) { Calls++; return new RendererComponentIdentityEvidence { renderer=r,rendererPath=r.Path,componentId=r.Id }; }
}
static class EditorUtility { public static bool IsDirty(object value) => false; }
static class SceneObjectCopyCore {
 public static int Reads; public static string Digest="before";
 public static readonly Dictionary<string,Renderer> Renderers = new Dictionary<string,Renderer>();
 public static SavedSceneSnapshot ResolveSavedScene(string path,string label) { Reads++; return new SavedSceneSnapshot {Path=path,Scene=path,FileDigest=Digest}; }
 public static GameObject ResolveUniqueGameObject(object scene,string path,string label) { return new GameObject { Value=Renderers[path] }; }
}
static class RendererMaterialSlotTool {
 internal sealed class Target { internal SavedSceneSnapshot Scene; internal Renderer Renderer; internal RendererComponentIdentityEvidence Identity; }
 internal sealed class MaterialEvidence { internal Material Material; internal string Path, Guid="material-guid", Digest="material-digest"; }
 static string FindScenePath(string path) => path.StartsWith("Other/") ? "Assets/Other.unity" : "Assets/Main.unity";
 internal static string NormalizeMaterialPath(string path) => path;
 internal static MaterialEvidence ReadMaterial(string path,string label) => new MaterialEvidence {Path=path,Material=new Material {Path=path}};
 internal static MaterialEvidence ReadMaterialAsset(Material value,string label) => ReadMaterial(value.Path,label);
 internal static string MaterialIdentity(Material value) => value == null ? "null" : value.Path+"|material-guid";
 internal static object MaterialPayload(MaterialEvidence value) => value == null ? null : new {assetPath=value.Path,assetGuid=value.Guid,fileDigest=value.Digest};
SLOT_METHOD
}
class Probe {
BATCH_METHODS
 static void Check(bool ok,string label) { if (!ok) throw new Exception(label); Console.WriteLine("PASS "+label); }
 static bool Reject(Action action) { try { action(); return false; } catch(InvalidOperationException) { return true; } }
 static JArray Rows() => JArray.Parse(@"[
 {'rendererPath':'Avatar/A','rendererComponentId':'aaa','slotIndex':0,'newMaterialAssetPath':'Assets/New.mat'},
 {'rendererPath':'Avatar/A','rendererComponentId':'aaa','slotIndex':1,'newMaterialAssetPath':'Assets/New.mat'},
 {'rendererPath':'Avatar/B','rendererComponentId':'bbb','slotIndex':0,'newMaterialAssetPath':'Assets/New.mat'}]");
 public static int Main() {
  SceneObjectCopyCore.Renderers["Avatar/A"]=new Renderer {Path="Avatar/A",Id="aaa",sharedMaterials=new Material[2]};
  SceneObjectCopyCore.Renderers["Avatar/B"]=new Renderer {Path="Avatar/B",Id="bbb",sharedMaterials=new Material[1]};
  SceneObjectCopyCore.Renderers["Other/C"]=new Renderer {Path="Other/C",Id="ccc",sharedMaterials=new Material[1]};
  var first=Build(Rows());
  Console.WriteLine("COUNTS first scene="+SceneObjectCopyCore.Reads+" renderer="+RendererComponentIdentity.Calls);
  Check(SceneObjectCopyCore.Reads==1,"one scene observation for three assignments");
  Check(RendererComponentIdentity.Calls==2,"one identity per unique renderer");
  Check(first.Targets.Count==2 && ((JArray)first.Evidence["assignments"]).Count==3,"plan preserves assignments");
  var second=Build(Rows());
  Check(SceneObjectCopyCore.Reads==2 && RendererComponentIdentity.Calls==4,"fresh observation at next Build boundary");
  Verify(first.Evidence,second.Evidence);
  SceneObjectCopyCore.Digest="after";
  var saved=SceneObjectCopyCore.ResolveSavedScene("Assets/Main.unity","saved");
  var reads=SceneObjectCopyCore.Reads; var identities=RendererComponentIdentity.Calls;
  var readback=READBACK;
  Check(SceneObjectCopyCore.Reads==reads && RendererComponentIdentity.Calls==identities+2,"readback uses fresh saved scene and fresh renderer identities");
  Check(readback.Count==2,"readback retains both renderers");
  Check(Reject(()=>Verify(first.Evidence,Build(Rows()).Evidence)),"scene drift remains rejected at next boundary");
  var other=Rows(); other[2]["rendererPath"]="Other/C";other[2]["rendererComponentId"]="ccc";
  Check(Reject(()=>Build(other)),"cross-scene batch remains rejected");
  var bad=Rows();bad[1]["rendererPath"]="Avatar/B";
  Check(Reject(()=>Build(bad)),"same ID with wrong path remains rejected");
  return 0;
 }
}
'''.replace("SLOT_METHOD", method(slot, "internal static SavedSceneSnapshot ResolveTarget"))
    program = program.replace("BATCH_METHODS", selected).replace("READBACK", readback_call)
    result = compile_and_run(program, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COUNTS first scene=1 renderer=2" in result.stdout
