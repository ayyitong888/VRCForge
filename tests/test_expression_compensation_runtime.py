"""Execute actual expression failure handlers; recovery itself is a boundary double."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method


def test_expression_failures_attempt_recovery_and_preserve_original_error(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    ref = os.environ.get("VRCFORGE_EXPRESSION_RECOVERY_GIT_REF")
    source = subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True) if ref else (root / path).read_text(encoding="utf-8-sig")
    parameter = source.split("public static class EnsureExpressionParameterTool", 1)[1].split("public static class EnsureExpressionMenuControlTool", 1)[0]
    menu = source.split("public static class EnsureExpressionMenuControlTool", 1)[1].split("public static class EnsureAnimatorStateTool", 1)[0]
    program = r'''
using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;
class Receipt {public string Status="not_attempted",Error="";public bool RolledBack;}
class Recovery {public static int Calls;public bool Result;public bool Restore(){Calls++;return Result;}}
static class VRCForgeToolResult {public static object Failed(string message,object details)=>new JObject{{"message",message},{"details",JObject.FromObject(details)}};}
class Parameter {
 static object BuildTransaction(List<Receipt> r,string h)=>new {handle=h};
 public static object Run(bool mutationStarted,bool restore){var recovery=new Recovery{Result=restore};var receipts=new List<Receipt>{new Receipt()};var transactionHandle="Assets/P.asset";
 try{throw new InvalidOperationException("original save failure");} PARAMETER_CATCH
 }
}
class Menu {
 public static bool ThrowReadback;
 static Dictionary<string,JToken> CaptureMenuGraph(object root){if(ThrowReadback)throw new InvalidOperationException("secondary readback failure");return new Dictionary<string,JToken>();}
 static object BuildMenuTransaction(Dictionary<string,JToken> before,Dictionary<string,JToken> after,string handle,bool missing,string failure)=>new {handle};
 public static object Run(bool mutationStarted,bool restore){var recovery=new Recovery{Result=restore};var beforeGraph=new Dictionary<string,JToken>();object trackedRoot=new object();var rootWasMissing=true;var transactionHandle="Assets/M.asset";
 try{throw new InvalidOperationException("original save failure");} MENU_CATCH
 }
}
class Probe {
 static int failures;
 static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 static JObject Run(Func<object> invoke){try{return JObject.FromObject(invoke());}catch(Exception e){return new JObject{{"escaped",e.Message},{"details",new JObject()}};}}
 static void Verify(string label,Func<bool,bool,object> invoke){
  Recovery.Calls=0;var before=Run(()=>invoke(false,true));Check(Recovery.Calls==0&&(string)before["details"]["commitState"]=="not_started",label+" pre-mutation rejection does not restore");
  Recovery.Calls=0;var restored=Run(()=>invoke(true,true));Check(Recovery.Calls==1&&(string)restored["details"]["commitState"]=="rolled_back"&&(bool?)restored["details"]["checkpointRecoveryRequired"]==false,label+" successful compensation reported");
  Recovery.Calls=0;var failed=Run(()=>invoke(true,false));Check(Recovery.Calls==1&&(string)failed["details"]["commitState"]=="unknown"&&(bool?)failed["details"]["checkpointRecoveryRequired"]==true,label+" incomplete compensation remains unknown");
 }
 static int Main(){
  Verify("parameter",Parameter.Run);Verify("menu",Menu.Run);
  Menu.ThrowReadback=true;var failed=Run(()=>Menu.Run(true,false));
  Check(failed["escaped"]==null&&((string)failed["message"]).Contains("original save failure")&&(bool?)failed["details"]["failureReadbackComplete"]==false,"secondary menu readback failure preserves original error");
  var restored=Run(()=>Menu.Run(true,true));Check(restored["escaped"]==null&&(string)restored["details"]["commitState"]=="rolled_back","restored menu avoids stale-object readback");
  return failures==0?0:1;
 }
}
'''.replace("PARAMETER_CATCH", method(parameter, "catch (Exception ex)")).replace("MENU_CATCH", method(menu, "catch (Exception ex)"))
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet = shutil.which("dotnet")
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    json_dll = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    dll = tmp_path / "Probe.dll"
    cs.write_text(program, encoding="utf-8")
    command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{ref}" for ref in refs[-1].glob("*.dll")] + [f"-r:{json_dll}", str(cs)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(json_dll, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
