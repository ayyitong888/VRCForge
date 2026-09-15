"""Run the exact production failure branch and cleanup; not the whole PNG tool."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_texture_patch_failure_reports_verified_absence_or_unknown(tmp_path):
    path = "Assets/VRCForge/Editor/TexturePatchTool.cs"
    revision = os.environ.get("VRCFORGE_TEXTURE_PATCH_GIT_REF")
    raw = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT,
                                  text=True, encoding="utf-8") if revision else (ROOT / path).read_text(encoding="utf-8-sig")
    handler = method(raw, "public static object HandleCommand(")
    catch = handler[handler.index("catch (Exception exception)"):]
    # Both signatures are real revisions; no synthetic old implementation.
    marker = "private static bool CleanupTarget(" if "private static bool CleanupTarget(" in raw else "private static void CleanupTarget("
    cleanup = method(raw, marker)
    program = r'''
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
namespace UnityEngine { public class Object {} }
static class AssetDatabase {
    public static bool Imported;
    public static T LoadAssetAtPath<T>(string path) where T:new() { return Imported ? new T() : default(T); }
    public static bool DeleteAsset(string path) { return false; }
}
static class File {
    public static bool Png, Meta, FailPng, FailMeta, FailRead;
    public static readonly List<string> Deletes = new List<string>();
    public static bool Exists(string path) { return path.EndsWith(".meta") ? Meta : Png; }
    public static FileAttributes GetAttributes(string path) {
        if (FailRead) throw new UnauthorizedAccessException("attribute read denied");
        if (!Exists(path)) throw new FileNotFoundException();
        return FileAttributes.Normal;
    }
    public static void Delete(string path) {
        Deletes.Add(path);
        if (path != "Output.png" && path != "Output.png.meta") throw new Exception("cleanup escaped target");
        bool meta = path.EndsWith(".meta");
        if (meta ? FailMeta : FailPng) throw new UnauthorizedAccessException(meta ? "meta locked" : "PNG locked");
        if (meta) Meta=false; else Png=false;
    }
}
static class VRCForgeToolResult {
    public static object FailedWithCode(string code,string message,object payload) { return new {code,message,data=payload}; }
    public static object RejectedBeforeMutation(string code,string message,string layer,string phase,bool retry) { return new {code,message}; }
}
class Probe {
    const string ResultSchema="vrcforge.texture_patch.v1";
    // CLEANUP
    static object Run(bool mutationStarted) {
        string sourceTexturePath="Assets/Source.png",targetTexturePath="Assets/Output.png",targetFilePath="Output.png";
        try { throw new InvalidOperationException("original pixel readback failed"); }
        // CATCH
    public static void Main() {
        foreach (var scenario in new [] {"already_absent", "delete_success", "png_locked", "meta_locked", "imported_remains", "before_mutation", "attribute_read_denied"}) {
            File.Png = scenario != "already_absent";
            File.Meta = scenario != "already_absent";
            File.FailPng = scenario == "png_locked";
            File.FailMeta = scenario == "meta_locked";
            File.FailRead = scenario == "attribute_read_denied";
            File.Deletes.Clear();
            AssetDatabase.Imported = scenario == "imported_remains";
            var result=JObject.FromObject(Run(scenario != "before_mutation"));
            Console.WriteLine(new JObject { ["scenario"]=scenario, ["png"]=File.Png, ["meta"]=File.Meta,
                ["imported"]=AssetDatabase.Imported, ["deletes"]=new JArray(File.Deletes), ["result"]=result }.ToString(Newtonsoft.Json.Formatting.None));
        }
    }
}
'''.replace("// CLEANUP", cleanup).replace("// CATCH", catch)
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = tmp_path / "Probe.cs"
    source.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}", f"-r:{newtonsoft}"]
    command += [f"-r:{reference}" for reference in refs[-1].glob("*.dll")] + [str(source)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    run = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    rows = [json.loads(line) for line in run.stdout.splitlines()]
    assert len(rows) == 7
    # Check a failed deletion first so the historical red proves false restoration,
    # independently of the additional original-error preservation assertion.
    for row in sorted(rows, key=lambda row: row["scenario"] != "png_locked"):
        assert set(row["deletes"]) <= {"Output.png", "Output.png.meta"}
        if row["scenario"] == "before_mutation":
            assert row["deletes"] == []
            assert row["result"]["code"] == "vrc_texture_patch_rejected"
            continue
        absent = not (row["png"] or row["meta"] or row["imported"]) and row["scenario"] != "attribute_read_denied"
        payload = row["result"]["data"]
        assert payload["restorationVerified"] is absent, row
        assert payload["commitState"] == ("rolled_back" if absent else "unknown"), row
        assert payload["commitStateKnown"] is absent, row
        assert payload["checkpointRecoveryRequired"] is (not absent), row
        assert "original pixel readback failed" in row["result"]["message"], row
        if not absent:
            assert payload["cleanupError"], row
