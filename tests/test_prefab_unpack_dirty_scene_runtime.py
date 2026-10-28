"""Run the production saved-scene resolver with modeled Unity scene ports.

This exercises the dirty-scene exception used only by completely unpack; it
does not load Unity or modify project assets. The handler source check pins the
identity/receipt/Undo/save ordering around that resolver.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def _method(source, signature):
    match = re.search(signature, source)
    assert match, f"production method not found: {signature}"
    start = source.rfind("\n", 0, match.start()) + 1
    brace = source.find("{", match.end())
    assert brace >= 0
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated production method")


def test_dirty_scene_resolver_is_strict_except_for_completely_unpack(tmp_path):
    source = _source("Assets/VRCForge/Editor/SceneObjectCopyCore.cs")
    strict = _method(source, r"internal static SavedSceneSnapshot ResolveSavedScene\(string rawPath, string label\)")
    unpack = _method(source, r"internal static SavedSceneSnapshot ResolveSavedSceneForPrefabUnpack\(GameObject gameObject\)")
    shared = _method(source, r"private static SavedSceneSnapshot ResolveSavedScene\(string rawPath, string label, bool allowDirtyScene, bool includeContentDigests\)")
    harness = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;
class SceneObjectCopyException : Exception { public SceneObjectCopyException(string m):base(m){} }
class SavedSceneSnapshot { public Scene Scene; public string Path, Guid, FileDigest, FileIdentity, MetaDigest, MetaIdentity; public int Handle; }
class StableFileEvidence { public string Digest, Identity; }
class StableAssetEvidence { public string Guid; public StableFileEvidence File, Meta; }
static class Probe {
 public static List<Scene> Scenes = new List<Scene>();
 public static StableAssetEvidence ReadStableAssetEvidence(string path,string label) => new StableAssetEvidence { Guid="synthetic-guid", File=new StableFileEvidence{Digest="file-digest",Identity="file-id"}, Meta=new StableFileEvidence{Digest="meta-digest",Identity="meta-id"} };
 public static string NormalizeSceneAssetPath(string value,string label) { if (String.IsNullOrWhiteSpace(value) || !value.StartsWith("Assets/") || !value.EndsWith(".unity") || value.Contains("\\") || value.Contains("..")) throw new SceneObjectCopyException("invalid scene path"); return value; }
 public static string NormalizeSlashes(string value) => (value ?? "").Replace('\\','/');
}
namespace UnityEngine { public class GameObject { public UnityEngine.SceneManagement.Scene scene; } }
namespace UnityEngine.SceneManagement {
 public struct Scene { public bool valid,isLoaded,isDirty; public string path; public int handle; public bool IsValid()=>valid; }
 public static class SceneManager { public static int sceneCount=>Probe.Scenes.Count; public static Scene GetSceneAt(int i)=>Probe.Scenes[i]; }
}
static class SceneObjectCopyCore {
 static string NormalizeSceneAssetPath(string p,string l)=>Probe.NormalizeSceneAssetPath(p,l);
 static string NormalizeSlashes(string p)=>Probe.NormalizeSlashes(p);
 static StableAssetEvidence ReadStableAssetEvidence(string p,string l,bool includeContentDigests=true)=>Probe.ReadStableAssetEvidence(p,l);
 // production methods
 STRICT
 UNPACK
 SHARED
}
class Program {
 static Scene Target(bool dirty=true) => new Scene{valid=true,isLoaded=true,isDirty=dirty,path="Assets/Synthetic.unity",handle=17};
 static void Throws(Action a,string expected){try{a();}catch(SceneObjectCopyException e){if(e.Message==expected)return;throw;}throw new Exception("expected resolver rejection: "+expected);}
 static void Main(){
  Probe.Scenes=new List<Scene>{Target(true)};
  Throws(()=>SceneObjectCopyCore.ResolveSavedScene("Assets/Synthetic.unity","ordinary"),"The selected scene has unsaved changes.");
  var result=SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(new GameObject{scene=Target(true)});
  if(result.Path!="Assets/Synthetic.unity"||result.Guid!="synthetic-guid"||result.Handle!=17||result.FileIdentity!="file-id")throw new Exception("complete-unpack did not bind to the loaded saved scene evidence");
  Probe.Scenes=new List<Scene>{Target(false)};
  if(SceneObjectCopyCore.ResolveSavedScene("Assets/Synthetic.unity","ordinary").Handle!=17)throw new Exception("clean strict resolution regressed");
  Throws(()=>SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(null),"Prefab unpack requires one loaded saved scene object.");
  var invalid=Target();invalid.valid=false;
  Throws(()=>SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(new GameObject{scene=invalid}),"Prefab unpack requires one loaded saved scene object.");
  var unloaded=Target();unloaded.isLoaded=false;
  Throws(()=>SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(new GameObject{scene=unloaded}),"Prefab unpack requires one loaded saved scene object.");
  var unsaved=Target();unsaved.path="";
  Throws(()=>SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(new GameObject{scene=unsaved}),"Prefab unpack requires one loaded saved scene object.");
  Probe.Scenes=new List<Scene>{Target(),Target()};
  Throws(()=>SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(new GameObject{scene=Target()}),"The saved scene selector is missing or ambiguous.");
  var other=Target();other.path="Assets/Other.unity";Probe.Scenes=new List<Scene>{other};
  Throws(()=>SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(new GameObject{scene=Target()}),"The saved scene selector is missing or ambiguous.");
  Console.WriteLine("PASS production dirty-scene resolver: complete-only exception, strict default, identity and ambiguity guards");
 }
}
'''.replace("STRICT", strict).replace("UNPACK", unpack).replace("SHARED", shared)
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    packs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not packs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack required")
    cs = tmp_path / "Probe.cs"
    cs.write_text(harness, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    built = subprocess.run([dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"] + [f"-r:{x}" for x in packs[-1].glob("*.dll")] + [str(cs)], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}), encoding="utf-8")
    run = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS production dirty-scene resolver" in run.stdout


def test_unpack_handler_checks_identity_before_resolver_then_consumes_receipt_and_saves():
    source = _source("Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs")
    block = source[source.index("public static class UnpackPrefabTool"):]
    handler = _method(block, r"public static object HandleCommand\(JObject @params\)")
    resolver = handler.index("SceneObjectCopyCore.ResolveSavedSceneForPrefabUnpack(go)")
    for guard in ("expectedGlobalObjectId", "expectedPrefabGuid", "expectedAssetDependencyHash", "expectedScenePath", "IsOutermostPrefabInstanceRoot(go)"):
        assert handler.index(guard) < resolver, f"{guard} must precede scene resolver"
    assert re.search(r"var beforeScene = completely\s*\? SceneObjectCopyCore\.ResolveSavedSceneForPrefabUnpack\(go\)\s*: ComponentCrudCore\.ResolveSavedSceneFor\(go\);", handler)
    assert resolver < handler.index("VRCForgeApprovedObjectReceipt.Consume(")
    assert handler.index("VRCForgeApprovedObjectReceipt.Consume(") < handler.index("PrefabUtility.UnpackPrefabInstance(")
    assert handler.index("PrefabUtility.UnpackPrefabInstance(") < handler.index("ComponentCrudCore.SaveAndResolveScene(beforeScene)")
    assert "var beforeScene = completely" in handler and "ResolveSavedSceneFor(go)" in handler
