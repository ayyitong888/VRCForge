"""Compile and execute the production SetupOutfit dispatch methods with Unity seams."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SETUP_SOURCE = ROOT / "Assets" / "VRCForge" / "Editor" / "SetupOutfitTool.cs"
CORE_SOURCE = ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"


def extract_method(source: str, marker: str) -> str:
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(marker)


def extract_class(source: str, marker: str) -> str:
    return extract_method(source, marker)


def build_program() -> str:
    setup = SETUP_SOURCE.read_text(encoding="utf-8-sig")
    core = CORE_SOURCE.read_text(encoding="utf-8-sig")
    job = extract_class(setup, "private sealed class SetupOutfitJob")
    schedule = extract_method(setup, "private static void ScheduleSetupJob")
    run = extract_method(setup, "private static void RunSetupJob")
    post = extract_method(core, "internal static bool TryPostToEditor")
    return f'''using System;
using System.Collections.Generic;
using System.Threading;
using Newtonsoft.Json.Linq;

static class EditorApplication {{
    public delegate void CallbackFunction();
    public static event CallbackFunction update;
    public static void DrainUpdate() {{ var current = update; update = null; current?.Invoke(); }}
}}
static class VRCForgeMcpCoreServer {{
    private static SynchronizationContext editorSynchronizationContext;
    public static void SetContext(SynchronizationContext value) => editorSynchronizationContext = value;
    {post}
}}
static class SetupOutfitTool {{
    {job}
    private static readonly object JobLock = new object();
    private static readonly Dictionary<string, SetupOutfitJob> Jobs = new Dictionary<string, SetupOutfitJob>();
    private static readonly HashSet<string> ScheduledJobIds = new HashSet<string>(StringComparer.Ordinal);
    private static int RunCount;
    private static void PersistJob(SetupOutfitJob job) {{ }}
    private static JObject ExecuteConfirmedSetup(SetupOutfitJob job) {{
        if (job.outfitGlobalObjectId != ActualObjectIds[job.jobId]) throw new InvalidOperationException("Setup Outfit target identity drifted after approval.");
        RunCount++; job.mutationStarted = true; return new JObject();
    }}
    private static void CompleteJob(string jobId, string status, JObject payload) {{ Jobs[jobId].status = status; Jobs[jobId].result = payload; }}
    {schedule}
    {run}
    public static void AddPending(string id, string expected, string actual) {{ Jobs[id] = new SetupOutfitJob {{ jobId = id, status = "pending", outfitGlobalObjectId = expected, approvedObjectReceiptNonce = "nonce" }}; ActualObjectIds[id] = actual; }}
    private static readonly Dictionary<string, string> ActualObjectIds = new Dictionary<string, string>();
    public static void Schedule(string id) {{ ScheduleSetupJob(id); }}
    public static string Status(string id) => Jobs[id].status;
    public static bool Mutated(string id) => Jobs[id].mutationStarted;
    public static int Runs => RunCount;
}}

sealed class PostedContext : SynchronizationContext {{ public readonly Queue<Action> Queue = new Queue<Action>(); public override void Post(SendOrPostCallback d, object state) => Queue.Enqueue(() => d(state)); public void Drain() {{ while (Queue.Count > 0) Queue.Dequeue()(); }} }}
static class Probe {{
    static void Require(bool ok, string message) {{ if (!ok) throw new Exception(message); }}
    public static int Main() {{
        var context = new PostedContext(); VRCForgeMcpCoreServer.SetContext(context);
        SetupOutfitTool.AddPending("post", "object-1", "object-1"); SetupOutfitTool.Schedule("post"); SetupOutfitTool.Schedule("post");
        EditorApplication.DrainUpdate(); context.Drain();
        Require(SetupOutfitTool.Status("post") == "completed" && SetupOutfitTool.Mutated("post") && SetupOutfitTool.Runs == 1, "Post dispatch was not exactly once");
        VRCForgeMcpCoreServer.SetContext(null);
        SetupOutfitTool.AddPending("fallback", "object-1", "object-1"); SetupOutfitTool.Schedule("fallback"); EditorApplication.DrainUpdate();
        Require(SetupOutfitTool.Status("fallback") == "completed" && SetupOutfitTool.Runs == 2, "update fallback failed");
        VRCForgeMcpCoreServer.SetContext(context);
        SetupOutfitTool.AddPending("identity", "object-1", "object-2"); SetupOutfitTool.Schedule("identity"); context.Drain();
        Require(SetupOutfitTool.Status("identity") == "error" && !SetupOutfitTool.Mutated("identity") && SetupOutfitTool.Runs == 2, "identity failure mutated");
        Console.WriteLine("PASS production extracted SetupOutfit dispatch"); return 0;
    }}
}}
'''


@pytest.fixture(scope="module")
def compiled_probe(tmp_path_factory):
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet"
    sdks = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    newtonsoft = base / "sdk" / "3.1.426" / "Newtonsoft.Json.dll"
    if not sdks or not refs or not newtonsoft.exists() or not (base / "dotnet.exe").exists():
        pytest.skip("local .NET 3.1 Roslyn references unavailable")
    folder = tmp_path_factory.mktemp("setup-outfit-production-dispatch")
    source = folder / "Probe.cs"; dll = folder / "Probe.dll"
    source.write_text(build_program(), encoding="utf-8")
    command = [str(base / "dotnet.exe"), str(sdks[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
    command += [f"-r:{ref}" for ref in refs[-1].glob("*.dll")] + [f"-r:{newtonsoft}", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    (folder / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    shutil.copy2(newtonsoft, folder / "Newtonsoft.Json.dll")
    return base / "dotnet.exe", dll


def test_setup_outfit_production_dispatch_runtime(compiled_probe):
    result = subprocess.run([str(compiled_probe[0]), str(compiled_probe[1])], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS production extracted SetupOutfit dispatch" in result.stdout
