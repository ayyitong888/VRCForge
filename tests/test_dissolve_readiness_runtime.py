"""Execute actual renderer-array planner and comparison methods, without Unity emulation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT=Path(__file__).resolve().parents[1]


def test_actual_dissolve_readiness_classifier(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/ShaderMaterialScanner.cs").read_text(encoding="utf-8")
    selected=[method(source,"private static DissolveReadiness GetDissolveReadiness")]
    if "internal static DissolveReadiness ClassifyDissolveReadiness" in source:
        selected.append(method(source,"internal static DissolveReadiness ClassifyDissolveReadiness"))
    runner=r'''
    static int failures;
    static void Check(string shader,int mode,string expected,string readiness="needs_feature_verification",bool props=true){var actual=GetDissolveReadiness(new Material{Mode=mode,Props=props},"lilToon",shader);bool pass=actual.renderingMode==expected&&actual.featureStatus=="unverified"&&actual.readiness==readiness;Console.WriteLine((pass?"PASS ":"FAIL ")+shader+" "+mode+" => "+actual.renderingMode+" "+actual.readiness);if(!pass)failures++;}
    public static int Main(){
      Check("Hidden/lilToonCutout",0,"Cutout");
      Check("Hidden/lilToonTransparent",0,"Transparent");
      Check("Hidden/lilToonOnePassTransparentOutline",6,"Transparent");
      Check("Hidden/lilToonTessellationCutoutOutline",0,"Cutout");
      Check("lilToon",1,"Opaque","needs_preparation");
      Check("Hidden/lilToonOutline",2,"Opaque","needs_preparation");
      Check("_lil/lilToonMulti",0,"Opaque","needs_preparation");
      Check("_lil/lilToonMulti",1,"Cutout");
      Check("Hidden/lilToonMultiOutline",2,"Transparent");
      Check("_lil/lilToonMulti",5,"GemOrFur","needs_preparation");
      Check("Hidden/lilToonFurCutout",1,"GemOrFur","needs_preparation");
      Check("Custom/MyCutout",1,"Unknown","needs_preparation");
      Check("Hidden/lilToonCutout",0,"Cutout","needs_preparation",false);
      Check("Hidden/lilToonLiteCutout",0,"Cutout","needs_preparation",false);
      Check("Hidden/lilToonUnknownTransparent",2,"Unknown","needs_preparation");
      return failures==0?0:1;
    }
    '''
    seam="class Material { public int Mode;public bool Props;public bool HasProperty(string p)=>p==\"_TransparentMode\"||Props;public float GetFloat(string p)=>Mode;} static class Mathf{public static int RoundToInt(float x)=>(int)Math.Round(x);}"
    program="using System;"+seam+method(source,"public sealed class DissolveReadiness")+"public class Probe {"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==15
