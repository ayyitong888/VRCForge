"""Execute the production safety predicate and exact path parser, without Unity."""
import copy
import json
import os
from pathlib import Path

import pytest

from test_material_checkpoint_safety_lane import ROOT, _run_safety_probe, _safety_predicate_methods


def _cases():
    rows = []
    binding = {
        "expectedRunIdDigest": "a" * 64,
        "expectedProjectPathDigest": "b" * 64,
        "expectedUnityProcessId": 123,
        "expectedUnityProcessStartedAtUtc": "2026-10-17T00:00:00Z",
        "expectedUnityExecutableDigest": "c" * 64,
    }
    for phase, tool, scope_key, base in (
        ("prepare", "vrc_prepare_checkpoint", "checkpointFiles", {"projectPath": "D:/Actual"}),
        ("prepare_restore", "vrc_reload_after_checkpoint_restore", "checkpointFiles",
         {"projectPath": "D:/Actual", "phase": "prepare_restore"}),
        ("reload", "vrc_reload_after_checkpoint_restore", "restoredAssetPaths",
         {"projectPath": "D:/Actual", "phase": "reload", "scenePaths": [],
          "activeScenePath": "", "refreshAssets": False}),
    ):
        def add(name, args, expected):
            rows.append({"name": phase + ":" + name, "tool": tool, "arguments": args, "expected": expected})

        add("legacy-unscoped", copy.deepcopy(base), True)
        exact = {**base, scope_key: ["Assets/Generated/Recovery_Work.controller",
                                    "Assets/Generated/Recovery_Work.controller.meta"]}
        add("exact-assets-and-meta", exact, True)
        add("complete-live-binding", {**exact, **binding}, True)
        add("partial-live-binding", {**exact, "expectedUnityProcessId": 123}, False)
        add("unknown-field", {**exact, "allowUnsafe": True}, False)
        add("other-phase-field", {**exact, ("checkpointFiles" if phase == "reload" else "restoredAssetPaths"): ["Assets/A.controller"]}, False)
        add("string-phase", {**exact, "phase": "other"}, False)
        if phase == "reload":
            empty = {**base, scope_key: []}
            add("empty-complete-live-binding", {**empty, **binding}, True)
            add("empty-refresh-enabled", {**empty, "refreshAssets": True}, True)
            add("empty-unknown-field", {**empty, "allowUnsafe": True}, False)
            add("empty-other-phase-field", {**empty, "checkpointFiles": []}, False)
        for name, scope in (
            ("empty", []), ("null", None), ("not-array", "Assets/A.controller"),
            ("object", {"path": "Assets/A.controller"}), ("non-string", [5]),
            ("null-element", [None]), ("empty-element", [""]),
            ("absolute-path", ["D:/Actual/Assets/A.controller"]),
            ("project-settings", ["ProjectSettings/ProjectSettings.asset"]),
            ("package", ["Packages/example/A.asset"]),
            ("parent-traversal", ["Assets/../A.controller"]),
            ("dot-segment", ["Assets/./A.controller"]),
            ("empty-segment", ["Assets//A.controller"]),
            ("backslash", ["Assets\\A.controller"]),
            ("mixed-valid-invalid", ["Assets/A.controller", "../secret"]),
        ):
            add("scope-" + name, {**base, scope_key: scope}, phase == "reload" and name == "empty")
    rows.append({"name": "unrelated-tool", "tool": "vrc_manage_fx_animator",
                 "arguments": {"projectPath": "D:/Actual", "checkpointFiles": ["Assets/A.controller"]},
                 "expected": False})
    return rows


CASES = _cases()


@pytest.fixture(scope="module")
def route_results(tmp_path_factory):
    source_path = Path(os.environ.get("VRCFORGE_SAFETY_ROUTE_SOURCE", "")) if os.environ.get("VRCFORGE_SAFETY_ROUTE_SOURCE") else ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs"
    methods, checkpoint_class = _safety_predicate_methods(source_path.read_text(encoding="utf-8"))
    payload = json.dumps(CASES, ensure_ascii=True).replace('"', '""')
    program = '''using System; using System.Linq; using System.Collections.Generic; using Newtonsoft.Json.Linq;
''' + checkpoint_class + '''class Probe {
''' + methods + '''
static int Main(){
var output = new JObject();
var rows = Newtonsoft.Json.JsonConvert.DeserializeObject<JArray>(@"''' + payload + '''",
new Newtonsoft.Json.JsonSerializerSettings { DateParseHandling = Newtonsoft.Json.DateParseHandling.None });
foreach(var row in rows){
var args = (JObject)row["arguments"];
var before = args.DeepClone();
var accepted = IsStrictSafetyControlRequest((string)row["tool"], args);
output[(string)row["name"]] = new JObject { ["accepted"] = accepted,
["unchanged"] = JToken.DeepEquals(before, args) };
}
Console.WriteLine(output.ToString(Newtonsoft.Json.Formatting.None));return 0;}}
'''
    return json.loads(_run_safety_probe(tmp_path_factory.mktemp("safety-route"), program))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_exact_scope_safety_route(case, route_results):
    actual = route_results[case["name"]]
    assert actual["unchanged"], "Safety validation mutated caller arguments"
    assert actual["accepted"] is case["expected"], case["name"]


def test_idempotent_reload_empty_scope_has_zero_imports(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs").read_text(encoding="utf-8")
    start = source.rindex("                if (refreshAssets)")
    end = source.index("                var assetReadback", start)
    import_branch = source[start:end]
    _, checkpoint_class = _safety_predicate_methods((ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8"))
    program = '''using System; using System.Linq; using System.Collections.Generic; using Newtonsoft.Json.Linq;
''' + checkpoint_class + '''
[Flags] enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
static class AssetDatabase {
internal static List<string> Calls = new List<string>();
internal static void ImportAsset(string path,ImportAssetOptions options){Calls.Add("import:"+path);}
internal static void Refresh(ImportAssetOptions options){Calls.Add("refresh");}
}
class Probe {
static void Check(bool value,string name){if(!value)throw new Exception(name);}
static void Reload(JObject @params,bool refreshAssets){
''' + import_branch + '''
}
static int Main(){
Reload(new JObject{["restoredAssetPaths"]=new JArray("Assets/Recovery.controller","Assets/Recovery.controller.meta")},true);
Check(AssetDatabase.Calls.SequenceEqual(new[]{"import:Assets/Recovery.controller"}),"first restore import scope");
Reload(new JObject{["restoredAssetPaths"]=new JArray()},true);
Check(AssetDatabase.Calls.Count==1,"idempotent restore unexpectedly imported or refreshed");
Reload(new JObject{["restoredAssetPaths"]=new JArray()},false);
Check(AssetDatabase.Calls.Count==1,"disabled refresh imported assets");
bool rejected=false;try{CheckpointPrepareTool.ExactFileScope(new JArray());}catch(InvalidOperationException){rejected=true;}
Check(rejected,"empty checkpointFiles allowed");
rejected=false;try{Reload(new JObject{["restoredAssetPaths"]=new JArray("Assets/../Wrong.controller")},true);}catch(InvalidOperationException){rejected=true;}
Check(rejected&&AssetDatabase.Calls.Count==1,"invalid scope touched asset database");
Reload(new JObject(),true);Check(AssetDatabase.Calls.Last()=="refresh","unscoped behavior changed");
Console.WriteLine("PASS idempotent empty reload");return 0;}}
'''
    assert "PASS idempotent empty reload" in _run_safety_probe(tmp_path, program)
