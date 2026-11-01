"""Native callbacks share the existing supervised transaction and honest report boundary."""
from copy import copy
from pathlib import Path
import pytest
from test_component_property_checkpoint_runtime import run
from unity_editor_extension_tools import diagnostic_fix_request

ROOT = Path(__file__).resolve().parents[1]


def test_native_fix_plan_checkpoint_and_preview(monkeypatch, tmp_path):
    import dashboard_server as server
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_apply_diagnostic_fix"]
    args = {"projectPath": str(tmp_path), "source": "sdk", "index": 2, "message": " exact original ", "avatarPath": "Avatar"}
    assert handler.requires_approved_execution_context and handler.pre_write_checkpoint_required
    assert handler.approved_execution_plan_builder(args) == [("vrc_apply_diagnostic_fix", diagnostic_fix_request(args))]
    calls = []
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda root: calls.append(root) or {"ok": True})
    assert handler.checkpoint_prepare_handler(tmp_path, {**args, "_checkpointTargetTool": handler.name}) == {"ok": True}
    assert calls == [tmp_path]
    assert diagnostic_fix_request(args, preview=True)["message"] == " exact original "
    assert diagnostic_fix_request(args, preview=True)["preview"] is True
    assert "vrcforge_preview_diagnostic_fix" in server.AGENT_GATEWAY._tools


@pytest.mark.parametrize("args", [dict(source="other", index=0, message="x"), dict(source="ndmf",index=True,message="x"), dict(source="sdk",index=0,message="x")])
def test_native_fix_does_not_guess_a_selection(args):
    with pytest.raises(ValueError):
        diagnostic_fix_request({"projectPath":"project", **args})


def test_actual_core_native_dispatch_preview_exception_and_readback(tmp_path):
    sources = [(ROOT / p).read_text(encoding="utf-8") for p in ["Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs", "Assets/VRCForge/Editor/NativeDiagnosticFixTool.cs"]]
    sources.append(r'''namespace VRCForge.Core.MCP {
 public enum VRCForgeCommandAccess { RequiresApproval }
 public class VRCForgeCommandAttribute:Attribute {public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;} public VRCForgeCommandAccess Access{get;set;}}
 public class VRCForgeInputAttribute:Attribute {public VRCForgeInputAttribute(string value){} public bool IsRequired{get;set;}}
}
namespace VRCForge.Editor {
 public static class CheckpointPrepareTool {public static void ValidateProject(JObject p){if((string)p["projectPath"]!="project")throw new Exception("wrong project");}}
 public static class NativeNdmfFixActions {public static int Calls;public static Action Resolve(JObject p){if((int)p["index"]!=0)throw new Exception("row changed");return ()=>{Calls++;if((string)p["message"]=="fail")throw new Exception("native partial failure");};}}
 public static class NativeSdkFixActions {public static Action Resolve(JObject p)=>()=>NativeNdmfFixActions.Calls++;}
 public static class VrchatSdkBuilderAlertsTool {public static int Reads;public static object HandleCommand(JObject p){Reads++;return VRCForgeToolResult.Completed("read",new JObject{{"available",true},{"alerts",new JArray()}});}}
}
class Probe {
 static void Check(bool value,string text){if(!value)throw new Exception(text);}
 static void Main(){
  var p=new JObject{{"projectPath","project"},{"source","ndmf"},{"index",0},{"message","native issue"},{"preview",true}};
  var r=(VRCForgeToolResult)VRCForge.Editor.NativeDiagnosticFixTool.HandleCommand(p);var data=(JObject)r.Payload;
  Check(r.IsSuccessful && !(bool)data["triggered"] && VRCForge.Editor.NativeNdmfFixActions.Calls==0,"preview never invokes");
  p["preview"]=false;r=(VRCForgeToolResult)VRCForge.Editor.NativeDiagnosticFixTool.HandleCommand(p);data=(JObject)r.Payload;
  Check(r.IsSuccessful && (bool)data["needsRecheck"] && (string)data["reportBoundary"]=="retained_build_report_not_refreshed" && (string)data["resultVerification"]=="pending","NDMF retained report is not fresh validation");
  p["message"]="fail";r=(VRCForgeToolResult)VRCForge.Editor.NativeDiagnosticFixTool.HandleCommand(p);data=(JObject)r.Payload;
  Check(!r.IsSuccessful && (bool)data["mutationStarted"] && (bool)data["checkpointRecoveryRequired"] && VRCForge.Editor.NativeNdmfFixActions.Calls==2,"failure preserves partial mutation facts");
  p["source"]="sdk";p["message"]="sdk issue";r=(VRCForgeToolResult)VRCForge.Editor.NativeDiagnosticFixTool.HandleCommand(p);data=(JObject)r.Payload;
  Check(r.IsSuccessful && VRCForge.Editor.VrchatSdkBuilderAlertsTool.Reads==1 && (bool)data["validationReadSucceeded"],"SDK reads once after native action");
  p["source"]="ndmf";p["index"]=1;r=(VRCForgeToolResult)VRCForge.Editor.NativeDiagnosticFixTool.HandleCommand(p);
  Check(!r.IsSuccessful && VRCForge.Editor.NativeNdmfFixActions.Calls==3,"stale row rejected without mutation");
  Console.WriteLine("PASS native dispatch");
 }
}''')
    lines="\n".join(sources).splitlines()
    program="\n".join(dict.fromkeys(line for line in lines if line.startswith("using ")))+"\n"+"\n".join(line for line in lines if not line.startswith("using "))
    assert "PASS native dispatch" in run(tmp_path,program)


from test_editor_menu_dispatch_lifecycle import dispatch_case, _execute

@pytest.mark.parametrize("lane", ["external", "internal"])
@pytest.mark.parametrize("failed", [False, True])
def test_native_fix_transaction_preserves_checkpoint_and_unverified_outcome(dispatch_case, lane, failed):
    import dashboard_server as server
    gateway, service, args, receipts, calls, checkpoints = dispatch_case
    name = "vrcforge_apply_diagnostic_fix"
    gateway._write_handlers[name] = copy(server.AGENT_GATEWAY._write_handlers[name])
    receipts[name] = {"ok": not failed, "source": "ndmf", "index": 0, "message": "original",
        "triggered": True, "state": "invocation_exception" if failed else "invoked",
        "resultVerification": "pending", "needsRecheck": True, "mutationStarted": True,
        "mutationMayHaveStarted": True, "checkpointRecoveryRequired": failed}
    if failed:
        receipts[name]["error"] = "Native callback partially changed assets and raised."
    result = _execute(dispatch_case, lane, name, {"projectPath": args["projectPath"], "source": "ndmf", "index": 0, "message": "original"})
    assert bool(result["ok"]) is not failed, result
    assert len(checkpoints) == 1 and calls == [name]
    if failed:
        assert service._ports.checkpoint.active_apply_recoveries()
        assert result["outcome"]["status"] == "failed"
    else:
        assert result["status"] == "invoked" and result["outcome"]["status"] == "pending"
        assert service._ports.checkpoint.active_apply_recoveries() == []


def test_actual_core_preview_policy_routes_native_diagnostics_without_write_permission(tmp_path):
    import re
    from test_curve_fx_authoring_runtime_contract import method
    source=(ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8-sig")
    declaration=re.search(r'private static readonly HashSet<string> PreviewTools = .*?\n        };',source,re.S).group(0)
    policy=method(source,"private static bool HasAllowedPreviewRequest(string toolName, JObject arguments)")
    explicit=method(source,"private static bool HasExplicitPreviewRequest(JObject arguments)")
    program="using System;using System.Collections.Generic;using Newtonsoft.Json.Linq;class Probe {"+declaration+policy+explicit+r'''
 static bool HasStrictRestoreBackupPreviewRequest(JObject p)=>false;
 static bool HasStrictSetupOutfitPreviewRequest(JObject p)=>false;
 static void Main(){
  if(!HasAllowedPreviewRequest("vrc_apply_diagnostic_fix",new JObject{{"preview",true}}))throw new Exception("native diagnostic preview was not routed");
  foreach(var p in new[]{new JObject(),new JObject{{"preview",false}},new JObject{{"preview","true"}}})
   if(HasAllowedPreviewRequest("vrc_apply_diagnostic_fix",p))throw new Exception("non-preview gained preview authority");
  Console.WriteLine("PASS native preview policy");
 }
}'''
    assert "PASS native preview policy" in run(tmp_path,program)
