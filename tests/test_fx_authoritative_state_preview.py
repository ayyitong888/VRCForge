"""Execute the production bounded state projection; Unity loading is checked separately."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_state_preview_reads_actual_before_and_does_not_mutate(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8-sig")
    fx = source[source.index("public static class ManageFxAnimatorTool"):]
    selected = method(fx, "internal static JObject BuildStateChange(")
    program = r'''
using System; using Newtonsoft.Json.Linq;
class AnimatorState {public string name="Fist",timeParameter="";public float speed=1f;public bool writeDefaultValues=true,timeParameterActive;public object motion;}
static class AssetDatabase { public static string GetAssetPath(object item)=>item==null?"":"Assets/Existing.anim";}
static class AvatarPrimitiveCrudCore { public static string NormalizeAssetPath(string path)=>path;}
class Probe {
 SELECTED
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static int Main(){
  var state=new AnimatorState{speed=1.25f};
  var args=new JObject{["stateName"]="Fist",["speed"]=.75f,["newName"]=" New ",["writeDefaults"]=false};
  var change=BuildStateChange(state,args);
  Check((float)change["before"]["speed"]==1.25f,"authoritative before ignores requested new value");
  Check((float)change["after"]["speed"]==.75f,"after applies requested speed");
  Check((string)change["after"]["name"]=="New" && !(bool)change["after"]["writeDefaultValues"],"other supported fields projected");
  Check(state.speed==1.25f && state.name=="Fist" && state.writeDefaultValues,"preview never mutates state");
  args["expectedSpeed"]=1f;
  try{BuildStateChange(state,args);Check(false,"wrong expected speed refused");}catch(InvalidOperationException){Check(true,"wrong expected speed refused");}
  args.Remove("expectedSpeed");
  var created=BuildStateChange(null,args);Check(created["before"].Type==JTokenType.Null && (float)created["after"]["speed"]==.75f,"new state is explicit");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    compile_probe(tmp_path, program, 6)


def compile_probe(tmp_path, program, expected_checks):
    base = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / "AppData/Local/Microsoft/dotnet")))
    compiler = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))[-1]
    dotnet = base / "dotnet.exe"
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs";cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(dotnet), str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs.glob("*.dll")]
    compiled = subprocess.run(command + [f"-r:{newtonsoft}", str(cs)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == expected_checks


def test_state_precondition_reaches_preview_and_frozen_write(monkeypatch):
    import dashboard_server as d
    from unity_shared_input_schemas import MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA
    args = {"projectPath": "/fixture", "controllerPath": "Assets/Test.controller", "action": "update_state",
            "layerName": "Left Hand", "stateName": "Fist", "speed": .75,
            "expectedSpeed": 1, "expectedStateDigest": "a" * 64}
    schema = MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["properties"]
    assert {"expectedSpeed", "expectedStateDigest"} <= schema.keys()
    frozen = d.AGENT_GATEWAY._write_handlers["vrcforge_manage_fx_animator"].approved_execution_plan_builder(args)
    calls = []
    monkeypatch.setattr(d, "load_dashboard_settings", lambda _: object())
    monkeypatch.setattr(d, "invoke_unity_mcp", lambda _settings, tool, request, **kw: calls.append((tool,request)) or type("R", (), {"payload": {"ok": True}})())
    d.manage_fx_animator_sync(args, preview=True)
    assert calls[0][1] == {**frozen[0][1], "preview": True}
    assert calls[0][1]["expectedSpeed"] == 1
    assert calls[0][1]["expectedStateDigest"] == "a" * 64


def test_native_preview_exposes_before_after_without_a_result_read(tmp_path, monkeypatch):
    import dashboard_server as d
    from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
    for name in ("Assets", "Packages", "ProjectSettings"):
        (tmp_path / name).mkdir()
    (tmp_path / "Packages/manifest.json").write_text("{}")
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n")
    gateway, model, _ = setup_gateway(tmp_path, [
        call("preview", "vrcforge_preview_manage_fx_animator", {
            "controllerPath": "Assets/Fixture.controller", "action": "update_state",
            "layerName": "Left Hand", "stateName": "Fist", "speed": .75,
        }), finish,
    ])
    handler = d.AGENT_GATEWAY._tools["vrcforge_preview_manage_fx_animator"].handler
    monkeypatch.setattr(d, "load_dashboard_settings", lambda _: object())
    raw = {"ok": True, "preview": True, "plan": {"stateName": "Fist"},
           "stateChange": {"before": {"speed": 1.25}, "after": {"speed": .75}},
           "expectedStateDigest": "a" * 64}
    monkeypatch.setattr(d, "invoke_unity_mcp", lambda *_a, **_kw: type("R", (), {"payload": raw})())
    gateway.register_tool("vrcforge_preview_manage_fx_animator", "when-to-use: preview FX. when-NOT-to-use: write.", "plan/preview", handler)
    response = run(gateway, tmp_path)
    visible = json.dumps(model.requests[-1]["messages"])
    assert "before" in visible and "1.25" in visible and "0.75" in visible, (len(model.requests), response.get("plan"), response.get("steps"))
    assert "expectedStateDigest" in visible
    assert not any(step.get("tool") == "vrcforge_read_tool_result" for step in response["steps"])
