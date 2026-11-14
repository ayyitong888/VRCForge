"""Run the production registry in fresh processes with no shared SessionState."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method


@pytest.fixture(scope="module")
def compiled_receipt_probe(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("core-receipt-probe")
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
        var receipts = Path.Combine(root, "Library", "VRCForge", "WriteReceipts");
        var mode = args[0];
        if (mode == "directory-unavailable") {
            Directory.CreateDirectory(Path.GetDirectoryName(receipts));
            File.WriteAllText(receipts, "not a directory");
        }
        UnityAsyncJobRegistry.RestoreMcpWriteReceipts(root);
        if (mode == "directory-unavailable" || mode == "unreadable-receipt") {
            if (mode == "unreadable-receipt") {
                Check((string)UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-done")?["status"] == "done",
                    "a failed restore discarded readable receipts");
            }
            var job = UnityAsyncJobRegistry.Create("other", root, new JObject(), null, TimeSpan.FromMinutes(1));
            Check(job != null, "a failed receipt restore disabled regular jobs");
            try {
                UnityAsyncJobRegistry.CreateMcpWrite("write", "must-not-run", root);
                throw new Exception("write admitted without all original execution IDs loaded");
            } catch (IOException) { }
            Check(UnityAsyncJobRegistry.ReadMcpWriteSnapshot("must-not-run") == null, "unpersisted write was admitted");
        } else if (mode == "failed-save") {
            var receipt = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-failure", root);
            var id = (string)receipt["jobId"];
            UnityAsyncJobRegistry.MarkRunning(id);
            Directory.Move(receipts, receipts + "-original");
            File.WriteAllText(receipts, "cannot commit terminal receipt");
            try {
                UnityAsyncJobRegistry.CompleteMcpWrite(id, new JObject { ["isError"] = false, ["result"] = 99 });
                throw new Exception("terminal transition unexpectedly persisted");
            } catch (IOException) { }
            var retained = UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-failure");
            Check((string)retained["status"] == "running" && retained["result"] == null,
                "in-memory indexes published a result that was not saved");
            UnityAsyncJobRegistry.MarkMcpWriteUnknown("exec-failure", null);
            Check((string)UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-failure")["status"] == "unknown",
                "execution index did not reflect failed persistence");
            Check((string)UnityAsyncJobRegistry.Poll(id)["status"] == "unknown",
                "job index did not reflect failed persistence");
        } else if (mode == "live") {
            var receipt = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-live", root);
            var id = (string)receipt["jobId"];
            var path = Path.Combine(receipts, id + ".json");
            File.Delete(path);
            UnityAsyncJobRegistry.MarkRunning(id);
            Check(File.Exists(path), "running receipt was not persisted");
            File.Delete(path);
            UnityAsyncJobRegistry.CompleteMcpWrite(id, new JObject { ["isError"] = false, ["result"] = 99 });
            var done = UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-live");
            Check((int)done["result"]["result"] == 99, "live job transition lost result");
            Check(File.Exists(path), "terminal receipt was not persisted");
        } else if (mode == "write") {
            var completed = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-done", root);
            var jobId = (string)completed["jobId"];
            UnityAsyncJobRegistry.MarkRunning(jobId);
            UnityAsyncJobRegistry.CompleteMcpWrite(jobId, JObject.Parse("{\\"isError\\":false,\\"result\\":42}"));
            File.WriteAllText(Path.Combine(root, "original-job"), jobId);
            var interrupted = UnityAsyncJobRegistry.CreateMcpWrite("write", "exec-running", root);
            UnityAsyncJobRegistry.MarkRunning((string)interrupted["jobId"]);
            File.WriteAllText(Path.Combine(root, "interrupted-job"), (string)interrupted["jobId"]);
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
            var interruptedPath = Path.Combine(receipts, File.ReadAllText(Path.Combine(root, "interrupted-job")) + ".json");
            Check((string)JObject.Parse(File.ReadAllText(interruptedPath))["status"] == "running",
                "restore rewrote immutable history rather than projecting its unknown state");
            UnityAsyncJobRegistry.Sweep(DateTime.UtcNow.AddYears(1));
            Check(JToken.DeepEquals(UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-done"), completed),
                "Core write receipt was purged by age");
            Check((string)UnityAsyncJobRegistry.ReadMcpWriteSnapshot("exec-running")?["status"] == "unknown",
                "interrupted receipt was purged by age");
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
    def run(mode, project):
        result = subprocess.run(
            [str(dotnet), str(assembly), mode, str(project)], capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return run


def test_completed_and_interrupted_receipts_survive_editor_process_restart(tmp_path, compiled_receipt_probe):
    compiled_receipt_probe("write", tmp_path)
    # Receipts saved by the previous Core may still carry an elapsed deadline.
    job_id = (tmp_path / "original-job").read_text()
    receipt = tmp_path / "Library" / "VRCForge" / "WriteReceipts" / f"{job_id}.json"
    old_record = json.loads(receipt.read_text(encoding="utf-8-sig"))
    old_record["purge_after_utc"] = "2000-01-01T00:00:00.0000000Z"
    receipt.write_text(json.dumps(old_record), encoding="utf-8")
    compiled_receipt_probe("read", tmp_path)


@pytest.mark.parametrize("mode", ["directory-unavailable", "unreadable-receipt"])
def test_mcp_receipt_restore_failure_leaves_known_reads_available_and_refuses_writes(
    tmp_path, compiled_receipt_probe, mode,
):
    if mode == "unreadable-receipt":
        compiled_receipt_probe("write", tmp_path)
        job_id = (tmp_path / "interrupted-job").read_text()
        receipt = tmp_path / "Library" / "VRCForge" / "WriteReceipts" / f"{job_id}.json"
        receipt.write_text("damaged receipt with a missing execution ID", encoding="utf-8")
    compiled_receipt_probe(mode, tmp_path)


def test_live_mcp_job_transitions_use_memory_and_still_persist(tmp_path, compiled_receipt_probe):
    compiled_receipt_probe("live", tmp_path)


def test_failed_mcp_terminal_save_never_publishes_a_result(tmp_path, compiled_receipt_probe):
    compiled_receipt_probe("failed-save", tmp_path)
