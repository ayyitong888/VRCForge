"""Replay editor error branches without a Unity process or asset mutation."""
import os
import subprocess
from pathlib import Path

from test_component_property_checkpoint_runtime import run
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def source(relative):
    ref = os.environ.get("VRCFORGE_TOOL_ERROR_AUDIT_GIT_REF")
    return subprocess.check_output(["git", "show", f"{ref}:{relative}"], cwd=ROOT,
        text=True, encoding="utf-8") if ref else (ROOT / relative).read_text(encoding="utf-8-sig")


def failure_catch(relative, message):
    text = source("Assets/VRCForge/Editor/" + relative)
    start = 0
    while True:
        start = text.index("catch (Exception ex)", start)
        block = method(text[start:], "catch (Exception ex)")
        if message in block:
            return block
        start += len(block)


def test_tool_error_branches_preserve_inner_reason_and_existing_diagnostics(tmp_path):
    contract = source("Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs")
    contract = contract.replace("using System;", "").replace("using Newtonsoft.Json.Linq;", "")
    catches = [
        ("Object", "Generic/UnityGameObjectCrud.cs", "Get GameObject failed:"),
        ("Asset", "Generic/UnityAssetPrefabCrud.cs", "Find assets failed:"),
        ("Material", "Generic/UnityMaterialPropertyInspect.cs", "Material property inspection failed:"),
        ("MA", "MAComponentWriter.cs", "Inspect Modular Avatar component failed:"),
        ("Setup", "SetupOutfitTool.cs", "Setup Outfit failed:"),
        ("Checkpoint", "CheckpointRecoveryTool.cs", "Checkpoint preparation failed:"),
        ("Compile", "CompileErrorReader.cs", "Compile error check failed:"),
    ]
    handlers = "\n".join(
        f"public static VRCForgeToolResult {name}(Exception failure) {{ try {{ throw failure; }} "
        + failure_catch(relative, text) + " }" for name, relative, text in catches)
    core = method(source("Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs"), "private static JObject ToolError(")
    checks = "\n".join(f'CheckFailure("{name}", {name}(failure));' for name, _, _ in catches)
    async_catch = failure_catch("SetupOutfitTool.cs", 'CompleteJob(jobId, "error", payload);')
    program = r'''
using System;using System.Reflection;using Newtonsoft.Json.Linq;using VRCForge.Core.MCP;
CONTRACT
class Probe {
 HANDLERS
 CORE
 static JObject Last;
 static void CompleteJob(string id,string status,JObject payload){Last=payload;}
 static JObject Async(Exception originalFailure,bool started){
  var jobId="fixture";var job=new {mutationStarted=started,continuationConsumed=false,avatarPath="A",outfitPath="A/O",outfitGlobalObjectId="id"};
  try{throw originalFailure;} ASYNC_CATCH
  return Last;
 }
 static void Check(bool value,string message){if(!value)throw new Exception(message);}
 static void CheckFailure(string domain,VRCForgeToolResult result){
  var json=result.ToStructuredContent(); var diagnostics=json["data"]?["diagnostics"];
  Console.WriteLine(domain+" diagnostics="+(diagnostics!=null));
  Check(json["success"].Value<bool>()==false,domain+" failure status changed");
  Check(result.Message.Substring(0,Math.Min(100,result.Message.Length)).Contains("recover-exact-field"),domain+" hides the actionable cause after the summary");
  Check(diagnostics!=null&&diagnostics.ToString().Contains("recover-exact-field"),domain+" lost inner exception reason");
  Check(diagnostics.ToString().Contains("InvalidOperationException"),domain+" lost exception type");
 }
 static void Main(){
  var leaf=new InvalidOperationException("recover-exact-field: inspect the missing serialized field.");
  var failure=new TargetInvocationException("Reflection invocation wrapper.",leaf);
  CHECKS
  foreach(bool started in new[]{false,true}){
   var asyncResult=Async(failure,started);var error=(string)asyncResult["error"];
   Check(error.Substring(0,Math.Min(100,error.Length)).Contains("recover-exact-field"),"async job hides the inner failure");
   Check((string)asyncResult["commitState"]==(started?"unknown":"not_started"),"async mutation facts changed");
   Check(asyncResult["diagnostics"].ToString().Contains("InvalidOperationException"),"async original type missing");
  }
  var original=new JObject{{"schema","fixture.domain.diagnostics"},{"sourceError",new JObject{{"field","root.material"}}}};
  var merged=VRCForgeToolResult.FailedWithException("fixture","Original text",failure,
      new JObject{{"mutationStarted",true},{"commitState","rolled_back"},{"diagnostics",original}}).ToStructuredContent()["data"];
  Check((string)merged["diagnostics"]?["sourceError"]?["field"]=="root.material","existing diagnostics were discarded");
  Check((string)merged["diagnostics"]?["schema"]=="fixture.domain.diagnostics","existing diagnostics schema was overwritten");
  Check((string)merged["commitState"]=="rolled_back"&&merged["mutationStarted"].Value<bool>(),"mutation facts changed");
  Check(merged["diagnostics"].ToString().Contains("recover-exact-field"),"caught exception diagnostics missing");
  var direct=VRCForgeToolResult.FailedWithException("original_code","Choose an existing field.",
      new ArgumentException("Choose an existing field."),new JObject{{"nextAction","Inspect the exact component."}}).ToStructuredContent();
  Check((string)direct["error"]=="Choose an existing field."&&(string)direct["code"]=="original_code","ordinary exception message/code changed");
  Check((string)direct["data"]["nextAction"]=="Inspect the exact component.","existing correction route was discarded");
  var wire=ToolError("tool_handler_exception","Handler failed",true,false,failure)["structuredContent"];
  Check(((string)wire["error"]).Substring(0,100).Contains("recover-exact-field"),"Core summary hides actionable inner cause");
  Check((string)wire["rootCause"]?["message"]==leaf.Message,"Core rootCause points at reflection wrapper");
  Check(wire["diagnostics"].ToString().Contains("Reflection invocation wrapper."),"Core wrapper chain discarded");
  Check(wire["mutationStarted"].Type==JTokenType.Null&&(string)wire["commitState"]=="unknown","unknown write facts changed");
  Console.WriteLine("PASS horizontal production failure branches");
 }
}
'''.replace("CONTRACT", contract).replace("HANDLERS", handlers).replace("CORE", core).replace("CHECKS", checks).replace("ASYNC_CATCH", async_catch)
    assert "PASS horizontal production failure branches" in run(tmp_path, program)
