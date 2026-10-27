"""Replay production Core authorization methods across both queue stages (no Unity)."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_accepted_async_write_executes_once_and_rejects_replay(tmp_path):
    source_path = Path(os.environ.get("VRCFORGE_ASYNC_AUTH_SOURCE", ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs"))
    source = source_path.read_text(encoding="utf-8-sig")
    signatures = [
        "private static bool IsInvocationStillAuthorized(",
        "private static bool IsMcpWriteAuthorizedAtExecution(",
        "private static bool ValidateApprovedExecutionContext(",
        "private static bool ValidateExternalMcpExecutionContext(\n            JObject context,\n            string toolName,\n            JObject arguments,\n            out string failureCode)",
        "private static bool ValidateManagedWriteExecutionContext(",
        "private static bool ConsumeApprovedExecutionId(",
        "private static void PurgeExpiredExecutionIds(",
        "private static string RequiredBoundedString(",
        "private static bool IsLowerHex(",
    ]
    methods = "\n".join(method(source, signature) for signature in signatures)
    program = r'''
using System; using System.Collections.Generic; using Newtonsoft.Json.Linq;
enum VRCForgeCommandAccess { ReadOnly, Write }
class VRCForgeToolDescriptor { public VRCForgeCommandAccess Permission = VRCForgeCommandAccess.Write; }
static class VRCForgeMcpToolContract { public static bool IsExpectedDescriptor(VRCForgeToolDescriptor d) => d != null; }
class Probe {
 enum InvocationLane { DirectRead, AppPreview, AppSafetyControl, AppSetupOutfitPoll, AppUnityPackageImportPoll, AppBuildTestPoll, AppAvatarUploadPoll, ApprovedWrite, ExternalMcpWrite }
 class PendingInvocation { public InvocationLane Lane; public JObject ExecutionContext, Arguments; public string ToolName; public bool AsyncWrite; }
 static readonly object Gate = new object();
 static readonly Dictionary<string,long> ConsumedExecutionExpirations = new Dictionary<string,long>();
 static readonly Queue<KeyValuePair<string,long>> ConsumedExecutionOrder = new Queue<KeyValuePair<string,long>>();
 static readonly HashSet<string> ApprovedAppCoreTools = new HashSet<string>{"test_write"};
 const int ApprovedExecutionMaxLifetimeMilliseconds = 120000, ApprovedExecutionClockSkewMilliseconds = 5000, MaxConsumedExecutionIds = 4096;
 static string LastPeerReverifyFailure, descriptorInstanceId = "instance";
 static string GetProjectRoot() => "project";
 static string ComputeProjectId(string root) => "project-id";
 static string ComputeCanonicalJsonHash(JToken args) => new string('a',64);
 static bool ValidateExecutionTargetContext(JObject c,bool live,out string failure) { failure=""; return true; }
 static bool ReverifyManagedPeer(PendingInvocation p) => true;
 static bool IsStrictNoWritePayloadRead(string n,JObject a) => false;
 static bool HasAllowedPreviewRequest(string n,JObject a) => true;
 static bool IsStrictSafetyControlRequest(string n,JObject a) => true;
 static bool IsStrictSetupOutfitJobPoll(string n,JObject a) => true;
 static bool IsStrictUnityPackageImportJobPoll(string n,JObject a) => true;
 static bool IsStrictBuildTestJobPoll(string n,JObject a) => true;
 static bool IsStrictAvatarUploadJobPoll(string n,JObject a) => true;
 static bool ValidateManagedAppInstanceContext(JObject c) => true;
 METHODS
 static int failures;
 static void Check(bool condition,string name) { Console.WriteLine((condition?"PASS ":"FAIL ")+name); if(!condition) failures++; }
 static PendingInvocation Create(InvocationLane lane,bool asyncWrite,string id) {
   var now=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
   var c=new JObject { ["executionId"]=id,["targetTool"]="test_write",["unityToolName"]="test_write",["argumentsSha256"]=new string('a',64),["projectId"]="project-id",["instanceId"]="instance",["issuedAtUnixMs"]=now-1000,["expiresAtUnixMs"]=now+60000 };
   if(lane==InvocationLane.ExternalMcpWrite) c["operationId"]="operation";
   else { c["approvalId"]="approval"; c["checkpointId"]="checkpoint"; }
   return new PendingInvocation { Lane=lane,AsyncWrite=asyncWrite,ExecutionContext=c,Arguments=new JObject(),ToolName="test_write" };
 }
 static int Main() {
   var descriptor=new VRCForgeToolDescriptor();
   foreach(var lane in new[]{InvocationLane.ApprovedWrite,InvocationLane.ExternalMcpWrite}) {
     var pending=Create(lane,true,lane.ToString());
     Check(IsInvocationStillAuthorized(pending,descriptor),lane+" first queue accepts");
     int executed=0;
     if(IsMcpWriteAuthorizedAtExecution(pending,descriptor)) executed++;
     Check(executed==1,lane+" accepted async handler can run");
     Check(!IsMcpWriteAuthorizedAtExecution(pending,descriptor),lane+" duplicate execution rejected");
     Check(!IsInvocationStillAuthorized(pending,descriptor),lane+" external resubmission rejected");
     var expired=Create(lane,true,lane+"-expired");
     Check(IsInvocationStillAuthorized(expired,descriptor),lane+" delayed queue accepts initially");
     expired.ExecutionContext["expiresAtUnixMs"]=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()-1;
     Check(!IsMcpWriteAuthorizedAtExecution(expired,descriptor),lane+" expired queued work rejected");
     var sync=Create(lane,false,lane+"-sync");
     Check(IsInvocationStillAuthorized(sync,descriptor),lane+" sync still executes");
     Check(!IsInvocationStillAuthorized(sync,descriptor),lane+" sync replay still rejected");
   }
   return failures==0?0:1;
 }
}
'''.replace("METHODS", methods)
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
    assert result.stdout.count("PASS ") == 16
