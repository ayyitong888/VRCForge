"""Focused Core-runtime probes for the read-only recovery witness lane."""

from pathlib import Path

from test_material_checkpoint_safety_lane import ROOT, _run_safety_probe, _safety_predicate_methods


def test_recovery_evidence_route_is_red_before_green(tmp_path):
    server = (ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
    methods, checkpoint_class = _safety_predicate_methods(server)
    program = '''using System; using System.Collections.Generic; using System.Linq; using Newtonsoft.Json.Linq;
''' + checkpoint_class + '''
class Probe {
''' + methods + '''
static void Check(bool value,string name){if(!value)throw new Exception(name);}
static JObject Valid(){return new JObject { ["projectPath"]="D:/Actual",
 ["checkpointFiles"]=new JArray("Assets/Recovery.controller"), ["recoveryEvidenceOnly"]=true };}
static int Main(){
 var args=Valid(); Check(IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"valid witness rejected");
 args=Valid(); args["checkpointFiles"]=new JArray(); Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"empty witness scope accepted");
 args=Valid(); args.Remove("checkpointFiles"); Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"missing witness scope accepted");
 args=Valid(); args["recoveryEvidenceOnly"]="true"; Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"string witness flag accepted");
 args=Valid(); args["checkpointFiles"]=new JArray("Assets/../Recovery.controller"); Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"traversal witness scope accepted");
 args=new JObject { ["projectPath"]="D:/Actual", ["recoveryEvidenceOnly"]=false };
 Check(IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"false witness flag changed normal route");
 args=Valid(); args["extra"]=true; Check(!IsStrictSafetyControlRequest("vrc_prepare_checkpoint",args),"unknown witness field accepted");
 Console.WriteLine("PASS recovery witness route"); return 0; }}
'''
    assert "PASS recovery witness route" in _run_safety_probe(tmp_path, program)


def test_recovery_evidence_writer_fails_closed_without_full_json_response(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs").read_text(encoding="utf-8")
    start = source.index("    internal sealed class CheckpointEvidenceFiles")
    end = source.index("\n    [VRCForgeCommand(", start)
    writer = source[start:end]
    result = (ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs").read_text(encoding="utf-8")
    program = '''using System; using System.IO; using System.Linq; using System.Text;
using System.Collections.Generic; using System.Security.Cryptography; using Newtonsoft.Json; using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;
''' + result.replace("using System;", "").replace("using Newtonsoft.Json.Linq;", "") + writer + '''
class Probe {
static void Check(bool value,string name){if(!value)throw new Exception(name);}
static int Main(){
 var root=Path.Combine(Path.GetTempPath(),"vrc-witness-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
 try{
  using(var files=new CheckpointEvidenceFiles(root,
      ()=>new object[]{new JObject{["complete"]=false,["serializedState"]="opaque"}},
      "recoveryEvidence",true)){
   files.RecordBefore(new object[]{new JObject{["complete"]=false,["serializedState"]="opaque"}});
   var result=files.Complete(VRCForgeToolResult.Completed("ok",new {recoveryEvidenceOnly=true}));
   Check(!result.IsSuccessful,"incomplete witness succeeded");
   Check(result.ErrorCode=="checkpoint_recovery_evidence_incomplete","wrong witness error");
   var wire=result.ToStructuredContent().ToString(Formatting.None);
   Check(!wire.Contains("serializedState"),"full JSON leaked into response");
   var data=(JObject)result.ToStructuredContent()["data"];
   Check(data["recoveryEvidenceBefore"]!=null && data["recoveryEvidenceAfter"]!=null,"witness references missing");
   Check(data["recoveryEvidenceComplete"].Value<bool>()==false,"incomplete witness marked complete");
  }
  Check(Directory.GetFiles(Path.Combine(root,"Library/VRCForge/CheckpointEvidence")).Length==2,"witness files not published");
  Console.WriteLine("PASS recovery witness writer"); return 0;
 } finally { Directory.Delete(root,true); }
}}
'''
    assert "PASS recovery witness writer" in _run_safety_probe(tmp_path, program)


def test_recovery_witness_source_is_read_only_and_captures_global_state():
    source = (ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs").read_text(encoding="utf-8")
    start = source.index("internal static object CaptureRecoveryWitness")
    witness = source[start : source.index("private static string Fingerprint", start)]
    assert "EditorSceneManager.SaveScene" not in witness
    assert "EditorSceneManager.CloseScene" not in witness
    assert "EditorSceneManager.NewScene" not in witness
    assert "AssetDatabase.ImportAsset" not in witness
    assert "AssetDatabase.Refresh" not in witness
    for marker in (
        "dirtyCount", "serializedState", "serializedFingerprint", "loadedScenes",
        "CurrentInstanceId", "coreInstanceId", "coreProductIdentity",
        "unityProcessId", "unityProcessStartedAtUtc", "projectPathDigest", "complete",
    ):
        assert marker in witness
    assert "!string.IsNullOrWhiteSpace(coreInstanceId)" in witness
