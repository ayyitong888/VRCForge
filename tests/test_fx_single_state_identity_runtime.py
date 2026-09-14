"""Run production single-edit validation with nested Animator state stubs, not Unity."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_single_fx_edit_rejects_ambiguous_state_before_mutation(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8-sig")
    fx = source[source.index("public static class ManageFxAnimatorTool"):]
    handler = method(fx, "public static object HandleCommand(")
    assert handler.index("ValidateEdit(controller, action, @params)") < handler.index("recovery.Begin()") < handler.index("Apply(action, controller")
    selected = method(fx, "private static void ValidateEdit(")
    if "private static void ValidateStateNameIdentity(" in fx:
        selected += method(fx, "private static void ValidateStateNameIdentity(")
    core = source[:source.index("public static class ManageFxAnimatorTool")]
    lookup = method(core, "internal static AnimatorState FindState(")
    program = r'''
using System; using System.Linq; using System.Collections.Generic; using Newtonsoft.Json.Linq;
class AnimatorState {internal string name;}
class ChildAnimatorState {internal AnimatorState state;}
class ChildAnimatorStateMachine {internal AnimatorStateMachine stateMachine;}
class AnimatorStateMachine {internal ChildAnimatorState[] states=new ChildAnimatorState[0];internal ChildAnimatorStateMachine[] stateMachines=new ChildAnimatorStateMachine[0];}
class AnimatorControllerLayer {internal string name="FX";internal AnimatorStateMachine stateMachine;}
class AnimatorController {internal AnimatorControllerLayer[] layers;}
static class AvatarPrimitiveCrudCore {LOOKUP}
static class WriteAnimationCurveTool {internal static void RequireFinite(float value,string key){if(float.IsNaN(value)||float.IsInfinity(value))throw new InvalidOperationException(key);}}
class Probe {
 static string Required(JObject args,string key){var text=args[key]?.ToString();if(string.IsNullOrWhiteSpace(text))throw new InvalidOperationException(key);return text;}
 static AnimatorControllerLayer FindLayer(AnimatorController c,string name)=>c.layers.First(l=>l.name==name);
 static void ValidateTransitionPreview(AnimatorController c,JObject args){}
 static void ParseInterruptionSource(string text){}
 SELECTED
 static int failures;
 static void Check(bool value,string label){Console.WriteLine((value?"PASS ":"FAIL ")+label);if(!value)failures++;}
 static bool Reject(AnimatorController c,string action,JObject args){try{ValidateEdit(c,action,args);return false;}catch(InvalidOperationException e){return e.Message.Contains("ambiguous");}}
 static AnimatorStateMachine Machine(params string[] names)=>new AnimatorStateMachine{states=names.Select(n=>new ChildAnimatorState{state=new AnimatorState{name=n}}).ToArray()};
 static JObject Args(string name)=>new JObject{["layerName"]="FX",["stateName"]=name};
 public static int Main(){
  var root=Machine("Unique");root.stateMachines=new[]{new ChildAnimatorStateMachine{stateMachine=Machine("Idle")},new ChildAnimatorStateMachine{stateMachine=Machine("Idle")}};
  var c=new AnimatorController{layers=new[]{new AnimatorControllerLayer{stateMachine=root}}};
  foreach(var action in new[]{"ensure_state","update_state","delete_state"})Check(Reject(c,action,Args("Idle")),action+" rejects duplicate nested state");
  foreach(var action in new[]{"ensure_transition","delete_transition"}){
   var a=Args("Unique");a["sourceStateName"]="Idle";a["destinationStateName"]="Unique";Check(Reject(c,action,a),action+" rejects ambiguous source");
   a["sourceStateName"]="Unique";a["destinationStateName"]="Idle";Check(Reject(c,action,a),action+" rejects ambiguous destination");
  }
  Check(!Reject(c,"update_state",Args("Unique")),"unique state accepted even with unrelated duplicate names");
  Check(!Reject(c,"ensure_state",Args("New")),"new state creation unchanged");
  root.states=root.states.Concat(new[]{new ChildAnimatorState{state=new AnimatorState{name="Idle"}}}).ToArray();
  Check(Reject(c,"update_state",Args("Idle")),"root versus nested duplicate rejected");
  var uniqueNested=new AnimatorController{layers=new[]{new AnimatorControllerLayer{stateMachine=Machine()}}};uniqueNested.layers[0].stateMachine.stateMachines=new[]{new ChildAnimatorStateMachine{stateMachine=Machine("Nested")}};
  Check(!Reject(uniqueNested,"update_state",Args("Nested")),"unique nested state accepted");
  var duplicateLayers=new AnimatorController{layers=new[]{new AnimatorControllerLayer{stateMachine=Machine("Unique")},new AnimatorControllerLayer{stateMachine=Machine("Unique")}}};
  foreach(var action in new[]{"ensure_layer","delete_layer","ensure_state","update_state","delete_state","ensure_transition","delete_transition"})
   Check(Reject(duplicateLayers,action,Args("Unique")),action+" rejects duplicate target layer names");
  duplicateLayers.layers=duplicateLayers.layers.Concat(new[]{new AnimatorControllerLayer{name="Other",stateMachine=Machine("Unique")}}).ToArray();
  var other=Args("Unique");other["layerName"]="Other";Check(!Reject(duplicateLayers,"update_state",other),"unrelated duplicate layer names do not reject unique target layer");
  return failures==0?0:1;
 }
}
'''.replace("LOOKUP", lookup).replace("SELECTED", selected)
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
    assert result.stdout.count("PASS ") == 19
