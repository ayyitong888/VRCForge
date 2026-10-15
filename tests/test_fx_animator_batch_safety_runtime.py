"""Run the FX batch persistence preflight helpers against small .NET stubs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets" / "VRCForge" / "Editor" / "Generic" / "UnityFxAnimatorBatch.cs"


def extract_method(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


STUBS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
namespace UnityEngine { public class Object { public bool Persistent = true; } }
public static class AssetDatabase {
    public static readonly Dictionary<UnityEngine.Object, string> Paths = new Dictionary<UnityEngine.Object, string>();
    public static UnityEngine.Object[] Objects = Array.Empty<UnityEngine.Object>();
    public static UnityEngine.Object[] LoadAllAssetsAtPath(string path) => Objects;
    public static string GetAssetPath(UnityEngine.Object value) => Paths.TryGetValue(value, out var path) ? path : "";
}
public static class EditorUtility { public static bool IsPersistent(UnityEngine.Object value) => value != null && value.Persistent; }
public static class SceneObjectCopyCore {
    public static string ToAbsoluteAssetPath(string path) => path;
}
'''


RUNNER = r'''
static int failures;
static void Check(bool value, string name) { Console.WriteLine((value ? "PASS " : "FAIL ") + name); if (!value) failures++; }
static bool Reject(Action action) { try { action(); return false; } catch (InvalidOperationException) { return true; } }
public static int Main() {
    var path = "Assets/FX.controller";
    var first = new UnityEngine.Object();
    var second = new UnityEngine.Object();
    var transient = new UnityEngine.Object { Persistent = false };
    var foreign = new UnityEngine.Object();
    AssetDatabase.Paths[first] = path;
    AssetDatabase.Paths[second] = path;
    AssetDatabase.Paths[transient] = path;
    AssetDatabase.Paths[foreign] = "Assets/Other.controller";
    AssetDatabase.Objects = new UnityEngine.Object[] { first, second, null, transient };
    Check(LoadPersistenceObjects(path, first).Length == 2, "null and nonpersistent diagnostics are ignored");
    Check(!Reject(() => ValidatePersistenceObjectSet(path, first, AssetDatabase.Objects)), "valid main object set is accepted");
    Check(Reject(() => ValidatePersistenceObjectSet(path, new UnityEngine.Object(), AssetDatabase.Objects)), "unbound main object is rejected");
    Check(Reject(() => ValidatePersistenceObjectSet(path, new UnityEngine.Object { Persistent = false }, AssetDatabase.Objects)), "nonpersistent main object is rejected");
    Check(Reject(() => ValidatePersistenceObjectSet(path, first, new UnityEngine.Object[0])), "empty object listing is rejected");
    AssetDatabase.Objects = new UnityEngine.Object[] { first, foreign };
    Check(Reject(() => LoadPersistenceObjects(path, first)), "persistent object from another path is rejected");

    var root = Path.Combine(Path.GetTempPath(), "vrcforge-fx-safety-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(root);
    var asset = Path.Combine(root, "FX.controller");
    File.WriteAllBytes(asset, new byte[] { 1, 2, 3 });
    File.WriteAllBytes(asset + ".meta", new byte[] { 4, 5, 6 });
    ProbeExclusiveAssetFiles(asset);
    Check(true, "unlocked controller and metadata pass the write probe");
    using (var held = new FileStream(asset, FileMode.Open, FileAccess.Read, FileShare.Read))
        Check(Reject(() => ProbeExclusiveAssetFiles(asset)), "shared-read lock rejects before mutation");
    using (var heldMeta = new FileStream(asset + ".meta", FileMode.Open, FileAccess.Read, FileShare.Read))
        Check(Reject(() => ProbeExclusiveAssetFiles(asset)), "metadata shared-read lock rejects before mutation");
    Check(File.ReadAllBytes(asset).SequenceEqual(new byte[] { 1, 2, 3 }), "lock probe does not modify controller bytes");
    Directory.Delete(root, true);
    return failures == 0 ? 0 : 1;
}
'''


def compile_and_run(source: str, output: Path) -> subprocess.CompletedProcess:
    dotnet = shutil.which("dotnet")
    dotnet_root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    sdks = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not sdks or not refs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack are required")
    output.mkdir(parents=True, exist_ok=True)
    selected = [
        extract_method(source, "private static UnityEngine.Object[] LoadPersistenceObjects"),
        extract_method(source, "private static void ValidatePersistenceObjectSet"),
        extract_method(source, "private static void ProbeExclusiveAssetFiles"),
    ]
    program = STUBS + "public class Probe {\n" + "\n".join(selected) + RUNNER + "\n}"
    cs = output / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [dotnet, str(sdks[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{item}" for item in refs[-1].glob("*.dll")]
    command += [str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (output / "Probe.runtimeconfig.json").write_text(
        json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}),
        encoding="utf-8",
    )
    return subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)


def test_fx_batch_preflight_rejects_incomplete_objects_and_shared_locks(tmp_path: Path) -> None:
    completed = compile_and_run(SOURCE.read_text(encoding="utf-8"), tmp_path / "csharp")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("PASS ") == 10
