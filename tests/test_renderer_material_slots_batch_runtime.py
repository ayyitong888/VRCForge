"""Execute actual renderer-array planner and comparison methods, without Unity emulation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT=Path(__file__).resolve().parents[1]


def test_actual_renderer_array_planner(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityRendererMaterialSlotsBatch.cs").read_text(encoding="utf-8")
    selected=[method(source,sig) for sig in ("internal static JArray ValidateEnvelope", "private static string Text", "internal static JArray BuildExpected", "internal static void Verify")]
    runner=r'''
    static int failures;
    static void Check(bool v,string name){Console.WriteLine((v?"PASS ":"FAIL ")+name);if(!v)failures++;}
    static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
    static JArray Before()=>JArray.Parse(@"[{'rendererPath':'Avatar/A','rendererComponentId':'aaa','slots':['old|1','null','keep|2']},{'rendererPath':'Avatar/B','rendererComponentId':'bbb','slots':['old|1']}]" );
    static JArray Rows()=>JArray.Parse(@"[{'rendererPath':'Avatar/A','rendererComponentId':'aaa','slotIndex':0,'newMaterial':{'assetPath':'new','assetGuid':'3'}},{'rendererPath':'Avatar/B','rendererComponentId':'bbb','slotIndex':0,'newMaterial':{'assetPath':'new','assetGuid':'3'}}]");
    static JObject Request(int count) { var rows=new JArray();for(int i=0;i<count;i++)rows.Add(new JObject{["rendererPath"]="Avatar/A",["rendererComponentId"]=new string('a',64),["slotIndex"]=i,["newMaterialAssetPath"]="Assets/New.mat"});return new JObject{["assignments"]=rows}; }
    public static int Main(){
      var before=Before();var original=before.DeepClone();var after=BuildExpected(before,Rows());
      Check(after[0]["slots"][0].Value<string>()=="new|3" && after[1]["slots"][0].Value<string>()=="new|3","multiple renderers updated");
      Check(after[0]["slots"][1].Value<string>()=="null" && after[0]["slots"][2].Value<string>()=="keep|2","untouched null order retained");
      Check(JToken.DeepEquals(before,original),"planning immutable");
      var rows=Rows();rows.Add(rows[0].DeepClone());Check(Reject(()=>BuildExpected(before,rows)),"duplicate slot rejects");
      rows=Rows();rows[1]["slotIndex"]=2;Check(Reject(()=>BuildExpected(before,rows)) && JToken.DeepEquals(before,original),"illegal final item rejects without partial plan mutation");
      rows=Rows();rows[1]["rendererComponentId"]="missing";Check(Reject(()=>BuildExpected(before,rows)),"missing renderer rejects");
      rows=Rows();rows[1]["newMaterial"]=null;Check(Reject(()=>BuildExpected(before,rows)),"missing material rejects");
      rows=Rows();rows[0]["newMaterial"]["assetPath"]="old";rows[0]["newMaterial"]["assetGuid"]="1";Check(Reject(()=>BuildExpected(before,rows)),"unchanged row rejects");
      Check(ValidateEnvelope(Request(256)).Count==256,"256 rows accepted");
      Check(Reject(()=>ValidateEnvelope(Request(257))),"257 rows reject");
      var req=Request(1);req["rendererPath"]="single";Check(Reject(()=>ValidateEnvelope(req)),"mixed fields reject");
      req=Request(1);req["assignments"][0]["slotIndex"]=-1;Check(Reject(()=>ValidateEnvelope(req)),"negative slot rejects");
      req=Request(1);req["assignments"][0]["rendererComponentId"]="";Check(Reject(()=>ValidateEnvelope(req)),"missing identity rejects");
      req=Request(1);req["expectedProjectPath"]=new string('x',512*1024);Check(Reject(()=>ValidateEnvelope(req)),"512KiB cap enforced");
      Check(!Reject(()=>Verify(after,after.DeepClone())),"exact readback passes");
      var drift=after.DeepClone();drift[0]["slots"][2]="changed";Check(Reject(()=>Verify(after,drift)),"untouched slot drift rejects");
      return failures==0?0:1;
    }
    '''
    program="using System;using System.Linq;using System.Text;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq; public class Probe { private const int MaxBytes = 512 * 1024;"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==16
