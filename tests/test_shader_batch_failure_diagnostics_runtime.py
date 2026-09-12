"""Compile/run the production in-memory shader save failure diagnostics."""
import json, os, shutil, subprocess
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]
def test_saved_memory_material_diagnostics(tmp_path):
    src=(ROOT/'Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs').read_text(encoding='utf-8')
    selected=[method(src,s) for s in ['internal static JObject SavedMemoryStateFailure(', 'private static JObject JsonEvidence(', 'private static JObject JsonDifference(', 'private static string Sha256(']]
    runner=r'''static int n;static void Check(bool b,string s){if(!b)throw new Exception(s);Console.WriteLine("PASS "+s);n++;}
public static int Main(){
 var b="{\"x\":1}";var a="{\"x\":2}";
 var d=SavedMemoryStateFailure(19,"Assets/A.mat",true,b,b,b);
 Check(d["rowIndex"].Value<int>()==19&&d["materialAssetPath"].Value<string>()=="Assets/A.mat","row/path");
 Check(d["failurePhase"].Value<string>()=="post_save_memory"&&d["failedPredicates"].Count()==1,"dirty-only predicate");
 Check(d["actual"]["dirty"].Value<bool>()&&d["actual"]["json"]["preview"].Value<string>()==b,"dirty evidence");
 d=SavedMemoryStateFailure(19,"Assets/A.mat",false,b,b,a);Check(d["failedPredicates"].Count()==1&&d["failedPredicates"][0].Value<string>()=="serializedMaterial","JSON-only predicate");
 Check(d["jsonDifference"]["fields"][0]["path"].Value<string>()=="/x","field evidence");
 d=SavedMemoryStateFailure(19,"Assets/A.mat",true,b,b,a);Check(d["failedPredicates"].Count()==2,"both predicates");
 Check(SavedMemoryStateFailure(19,"Assets/A.mat",false,b,b,b)==null,"fully consistent null");
 d=SavedMemoryStateFailure(19,"Assets/A.mat",false,b,b,"{ \"x\":1 }");Check(d["jsonDifference"]["representationOnly"].Value<bool>(),"representation mismatch rejects");
 var x=new JObject();var y=new JObject();for(var i=0;i<100;i++){x["p"+i]=i;y["p"+i]=i+1;}d=SavedMemoryStateFailure(19,"Assets/A.mat",false,b,x.ToString(),y.ToString());Check(d["jsonDifference"]["fields"].Count()==32&&d["jsonDifference"]["truncated"].Value<bool>(),"bounded difference count");
 d=SavedMemoryStateFailure(19,"Assets/A.mat",false,b,b,new string('x',300000));Check(d["jsonDifference"]["truncated"].Value<bool>()&&d.ToString().Length<10000,"oversize evidence bounded");return 0;}'''
    dotnet=shutil.which('dotnet'); assert dotnet, 'dotnet executable is required';base=Path(os.environ.get('DOTNET_ROOT') or dotnet).resolve();base=base.parent if base.is_file() else base;sdk=sorted((base/'sdk').glob('8.*/Roslyn/bincore/csc.dll'))[-1];refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))[-1];nj=sdk.parents[2]/'Newtonsoft.Json.dll'
    cs=tmp_path/'Probe.cs';cs.write_text('using System;using System.Linq;using System.Text;using System.Security.Cryptography;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq;public class Probe{'+''.join(selected)+runner+'}',encoding='utf-8');dll=tmp_path/'Probe.dll'
    r=subprocess.run([dotnet,str(sdk),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}',*[f'-r:{p}' for p in refs.glob('*.dll')],f'-r:{nj}',str(cs)],capture_output=True,text=True,timeout=30);assert r.returncode==0,r.stdout+r.stderr
    shutil.copyfile(nj,tmp_path/'Newtonsoft.Json.dll');(tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}))
    r=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stdout+r.stderr
    assert r.stdout.count('PASS ')==10


def test_saved_material_diagnostics(tmp_path):
    src=(ROOT/'Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs').read_text(encoding='utf-8')
    selected=[method(src,s) for s in ['internal static JObject SavedStateFailure(', 'private static JObject JsonEvidence(', 'private static JObject JsonDifference(', 'private static string Sha256(']]
    runner=r'''static int n;static void Check(bool b,string s){if(!b)throw new Exception(s);Console.WriteLine("PASS "+s);n++;}
public static int Main(){var b="{\"x\":1}";var a="{\"x\":2}";
var d=SavedStateFailure(17,"Assets/A.mat","g","g","m","m",true,true,b,b,a);
Check(d["rowIndex"].Value<int>()==17&&d["materialAssetPath"].Value<string>()=="Assets/A.mat","row identity");
Check(d["failedPredicates"].Count()==1&&d["failedPredicates"][0].Value<string>()=="serializedMaterial","exact JSON predicate");
Check(d["jsonDifference"]["fields"][0]["path"].Value<string>()=="/x"&&d["jsonDifference"]["fields"][0]["actual"]["preview"].Value<string>()=="2","exact field evidence");
Check(SavedStateFailure(0,"A","g","g","m","m",true,true,b,b,b)==null,"matching state unchanged");
d=SavedStateFailure(0,"A","g","bad","m","bad",false,false,b,b,null);Check(d["failedPredicates"].Count()==5,"all predicates retained");
d=SavedStateFailure(0,"A","g","g","m","m",true,true,b,b,"{ \"x\":1 }");Check(d["jsonDifference"]["representationOnly"].Value<bool>(),"text mismatch remains rejection");
var x=new JObject();var y=new JObject();for(var i=0;i<100;i++){x["p"+i]=i;y["p"+i]=i+1;}d=SavedStateFailure(0,"A","g","g","m","m",true,true,b,x.ToString(),y.ToString());Check(d["jsonDifference"]["fields"].Count()==32&&d["jsonDifference"]["truncated"].Value<bool>(),"bounded difference count");
d=SavedStateFailure(0,"A","g","g","m","m",true,true,b,b,new string('x',300000));Check(d["jsonDifference"]["truncated"].Value<bool>()&&d.ToString().Length<10000,"oversize summarized");return 0;}'''
    dotnet=shutil.which('dotnet');assert dotnet;base=Path(os.environ.get('DOTNET_ROOT') or dotnet).resolve();base=base.parent if base.is_file() else base;sdk=sorted((base/'sdk').glob('8.*/Roslyn/bincore/csc.dll'))[-1];refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))[-1];nj=sdk.parents[2]/'Newtonsoft.Json.dll'
    cs=tmp_path/'Probe.cs';cs.write_text('using System;using System.Linq;using System.Text;using System.Security.Cryptography;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq;public class Probe{'+''.join(selected)+runner+'}',encoding='utf-8');dll=tmp_path/'Probe.dll'
    r=subprocess.run([dotnet,str(sdk),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}',*[f'-r:{p}' for p in refs.glob('*.dll')],f'-r:{nj}',str(cs)],capture_output=True,text=True,timeout=30);assert r.returncode==0,r.stdout+r.stderr
    shutil.copyfile(nj,tmp_path/'Newtonsoft.Json.dll');(tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}))
    r=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stdout+r.stderr
    assert r.stdout.count('PASS ')==8
