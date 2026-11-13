"""Run the production registry in fresh processes with no shared SessionState."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method


def test_completed_and_interrupted_receipts_survive_editor_process_restart(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "Assets/VRCForge/Editor/EditorStateTools.cs").read_text(encoding="utf-8-sig")
    registry = method(source, "internal static class UnityAsyncJobRegistry")
    core = (root / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8-sig")
    writer = method(core, "static void WriteJsonAtomically(")
    program = """
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
static class SessionState {
    static readonly Dictionary<string, string> State = new Dictionary<string, string>();
    public static string GetString(string key, string fallback) => State.TryGetValue(key, out var value) ? value : fallback;
    public static void SetString(string key, string value) => State[key] = value;
    public static void EraseString(string key) => State.Remove(key);
}
static class Debug { public static void LogWarning(string message) => Console.WriteLine(message); }
class Probe {
    static void Check(bool value, string label) { if (!value) throw new Exception(label); }
    static int Main(string[] args) {
        var root = args[1];
        UnityAsyncJobRegistry.RestoreMcpWriteReceipts(root);
        if (args[0] == "write") {
            var completed = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-done", root);
            var jobId = (string)completed["jobId"];
            UnityAsyncJobRegistry.MarkRunning(jobId);
            UnityAsyncJobRegistry.CompleteMcpWrite(jobId, JObject.Parse("{\\"isError\\":false,\\"result\\":42}"));
            File.WriteAllText(Path.Combine(root, "original-job"), jobId);
            var interrupted = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-running", root);
            UnityAsyncJobRegistry.MarkRunning((string)interrupted["jobId"]);
        } else {
            var completed = UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-done");
            Check((string)completed?["status"] == "done", "completed receipt lost after restart");
            Check((string)completed["jobId"] == File.ReadAllText(Path.Combine(root, "original-job")), "job ID changed");
            Check((int)completed["result"]["result"] == 42, "result lost");
            var repeat = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-done", root);
            Check(JToken.DeepEquals(repeat, completed), "existing execution ID created another job");
            var interrupted = UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-running");
            Check((string)interrupted?["status"] == "unknown", "interrupted write reported as running or completed");
            Check(interrupted["result"] == null, "interrupted write invented a result");
            UnityAsyncJobRegistry.Sweep(DateTime.UtcNow.AddHours(1));
            Check(UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-done") == null, "expired receipt still served");
        }
        return 0;
    }
}
"""
    base = Path(os.environ.get("DOTNET_ROOT", Path.home() / "AppData/Local/Microsoft/dotnet"))
    compilers = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    dotnet = base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not dotnet.is_file() or not compilers or not references:
        pytest.skip("Local .NET 8 SDK/reference pack required")
    newtonsoft = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    source_file = tmp_path / "Probe.cs"
    source_file.write_text(
        program + "\n" + registry + "\nstatic class VRCForgeMcpCoreServer { internal " + writer + "}",
        encoding="utf-8",
    )
    assembly = tmp_path / "Probe.dll"
    compiled = subprocess.run(
        [str(dotnet), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{assembly}"]
        + [f"-r:{path}" for path in references[-1].glob("*.dll")]
        + [f"-r:{newtonsoft}", str(source_file)],
        capture_output=True, text=True, timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / newtonsoft.name)
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({
        "runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}},
    }), encoding="utf-8")
    project = tmp_path / "Project"
    project.mkdir()
    for mode in ("write", "read"):
        result = subprocess.run(
            [str(dotnet), str(assembly), mode, str(project)], capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
