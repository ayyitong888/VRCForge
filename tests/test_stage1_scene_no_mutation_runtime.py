"""Compiled production failure producers -> unchanged backend recovery predicate.

This is an offline receipt replay, not Unity save or reload acceptance. Optional
VRCFORGE_SCENE_FAILURE_RECEIPT selects a retained real incident evidence file.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_approval_transactions import _confirmed_no_write_failure, _write_failure_facts
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("VRCFORGE_SCENE_BEFORE_SOURCE",
    ROOT / "Assets/VRCForge/Editor/Stage1SceneTools.cs"))


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    output = tmp_path_factory.mktemp("scene-no-mutation")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet = shutil.which("dotnet")
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK and reference pack required")
    source = SOURCE.read_text(encoding="utf-8-sig")
    helpers = "\n".join(method(source, signature) for signature in (
        "internal static object NoMutation(", "internal static object UnknownAfterMutation("))
    catches = []
    for name in ("SceneSaveTool", "SceneTransitionTool"):
        body = source.split("public static class " + name, 1)[1]
        catches.append(method(body, "catch (Exception ex)"))
    program = '''using System; using System.IO; using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;
static class Stage1SceneToolCore {
 internal const string SaveSchema="vrcforge.scene_save.v1";
 internal const string TransitionSchema="vrcforge.scene_transition.v1";
''' + helpers + "}\nclass Probe {\n"
    for name, catch in zip(("Save", "Transition"), catches):
        program += f'''static object {name}(bool mutationStarted,string message) {{
var action="save";var project="C:/Project";
try {{throw new InvalidOperationException(message);}} {catch} }}\n'''
    program += '''static JObject Receipt(object value) {
 var result=(VRCForgeToolResult)value;
 var output=JObject.FromObject(result);
 output["wire"]=new JObject { ["isError"]=!result.IsSuccessful,
  ["structuredContent"]=result.ToStructuredContent() };
 return output;
}
static void Main(string[] args) {
 var input=JObject.Parse(File.ReadAllText(args[0]));
 var message=(string)input["message"];
 Console.WriteLine(new JObject {
  ["save"]=Receipt(Save(false,message)),
  ["transition"]=Receipt(Transition(false,message)),
  ["late"]=Receipt(Save(true,message)),
  ["noop"]=Receipt(VRCForgeToolResult.Completed("no change",
   Stage1SceneToolCore.NoMutation("vrcforge.scene_save.v1","save","C:/Project","digest","no change",true)))
 }.ToString(Newtonsoft.Json.Formatting.None));
}}
'''
    cs = output / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    compiler = compilers[-1]
    dependency = compiler.parents[2] / "Newtonsoft.Json.dll"
    dll = output / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{item}" for item in refs[-1].glob("*.dll")]
    command += [f"-r:{dependency}", str(cs), str(ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs")]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(dependency, output / dependency.name)
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}))
    return dotnet, dll, output


@pytest.fixture(scope="module")
def replay(compiled):
    original = {"schema": "vrcforge.scene_save.v1", "operation": "save", "ok": False,
                "mutationStarted": False, "commitState": "not_started", "committed": None,
                "checkpointRecoveryRequired": False,
                "message": "The approved scene preview evidence changed before apply."}
    receipt = os.environ.get("VRCFORGE_SCENE_FAILURE_RECEIPT")
    if receipt:
        evidence = json.loads(Path(receipt).read_text(encoding="utf-8"))
        original = evidence["toolResults"][0]["parsedParts"][0]["structuredEvidence"]["data"]
    assert original["mutationStarted"] is False and original.get("committed") is None
    assert original["commitState"] == "not_started"
    message = original.get("message") or original.get("error")
    path = compiled[2] / "input.json"
    path.write_text(json.dumps({"message": message}), encoding="utf-8")
    result = subprocess.run([compiled[0], str(compiled[1]), str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return original, json.loads(result.stdout)


def facts(payload, wire=None):
    # Match the Core MCP error envelope, rather than an arbitrary nested data
    # object (the backend deliberately does not trust that shape).
    wire = wire or {"isError": True, "structuredContent": {"success": False,
                   "code": "scene_save_rejected", "data": payload}}
    return _write_failure_facts(wire, failure_layer="write_handler",
        verification_baseline={}, checkpoint={"id": "checkpoint", "ok": True},
        recovery={"id": "recovery"}, no_write_conflict=False,
        core_call_audits=[{"toolName": "vrc_scene_save", "inputSha256": "a" * 64}])


@pytest.mark.parametrize("operation,schema", [("save", "vrcforge.scene_save.v1"),
                                              ("transition", "vrcforge.scene_transition.v1")])
def test_actual_catch_releases_only_confirmed_no_write(replay, operation, schema):
    original, produced = replay
    assert not _confirmed_no_write_failure(facts(original))
    result = produced[operation]
    assert result["IsSuccessful"] is False
    payload = result["Payload"]
    assert payload["schema"] == schema and payload["ok"] is False
    assert payload["preview"] is False and payload["verified"] is False
    assert payload["changed"] is False and payload["applyBinding"] == {} and payload["readback"] == {}
    assert payload["committed"] is False
    assert payload["checkpointRecoveryRequired"] is False
    projected = facts(payload, result["wire"])
    assert _confirmed_no_write_failure(projected), projected
    assert projected["toolRoutingStarted"] is True


def test_late_failure_keeps_recovery(replay):
    payload = replay[1]["late"]["Payload"]
    assert payload["mutationStarted"] is True and payload["commitState"] == "unknown"
    assert payload["checkpointRecoveryRequired"] is True
    assert not _confirmed_no_write_failure(facts(payload))


def test_successful_noop_stays_successful(replay):
    result = replay[1]["noop"]
    assert result["IsSuccessful"] is True and result["ErrorCode"] == ""
    assert result["wire"]["isError"] is False
    assert result["wire"]["structuredContent"]["success"] is True
    payload = result["Payload"]
    assert payload["ok"] is True and payload["message"] == "no change"
    assert payload["previewDigest"] == "digest" and payload["changed"] is False
    assert payload["mutationStarted"] is False and payload["committed"] is False


def test_no_mutation_callers_are_bounded():
    source = SOURCE.read_text(encoding="utf-8-sig")
    assert source.count("Stage1SceneToolCore.NoMutation(") == 2
