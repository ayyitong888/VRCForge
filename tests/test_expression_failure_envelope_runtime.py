"""Actual menu builder/planner regression; fake asset database only, no Unity writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]


def test_ensure_parameter_actual_failure_envelope(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8").split("public static class EnsureExpressionParameterTool",1)[1].split("public static class ",1)[0]
    actual_catch=method(source,"catch (Exception ex)")
    stubs=r'''
class Receipt {public string Status="not_attempted";public string Error="";}
static class VRCForgeToolResult {public static object Failed(string message,object detail)=>detail;}
public class Probe {
 static object BuildTransaction(List<Receipt> r,string h)=>new {handle=h};
 static object Run(bool mutationStarted){var receipts=new List<Receipt>{new Receipt()};var transactionHandle="Assets/P.asset";try{throw new InvalidOperationException("persisted readback mismatch");}
'''
    runner=r'''
 }
 static int failures;static void Check(bool b,string n){Console.WriteLine((b?"PASS ":"FAIL ")+n);if(!b)failures++;}
 public static int Main(){
 var before=JObject.FromObject(Run(false));var after=JObject.FromObject(Run(true));
 Check((string)before["schema"]=="vrcforge.expression_write.v1"&&(string)after["schema"]=="vrcforge.expression_write.v1","failure schema recognized");
 Check((bool?)before["mutationStarted"]==false&&(string)before["commitState"]=="not_started"&&(bool?)before["checkpointRecoveryRequired"]==false,"pre-mutation rejection states not started");
 Check((bool?)after["mutationStarted"]==true&&(string)after["commitState"]=="unknown"&&(bool?)after["checkpointRecoveryRequired"]==true,"late failure retains checkpoint recovery requirement");
 Check((bool?)after["verified"]==false&&after["transaction"]!=null,"late failure is not verified and preserves transaction");
 return failures==0?0:1;
 }
}
'''
    program="using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;"+stubs+actual_catch+runner
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==4
