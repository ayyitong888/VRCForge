"""Compile actual batch preflight and curve methods; Unity persistence is not simulated."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method, STUBS

ROOT = Path(__file__).resolve().parents[1]


def test_actual_batch_preflight_methods(tmp_path):
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet = shutil.which("dotnet")
    if not dotnet or not compilers or not refs:
        pytest.skip("Installed .NET SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAnimationCurveBatch.cs").read_text(encoding="utf-8")
    original = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8-sig")
    selected = [method(source, sig) for sig in ("internal static JArray ValidateEnvelope", "internal static void ValidateBinding", "private static bool SameBinding")]
    selected += [method(original, sig) for sig in ("private static AnimationCurve BuildCurve", "internal static void RequireFinite", "private static float ReadWeight")]
    runner = r'''
    static int failures;
    static void Check(bool value, string label) { Console.WriteLine((value ? "PASS " : "FAIL ") + label); if(!value) failures++; }
    static bool Reject(Action action) { try { action(); return false; } catch(InvalidOperationException) { return true; } }
    static JObject Args(int count) { var curves = new JArray(); for(int i=0;i<count;i++) curves.Add(new JObject { ["propertyName"]="p"+i, ["constantFloat"]=0 }); return new JObject { ["clipPath"]="Assets/Test.anim", ["curves"]=curves }; }
    public static int Main() {
        Check(ValidateEnvelope(Args(256)).Count==256,"256 curves accepted");
        Check(Reject(()=>ValidateEnvelope(Args(257))),"257 curves reject");
        Check(Reject(()=>ValidateEnvelope(Args(0))),"empty batch rejects");
        var a=Args(1); a["propertyName"]="single";
        Check(Reject(()=>ValidateEnvelope(a)),"mixed single and batch rejects");
        a=Args(1); a["action"]="retarget_curve";
        Check(Reject(()=>ValidateEnvelope(a)),"non-set action rejects");
        a=Args(1); a["curves"][0]["constantFloat"]=null; a["curves"][0]["keys"]=new JArray();
        Check(Reject(()=>ValidateEnvelope(a)),"ambiguous value form rejects");
        a=Args(1); ((JObject)a["curves"][0]).Remove("constantFloat");
        var keys=new JArray(); for(int i=0;i<4096;i++) keys.Add(new JObject { ["time"]=i, ["value"]=0 }); a["curves"][0]["keys"]=keys;
        Check(ValidateEnvelope(a).Count==1,"4096 keys accepted");
        ((JArray)a["curves"][0]["keys"]).Add(new JObject { ["time"]=4096 });
        Check(Reject(()=>ValidateEnvelope(a)),"4097 keys reject");
        a=Args(1); a["curves"][0]["propertyName"]=new string('x',512*1024);
        Check(Reject(()=>ValidateEnvelope(a)),"512 KiB budget rejects oversized envelope");
        var binding=new EditorCurveBinding { path="Body", type=typeof(Keyframe), propertyName="p" };
        Check(Reject(()=>ValidateBinding(binding,new[]{binding},new EditorCurveBinding[0],true)),"duplicate binding rejects even overwrite");
        Check(Reject(()=>ValidateBinding(binding,new EditorCurveBinding[0],new[]{binding},false)),"existing binding requires overwrite");
        ValidateBinding(binding,new EditorCurveBinding[0],new[]{binding},true); Check(true,"explicit overwrite accepted");
        Check(Reject(()=>BuildCurve(JObject.Parse(@"{'keys':[{'time':0,'value':0},{'time':0,'value':1}]}"))),"invalid final curve duplicate times reject");
        Check(Reject(()=>BuildCurve(JObject.Parse(@"{'keys':[{'inWeight':2}]}"))),"invalid final curve weight rejects");
        return failures==0?0:1;
    }
    '''
    program = "using System.Text; using Newtonsoft.Json;\n" + STUBS + "public struct EditorCurveBinding { public string path,propertyName; public Type type; } public class Probe {\n" + "\n".join(selected) + runner + "}"
    cs=tmp_path / "Probe.cs"; cs.write_text(program, encoding="utf-8")
    dll=tmp_path / "Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==14
