"""Replay production queues using editor ticks while Unity's game frame stays fixed."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_async_write_runs_on_next_editor_tick_without_game_frame_advance(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8-sig")
    signatures = ["private sealed class PendingInvocation", "internal static void EnsureInvocationPumpRegistered(",
                  "private static void DrainInvocations(", "private static void DrainMcpWriteExecutions("]
    if "private static void OnEditorUpdate(" in source:
        signatures.append("private static void OnEditorUpdate(")
    if "private static void QueueMcpWriteExecution(" in source:
        signatures.append("private static void QueueMcpWriteExecution(")
    methods = "\n".join(method(source, signature) for signature in signatures)
    program = r'''
using System; using System.Collections.Concurrent; using System.Net.Sockets; using System.Reflection; using System.Threading; using Newtonsoft.Json.Linq;
static class EditorApplication { public static Action update; public static void Tick() => update?.Invoke(); }
static class Time { public static int frameCount=0; }
class VRCForgeMcpPeerProcessEvidence {}
class Descriptor { public MethodInfo Handler=typeof(Probe).GetMethod("Handler"); }
static class VRCForgeMcpToolContract { public static bool IsExpectedDescriptor(Descriptor d) => d!=null; }
static class UnityAsyncJobRegistry {
 public static JObject Snapshot;
 public static JObject CreateMcpWrite(string tool,string id,string root) => Snapshot=new JObject { ["jobId"]="job",["executionId"]=id,["status"]="queued" };
 public static void MarkRunning(string id) { Snapshot["status"]="running"; }
 public static void CompleteMcpWrite(string id,JObject result) { Snapshot["status"]="done"; Snapshot["result"]=result; }
 public static void MarkMcpWriteUnknown(string id,string error) { throw new Exception(error); }
}
class Probe {
 enum InvocationLane { ApprovedWrite }
 static readonly ConcurrentQueue<PendingInvocation> PendingInvocations=new ConcurrentQueue<PendingInvocation>();
 static readonly ConcurrentQueue<PendingInvocation> PendingMcpWriteExecutions=new ConcurrentQueue<PendingInvocation>();
 static readonly ConcurrentDictionary<string,PendingInvocation> McpWriteSubmissions=new ConcurrentDictionary<string,PendingInvocation>();
 static SynchronizationContext editorSynchronizationContext;
 static long editorUpdateTick;
 static string LastPeerReverifyFailure;
 static string GetProjectRoot()=>"project";
 static Descriptor FindTool(string name)=>new Descriptor();
 static bool IsInvocationStillAuthorized(PendingInvocation p,Descriptor d)=>true;
 static bool IsMcpWriteAuthorizedAtExecution(PendingInvocation p,Descriptor d)=>true;
 static JObject AsyncWriteSubmissionResponse(JObject snapshot)=>(JObject)snapshot.DeepClone();
 static JObject ToolError(params object[] args)=>new JObject { ["isError"]=true };
 static JObject ToolResult(object value,bool modern)=>new JObject { ["isError"]=false };
 static int calls,failures;
 public static object Handler(JObject args) { calls++; return new object(); }
 METHODS
 static void Check(bool value,string label) { Console.WriteLine((value?"PASS ":"FAIL ")+label);if(!value)failures++; }
 static int Main() {
   EnsureInvocationPumpRegistered();
   var pending=new PendingInvocation { ToolName="write",Arguments=new JObject(),ExecutionContext=new JObject(),AsyncWrite=true,Modern=true,ExecutionId="exec" };
   PendingInvocations.Enqueue(pending);
   EditorApplication.Tick();
   Check(calls==0 && (string)UnityAsyncJobRegistry.Snapshot["status"]=="queued","first editor tick acknowledges without executing");
   Check(pending.Completion.IsSet,"submission completion released before execution");
   EditorApplication.Tick();
   Check(calls==1 && (string)UnityAsyncJobRegistry.Snapshot["status"]=="done","next editor tick executes despite static game frame");
   EditorApplication.Tick();
   Check(calls==1,"later ticks do not execute twice");
   Check(Time.frameCount==0,"game frame never advanced");
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
    newtonsoft = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    compiled = subprocess.run([str(dotnet), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
        + [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [f"-r:{newtonsoft}", str(cs)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 5
