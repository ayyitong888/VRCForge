"""Real assignment/scene-reader methods, Unity and stable-evidence API doubles."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_parameter_writer_runtime import _extract_method


def test_fx_assignment_rejects_unresolved_reference_before_creating_assets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    revision = os.environ.get("VRCFORGE_FX_ASSIGNMENT_GIT_REF")
    source = subprocess.check_output(["git", "show", f"{revision}:{path}"], text=True) if revision else (root / path).read_text(encoding="utf-8-sig")
    methods = "\n".join(_extract_method(source, signature) for signature in [
        "internal static AnimatorController EnsureFxController", "internal static AnimatorController GetFxController"])
    reader = source[source.index("internal static JObject ReadFxSceneReference"):source.index("        private static object DescribeAnimatorState")]
    program = r'''
using System;using System.IO;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;
class AnimatorController {public static int Created;public static AnimatorController CreateAnimatorControllerAtPath(string p){Created++;return new AnimatorController();}}
class Scene {public bool isLoaded=true,isDirty;public string path;public bool IsValid()=>true;}
class GameObject {public Scene scene=new Scene();}
class VRCAvatarDescriptor {public string name="Avatar";public GameObject gameObject=new GameObject();public CustomAnimLayer[] baseAnimationLayers=Array.Empty<CustomAnimLayer>();public enum AnimLayerType{FX=4}public struct CustomAnimLayer {public AnimLayerType type;public bool isDefault;public object animatorController;}}
struct GlobalObjectId {public ulong targetObjectId;public static GlobalObjectId GetGlobalObjectIdSlow(object o)=>new GlobalObjectId{targetObjectId=42};}
static class Undo {public static void RegisterCreatedObjectUndo(object o,string n){}}
static class EditorUtility {public static void SetDirty(object o){}}
static class GeneratedAssetPaths {public static string UniqueAssetPath(string p)=>p;public static string ValidateNewAssetPath(string p)=>p;}
static class SceneObjectCopyCore {public static void ReadStableAssetEvidence(string p,string l,Action<string,string> read){read(p,"");}}
static class EnsureAnimatorStateTool {READER}
static class Authoring {
 public static int Folders;static void EnsureAssetFolder(string p){Folders++;}static string Sanitize(string s,string fallback)=>s;
 METHODS
}
class Probe {
 static int failures;
 static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 static bool Reject(VRCAvatarDescriptor d){try{Authoring.EnsureFxController(d,"Assets/Test","Assets/Test/FX.controller");return false;}catch(InvalidOperationException){return true;}}
 static string SceneText(bool occupied)=>"--- !u!114 &42\nMonoBehaviour:\n  baseAnimationLayers:\n  - isEnabled: 0\n    type: 4\n    animatorController: {fileID: "+(occupied?"9100000, guid: "+new string('a',32)+", type: 2":"0")+"}\n  specialAnimationLayers: []\n";
 static void Reset(){Authoring.Folders=0;AnimatorController.Created=0;}
 public static int Main(string[] args){
  var descriptor=new VRCAvatarDescriptor();descriptor.gameObject.scene.path=args[0];
  File.WriteAllText(args[0],SceneText(true));Reset();
  Check(Reject(descriptor)&&Authoring.Folders==0&&AnimatorController.Created==0,"unresolved serialized reference preserved without new artifacts");
  descriptor.baseAnimationLayers=Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>();
  File.WriteAllText(args[0],SceneText(false));descriptor.gameObject.scene.isDirty=true;Reset();
  Check(Reject(descriptor)&&Authoring.Folders==0&&AnimatorController.Created==0,"dirty scene rejected before new assets");
  descriptor.baseAnimationLayers=Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>();
  descriptor.gameObject.scene.isDirty=false;Reset();
  var created=Authoring.EnsureFxController(descriptor,"Assets/Test","Assets/Test/FX.controller");
  Check(created!=null&&AnimatorController.Created==1&&ReferenceEquals(descriptor.baseAnimationLayers[0].animatorController,created),"empty saved FX slot assigned normally");
  descriptor.gameObject.scene.isDirty=true;Reset();
  Check(ReferenceEquals(Authoring.EnsureFxController(descriptor,"Assets/Test"),created)&&Authoring.Folders==0&&AnimatorController.Created==0,"existing controller reuse does not save unrelated scene edits");
  return failures==0?0:1;
 }
}
'''.replace("METHODS", methods).replace("READER", reader)
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    dotnet = shutil.which("dotnet")
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    json_dll = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    dll = tmp_path / "Probe.dll"
    cs.write_text(program, encoding="utf-8")
    command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{ref}" for ref in refs[-1].glob("*.dll")] + [f"-r:{json_dll}", str(cs)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(json_dll, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([dotnet, str(dll), str(tmp_path / "scene.txt")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
