"""Save-scope characterization: global SaveAssets must not persist task-external dirty materials."""
import json, os, shutil, subprocess
from pathlib import Path
import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs").read_text(encoding="utf-8")


def test_task_external_dirty_material_is_untouched_on_success_and_postsave_failure(tmp_path):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("dotnet required")
    sdk_root = Path(os.environ.get("DOTNET_ROOT", Path(dotnet).resolve().parent))
    sdk = sorted((sdk_root / "sdk").glob("3.1.*/Roslyn/bincore/csc.dll"))
    refs = sorted((sdk_root / "packs/Microsoft.NETCore.App.Ref").glob("3.1.*/ref/netcoreapp3.1"))
    if not sdk or not refs:
        pytest.skip("Local SDK3.1 compiler and netcoreapp3.1 reference pack required")
    compiler = sdk[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    selected = [method(SOURCE, name) for name in (
        "internal static JArray Validate", "private static void CheckSize", "internal static string Digest",
        "private static JObject Preview", "private sealed class Edit", "private static bool Restore",
        "internal static JObject SavedMemoryStateFailure", "internal static JObject SavedStateFailure",
        "private static string Sha256", "private static JObject JsonEvidence", "private static JObject JsonDifference",
        "private static JObject PreflightAssignments", "internal static object HandleCommand")]
    stubs = r'''
namespace VRCForge.Core.MCP { public class VRCForgeToolResult { public bool IsSuccessful; public object Payload; public string Message; public static VRCForgeToolResult Completed(string m, object p)=>new VRCForgeToolResult{IsSuccessful=true,Payload=p,Message=m}; public static VRCForgeToolResult FailedWithCode(string c,string m,object p)=>new VRCForgeToolResult{IsSuccessful=false,Payload=p,Message=m}; } }
namespace UnityEngine { public class Shader { public string name; } public class Material { public string Path; private Shader current; public Shader shader { get=>current; set { current=value; Json=value == null ? "old" : value.name; } } public string Json; public bool Dirty; public Material(string p,string n){Path=p;current=new Shader{name=n};Json="old";} } }
namespace UnityEditor { using UnityEngine; [Flags] public enum ImportAssetOptions { ForceSynchronousImport=1,ForceUpdate=2 } public static class EditorUtility { public static bool IsDirty(Material m)=>m.Dirty; public static void SetDirty(Material m){m.Dirty=true;} } public static class EditorJsonUtility { public static string ToJson(Material m)=>m.Json; } public static class AssetDatabase { public static Dictionary<string,Material> Mats=new Dictionary<string,Material>(); public static string FaultPath=""; public static string ForeignPath=""; public static string Project=Directory.GetCurrentDirectory(); public static Material LoadAssetAtPath<Material>(string p) where Material:class=>Mats[p] as Material; public static void SaveAssets(){ foreach(var m in Mats.Values) if(m.Dirty){File.WriteAllText(m.Path,m.Json);m.Dirty=false;} if(FaultPath!="") Mats[FaultPath].Json="drift-after-disk-write"; if(ForeignPath!="") File.WriteAllText(ForeignPath,"foreign"); } public static void ImportAsset(string p,ImportAssetOptions o){Mats[p].Json=File.ReadAllText(p);Mats[p].Dirty=false;} public static string GetAssetPath(Shader s)=>"Assets/Native.shader"; public static string AssetPathToGUID(string p)=>"guid-"+p.Replace('/','_'); } }
namespace VRCForge.Editor { using UnityEngine; public sealed class StableFile { public string Digest; } public sealed class StableAssetEvidence { public string Guid; public StableFile File=new StableFile(); public StableFile Meta=new StableFile(); } public static class SceneObjectCopyCore { public static string ToAbsoluteAssetPath(string p)=>p; public static StableAssetEvidence ReadStableAssetEvidence(string p,string l)=>new StableAssetEvidence{Guid=UnityEditor.AssetDatabase.AssetPathToGUID(p),File=new StableFile{Digest=Digest(File.ReadAllText(p))},Meta=new StableFile{Digest="meta"}}; public static bool StableAssetEvidenceMatches(StableAssetEvidence a,StableAssetEvidence b,bool v)=>a!=null&&b!=null&&a.Guid==b.Guid&&a.File.Digest==b.File.Digest&&a.Meta.Digest==b.Meta.Digest; static string Digest(string s){using(var h=SHA256.Create())return string.Concat(h.ComputeHash(Encoding.UTF8.GetBytes(s)).Select(x=>x.ToString("x2")));} } public static class MaterialShaderTool { public sealed class BatchSharedMaterialImpact{} public static bool MatchesCurrentProject(string p)=>p==Directory.GetCurrentDirectory(); public static string NormalizeOptionalAssetPath(string p,bool x)=>p; public static string NormalizeResolvedShaderAssetPath(string p)=>p; public static Material InspectWritableMaterialAsset(Material m)=>m; public static Shader ResolveShader(string n,string p)=>new Shader{name=n}; public static JObject CaptureMaterialRenderState(Material m)=>new JObject(); public static void RestoreMaterialRenderState(Material m,JObject j){} public static void VerifyMaterialRenderState(Material m,JObject j){} public static JObject PreflightShaderAssignment(Material m,Shader s,IDictionary<Material,Shader> a=null)=>null; public static BatchSharedMaterialImpact BuildBatchSharedMaterialImpact(JArray r)=>new BatchSharedMaterialImpact(); public static object HandleSingleCommand(JObject r,BatchSharedMaterialImpact c){var p=r["materialAssetPath"].Value<string>();var n=r["shaderName"].Value<string>();var before=SceneObjectCopyCore.ReadStableAssetEvidence(p,"preview");return VRCForge.Core.MCP.VRCForgeToolResult.Completed("preview",new JObject{{"verified",true},{"preview",true},{"materialAssetPath",p},{"materialAssetGuid",before.Guid},{"materialFileDigestBefore",before.File.Digest},{"beforeShader","Old"},{"beforeShaderAssetPath","Assets/Old.shader"},{"beforeShaderAssetGuid","old-guid"},{"requestedShader",n},{"shaderAssetPath","Assets/Native.shader"},{"shaderAssetGuid","native-guid"},{"sharedImpactDigest","impact"}});} } }
'''
    runner = r'''
    public static class Probe { static int failures; static void Check(bool x,string n){Console.WriteLine((x?"PASS " : "FAIL ")+n);if(!x)failures++;} static JObject Apply(bool fault){
var rows=new JArray(); foreach(var p in new[]{"Assets/A.mat","Assets/B.mat","Assets/C.mat"}) rows.Add(new JObject{{"materialAssetPath",p},{"shaderName","Native"}});
var preview=new JObject{{"assignments",rows},{"expectedProjectPath",Directory.GetCurrentDirectory()},{"preview",true},{"saveAssets",true}}; var pr=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialShaderBatch.HandleCommand(preview); Check(pr.IsSuccessful,"preview"); var pp=JObject.FromObject(pr.Payload);
var apply=(JObject)preview.DeepClone(); apply["preview"]=false; apply["expectedPreviewDigest"]=pp["previewDigest"]; apply["expectedAssignments"]=pp["assignments"];
UnityEditor.AssetDatabase.FaultPath=fault?"Assets/B.mat":""; var result=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialShaderBatch.HandleCommand(apply); Check(fault ? !result.IsSuccessful : result.IsSuccessful, fault ? "failure result is unsuccessful" : "success result is successful"); var payload=JObject.FromObject(result.Payload); if(fault){ Check(payload["failureDetails"]["rowIndex"].Value<int>()==1 && payload["failureDetails"]["failurePhase"].Value<string>()=="post_save_memory", "failure identifies row1 post-save-memory"); } return payload; }
public static int Main(){Directory.CreateDirectory("Assets");
var paths=new[]{"Assets/A.mat","Assets/B.mat","Assets/C.mat","Assets/D.mat"}; foreach(var p in paths){File.WriteAllText(p,"old");File.WriteAllText(p+".meta","meta");UnityEditor.AssetDatabase.Mats[p]=new UnityEngine.Material(p,"Old");}
var d=UnityEditor.AssetDatabase.Mats["Assets/D.mat"]; d.Json="foreign-dirty"; UnityEditor.EditorUtility.SetDirty(d); var before=File.ReadAllText("Assets/D.mat"); Apply(false); Check(File.ReadAllText("Assets/D.mat")==before,"success leaves external D file unchanged"); Check(UnityEditor.AssetDatabase.Mats["Assets/D.mat"].Dirty,"success leaves external D dirty");
foreach(var p in paths){File.WriteAllText(p,"old");File.WriteAllText(p+".meta","meta");UnityEditor.AssetDatabase.Mats[p]=new UnityEngine.Material(p,"Old");} d=UnityEditor.AssetDatabase.Mats["Assets/D.mat"]; d.Json="foreign-dirty"; UnityEditor.EditorUtility.SetDirty(d); before=File.ReadAllText("Assets/D.mat"); Apply(true); Check(File.ReadAllText("Assets/D.mat")==before,"failure leaves external D file unchanged"); Check(UnityEditor.AssetDatabase.Mats["Assets/D.mat"].Dirty,"failure leaves external D dirty"); return failures==0?0:1; }}
'''
    extracted = "namespace VRCForge.Editor { using UnityEditor; using UnityEngine; using VRCForge.Core.MCP; internal static class UnityMaterialShaderBatch { private const string Schema=\"v1\"; private const int MaxBytes=512*1024;" + "\n".join(selected) + "}}"
    program = "using System;using System.IO;using System.Linq;using System.Text;using System.Security.Cryptography;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq;using UnityEngine;" + extracted + stubs + runner
    cs = tmp_path / "Probe.cs"; cs.write_text(program, encoding="utf-8"); dll = tmp_path / "Probe.dll"
    cmd=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(cmd,capture_output=True,text=True,timeout=60); assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll"); (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}))
    run=subprocess.run([dotnet,str(dll)],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    print(run.stdout + run.stderr, end="")
    assert run.returncode == 0, run.stdout + run.stderr
