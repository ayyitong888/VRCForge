"""Explicit restore selections must resolve before any target file is touched."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from tests.test_component_property_batch_runtime import toolchain


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/PrefabTools.cs"


def test_restore_missing_explicit_assets_replays_real_owner_before_any_copy(tmp_path):
    source = SOURCE.read_text(encoding="utf-8")
    result_source = (ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs").read_text(encoding="utf-8")
    result_source = "\n".join(line for line in result_source.splitlines() if not line.startswith("using "))
    seams = r'''
namespace UnityEngine {public static class Application {public static string dataPath;}}
namespace UnityEditor {public static class AssetDatabase {public static void Refresh(){}}}
namespace VRCForge.Core.MCP {
class VRCForgeCommandAttribute:System.Attribute {public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;}}
class VRCForgeInputAttribute:System.Attribute {public VRCForgeInputAttribute(string text){} public bool IsRequired{get;set;}}
}
namespace VRCForge.Editor {
class VRCForgeOutputPathGuard {public static string ResolveManagedProjectPath(string path,string fallback,string scope,string label)=>System.IO.Path.GetFullPath(System.IO.Path.Combine(System.IO.Directory.GetParent(UnityEngine.Application.dataPath).FullName,string.IsNullOrWhiteSpace(path)?fallback:path));}
class Probe {
static string Hash(byte[] value){using(var h=System.Security.Cryptography.SHA256.Create())return System.BitConverter.ToString(h.ComputeHash(value)).Replace("-","").ToLowerInvariant();}
static void Main(string[] args){
 var root=args[0];UnityEngine.Application.dataPath=System.IO.Path.Combine(root,"Assets");System.IO.Directory.CreateDirectory(UnityEngine.Application.dataPath);
 var target=System.IO.Path.Combine(root,"Assets/file.asset");System.IO.File.WriteAllText(target,"original");
 var bytes=System.Text.Encoding.UTF8.GetBytes("replacement");
 var backup=System.IO.Path.Combine(root,"Library/VRCForge/Backups/test");System.IO.Directory.CreateDirectory(backup);
 System.IO.File.WriteAllBytes(System.IO.Path.Combine(backup,"file.asset"),bytes);
 var manifest=Newtonsoft.Json.Linq.JObject.FromObject(new{type="vrcforge_safe_backup",backup_id="test",project_identity=new{project_root_hash=Hash(System.Text.Encoding.UTF8.GetBytes(root.Replace("\\","/").ToLowerInvariant()))},files=new[]{new{project_relative_path="Assets/file.asset",backup_relative_path="file.asset",sha256=Hash(bytes)}}});
 System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"backup.json"),manifest.ToString());
 foreach(var confirm in new[]{false,true})foreach(var mixed in new[]{false,true}){
  var paths=mixed?new[]{"Assets/file.asset","Assets/missing.asset"}:new[]{"Assets/missing.asset"};
  var result=(VRCForge.Core.MCP.VRCForgeToolResult)PrefabTools.HandleCommand(Newtonsoft.Json.Linq.JObject.FromObject(new{backupPath=backup,assetPaths=paths,confirmRestore=confirm,allowOverwriteChanged=true,refreshAssets=false}));
  System.Console.WriteLine("missing "+result.ToStructuredContent().ToString(Newtonsoft.Json.Formatting.None));
  if(System.IO.File.ReadAllText(target)!="original")throw new System.Exception("A rejected selection copied an earlier valid file.");
  if(System.IO.Directory.GetDirectories(backup,".restore-*").Length!=0)throw new System.Exception("A rejected selection created recovery artifacts.");
 }
 foreach(var path in new[]{"Assets/file.asset","Assets"}){
  var result=(VRCForge.Core.MCP.VRCForgeToolResult)PrefabTools.HandleCommand(Newtonsoft.Json.Linq.JObject.FromObject(new{backupPath=backup,assetPaths=new[]{path},confirmRestore=false,allowOverwriteChanged=true,refreshAssets=false}));
  System.Console.WriteLine("valid "+result.ToStructuredContent().ToString(Newtonsoft.Json.Formatting.None));
 }
 var all=(VRCForge.Core.MCP.VRCForgeToolResult)PrefabTools.HandleCommand(Newtonsoft.Json.Linq.JObject.FromObject(new{backupPath=backup,confirmRestore=false,allowOverwriteChanged=true,refreshAssets=false}));
 System.Console.WriteLine("valid "+all.ToStructuredContent().ToString(Newtonsoft.Json.Formatting.None));
 foreach(Newtonsoft.Json.Linq.JObject request in Newtonsoft.Json.Linq.JArray.Parse(System.IO.File.ReadAllText(args[1]))){
  request["backupPath"]=backup;
  var result=(VRCForge.Core.MCP.VRCForgeToolResult)PrefabTools.HandleCommand(request);
  System.Console.WriteLine("blank "+result.ToStructuredContent().ToString(Newtonsoft.Json.Formatting.None));
  if(System.IO.File.ReadAllText(target)!="original")throw new System.Exception("A blank selection restored all assets.");
  if(System.IO.Directory.GetDirectories(backup,".restore-*").Length!=0)throw new System.Exception("A blank selection created recovery artifacts.");
 }
 var first=System.IO.Path.Combine(root,"Assets/first.asset");System.IO.File.WriteAllText(first,"first-original");
 System.IO.File.WriteAllBytes(System.IO.Path.Combine(backup,"first.asset"),bytes);
 ((Newtonsoft.Json.Linq.JArray)manifest["files"]).Insert(0,Newtonsoft.Json.Linq.JObject.FromObject(new{project_relative_path="Assets/first.asset",backup_relative_path="first.asset",sha256=Hash(bytes)}));
 System.IO.File.WriteAllText(System.IO.Path.Combine(backup,"backup.json"),manifest.ToString());
 System.IO.File.Delete(System.IO.Path.Combine(backup,"file.asset"));
 foreach(var confirm in new[]{false,true})foreach(var mode in new[]{"single","mixed","folder","all"}){
  var request=Newtonsoft.Json.Linq.JObject.FromObject(new{backupPath=backup,confirmRestore=confirm,allowOverwriteChanged=true,refreshAssets=false});
  if(mode=="single")request["assetPaths"]=new Newtonsoft.Json.Linq.JArray("Assets/file.asset");
  if(mode=="mixed")request["assetPaths"]=new Newtonsoft.Json.Linq.JArray("Assets/first.asset","Assets/file.asset");
  if(mode=="folder")request["assetPaths"]=new Newtonsoft.Json.Linq.JArray("Assets");
  var result=(VRCForge.Core.MCP.VRCForgeToolResult)PrefabTools.HandleCommand(request);
  System.Console.WriteLine("snapshot "+result.ToStructuredContent().ToString(Newtonsoft.Json.Formatting.None));
  if(System.IO.File.ReadAllText(first)!="first-original"||System.IO.File.ReadAllText(target)!="original")throw new System.Exception("A missing snapshot source copied an earlier valid file.");
  if(System.IO.Directory.GetDirectories(backup,".restore-*").Length!=0)throw new System.Exception("A missing snapshot source created recovery artifacts.");
 }
}
}}
'''
    cs = tmp_path / "Probe.cs"
    cs.write_text(source + seams + result_source, encoding="utf-8")
    base, compiler, refs, newtonsoft = toolchain()
    dll = tmp_path / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:exe", f"/out:{dll}",
               f"/r:{newtonsoft}", str(cs), *[f"/r:{p}" for p in refs.glob("*.dll")]]
    built = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / newtonsoft.name)
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}
    }}), encoding="utf-8")
    from dashboard_server import build_safe_backup_restore_request
    requests = []
    for confirm in (False, True):
        for paths in ([" "], [""], [None], ["Assets/file.asset", " "]):
            for alias in ("assetPaths", "asset_paths"):
                request = build_safe_backup_restore_request({alias: paths, "allowOverwriteChanged": True}, confirm)
                assert request["assetPaths"] == paths, "The shared caller must not turn an invalid subset into restore-all."
                if alias == "assetPaths":
                    requests.append(request)
    request_file = tmp_path / "gateway-requests.json"
    request_file.write_text(json.dumps(requests), encoding="utf-8")
    replay = subprocess.run([str(base / "dotnet.exe"), str(dll), str(tmp_path), str(request_file)], capture_output=True, text=True, timeout=30)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    from dashboard_server import extract_tool_result_payload
    from agent_tool_result_contract import normalize_agent_tool_result
    failures = {}
    counts = {"missing": 0, "blank": 0, "snapshot": 0}
    for line in replay.stdout.splitlines():
        case, text = line.split(" ", 1)
        receipt = json.loads(text)
        payload = extract_tool_result_payload(SimpleNamespace(payload={"structuredContent": receipt}))
        if case == "valid":
            assert receipt["success"] is True and payload["summary"]["plannedCount"] == 1
            continue
        counts[case] += 1
        assert receipt["success"] is False
        failures.setdefault(case, payload)
        if case == "missing":
            assert payload["code"] == "backup_asset_selection_missing"
            assert payload["missingAssetPaths"] == ["Assets/missing.asset"]
            assert payload["availableAssetPaths"] == ["Assets/file.asset"]
        elif case == "blank":
            assert payload["code"] == "backup_asset_selection_invalid"
            assert payload["invalidAssetPathIndices"] in ([0], [1])
        else:
            assert payload["code"] == "backup_snapshot_source_missing"
            assert payload["missingSnapshotAssetPaths"] == ["Assets/file.asset"]
            assert Path(payload["missingSnapshotFiles"][0]).name == "file.asset"
        assert payload["mutationStarted"] is False and payload["commitState"] == "not_started"
        assert "preview" in payload["nextAction"]
        for write in (False, True):
            outcome = normalize_agent_tool_result(payload, fallback_summary="Restore selection", write=write)
            assert outcome["status"] == "failed"
            assert outcome["error"]["code"] == payload["code"]
            assert outcome["nextAction"] == payload["nextAction"]
    assert counts == {"missing": 4, "blank": 8, "snapshot": 8}

    from agent_gateway import create_agent_mcp_app
    from tests.test_native_runtime_gateway import setup_gateway, call, run
    from tests.test_internal_external_tool_result_parity import TOOL_NAME, _mcp_2026_tool_call

    for case, payload in failures.items():
        gateway, model, _ = setup_gateway(tmp_path / "runtime" / case, [
            call("preview", "vrcforge_preview_restore_backup", {"case": case}),
            {"role": "assistant", "content": "Correct the backup request before retrying."},
        ])
        gateway.register_tool("vrcforge_preview_restore_backup", "When to use: preview. When NOT to use: restore.",
                              "plan/preview", lambda _, value=payload: value)
        result = run(gateway, tmp_path, maxAgenticTurns=3)
        message = next(m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "preview")
        observation = json.loads(message["content"])["observations"][0]
        assert observation["outcome"]["status"] == "failed"
        assert payload["code"] in message["content"] and "preview" in message["content"]
        assert result["plan"]["nextStep"] != "done"
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.register_tool(TOOL_NAME, "Inspect the real restore selection error.", "read/debug", lambda args: failures[args["case"]])
    gateway.register_external_mcp_unity_tool(TOOL_NAME, "diagnostics")
    app = create_agent_mcp_app(gateway)
    for case, payload in failures.items():
        external = _mcp_2026_tool_call(app, token=config.token, arguments={"case": case})
        assert external["outcome"]["status"] == "failed"
        assert external["outcome"]["error"]["code"] == payload["code"]
        assert "preview" in external["outcome"]["nextAction"]
