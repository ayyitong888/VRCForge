"""Execute actual renderer-array planner and comparison methods, without Unity emulation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT=Path(__file__).resolve().parents[1]


def test_actual_shader_batch_restore_ownership(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs").read_text(encoding="utf-8")
    selected=[method(source,s) for s in ("private sealed class Edit","private static bool Restore","internal static JArray Validate","private static void CheckSize","internal static string Digest")]
    seam=r'''
class Shader {}
class Material {internal string Json;}
static class EditorJsonUtility {internal static string ToJson(Material m)=>m.Json;}
[Flags] enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
static class AssetDatabase {
 internal static Dictionary<string,Material> Materials=new Dictionary<string,Material>();
 internal static bool FailImport;
 internal static void ImportAsset(string p,ImportAssetOptions o){if(FailImport)throw new Exception("import");Materials[p].Json=File.ReadAllText(p);}
 internal static T LoadAssetAtPath<T>(string p) where T:class=>Materials[p] as T;
}
class FileEvidence {internal string Digest,Identity="id";}
class StableAssetEvidence {internal string Guid="guid";internal FileEvidence File,Meta=new FileEvidence{Digest="meta"};}
static class SceneObjectCopyCore {
 internal static string ToAbsoluteAssetPath(string p)=>p;
 internal static StableAssetEvidence ReadStableAssetEvidence(string p,string label)=>new StableAssetEvidence{File=new FileEvidence{Digest=File.ReadAllText(p)}};
 internal static bool StableAssetEvidenceMatches(StableAssetEvidence a,StableAssetEvidence b,bool v)=>a.Guid==b.Guid&&a.File.Digest==b.File.Digest&&a.Meta.Digest==b.Meta.Digest;
}
static class MaterialShaderTool {internal static string NormalizeOptionalAssetPath(string p,bool packages)=>p;}
'''
    runner=r'''
 static int failures;
 static void Check(bool b,string name){Console.WriteLine((b?"PASS ":"FAIL ")+name);if(!b)failures++;}
 static Edit Make(string path,bool saved=true){File.WriteAllText(path,saved?"new":"old");var m=new Material{Json="new"};AssetDatabase.Materials[path]=m;return new Edit{Path=path,Material=m,BeforeJson="old",ExpectedJson="new",Bytes=Encoding.UTF8.GetBytes("old"),Mutated=true,Before=new StableAssetEvidence{File=new FileEvidence{Digest="old"}},Owned=saved?new StableAssetEvidence{File=new FileEvidence{Digest="new"}}:null};}
 static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
 public static int Main(){
  var dir=Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString("N"));Directory.CreateDirectory(dir);var path=Path.Combine(dir,"A.mat");
  var e=Make(path);Check(Restore(e)&&File.ReadAllText(path)=="old","owned saved state restored");
  e=Make(path,false);Check(Restore(e)&&e.Material.Json=="old","failed save unchanged disk restores memory");
  e=Make(path);File.WriteAllText(path,"user");Check(!Restore(e)&&File.ReadAllText(path)=="user","unknown disk change preserved");
  e=Make(path);e.Material.Json="user";Check(!Restore(e)&&File.ReadAllText(path)=="new","unknown memory change preserved");
  e=Make(path,false);File.WriteAllText(path,"partial");Check(!Restore(e)&&File.ReadAllText(path)=="partial","unowned partial save preserved");
  e=Make(path);AssetDatabase.FailImport=true;Check(!Restore(e),"failed restore import not verified");AssetDatabase.FailImport=false;
  var req=JObject.Parse(@"{'assignments':[{'materialAssetPath':'Assets/A.mat','shaderName':'Toon'}]}");Check(Validate(req).Count==1,"one valid assignment");
  req["assignments"].Last.AddAfterSelf(req["assignments"][0].DeepClone());Check(Reject(()=>Validate(req)),"duplicate targets reject");
  req=JObject.Parse(@"{'assignments':[{'materialAssetPath':'Assets/A.mat','shaderName':'Toon'},{'rendererPath':'Avatar/A','shaderName':'Toon'}]}");Check(Reject(()=>Validate(req)),"illegal final renderer row rejects");
  req=new JObject{["assignments"]=new JArray(),["expectedAssignments"]=new string('x',512*1024)};Check(Reject(()=>Validate(req)),"sealed size bound");
  return failures==0?0:1;
 }
'''
    program="using System;using System.IO;using System.Linq;using System.Text;using System.Security.Cryptography;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq;"+seam+"public class Probe {private const int MaxBytes=512*1024;"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==10


def test_actual_shader_batch_catch_reports_all_recovery_rows(tmp_path):
    """Execute the production catch aggregation against three real Restore seams."""
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs").read_text(encoding="utf-8")
    selected=[method(source,s) for s in ("private sealed class Edit","private static bool Restore")]
    aggregation_start=source.index("                var restored = true;")
    aggregation_end=source.index("                var started = edits.Any", aggregation_start)
    aggregation=source[aggregation_start:aggregation_end]
    seam=r'''
class Shader {}
class Material {internal string Json;}
static class EditorJsonUtility {internal static string ToJson(Material m)=>m.Json;}
[Flags] enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
static class AssetDatabase {
 internal static Dictionary<string,Material> Materials=new Dictionary<string,Material>();
 internal static void ImportAsset(string p,ImportAssetOptions o){Materials[p].Json=File.ReadAllText(p);}
 internal static T LoadAssetAtPath<T>(string p) where T:class=>Materials[p] as T;
}
class FileEvidence {internal string Digest,Identity="id";}
class StableAssetEvidence {internal string Guid="guid";internal FileEvidence File,Meta=new FileEvidence{Digest="meta"};}
static class SceneObjectCopyCore {
 internal static string ToAbsoluteAssetPath(string p)=>p;
 internal static StableAssetEvidence ReadStableAssetEvidence(string p,string label)=>new StableAssetEvidence{File=new FileEvidence{Digest=File.ReadAllText(p)}};
 internal static bool StableAssetEvidenceMatches(StableAssetEvidence a,StableAssetEvidence b,bool v)=>a.Guid==b.Guid&&a.File.Digest==b.File.Digest&&a.Meta.Digest==b.Meta.Digest;
}
static class MaterialShaderTool {internal static string NormalizeOptionalAssetPath(string p,bool packages)=>p;}
'''
    runner=r'''
 static void Check(bool b,string name){Console.WriteLine((b?"PASS ":"FAIL ")+name);if(!b)throw new Exception(name);}
 static Edit Make(string path,bool owned,bool memoryDrift){File.WriteAllText(path,"new");var m=new Material{Json=memoryDrift?"user":"new"};AssetDatabase.Materials[path]=m;return new Edit{Path=path,Material=m,BeforeJson="old",ExpectedJson="new",Bytes=Encoding.UTF8.GetBytes("old"),Mutated=true,Before=new StableAssetEvidence{File=new FileEvidence{Digest="old"}},Owned=owned?new StableAssetEvidence{File=new FileEvidence{Digest="new"}}:null};}
 public static int Main(){
  var dir=Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString("N"));Directory.CreateDirectory(dir);
  var edits=new List<Edit>{Make(Path.Combine(dir,"owned.mat"),true,false),Make(Path.Combine(dir,"memory.mat"),true,true),Make(Path.Combine(dir,"unowned.mat"),false,false)};
  edits[0].RowIndex=0;edits[1].RowIndex=1;edits[2].RowIndex=2;
'''
    runner += "   " + "\n   ".join(line.strip() for line in aggregation.splitlines()) + "\n"
    runner += r'''
  Check(recoveryResults.Count==3,"all mutated rows attempted");
  Check(recoveryResults[0]["rowIndex"].Value<int>()==2&&recoveryResults[1]["rowIndex"].Value<int>()==1&&recoveryResults[2]["rowIndex"].Value<int>()==0,"reverse row order");
  Check(recoveryResults.All(row=>row["mutationStarted"].Value<bool>()&&row["materialAssetPath"].Value<string>()==edits[row["rowIndex"].Value<int>()].Path),"exact path and mutation identity");
  Check(recoveryResults[0]["restored"].Value<bool>()==false&&recoveryResults[1]["restored"].Value<bool>()==false&&recoveryResults[2]["restored"].Value<bool>(),"per-row restore booleans");
  Check(recoveryResults[0]["hadSavedOwnership"].Value<bool>()==false&&recoveryResults[1]["hadSavedOwnership"].Value<bool>()&&recoveryResults[2]["hadSavedOwnership"].Value<bool>(),"ownership markers");
  Check(!restored&&File.ReadAllText(edits[0].Path)=="old"&&File.ReadAllText(edits[1].Path)=="new"&&File.ReadAllText(edits[2].Path)=="new","aggregate remains failed");
  return 0;}
'''
    program="using System;using System.IO;using System.Linq;using System.Text;using System.Collections.Generic;using Newtonsoft.Json.Linq;"+seam+"public class Probe {"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==6
