"""Replay the production duplicate-prefab preflight branch as a C# Core result."""
import os
from pathlib import Path

from test_constraint_conversion_scope_runtime import run


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = "Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs"


def read_source():
    revision = os.environ.get("VRCFORGE_PREFAB_PREFLIGHT_GIT_REF")
    if revision:
        import subprocess

        return subprocess.check_output(
            ["git", "show", f"{revision}:{SOURCE_PATH}"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    return (ROOT / SOURCE_PATH).read_text(encoding="utf-8-sig")


def test_duplicate_prefab_preflight_reports_typed_no_mutation_facts(tmp_path):
    source = read_source()
    tool = source.split("public static class InstantiatePrefabTool", 1)[1].split(
        "public static class UnpackPrefabTool", 1
    )[0]
    branch = (
        "if (AssetPrefabCore.CountHierarchyPath(expectedResultPath, scene.handle) != 0)"
        + tool.split(
            "if (AssetPrefabCore.CountHierarchyPath(expectedResultPath, scene.handle) != 0)",
            1,
        )[1].split("var before = new", 1)[0]
    )

    helper_start = "private static VRCForgeToolResult PreflightFailure("
    if helper_start in tool:
        helper = helper_start + tool.split(helper_start, 1)[1].split(
            "private static VRCForgeToolResult CommittedFailure(", 1
        )[0]
    else:
        # Baseline adapter lets the exact old return branch run and expose its missing facts.
        helper = """private static VRCForgeToolResult PreflightFailure(string message)
        {
            return VRCForgeToolResult.Failed(message);
        }
        """

    run(
        tmp_path,
        r'''
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
namespace Newtonsoft.Json.Linq {
    public sealed class JObject {
        private readonly Dictionary<string, object> values = new Dictionary<string, object>();
        public object this[string key] { get { return values.ContainsKey(key) ? values[key] : null; } set { values[key] = value; } }
    }
}
class Scene { public int handle = 7; }
class VRCForgeToolResult { public static VRCForgeToolResult FailedWithException(string code,string message,System.Exception exception,object payload=null)=>Failed(message); public static VRCForgeToolResult FailedWithException(string message,System.Exception exception,object payload=null)=>Failed(message);
    public object Payload;
    public static VRCForgeToolResult Failed(string message) { return new VRCForgeToolResult(); }
    public static VRCForgeToolResult RejectedBeforeMutation(string code, string message, string layer, string phase, bool retryable = false, object details = null,System.Exception exception=null) {
        return new VRCForgeToolResult { Payload = new JObject {
            ["mutationStarted"] = false, ["committed"] = false, ["commitState"] = "not_started",
            ["commitStateKnown"] = true, ["retryable"] = retryable, ["checkpointRecoveryRequired"] = false,
            ["temporaryCleanupRequired"] = false
        } };
    }
}
static class AssetPrefabCore { public static int CountHierarchyPath(string path, int sceneHandle) { return 1; } }
static class PrefabUtility {
    public static int instantiateCalls;
    public static object InstantiatePrefab(object asset, Scene scene) { instantiateCalls++; return new object(); }
}
class Probe {
    static VRCForgeToolResult Attempt() {
        var expectedResultPath = "Avatar/GestureManager";
        var scene = new Scene();
''' + branch + r'''
        var instance = PrefabUtility.InstantiatePrefab(new object(), scene);
        return VRCForgeToolResult.Failed("unexpected continuation");
    }
''' + helper + r'''
    static int Main() {
        var result = Attempt();
        var facts = result.Payload as JObject;
        if (facts == null) throw new Exception("Core duplicate-path error omitted typed no-mutation facts");
        if ((bool)facts["mutationStarted"] || (bool)facts["committed"] ||
            (string)facts["commitState"] != "not_started" ||
            (bool)facts["recoveryRequired"] || !(bool)facts["safeToRetry"] ||
            (bool)facts["checkpointRecoveryRequired"] || !(bool)facts["retryable"])
            throw new Exception("Core duplicate-path error reported incorrect preflight facts");
        if (PrefabUtility.instantiateCalls != 0)
            throw new Exception("Duplicate-path preflight reached InstantiatePrefab");
        return 0;
    }
}
''',
    )

    preflight = tool.split("var instance = PrefabUtility.InstantiatePrefab(", 1)[0]
    after_instantiate = tool.split("var instance = PrefabUtility.InstantiatePrefab(", 1)[1]
    assert "return VRCForgeToolResult.Failed(" not in preflight
    assert "return PreflightFailure(" in preflight
    assert 'return VRCForgeToolResult.Failed($"Unity refused to instantiate the prefab' in after_instantiate
    assert 'return VRCForgeToolResult.FailedWithException($"Instantiate prefab failed:' in after_instantiate
