"""Exercise the production FX HandleCommand ordering with isolated Unity stubs."""
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
using Newtonsoft.Json.Linq;
namespace UnityEngine { public class Object { public bool Persistent = true; } }
public class AnimationClip : UnityEngine.Object {}
public class Transition { public bool hasFixedDuration; }
public class State { public Transition[] transitions = new Transition[] { new Transition() }; }
public class StateMachine { public Transition[] anyStateTransitions = new Transition[] { new Transition() }; }
public class AnimatorControllerLayer { public string name = "Layer"; public StateMachine stateMachine = new StateMachine(); }
public class AnimatorController : UnityEngine.Object { public AnimatorControllerLayer[] layers = new AnimatorControllerLayer[] { new AnimatorControllerLayer() }; }
public enum ImportAssetOptions { ForceSynchronousImport = 1, ForceUpdate = 2 }
public static class EditorUtility { public static bool IsDirty(UnityEngine.Object value) => false; public static bool IsPersistent(UnityEngine.Object value) => value != null && value.Persistent; public static void SetDirty(UnityEngine.Object value) {} }
public static class Undo { public static int Registers; public static void RegisterCompleteObjectUndo(UnityEngine.Object[] values, string name) { Registers++; if (values.Any(value => value == null)) throw new InvalidOperationException("null undo target"); } }
public static class AssetDatabase {
    public static AnimatorController Controller;
    public static UnityEngine.Object[] Objects = Array.Empty<UnityEngine.Object>();
    public static int Saves;
    public static string AssetPathToGUID(string path) => "guid:" + path;
    public static string GetAssetPath(UnityEngine.Object value) => value == null ? "" : "Assets/FX.controller";
    public static UnityEngine.Object[] LoadAllAssetsAtPath(string path) => Objects;
    public static T LoadAssetAtPath<T>(string path) where T : UnityEngine.Object {
        if (typeof(T) == typeof(AnimatorController)) return Controller as T;
        if (typeof(T) == typeof(AnimationClip)) return new AnimationClip() as T;
        return null;
    }
    public static void SaveAssetIfDirty(UnityEngine.Object value) { Saves++; }
    public static void ImportAsset(string path, ImportAssetOptions options) {}
}
public static class SceneObjectCopyCore {
    public static string AbsoluteAssetPath;
    public sealed class Evidence { public string Guid = "guid"; public FileData File = new FileData(); }
    public sealed class FileData { public string Digest = "digest"; }
    public static string ToAbsoluteAssetPath(string path) => AbsoluteAssetPath;
    public static Evidence ReadStableAssetEvidence(string path, string label) => new Evidence();
}
public static class AvatarPrimitiveCrudCore { public static string NormalizeAssetPath(string value) => value; public static State FindState(StateMachine machine, string name) => new State(); }
public static class ManageFxAnimatorTool {
    public static int Applied;
    public static object DescribeForBatch(AnimatorController controller) => new JObject();
    public static void ApplyForBatch(string action, AnimatorController controller, JObject edit) { Applied++; }
}
public sealed class Result { public bool ok; public bool mutationStarted; }
public static class VRCForgeToolResult { public static Result FailedWithException(string code,string message,System.Exception exception,object payload=null)=>FailedWithCode(code,message,payload); public static Result FailedWithException(string message,System.Exception exception,object payload=null)=>FailedWithCode(message,message,payload);
    public static Result Completed(string message, object payload) => new Result { ok = true, mutationStarted = true };
    public static Result RejectedBeforeMutation(string code, string message, string layer, string phase,System.Exception exception=null) => new Result { ok = false, mutationStarted = false };
    public static Result FailedWithCode(string code, string message, object payload) => new Result { ok = false, mutationStarted = true };
}
public static class WriteAnimationCurveTool {
    public static object ResolveBindingRoot(JObject arguments) => throw new Exception("This no-motion fixture must not resolve scene bindings.");
    public static void ValidateClipBindings(object root, AnimationClip clip) => throw new Exception("This no-motion fixture must not validate scene bindings.");
    public sealed class AssetEditRecovery {
        public static int Captures, Begins, Completes;
        public void Capture(string path) { Captures++; }
        public void Begin() { Begins++; }
        public void Complete() { Completes++; }
        public bool Restore() => true;
    }
    public static Result EditFailure(string code, Exception exception, bool mutationStarted, string phase, AssetEditRecovery recovery) =>
        mutationStarted ? VRCForgeToolResult.FailedWithCode(code, exception.Message, null) : VRCForgeToolResult.RejectedBeforeMutation(code, exception.Message, "unity_core_tool", phase);
}
'''


PROBE = r'''
static int failures;
static void Check(bool value, string name) { Console.WriteLine((value ? "PASS " : "FAIL ") + name); if (!value) failures++; }
static void ResetCounters() { AssetDatabase.Saves = 0; Undo.Registers = 0; ManageFxAnimatorTool.Applied = 0; WriteAnimationCurveTool.AssetEditRecovery.Captures = 0; WriteAnimationCurveTool.AssetEditRecovery.Begins = 0; }
public static int Main() {
    var root = Path.Combine(Path.GetTempPath(), "vrcforge-fx-handle-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(root);
    var asset = Path.Combine(root, "FX.controller");
    File.WriteAllBytes(asset, new byte[] { 1, 2, 3 });
    File.WriteAllBytes(asset + ".meta", new byte[] { 4, 5, 6 });
    SceneObjectCopyCore.AbsoluteAssetPath = asset;
    AssetDatabase.Controller = new AnimatorController();
    AssetDatabase.Objects = new UnityEngine.Object[] { AssetDatabase.Controller, null, new UnityEngine.Object { Persistent = false } };
    var request = JObject.Parse(@"{'controllerPath':'Assets/FX.controller','edits':[{'action':'ensure_state','layerName':'Layer'}]}");
    using (var held = new FileStream(asset, FileMode.Open, FileAccess.Read, FileShare.Read)) {
        ResetCounters();
        var result = (Result)HandleCommand(request);
        Check(!result.ok && !result.mutationStarted, "locked HandleCommand rejects before mutation");
        Check(WriteAnimationCurveTool.AssetEditRecovery.Captures == 0 && WriteAnimationCurveTool.AssetEditRecovery.Begins == 0, "locked path does not capture or begin recovery");
        Check(Undo.Registers == 0 && ManageFxAnimatorTool.Applied == 0 && AssetDatabase.Saves == 0, "locked path does not Undo, apply, or save");
    }
    ResetCounters();
    var success = (Result)HandleCommand(request);
    Check(success.ok && success.mutationStarted, "unlocked HandleCommand completes");
    Check(WriteAnimationCurveTool.AssetEditRecovery.Captures == 1 && WriteAnimationCurveTool.AssetEditRecovery.Begins == 1, "unlocked path captures and begins recovery");
    Check(Undo.Registers == 1 && ManageFxAnimatorTool.Applied == 1 && AssetDatabase.Saves == 1, "unlocked path reaches Undo, apply, and save");
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
    compiler = sdks[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    if not newtonsoft.is_file():
        pytest.skip("SDK Newtonsoft.Json assembly unavailable")
    output.mkdir(parents=True, exist_ok=True)
    selected = [
        extract_method(source, "internal static object HandleCommand"),
        extract_method(source, "private static UnityEngine.Object[] LoadPersistenceObjects"),
        extract_method(source, "private static void ValidatePersistenceObjectSet"),
        extract_method(source, "private static void ProbeExclusiveAssetFiles"),
    ]
    # These pure planning/readback methods are replaced by deterministic test stubs;
    # HandleCommand and the production preflight methods remain the code under test.
    replacements = r'''
private static JArray ValidateEnvelope(JObject arguments) => (JArray)arguments["edits"].DeepClone();
private static string Required(JObject value, string name) => value[name]?.ToString() ?? throw new InvalidOperationException(name + " is required.");
private static JObject Snapshot(AnimatorController controller) => new JObject();
private static JObject BuildPlan(JObject before, JArray edits, ISet<string> existingMotions) => new JObject();
private static JObject Projection(JObject value) => value;
private static void Verify(JObject expected, JObject actual) {}
private static void ApplyLayerDeletion(AnimatorController controller, string layerName) {}
'''
    program = STUBS + "public class Probe {\n" + "\n".join(selected) + replacements + PROBE + "\n}"
    cs = output / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{item}" for item in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, output / "Newtonsoft.Json.dll")
    (output / "Probe.runtimeconfig.json").write_text(
        json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}),
        encoding="utf-8",
    )
    return subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)


def test_handle_command_stops_before_undo_apply_and_save_when_locked(tmp_path: Path) -> None:
    completed = compile_and_run(SOURCE.read_text(encoding="utf-8"), tmp_path / "csharp")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("PASS ") == 6
